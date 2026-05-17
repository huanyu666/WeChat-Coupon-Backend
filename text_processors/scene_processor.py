"""
Scene 管理处理器
"""
from typing import Any, Dict, Optional

from .base_processor import BaseTextProcessor
from utils.response import TextRspMsg
from utils.scene_storage import get_scene_storage
from utils.verification_code import get_link_verification_manager
from config.config import ACCOUNT_SPECIFIC_CONFIGS


class SceneProcessor(BaseTextProcessor):
    def __init__(self, logger):
        super().__init__(logger)
        self.scene_storage = get_scene_storage()
        self.activation_manager = get_link_verification_manager()

    def _get_settings(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        account_id = msg.get("ToUserName", "")
        specific_config = ACCOUNT_SPECIFIC_CONFIGS.get(account_id, {})
        settings = specific_config.get("scene_settings", {})
        return settings if isinstance(settings, dict) else {}

    def _get_text(self, msg: Dict[str, Any], key: str, default: str) -> str:
        value = self._get_settings(msg).get(key)
        return str(value) if value not in (None, "") else default

    def _get_keywords(self, msg: Dict[str, Any], key: str, default: list[str]) -> list[str]:
        raw_value = self._get_settings(msg).get(key, default)
        if isinstance(raw_value, str):
            raw_items = raw_value.splitlines()
        elif isinstance(raw_value, (list, tuple, set)):
            raw_items = list(raw_value)
        else:
            raw_items = list(default)
        return [str(item or "").strip() for item in raw_items if str(item or "").strip()]

    def _check_activation_code(self, msg: Dict[str, Any]) -> bool:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        valid_code_info = self.activation_manager.has_valid_code(user_id)
        if not valid_code_info:
            self.logger.debug(f"[{account_name}] 用户 {user_id} 没有有效的激活码，跳过Scene处理")
            return False
        return True

    def _build_activation_required_response(self, msg: Dict[str, Any]) -> Any:
        rsp = TextRspMsg(msg)
        rsp.content = self._get_text(msg, "activation_required_message", "❌ 当前还没有有效激活码\n\n请先完成激活后再使用scene管理功能。")
        return rsp

    def is_trigger(self, text: str) -> bool:
        return False

    def is_trigger_for_message(self, text: str, msg: Dict[str, Any]) -> bool:
        text_lower = text.lower().strip()
        all_keywords = (
            self._get_keywords(msg, "add_keywords", ["添加scene", "新增scene", "绑定scene"])
            + self._get_keywords(msg, "query_keywords", ["查询scene", "查看scene", "我的scene", "scene列表"])
            + self._get_keywords(msg, "update_keywords", ["更新scene", "修改scene", "编辑scene"])
            + self._get_keywords(msg, "delete_keywords", ["删除scene", "移除scene"])
            + self._get_keywords(msg, "switch_keywords", ["切换scene", "使用scene", "设置scene"])
        )
        return any(keyword.lower() in text_lower for keyword in all_keywords)

    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        text_lower = text.lower().strip()
        add_keywords = self._get_keywords(msg, "add_keywords", ["添加scene", "新增scene", "绑定scene"])
        query_keywords = self._get_keywords(msg, "query_keywords", ["查询scene", "查看scene", "我的scene", "scene列表"])
        update_keywords = self._get_keywords(msg, "update_keywords", ["更新scene", "修改scene", "编辑scene"])
        delete_keywords = self._get_keywords(msg, "delete_keywords", ["删除scene", "移除scene"])
        switch_keywords = self._get_keywords(msg, "switch_keywords", ["切换scene", "使用scene", "设置scene"])
        if any(keyword.lower() in text_lower for keyword in add_keywords):
            return self._handle_add(msg, text)
        if any(keyword.lower() in text_lower for keyword in query_keywords):
            return self._handle_query(msg)
        if any(keyword.lower() in text_lower for keyword in update_keywords):
            return self._handle_update(msg, text)
        if any(keyword.lower() in text_lower for keyword in delete_keywords):
            return self._handle_delete(msg, text)
        if any(keyword.lower() in text_lower for keyword in switch_keywords):
            return self._handle_switch(msg, text)
        return None

    def _handle_add(self, msg: Dict[str, Any], text: str) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        self.logger.info(f"[{account_name}] 用户 {user_id} 请求添加Scene")

        parts = text.strip().split(maxsplit=2)
        if len(parts) < 2:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "add_missing_value_message", "❌ 请提供scene\n\n格式：添加scene <scene> [别名]\n例如：添加scene abc123 常用")
            return rsp

        scene = parts[1].strip()
        alias = parts[2].strip() if len(parts) > 2 else None

        existing_id = self.scene_storage.find_by_scene(user_id, scene)
        if existing_id:
            existing_info = self.scene_storage.get_all(user_id)[existing_id]
            rsp = TextRspMsg(msg)
            alias = existing_info.get('alias') or self._get_text(msg, "list_alias_fallback", "未设置")
            rsp.content = self._get_text(msg, "duplicate_value_template", "❌ 该scene已存在\n\n别名：{alias}\nscene：{scene}").format(alias=alias, scene=scene)
            return rsp

        if alias:
            existing_by_alias = self.scene_storage.find_by_alias(user_id, alias)
            if existing_by_alias:
                rsp = TextRspMsg(msg)
                rsp.content = self._get_text(msg, "duplicate_alias_template", "❌ 别名「{alias}」已被使用\n\n请使用其他别名").format(alias=alias)
                return rsp

        self.scene_storage.add(user_id, scene, alias=alias)
        rsp = TextRspMsg(msg)
        if alias:
            rsp.content = self._get_text(msg, "add_success_with_alias_template", "✅ scene添加成功\n\n别名：{alias}\nscene：{scene}\n当前已设置为使用此scene").format(alias=alias, scene=scene)
        else:
            rsp.content = self._get_text(msg, "add_success_without_alias_template", "✅ scene添加成功\n\nscene：{scene}\n当前已设置为使用此scene").format(scene=scene)
        return rsp

    def _handle_query(self, msg: Dict[str, Any]) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        all_scenes = self.scene_storage.get_all(user_id)
        current_id = self.scene_storage.get_current_id(user_id)
        rsp = TextRspMsg(msg)
        if not all_scenes:
            rsp.content = self._get_text(msg, "empty_list_message", "您暂时没有绑定scene\n\n发送「添加scene <scene> [别名]」可添加新的scene")
            return rsp

        from datetime import datetime

        content_parts = [self._get_text(msg, "list_header_template", "📋 您的scene列表（共{count}个）：\n").format(count=len(all_scenes))]
        current_marker = self._get_text(msg, "list_current_marker", "⭐")
        alias_fallback = self._get_text(msg, "list_alias_fallback", "未设置")
        time_fallback = self._get_text(msg, "list_time_fallback", "未知")
        current_tag = self._get_text(msg, "list_current_tag", "【当前使用】")
        for idx, (scene_id, scene_info) in enumerate(all_scenes.items(), 1):
            is_current = current_marker if scene_id == current_id else "  "
            alias = scene_info.get("alias", alias_fallback)
            scene = scene_info.get("scene", "")
            created_at = scene_info.get("created_at", 0)
            time_str = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M") if created_at else time_fallback
            content_parts.append(f"\n{is_current} {idx}. {alias}")
            content_parts.append(f"   scene：{scene}")
            content_parts.append(f"   创建时间：{time_str}")
            if scene_id == current_id:
                content_parts.append(f"   {current_tag}")

        content_parts.append(f"\n\n{self._get_text(msg, 'list_tips_header', '💡 提示:')}")
        content_parts.append(self._get_text(msg, "list_tip_switch", "• 发送「切换scene <别名或scene>」切换当前使用的scene"))
        content_parts.append(self._get_text(msg, "list_tip_delete", "• 发送「删除scene <别名或scene>」删除指定scene"))
        content_parts.append(self._get_text(msg, "list_tip_update", "• 发送「更新scene <别名或scene> <新scene> [新别名]」更新scene"))
        rsp.content = "\n".join(content_parts)
        return rsp

    def _handle_update(self, msg: Dict[str, Any], text: str) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        parts = text.strip().split(maxsplit=3)
        if len(parts) < 3:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "update_missing_args_message", "❌ 请提供别名/scene和新scene\n\n格式：更新scene <别名或scene> <新scene> [新别名]")
            return rsp
        identifier = parts[1].strip()
        new_scene = parts[2].strip()
        new_alias = parts[3].strip() if len(parts) > 3 else NotImplemented

        scene_id = self.scene_storage.find_by_alias(user_id, identifier)
        if not scene_id:
            scene_id = self.scene_storage.find_by_scene(user_id, identifier)
        if not scene_id:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "not_found_template", "❌ 未找到scene：{identifier}\n\n请使用别名或scene").format(identifier=identifier)
            return rsp

        if new_alias:
            existing_by_alias = self.scene_storage.find_by_alias(user_id, new_alias)
            if existing_by_alias and existing_by_alias != scene_id:
                rsp = TextRspMsg(msg)
                rsp.content = self._get_text(msg, "duplicate_alias_template", "❌ 别名「{alias}」已被使用\n\n请使用其他别名").format(alias=new_alias)
                return rsp

        success = self.scene_storage.update(user_id, scene_id, scene=new_scene, alias=new_alias)
        rsp = TextRspMsg(msg)
        if not success:
            rsp.content = self._get_text(msg, "update_failed_message", "❌ 更新失败")
            return rsp
        if new_alias:
            rsp.content = self._get_text(msg, "update_success_with_alias_template", "✅ scene更新成功\n\n新别名：{alias}\n新scene：{scene}").format(alias=new_alias, scene=new_scene)
        else:
            rsp.content = self._get_text(msg, "update_success_without_alias_template", "✅ scene更新成功\n\n新scene：{scene}").format(scene=new_scene)
        return rsp

    def _handle_delete(self, msg: Dict[str, Any], text: str) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "delete_missing_identifier_message", "❌ 请指定要删除的scene\n\n格式：删除scene <别名或scene>")
            return rsp
        identifier = parts[1].strip()
        all_scenes = self.scene_storage.get_all(user_id)
        scene_id = self.scene_storage.find_by_alias(user_id, identifier)
        if not scene_id:
            scene_id = self.scene_storage.find_by_scene(user_id, identifier)
        if not scene_id:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "not_found_template", "❌ 未找到scene：{identifier}\n\n请使用别名或scene").format(identifier=identifier)
            return rsp

        scene_info = all_scenes.get(scene_id, {})
        success = self.scene_storage.delete(user_id, scene_id)
        rsp = TextRspMsg(msg)
        if not success:
            rsp.content = self._get_text(msg, "delete_failed_message", "❌ 删除失败")
            return rsp
        rsp.content = self._get_text(msg, "delete_success_template", "✅ scene删除成功\n\n已删除：{alias}\nscene：{scene}").format(
            alias=scene_info.get('alias', self._get_text(msg, "list_alias_fallback", "未设置")),
            scene=scene_info.get('scene', ''),
        )
        return rsp

    def _handle_switch(self, msg: Dict[str, Any], text: str) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "switch_missing_identifier_message", "❌ 请指定要切换的scene\n\n格式：切换scene <别名或scene>")
            return rsp
        identifier = parts[1].strip()
        all_scenes = self.scene_storage.get_all(user_id)
        scene_id = self.scene_storage.find_by_alias(user_id, identifier)
        if not scene_id:
            scene_id = self.scene_storage.find_by_scene(user_id, identifier)
        if not scene_id:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "not_found_template", "❌ 未找到scene：{identifier}\n\n请使用别名或scene").format(identifier=identifier)
            return rsp
        success = self.scene_storage.set_current(user_id, scene_id)
        rsp = TextRspMsg(msg)
        if not success:
            rsp.content = self._get_text(msg, "switch_failed_message", "❌ 切换失败")
            return rsp
        scene_info = all_scenes[scene_id]
        rsp.content = self._get_text(msg, "switch_success_template", "✅ 已切换scene\n\n当前使用：{alias}\nscene：{scene}").format(
            alias=scene_info.get('alias', self._get_text(msg, "list_alias_fallback", "未设置")),
            scene=scene_info.get('scene', ''),
        )
        return rsp
