"""
京东链接处理器

将用户输入的 https://u.jd.com/xxx 链接 URL 编码后拼接到
pages/union/proxy/proxy?spreadUrl= 后面，生成京东小程序/MP 链接。
"""
import asyncio
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import re
from urllib.parse import quote
from .stateful_processor import StatefulTextProcessor
from utils.response import TextRspMsg
from utils.account_config import resolve_zmkey
from link_handlers.api_client import LinkConversionAPI
from utils.verification_code import (
    get_link_verification_manager,
    get_verification_manager,
    migrate_code_between_pools,
)

         
JD_APPID = "wx91d27dbf599dff74"
JD_PATH_PREFIX = "pages/union/proxy/proxy?spreadUrl="


class GetJdProcessor(StatefulTextProcessor):
    STATE_WAITING_ACTIVATION_CODE = "waiting_activation_code"
    STATE_WAITING_LINK = "waiting_link"
    STATE_WAITING_MP_CHOICE = "waiting_mp_choice"

    def __init__(self, logger):
        super().__init__(logger, state_timeout=600)
        self.activation_manager = get_link_verification_manager()
        self.unbound_activation_manager = get_verification_manager()
        self._reload_configs()
        self.logger.info("GetJdProcessor 初始化完成")

    def _reload_configs(self):
        try:
            from config.config import PROMPTS_CONFIG
            self.prompts_config = PROMPTS_CONFIG.get('get_jd_flow', {})
            trigger_keyword = self.prompts_config.get('trigger_keyword', '获取京东')
            self.trigger_keywords = [trigger_keyword]
            self.logger.info("GetJdProcessor 配置重新加载成功")
        except Exception as e:
            self.logger.error(f"GetJdProcessor 配置重新加载失败: {e}")

    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        self.logger.info(f"[{account_name}] 用户 {user_id} 触发获取京东链接功能")

        valid_code_info = self.activation_manager.has_valid_code(user_id)

        if valid_code_info:
            self.logger.info(f"[{account_name}] 用户 {user_id} 有有效的激活码")
            self.set_user_state(user_id, self.STATE_WAITING_LINK, {
                "verified": True,
                "activation_code": valid_code_info["code"]
            })
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('request_link', '请输入京东短链接（支持多个）：')
            return rsp

        self.set_user_state(user_id, self.STATE_WAITING_ACTIVATION_CODE)
        rsp = TextRspMsg(msg)
        rsp.content = "请输入激活码"
        return rsp

    async def ahandle_state(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Optional[Any]:
        import time
        from .stateful_processor import USER_STATES

        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        current_state = state.get("state")

        cancel_keywords = self.prompts_config.get('cancel_keywords', [])
        default_exit_keywords = ["取消", "退出", "返回", "quit", "cancel", "back"]
        all_cancel_keywords = list(set(cancel_keywords + default_exit_keywords))

        if text.strip() in all_cancel_keywords:
            self.clear_user_state(user_id, "用户取消操作")
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('cancel_message', '已取消操作')
            return rsp

        if user_id in USER_STATES:
            USER_STATES[user_id]["last_update_time"] = time.time()

        if current_state == self.STATE_WAITING_ACTIVATION_CODE:
            return await self._ahandle_activation_code(msg, text, state)
        if current_state == self.STATE_WAITING_MP_CHOICE:
            return await self._ahandle_mp_choice(msg, text, state)
        if current_state == self.STATE_WAITING_LINK:
            return await self._ahandle_link(msg, text, state)

        self.logger.warning(f"[{account_name}] 未知状态: {current_state}")
        self.clear_user_state(user_id, "未知状态")
        return None

    async def _ahandle_activation_code(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        code = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 输入激活码: {code}")

        ok, msg_text = migrate_code_between_pools(
            code,
            user_id,
            self.unbound_activation_manager,
            self.activation_manager,
            logger=self.logger,
        )
        if not ok:
            self.logger.warning(f"[{account_name}] 激活码验证失败: {msg_text}")
            self.clear_user_state(user_id, "激活码验证失败")
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 激活码验证失败: {msg_text}\n\n请重新获取激活码后再试。"
            return rsp

        self.logger.info(f"[{account_name}] 用户 {user_id} 激活码验证成功并已绑定")
        self.set_user_state(user_id, self.STATE_WAITING_LINK, {
            "verified": True,
            "activation_code": code
        })
        rsp = TextRspMsg(msg)
        rsp.content = self.prompts_config.get('request_link', '请输入京东短链接（支持多个）：')
        return rsp

    async def _ahandle_mp_choice(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        choice = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 选择链接类型: {choice}")
        if choice not in ["1", "2"]:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('error_invalid_mp_choice', '输入无效，请输入 1 或 2')
            return rsp

        state["mp_choice"] = choice
        original_text = state.get("original_text", "")
        if not original_text:
            self.logger.error(f"[{account_name}] 状态中缺少原始文本")
            self.clear_user_state(user_id, "状态错误")
            rsp = TextRspMsg(msg)
            rsp.content = "处理错误，请重新开始"
            return rsp

        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
        return await self._aprocess_links(msg, original_text, state)

    async def _ahandle_link(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        self.logger.info(f"[{account_name}] 用户 {user_id} 发送链接: {text}")
        original_text = state.get("original_text", "")
        if original_text and original_text != text:
            self.logger.info(f"[{account_name}] 检测到新输入，清除之前的选择")
            state.pop("mp_choice", None)

        link_infos = self._extract_jd_links_with_positions(text)
        if not link_infos:
            self.logger.warning(f"[{account_name}] 未找到有效的京东短链接: {text}")
            rsp = TextRspMsg(msg)
            error_msg = self.prompts_config.get('error_invalid_link', '❌ 未找到有效的京东短链接（https://u.jd.com/xxx）')
            continue_prompt = self.prompts_config.get('continue_prompt', '\n\n💡 可以继续输入链接进行处理，或输入"退出"结束')
            rsp.content = error_msg + continue_prompt
            self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
            return rsp

        if "mp_choice" not in state:
            state["original_text"] = text
            self.set_user_state(user_id, self.STATE_WAITING_MP_CHOICE, state)
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('request_mp_choice',
                '请选择链接类型：\n<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=1">1. 原始链接</a>\n<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=2">2. MP链接（mp://格式）</a>\n\n请输入 1 或 2')
            return rsp
        return await self._aprocess_links(msg, text, state)

    async def _aprocess_links(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        account_config = msg.get("_account_config", {})
        mp_choice = state.get("mp_choice", "1")
        convert_to_mp = (mp_choice == "2")
        link_infos = self._extract_jd_links_with_positions(text)
        self.logger.info(f"[{account_name}] 识别到 {len(link_infos)} 个京东短链接，转MP: {convert_to_mp}")

        zmkey = resolve_zmkey(msg, account_config)
        if not zmkey:
            self.logger.error(f"[{account_name}] 未配置zmkey")
            self.clear_user_state(user_id, "配置错误")
            rsp = TextRspMsg(msg)
            rsp.content = "配置错误，请联系管理员"
            return rsp

        api_client = LinkConversionAPI(zmkey, self.logger)
        activation_code = state.get("activation_code", "")
        remaining_count = 0
        activation_code_info = None
        if activation_code:
            is_valid, error_msg = self.activation_manager.verify_code(activation_code, user_id=user_id, consume=False)
            if is_valid:
                with self.activation_manager._lock:
                    code_upper = activation_code.upper().strip()
                    code_index = self.activation_manager._code_index
                    if code_upper in code_index:
                        index_entry = code_index[code_upper]
                        matched_code = index_entry["code_info"]
                        max_uses = matched_code.get("max_uses", 1)
                        used_count = matched_code.get("used_count", 0)
                        remaining_count = max_uses - used_count
                        activation_code_info = {
                            "code": activation_code,
                            "code_upper": code_upper,
                            "matched_code": matched_code,
                            "matched_user_id": index_entry["user_id"]
                        }
                        self.logger.info(f"[{account_name}] 激活码剩余次数: {remaining_count}")
                    else:
                        self.logger.warning(f"[{account_name}] 无法获取激活码信息")
            else:
                self.logger.warning(f"[{account_name}] 激活码验证失败: {error_msg}")

        count_lock = asyncio.Lock()
        remaining_count_ref = [remaining_count]
        result_map = {}
        failed_map = {}

        async def process_single_link(idx: int, link_info: Dict[str, Any]):
            short_link = link_info['content']
            if activation_code_info:
                async with count_lock:
                    if remaining_count_ref[0] <= 0:
                        self.logger.warning(f"[{account_name}] 激活码次数已用完，跳过第 {idx} 个链接")
                        return idx, None, "激活码次数不足"
                    remaining_count_ref[0] -= 1
                    current_remaining = remaining_count_ref[0]
                self.logger.info(f"[{account_name}] 处理第 {idx} 个链接: {short_link}，剩余次数: {current_remaining}")
            else:
                self.logger.info(f"[{account_name}] 处理第 {idx} 个链接: {short_link}")
            try:
                spread_url_encoded = quote(short_link, safe='')
                path = f"{JD_PATH_PREFIX}{spread_url_encoded}"
                result_link = await api_client.agenerate_custom_link(JD_APPID, path)
                if not result_link:
                    if activation_code_info:
                        async with count_lock:
                            remaining_count_ref[0] += 1
                    return idx, None, "生成小程序链接失败"
                if convert_to_mp:
                    result_link = self._convert_to_mp_link(result_link)
                    if not result_link:
                        if activation_code_info:
                            async with count_lock:
                                remaining_count_ref[0] += 1
                        return idx, None, "转换MP格式失败"
                return idx, result_link, None
            except Exception as e:
                self.logger.error(f"[{account_name}] 第 {idx} 个链接处理异常: {e}")
                if activation_code_info:
                    async with count_lock:
                        remaining_count_ref[0] += 1
                return idx, None, "处理失败"

        tasks = [process_single_link(idx, link_info) for idx, link_info in enumerate(link_infos)]
        for idx, result_link, failed_reason in await asyncio.gather(*tasks):
            if result_link:
                result_map[idx] = result_link
            else:
                failed_map[idx] = failed_reason or "处理失败"

        if result_map and activation_code_info:
            success_count = len(result_map)
            consumed_count = remaining_count - remaining_count_ref[0]
            if consumed_count > 0:
                def save_activation_code_async():
                    try:
                        with self.activation_manager._lock:
                            code_upper = activation_code_info["code_upper"]
                            code_index = self.activation_manager._code_index
                            if code_upper not in code_index:
                                self.logger.warning(f"[{account_name}] 激活码已不存在，跳过保存")
                                return
                            index_entry = code_index[code_upper]
                            matched_code = index_entry["code_info"]
                            matched_user_id = index_entry["user_id"]
                            matched_code["used_count"] = matched_code.get("used_count", 0) + consumed_count
                            max_uses = matched_code.get("max_uses", 1)
                            if matched_code["used_count"] >= max_uses:
                                matched_code["used"] = True
                                if matched_user_id in self.activation_manager._codes:
                                    if matched_code in self.activation_manager._codes[matched_user_id]:
                                        self.activation_manager._codes[matched_user_id].remove(matched_code)
                                    if not self.activation_manager._codes[matched_user_id]:
                                        del self.activation_manager._codes[matched_user_id]
                                if code_upper in code_index:
                                    del code_index[code_upper]
                            self.activation_manager._save_unlocked()
                        self.logger.info(f"[{account_name}] 激活码 {activation_code} 已异步保存 {consumed_count} 次消耗（共转换 {success_count} 个链接）")
                    except Exception as e:
                        self.logger.error(f"[{account_name}] 异步保存激活码消耗失败: {e}", exc_info=True)
                threading.Thread(target=save_activation_code_async, daemon=True).start()
                self.logger.info(f"[{account_name}] 激活码 {activation_code} 已消耗 {consumed_count} 次使用次数（共转换 {success_count} 个链接），正在异步保存")

        rsp = TextRspMsg(msg)
        if result_map or failed_map:
            result_text = text
            sorted_indices = sorted(range(len(link_infos)), key=lambda i: link_infos[i].get('start', 0), reverse=True)
            for idx in sorted_indices:
                link_info = link_infos[idx]
                start_pos = link_info.get('start', -1)
                end_pos = link_info.get('end', -1)
                link_content = link_info['content']
                if idx in result_map:
                    processed_link = result_map[idx]
                    result_text = result_text[:start_pos] + processed_link + result_text[end_pos:] if start_pos >= 0 and end_pos > start_pos else result_text.replace(link_content, processed_link, 1)
                elif idx in failed_map:
                    failure_text = failed_map[idx]
                    result_text = result_text[:start_pos] + failure_text + result_text[end_pos:] if start_pos >= 0 and end_pos > start_pos else result_text.replace(link_content, failure_text, 1)
                else:
                    result_text = result_text[:start_pos] + "处理失败" + result_text[end_pos:] if start_pos >= 0 and end_pos > start_pos else result_text.replace(link_content, "处理失败", 1)
            rsp.content = result_text
        else:
            error_msg = self.prompts_config.get('error_all_failed', '❌ 所有链接转换失败')
            continue_prompt = self.prompts_config.get('continue_prompt', '\n\n💡 可以继续输入链接进行处理，或输入"退出"结束')
            rsp.content = error_msg + continue_prompt

        state.pop("mp_choice", None)
        state.pop("original_text", None)
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
        return rsp

    def _extract_jd_links_with_positions(self, text: str) -> list:
        """提取京东短链接 https://u.jd.com/xxx"""
        pattern = r"https?://u\.jd\.com/[a-zA-Z0-9\-._~]+"
        link_infos = []
        for match in re.finditer(pattern, text):
            link_infos.append({
                "type": "jd_short_link",
                "content": match.group(0),
                "start": match.start(),
                "end": match.end(),
                "original": match.group(0)
            })
        self.logger.info(f"提取到{len(link_infos)}个京东短链接（带位置信息）")
        return link_infos

    def _convert_to_mp_link(self, link: str) -> Optional[str]:
        try:
            last_slash_pos = link.rfind('/')
            if last_slash_pos == -1:
                self.logger.warning(f"无法找到路径分隔符: {link}")
                return None
            path_part = link[last_slash_pos + 1:]
            if not path_part:
                self.logger.warning(f"路径部分为空: {link}")
                return None
            return f"mp://{path_part}"
        except Exception as e:
            self.logger.error(f"转换MP链接失败: {e}")
            return None
