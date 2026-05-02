import asyncio
import re
import threading
import urllib.parse
from typing import Any, Dict, Optional

from .stateful_processor import StatefulTextProcessor
from link_handlers.api_client import LinkConversionAPI
from utils.account_config import resolve_zmkey
from utils.response import TextRspMsg
from utils.scene_storage import get_scene_storage
from utils.verification_code import (
    get_link_verification_manager,
    get_verification_manager,
    migrate_code_between_pools,
)


ELEME_APPID = "wxece3a9a4c82f58c9"
ELEME_PAGE_PREFIX = "pages/container/index"


class GetElemeProcessor(StatefulTextProcessor):
    STATE_WAITING_ACTIVATION_CODE = "waiting_activation_code"
    STATE_WAITING_LINK = "waiting_link"
    STATE_WAITING_MP_CHOICE = "waiting_mp_choice"

    def __init__(self, logger):
        super().__init__(logger, state_timeout=600)
        self.activation_manager = get_link_verification_manager()
        self.unbound_activation_manager = get_verification_manager()
        self.scene_storage = get_scene_storage()
        self._reload_configs()
        self.logger.info("GetElemeProcessor 初始化完成")

    def _reload_configs(self):
        try:
            from config.config import PROMPTS_CONFIG

            self.prompts_config = PROMPTS_CONFIG.get("get_eleme_flow", {})
            trigger_keyword = self.prompts_config.get("trigger_keyword", "获取饿了么")
            self.trigger_keywords = [trigger_keyword]
            self.logger.info("GetElemeProcessor 配置重新加载成功")
        except Exception as e:
            self.logger.error(f"GetElemeProcessor 配置重新加载失败: {e}")

    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        self.logger.info(f"[{account_name}] 用户 {user_id} 触发获取饿了么功能")

        valid_code_info = self.activation_manager.has_valid_code(user_id)
        if valid_code_info:
            current_scene = self.scene_storage.get_current(user_id)
            if not current_scene:
                rsp = TextRspMsg(msg)
                rsp.content = "❌ 还未设置scene\n\n请先发送「添加scene <scene> [别名]」或「切换scene <别名或scene>」"
                return rsp
            self.set_user_state(
                user_id,
                self.STATE_WAITING_LINK,
                {
                    "verified": True,
                    "activation_code": valid_code_info["code"],
                    "scene": current_scene,
                },
            )
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "request_link",
                "请输入饿了么链接（支持 pages/container/index、mp://、#小程序://饿了么）：",
            )
            return rsp

        self.set_user_state(
            user_id,
            self.STATE_WAITING_ACTIVATION_CODE,
        )
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
            return await self._ahandle_activation_code(msg, text, state)
        if current_state == self.STATE_WAITING_MP_CHOICE:
            return await self._ahandle_mp_choice(msg, text, state)
        if current_state == self.STATE_WAITING_LINK:
            return await self._ahandle_link(msg, text, state)
        self.clear_user_state(user_id, "未知状态")
        return None

    async def _ahandle_activation_code(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
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
            self.clear_user_state(user_id, "激活码验证失败")
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 激活码验证失败: {msg_text}\n\n请重新获取激活码后再试。"
            return rsp

        scene = self.scene_storage.get_current(user_id)
        if not scene:
            self.clear_user_state(user_id, "未设置scene")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 激活码验证成功，但还未设置scene\n\n请先发送「添加scene <scene> [别名]」或「切换scene <别名或scene>」"
            return rsp
        self.set_user_state(
            user_id,
            self.STATE_WAITING_LINK,
            {
                "verified": True,
                "activation_code": code,
                "scene": scene,
            },
        )
        rsp = TextRspMsg(msg)
        rsp.content = self.prompts_config.get(
            "request_link",
            "请输入饿了么链接（支持 pages/container/index、mp://、#小程序://饿了么）：",
        )
        return rsp

    async def _ahandle_mp_choice(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        choice = text.strip()
        if choice not in ["1", "2"]:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "error_invalid_mp_choice", "输入无效，请输入 1 或 2"
            )
            return rsp

        state["mp_choice"] = choice
        original_text = state.get("original_text", "")
        if not original_text:
            self.clear_user_state(user_id, "状态错误")
            rsp = TextRspMsg(msg)
            rsp.content = "处理错误，请重新开始"
            return rsp
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
        return await self._aprocess_links(msg, original_text, state)

    async def _ahandle_link(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        original_text = state.get("original_text", "")
        if original_text and original_text != text:
            state.pop("mp_choice", None)

        link_infos = self._extract_links_with_positions(text)
        if not link_infos:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "error_invalid_link",
                "❌ 未找到有效的饿了么链接（pages/container/index、mp://、#小程序://饿了么）",
            ) + self.prompts_config.get(
                "continue_prompt",
                '\n\n💡 可以继续输入链接进行处理，或输入"退出"结束',
            )
            self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
            return rsp

        if "mp_choice" not in state:
            state["original_text"] = text
            self.set_user_state(user_id, self.STATE_WAITING_MP_CHOICE, state)
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get(
                "request_mp_choice",
                '请选择链接类型：\n<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=1">1. 原始链接</a>\n<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=2">2. MP链接（mp://格式）</a>\n\n请输入 1 或 2',
            )
            return rsp
        return await self._aprocess_links(msg, text, state)

    async def _aprocess_links(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        account_config = msg.get("_account_config", {})
        scene = state.get("scene") or self.scene_storage.get_current(user_id)
        if not scene:
            self.clear_user_state(user_id, "未设置scene")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 还未设置scene\n\n请先发送「添加scene <scene> [别名]」或「切换scene <别名或scene>」"
            return rsp

        mp_choice = state.get("mp_choice", "1")
        convert_to_mp = mp_choice == "2"
        link_infos = self._extract_links_with_positions(text)
        zmkey = resolve_zmkey(msg, account_config)
        if not zmkey:
            self.clear_user_state(user_id, "配置错误")
            rsp = TextRspMsg(msg)
            rsp.content = "配置错误，请联系管理员"
            return rsp

        api_client = LinkConversionAPI(zmkey, self.logger)
        activation_code = state.get("activation_code", "")
        remaining_count = 0
        activation_code_info = None
        if activation_code:
            is_valid, _ = self.activation_manager.verify_code(
                activation_code, user_id=user_id, consume=False
            )
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
                        }

        result_map = {}
        failed_map = {}
        count_lock = asyncio.Lock()
        remaining_count_ref = [remaining_count]

        async def process_single_link(idx: int, link_info: Dict[str, Any]):
            if activation_code_info:
                async with count_lock:
                    if remaining_count_ref[0] <= 0:
                        return idx, None, "激活码次数不足"
                    remaining_count_ref[0] -= 1
            try:
                page_path = await self._aresolve_page_path(api_client, link_info["content"])
                if not page_path:
                    if activation_code_info:
                        async with count_lock:
                            remaining_count_ref[0] += 1
                    return idx, None, "链接解析失败"

                self.logger.info(f"[{account_name}] 饿了么替换前page_path: {page_path}")
                updated_page_path = self._replace_scene_in_page_path(page_path, scene)
                if not updated_page_path:
                    if activation_code_info:
                        async with count_lock:
                            remaining_count_ref[0] += 1
                    return idx, None, "处理失败"
                self.logger.info(f"[{account_name}] 饿了么替换后page_path: {updated_page_path}")

                result_link = await api_client.agenerate_custom_link(ELEME_APPID, updated_page_path)
                if not result_link:
                    if activation_code_info:
                        async with count_lock:
                            remaining_count_ref[0] += 1
                    return idx, None, "生成链接失败"

                if convert_to_mp:
                    result_link = self._convert_to_mp_link(result_link)
                    if not result_link:
                        if activation_code_info:
                            async with count_lock:
                                remaining_count_ref[0] += 1
                        return idx, None, "转换MP格式失败"
                return idx, result_link, None
            except Exception as e:
                self.logger.error(f"[{account_name}] 第 {idx} 个饿了么链接处理异常: {e}")
                if activation_code_info:
                    async with count_lock:
                        remaining_count_ref[0] += 1
                return idx, None, "处理失败"

        processed_results = await asyncio.gather(
            *(process_single_link(idx, link_info) for idx, link_info in enumerate(link_infos))
        )
        for idx, result_link, failed_reason in processed_results:
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
                                return
                            index_entry = code_index[code_upper]
                            matched_code = index_entry["code_info"]
                            matched_user_id = index_entry["user_id"]
                            matched_code["used_count"] = matched_code.get("used_count", 0) + consumed_count
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
                            f"[{account_name}] 激活码 {activation_code} 已异步保存 {consumed_count} 次消耗（共转换 {success_count} 个饿了么链接）"
                        )
                    except Exception as e:
                        self.logger.error(f"[{account_name}] 异步保存激活码消耗失败: {e}", exc_info=True)

                threading.Thread(target=save_activation_code_async, daemon=True).start()

        rsp = TextRspMsg(msg)
        if result_map or failed_map:
            result_text = text
            sorted_indices = sorted(
                range(len(link_infos)),
                key=lambda i: link_infos[i].get("start", 0),
                reverse=True,
            )
            for idx in sorted_indices:
                link_info = link_infos[idx]
                start_pos = link_info.get("start", -1)
                end_pos = link_info.get("end", -1)
                link_content = link_info["content"]
                if idx in result_map:
                    processed_link = result_map[idx]
                    if start_pos >= 0 and end_pos > start_pos:
                        result_text = result_text[:start_pos] + processed_link + result_text[end_pos:]
                    else:
                        result_text = result_text.replace(link_content, processed_link, 1)
                elif idx in failed_map:
                    failure_text = failed_map[idx]
                    if start_pos >= 0 and end_pos > start_pos:
                        result_text = result_text[:start_pos] + failure_text + result_text[end_pos:]
                    else:
                        result_text = result_text.replace(link_content, failure_text, 1)
            rsp.content = result_text
        else:
            rsp.content = self.prompts_config.get("error_all_failed", "❌ 所有链接转换失败") + self.prompts_config.get(
                "continue_prompt",
                '\n\n💡 可以继续输入链接进行处理，或输入"退出"结束',
            )

        state.pop("mp_choice", None)
        state.pop("original_text", None)
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
        return rsp

    def _extract_links_with_positions(self, text: str) -> list:
        patterns = [
            r"pages/container/index\?[^\s]+",
            r"mp://[A-Za-z0-9]+",
            r"#小程序://饿了么[^\s]+",
        ]
        link_infos = []
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                link_infos.append(
                    {
                        "content": match.group(0),
                        "start": match.start(),
                        "end": match.end(),
                    }
                )
        link_infos.sort(key=lambda item: item["start"])
        return link_infos

    async def _aresolve_page_path(
        self, api_client: LinkConversionAPI, link_content: str
    ) -> Optional[str]:
        normalized = link_content.strip()
        if normalized.startswith("pages/") or normalized.startswith("commercialize/"):
            return normalized

        step1_result = await api_client.astep1_parse_link(normalized)
        if not step1_result:
            return None
        appid = step1_result.get("appid", "")
        if appid and appid != ELEME_APPID:
            return None
        page = step1_result.get("page", "")
        if not page:
            return None
        if page:
            return page
        decoded_page = urllib.parse.unquote(page)
        if decoded_page:
            return decoded_page
        return None

    def _replace_scene_in_page_path(self, page_path: str, scene: str) -> Optional[str]:
        encoded_scene = urllib.parse.quote(scene, safe="")

        if "?" not in page_path:
            return f"{page_path}?scene={scene}"

        base_path, query = page_path.split("?", 1)
        q_match = re.search(r"(^|&)q=", query)
        if q_match:
            q_value_start = q_match.end()
            q_value_end = len(query)
            next_param_pos = query.find("&", q_value_start)
            q_head = query[q_value_start:next_param_pos if next_param_pos >= 0 else len(query)]
            q_is_encoded_url = self._looks_like_encoded_url(q_head)
            if q_is_encoded_url and next_param_pos >= 0:
                q_value_end = next_param_pos
            q_value = query[q_value_start:q_value_end]

            encoded_scene_pattern = re.compile(
                r"(%26scene%3D)(.*?)(?=(?:%26|&|$))", re.IGNORECASE
            )
            decoded_scene_pattern = re.compile(
                r"(scene=)(.*?)(?=(?:%26|&|$))", re.IGNORECASE
            )

            if encoded_scene_pattern.search(q_value):
                new_q_value = encoded_scene_pattern.sub(
                    lambda match: f"{match.group(1)}{encoded_scene}",
                    q_value,
                    count=1,
                )
                new_q_value = self._remove_duplicate_scene_params(new_q_value)
            elif decoded_scene_pattern.search(q_value):
                new_q_value = decoded_scene_pattern.sub(
                    lambda match: f"{match.group(1)}{scene}",
                    q_value,
                    count=1,
                )
                new_q_value = self._remove_duplicate_scene_params(new_q_value)
            else:
                last_amp = q_value.rfind("&")
                last_encoded_amp = q_value.rfind("%26")
                if q_is_encoded_url or (
                    last_encoded_amp >= 0 and (last_amp < 0 or last_encoded_amp > last_amp)
                ):
                    new_q_value = f"{q_value}%26scene%3D{encoded_scene}"
                elif last_amp >= 0 and (
                    last_encoded_amp < 0 or last_amp > last_encoded_amp
                ):
                    new_q_value = f"{q_value}&scene={scene}"
                else:
                    new_q_value = f"{q_value}&scene={scene}"

            outer_tail = self._replace_outer_scene_params(query[q_value_end:], scene)
            new_query = query[:q_value_start] + new_q_value + outer_tail
            return f"{base_path}?{new_query}"

        outer_scene_pattern = re.compile(r"(scene=)(.*?)(?=(?:&|%26|$))", re.IGNORECASE)
        if outer_scene_pattern.search(query):
            new_query = outer_scene_pattern.sub(
                lambda match: f"{match.group(1)}{scene}",
                query,
                count=1,
            )
            return f"{base_path}?{new_query}"

        last_amp = query.rfind("&")
        last_encoded_amp = query.rfind("%26")
        if last_encoded_amp >= 0 and (last_amp < 0 or last_encoded_amp > last_amp):
            new_query = f"{query}%26scene%3D{encoded_scene}"
        elif last_amp >= 0 and (last_encoded_amp < 0 or last_amp > last_encoded_amp):
            new_query = f"{query}&scene={scene}"
        else:
            new_query = f"{query}&scene={scene}" if query else f"scene={scene}"
        return f"{base_path}?{new_query}"

    def _replace_outer_scene_params(self, query_tail: str, scene: str) -> str:
        if not query_tail:
            return query_tail
        scene_pattern = re.compile(r"(&scene=)(.*?)(?=(?:&|$))", re.IGNORECASE)
        if not scene_pattern.search(query_tail):
            return query_tail
        return scene_pattern.sub(
            lambda match: f"{match.group(1)}{scene}",
            query_tail,
        )

    def _looks_like_encoded_url(self, value: str) -> bool:
        normalized = str(value or "").lower()
        return (
            "%3a%2f%2f" in normalized
            or normalized.startswith("http%3a%2f%2f")
            or normalized.startswith("https%3a%2f%2f")
        )

    def _remove_duplicate_scene_params(self, value: str) -> str:
        scene_seen = False

        def replace_decoded(match):
            nonlocal scene_seen
            if not scene_seen:
                scene_seen = True
                return match.group(0)
            return ""

        result = re.sub(
            r"&scene=[^&%]*(?=(?:&|%26|$))",
            replace_decoded,
            value,
            flags=re.IGNORECASE,
        )

        scene_seen = False

        def replace_encoded(match):
            nonlocal scene_seen
            if not scene_seen:
                scene_seen = True
                return match.group(0)
            return ""

        return re.sub(
            r"%26scene%3D[^&%]*(?=(?:%26|&|$))",
            replace_encoded,
            result,
            flags=re.IGNORECASE,
        )

    def _convert_to_mp_link(self, link: str) -> Optional[str]:
        try:
            last_slash_pos = link.rfind("/")
            if last_slash_pos == -1:
                return None
            path_part = link[last_slash_pos + 1 :]
            if not path_part:
                return None
            return f"mp://{path_part}"
        except Exception:
            return None
