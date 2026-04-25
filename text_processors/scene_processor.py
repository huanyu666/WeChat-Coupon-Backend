"""
Scene 管理处理器
"""
from typing import Any, Dict, Optional

from .base_processor import BaseTextProcessor
from utils.response import TextRspMsg
from utils.scene_storage import get_scene_storage
from utils.verification_code import get_link_verification_manager


class SceneProcessor(BaseTextProcessor):
    def __init__(self, logger):
        super().__init__(logger)
        self.scene_storage = get_scene_storage()
        self.activation_manager = get_link_verification_manager()

        self.add_keywords = ["添加scene", "新增scene", "绑定scene"]
        self.query_keywords = ["查询scene", "查看scene", "我的scene", "scene列表"]
        self.update_keywords = ["更新scene", "修改scene", "编辑scene"]
        self.delete_keywords = ["删除scene", "移除scene"]
        self.switch_keywords = ["切换scene", "使用scene", "设置scene"]

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
        rsp.content = "❌ 当前还没有有效激活码\n\n请先完成激活后再使用scene管理功能。"
        return rsp

    def is_trigger(self, text: str) -> bool:
        text_lower = text.lower().strip()
        all_keywords = (
            self.add_keywords
            + self.query_keywords
            + self.update_keywords
            + self.delete_keywords
            + self.switch_keywords
        )
        return any(keyword.lower() in text_lower for keyword in all_keywords)

    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        text_lower = text.lower().strip()
        if any(keyword.lower() in text_lower for keyword in self.add_keywords):
            return self._handle_add(msg, text)
        if any(keyword.lower() in text_lower for keyword in self.query_keywords):
            return self._handle_query(msg)
        if any(keyword.lower() in text_lower for keyword in self.update_keywords):
            return self._handle_update(msg, text)
        if any(keyword.lower() in text_lower for keyword in self.delete_keywords):
            return self._handle_delete(msg, text)
        if any(keyword.lower() in text_lower for keyword in self.switch_keywords):
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
            rsp.content = "❌ 请提供scene\n\n格式：添加scene <scene> [别名]\n例如：添加scene abc123 常用"
            return rsp

        scene = parts[1].strip()
        alias = parts[2].strip() if len(parts) > 2 else None

        existing_id = self.scene_storage.find_by_scene(user_id, scene)
        if existing_id:
            existing_info = self.scene_storage.get_all(user_id)[existing_id]
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 该scene已存在\n\n别名：{existing_info.get('alias', '未设置')}\nscene：{scene}"
            return rsp

        if alias:
            existing_by_alias = self.scene_storage.find_by_alias(user_id, alias)
            if existing_by_alias:
                rsp = TextRspMsg(msg)
                rsp.content = f"❌ 别名「{alias}」已被使用\n\n请使用其他别名"
                return rsp

        self.scene_storage.add(user_id, scene, alias=alias)
        rsp = TextRspMsg(msg)
        if alias:
            rsp.content = f"✅ scene添加成功\n\n别名：{alias}\nscene：{scene}\n当前已设置为使用此scene"
        else:
            rsp.content = f"✅ scene添加成功\n\nscene：{scene}\n当前已设置为使用此scene"
        return rsp

    def _handle_query(self, msg: Dict[str, Any]) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        all_scenes = self.scene_storage.get_all(user_id)
        current_id = self.scene_storage.get_current_id(user_id)
        rsp = TextRspMsg(msg)
        if not all_scenes:
            rsp.content = "您暂时没有绑定scene\n\n发送「添加scene <scene> [别名]」可添加新的scene"
            return rsp

        from datetime import datetime

        content_parts = [f"📋 您的scene列表（共{len(all_scenes)}个）：\n"]
        for idx, (scene_id, scene_info) in enumerate(all_scenes.items(), 1):
            is_current = "⭐" if scene_id == current_id else "  "
            alias = scene_info.get("alias", "未设置")
            scene = scene_info.get("scene", "")
            created_at = scene_info.get("created_at", 0)
            time_str = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M") if created_at else "未知"
            content_parts.append(f"\n{is_current} {idx}. {alias}")
            content_parts.append(f"   scene：{scene}")
            content_parts.append(f"   创建时间：{time_str}")
            if scene_id == current_id:
                content_parts.append("   【当前使用】")

        content_parts.append("\n\n💡 提示:")
        content_parts.append("• 发送「切换scene <别名或scene>」切换当前使用的scene")
        content_parts.append("• 发送「删除scene <别名或scene>」删除指定scene")
        content_parts.append("• 发送「更新scene <别名或scene> <新scene> [新别名]」更新scene")
        rsp.content = "\n".join(content_parts)
        return rsp

    def _handle_update(self, msg: Dict[str, Any], text: str) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        parts = text.strip().split(maxsplit=3)
        if len(parts) < 3:
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 请提供别名/scene和新scene\n\n格式：更新scene <别名或scene> <新scene> [新别名]"
            return rsp
        identifier = parts[1].strip()
        new_scene = parts[2].strip()
        new_alias = parts[3].strip() if len(parts) > 3 else NotImplemented

        scene_id = self.scene_storage.find_by_alias(user_id, identifier)
        if not scene_id:
            scene_id = self.scene_storage.find_by_scene(user_id, identifier)
        if not scene_id:
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 未找到scene：{identifier}\n\n请使用别名或scene"
            return rsp

        if new_alias:
            existing_by_alias = self.scene_storage.find_by_alias(user_id, new_alias)
            if existing_by_alias and existing_by_alias != scene_id:
                rsp = TextRspMsg(msg)
                rsp.content = f"❌ 别名「{new_alias}」已被其他scene使用\n\n请使用其他别名"
                return rsp

        success = self.scene_storage.update(user_id, scene_id, scene=new_scene, alias=new_alias)
        rsp = TextRspMsg(msg)
        if not success:
            rsp.content = "❌ 更新失败"
            return rsp
        if new_alias:
            rsp.content = f"✅ scene更新成功\n\n新别名：{new_alias}\n新scene：{new_scene}"
        else:
            rsp.content = f"✅ scene更新成功\n\n新scene：{new_scene}"
        return rsp

    def _handle_delete(self, msg: Dict[str, Any], text: str) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 请指定要删除的scene\n\n格式：删除scene <别名或scene>"
            return rsp
        identifier = parts[1].strip()
        all_scenes = self.scene_storage.get_all(user_id)
        scene_id = self.scene_storage.find_by_alias(user_id, identifier)
        if not scene_id:
            scene_id = self.scene_storage.find_by_scene(user_id, identifier)
        if not scene_id:
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 未找到scene：{identifier}\n\n请使用别名或scene"
            return rsp

        scene_info = all_scenes.get(scene_id, {})
        success = self.scene_storage.delete(user_id, scene_id)
        rsp = TextRspMsg(msg)
        if not success:
            rsp.content = "❌ 删除失败"
            return rsp
        rsp.content = f"✅ scene删除成功\n\n已删除：{scene_info.get('alias', '未设置')}\nscene：{scene_info.get('scene', '')}"
        return rsp

    def _handle_switch(self, msg: Dict[str, Any], text: str) -> Any:
        if not self._check_activation_code(msg):
            return self._build_activation_required_response(msg)
        user_id = msg.get("FromUserName", "")
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 请指定要切换的scene\n\n格式：切换scene <别名或scene>"
            return rsp
        identifier = parts[1].strip()
        all_scenes = self.scene_storage.get_all(user_id)
        scene_id = self.scene_storage.find_by_alias(user_id, identifier)
        if not scene_id:
            scene_id = self.scene_storage.find_by_scene(user_id, identifier)
        if not scene_id:
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 未找到scene：{identifier}\n\n请使用别名或scene"
            return rsp
        success = self.scene_storage.set_current(user_id, scene_id)
        rsp = TextRspMsg(msg)
        if not success:
            rsp.content = "❌ 切换失败"
            return rsp
        scene_info = all_scenes[scene_id]
        rsp.content = f"✅ 已切换scene\n\n当前使用：{scene_info.get('alias', '未设置')}\nscene：{scene_info.get('scene', '')}"
        return rsp
