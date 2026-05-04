import re
import threading
from typing import Any, Dict, Optional, Tuple

from .stateful_processor import StatefulTextProcessor
from utils.response import TextRspMsg
from utils.shortlink_service import get_shortlink_config, transform_shortlinks_in_text_async
from utils.verification_code import (
    get_link_verification_manager,
    get_verification_manager,
    migrate_code_between_pools,
)


TTL_CHOICES = {
    "1": (86400, "1天"),
    "2": (3 * 86400, "3天"),
    "3": (7 * 86400, "7天"),
    "4": (30 * 86400, "30天"),
    "5": (0, "永久"),
}


class ShortlinkGeneratorProcessor(StatefulTextProcessor):
    STATE_WAITING_ACTIVATION_CODE = "waiting_activation_code"
    STATE_WAITING_TTL_CHOICE = "waiting_ttl_choice"
    STATE_WAITING_CUSTOM_TTL = "waiting_custom_ttl"
    STATE_WAITING_LINK = "waiting_link"

    def __init__(self, logger):
        super().__init__(logger, state_timeout=600)
        self.activation_manager = get_link_verification_manager()
        self.unbound_activation_manager = get_verification_manager()
        self._reload_configs()
        self.logger.info("ShortlinkGeneratorProcessor 初始化完成")

    def _reload_configs(self):
        try:
            from config.config import PROMPTS_CONFIG

            self.prompts_config = PROMPTS_CONFIG.get("shortlink_generator_flow", {})
            primary_keyword = self.prompts_config.get("trigger_keyword", "生成短链")
            trigger_keywords = [primary_keyword, "短链接", "短链"]
            self.trigger_keywords = list(dict.fromkeys(trigger_keywords))
            self.logger.info("ShortlinkGeneratorProcessor 配置重新加载成功")
        except Exception as e:
            self.logger.error(f"ShortlinkGeneratorProcessor 配置重新加载失败: {e}")
            self.prompts_config = {}
            self.trigger_keywords = ["生成短链", "短链接", "短链"]

    def _enter_ttl_choice_state(
        self,
        msg: Dict[str, Any],
        user_id: str,
        state: Dict[str, Any],
    ) -> Any:
        self.set_user_state(user_id, self.STATE_WAITING_TTL_CHOICE, state)
        rsp = TextRspMsg(msg)
        rsp.content = self.prompts_config.get(
            "request_ttl_choice",
            "请选择短链接有效期：\n"
            '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=1">1. 1天</a>\n'
            '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=2">2. 3天</a>\n'
            '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=3">3. 7天</a>\n'
            '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=4">4. 30天</a>\n'
            '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=5">5. 永久</a>\n'
            '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=6">6. 自定义</a>\n\n'
            "请输入 1 到 6",
        )
        return rsp

    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        self.logger.info(f"[{account_name}] 用户 {user_id} 触发短链接生成功能")

        valid_code_info = self.activation_manager.has_valid_code(user_id)
        if valid_code_info:
            return self._enter_ttl_choice_state(
                msg,
                user_id,
                {
                    "verified": True,
                    "activation_code": valid_code_info["code"],
                },
            )

        self.set_user_state(user_id, self.STATE_WAITING_ACTIVATION_CODE)
        rsp = TextRspMsg(msg)
        rsp.content = "请输入激活码"
        return rsp

    async def ahandle_state(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Optional[Any]:
        import time
        from .stateful_processor import USER_STATES

        user_id = msg.get("FromUserName", "")
        current_state = state.get("state")
        cancel_keywords = self.prompts_config.get("cancel_keywords", [])
        default_exit_keywords = ["取消", "退出", "返回", "quit", "cancel", "back"]
        all_cancel_keywords = list(set(cancel_keywords + default_exit_keywords))

        if text.strip() in all_cancel_keywords:
            self.clear_user_state(user_id, "用户取消操作")
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get("cancel_message", "已取消操作")
            return rsp

        if user_id in USER_STATES:
            USER_STATES[user_id]["last_update_time"] = time.time()

        if current_state == self.STATE_WAITING_ACTIVATION_CODE:
            return await self._ahandle_activation_code(msg, text)
        if current_state == self.STATE_WAITING_TTL_CHOICE:
            return await self._ahandle_ttl_choice(msg, text, state)
        if current_state == self.STATE_WAITING_CUSTOM_TTL:
            return await self._ahandle_custom_ttl(msg, text, state)
        if current_state == self.STATE_WAITING_LINK:
            return await self._ahandle_link(msg, text, state)

        self.clear_user_state(user_id, "未知状态")
        return None

    async def _ahandle_activation_code(self, msg: Dict[str, Any], text: str) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        code = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 输入短链接激活码: {code}")

        ok, msg_text = migrate_code_between_pools(
            code,
            user_id,
            self.unbound_activation_manager,
            self.activation_manager,
            logger=self.logger,
        )
        if not ok:
            self.clear_user_state(user_id, "激活码验证失败")
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 激活码验证失败: {msg_text}\n\n请重新获取激活码后再试。"
            return rsp

        return self._enter_ttl_choice_state(
            msg,
            user_id,
            {
                "verified": True,
                "activation_code": code,
            },
        )

    async def _ahandle_ttl_choice(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        choice = text.strip()
        if choice == "6":
            self.set_user_state(user_id, self.STATE_WAITING_CUSTOM_TTL, state)
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "request_custom_ttl",
                "请输入自定义有效期，支持：30m / 30分钟 / 2h / 2小时 / 7d / 7天 / 永久",
            )
            return rsp

        ttl_info = TTL_CHOICES.get(choice)
        if not ttl_info:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "error_invalid_ttl_choice", "输入无效，请输入 1 到 6"
            )
            return rsp

        ttl_seconds, ttl_label = ttl_info
        state["ttl_seconds"] = ttl_seconds
        state["ttl_label"] = ttl_label
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
        rsp = TextRspMsg(msg)
        rsp.content = self.prompts_config.get(
            "request_link",
            "请发送需要生成短链接的文本，支持多个链接。\n"
            "支持 http://、https://、纯域名、域名路径。",
        )
        return rsp

    async def _ahandle_custom_ttl(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        ttl_seconds = self._parse_custom_ttl_seconds(text)
        if ttl_seconds is None:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "error_invalid_ttl",
                "有效期格式不正确，请输入 30m / 30分钟 / 2h / 2小时 / 7d / 7天 / 永久",
            )
            return rsp

        state["ttl_seconds"] = ttl_seconds
        state["ttl_label"] = text.strip()
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
        rsp = TextRspMsg(msg)
        rsp.content = self.prompts_config.get(
            "request_link",
            "请发送需要生成短链接的文本，支持多个链接。\n"
            "支持 http://、https://、纯域名、域名路径。",
        )
        return rsp

    async def _ahandle_link(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        activation_code = str(state.get("activation_code") or "").strip()
        ttl_raw = state.get("ttl_seconds")
        if ttl_raw is None:
            self.set_user_state(user_id, self.STATE_WAITING_TTL_CHOICE, state)
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "request_ttl_choice",
                "请选择短链接有效期：\n"
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=1">1. 1天</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=2">2. 3天</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=3">3. 7天</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=4">4. 30天</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=5">5. 永久</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=6">6. 自定义</a>\n\n'
                "请输入 1 到 6",
            )
            return rsp
        ttl_seconds = int(ttl_raw)

        remaining_count, activation_code_info = self._get_activation_code_remaining(
            activation_code, user_id
        )
        if remaining_count <= 0 or activation_code_info is None:
            self.set_user_state(user_id, self.STATE_WAITING_ACTIVATION_CODE)
            rsp = TextRspMsg(msg)
            rsp.content = "当前激活码已失效或次数不足，请重新输入激活码"
            return rsp

        try:
            shortlink_config = get_shortlink_config()
            if not shortlink_config.public_base_url:
                raise ValueError("短链域名未配置，请先到后台系统设置填写短链公开地址")
            transform_result = await transform_shortlinks_in_text_async(
                text,
                ttl_seconds=ttl_seconds,
                max_success_count=remaining_count,
                include_bare_urls=True,
            )
        except Exception as exc:
            self.logger.error(
                f"[{account_name}] 短链接批量生成失败: {exc}",
                exc_info=True,
            )
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "error_all_failed", "❌ 所有链接生成失败"
            ) + self.prompts_config.get(
                "continue_prompt",
                '\n\n💡 可以继续输入文本进行处理，或输入"退出"结束',
            )
            self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
            return rsp

        matched_count = int(transform_result.get("matched_count") or 0)
        success_count = int(transform_result.get("success_count") or 0)
        result_text = str(transform_result.get("text") or "")

        rsp = TextRspMsg(msg)
        if matched_count <= 0:
            rsp.content = self.prompts_config.get(
                "error_invalid_link",
                "❌ 未找到可识别链接，请发送 http://、https://、纯域名或域名路径",
            ) + self.prompts_config.get(
                "continue_prompt",
                '\n\n💡 可以继续输入文本进行处理，或输入"退出"结束',
            )
            self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
            return rsp

        if success_count <= 0:
            rsp.content = self.prompts_config.get(
                "error_all_failed", "❌ 所有链接生成失败"
            ) + self.prompts_config.get(
                "continue_prompt",
                '\n\n💡 可以继续输入文本进行处理，或输入"退出"结束',
            )
            self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
            return rsp

        self._schedule_activation_code_consumption(
            activation_code_info=activation_code_info,
            activation_code=activation_code,
            consume_count=success_count,
            account_name=account_name,
        )
        rsp.content = result_text
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
        return rsp

    def _get_activation_code_remaining(
        self, activation_code: str, user_id: str
    ) -> Tuple[int, Optional[Dict[str, str]]]:
        if not activation_code:
            return 0, None

        is_valid, _ = self.activation_manager.verify_code(
            activation_code, user_id=user_id, consume=False
        )
        if not is_valid:
            return 0, None

        with self.activation_manager._lock:
            code_upper = activation_code.upper().strip()
            code_index = self.activation_manager._code_index
            if code_upper not in code_index:
                return 0, None

            index_entry = code_index[code_upper]
            matched_code = index_entry["code_info"]
            max_uses = int(matched_code.get("max_uses", 1))
            used_count = int(matched_code.get("used_count", 0))
            remaining_count = max_uses - used_count
            if remaining_count <= 0:
                return 0, None

        return remaining_count, {
            "code_upper": code_upper,
        }

    def _schedule_activation_code_consumption(
        self,
        activation_code_info: Dict[str, str],
        activation_code: str,
        consume_count: int,
        account_name: str,
    ) -> None:
        if consume_count <= 0:
            return

        def save_activation_code_async():
            try:
                with self.activation_manager._lock:
                    code_upper = activation_code_info["code_upper"]
                    code_index = self.activation_manager._code_index
                    if code_upper not in code_index:
                        return
                    index_entry = code_index[code_upper]
                    matched_code = index_entry["code_info"]
                    matched_user_id = index_entry["user_id"]
                    matched_code["used_count"] = matched_code.get("used_count", 0) + consume_count
                    max_uses = matched_code.get("max_uses", 1)
                    if matched_code["used_count"] >= max_uses:
                        matched_code["used"] = True
                        code_upper_inner = matched_code["code"].upper()
                        if matched_user_id in self.activation_manager._codes:
                            if matched_code in self.activation_manager._codes[matched_user_id]:
                                self.activation_manager._codes[matched_user_id].remove(matched_code)
                            if not self.activation_manager._codes[matched_user_id]:
                                del self.activation_manager._codes[matched_user_id]
                        if code_upper_inner in code_index:
                            del code_index[code_upper_inner]
                    self.activation_manager._save_unlocked()
                self.logger.info(
                    f"[{account_name}] 激活码 {activation_code} 已异步保存 {consume_count} 次消耗（短链接生成）"
                )
            except Exception as e:
                self.logger.error(
                    f"[{account_name}] 异步保存短链接激活码消耗失败: {e}",
                    exc_info=True,
                )

        threading.Thread(target=save_activation_code_async, daemon=True).start()

    def _parse_custom_ttl_seconds(self, text: str) -> Optional[int]:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return None
        if normalized in {"永久", "forever", "permanent"}:
            return 0

        compact = re.sub(r"\s+", "", normalized)
        chinese_match = re.fullmatch(r"(\d+)(分钟|小时|天)", compact)
        if chinese_match:
            value = int(chinese_match.group(1))
            unit = chinese_match.group(2)
            if value <= 0:
                return None
            if unit == "分钟":
                return value * 60
            if unit == "小时":
                return value * 3600
            if unit == "天":
                return value * 86400

        ascii_match = re.fullmatch(r"(\d+)(m|h|d)", compact)
        if ascii_match:
            value = int(ascii_match.group(1))
            unit = ascii_match.group(2)
            if value <= 0:
                return None
            if unit == "m":
                return value * 60
            if unit == "h":
                return value * 3600
            if unit == "d":
                return value * 86400

        return None
