"""
P值管理处理器
"""
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor
from utils.response import TextRspMsg
from utils.p_value_storage import get_p_value_storage
from utils.verification_code import get_link_verification_manager
from config.config import ACCOUNT_SPECIFIC_CONFIGS


class PValueProcessor(BaseTextProcessor):
    """
    P值管理处理器
    
    """
    
    def __init__(self, logger):
        """
        初始化处理器
        
        Args:
            logger: 日志记录器
        """
        super().__init__(logger)
        self.p_value_storage = get_p_value_storage()
                      
        self.activation_manager = get_link_verification_manager()

    def _get_settings(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        account_id = msg.get("ToUserName", "")
        specific_config = ACCOUNT_SPECIFIC_CONFIGS.get(account_id, {})
        settings = specific_config.get("p_value_settings", {})
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
        """
        检查用户是否有有效的激活码
        
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        valid_code_info = self.activation_manager.has_valid_code(user_id)
        if not valid_code_info:
            self.logger.debug(f"[{account_name}] 用户 {user_id} 没有有效的激活码，跳过P值处理")
            return False
        
        return True
    
    def is_trigger(self, text: str) -> bool:
        """检查是否触发P值管理"""
        return False

    def is_trigger_for_message(self, text: str, msg: Dict[str, Any]) -> bool:
        text_lower = text.lower().strip()
        all_keywords = (
            self._get_keywords(msg, "add_keywords", ["添加P值", "新增P值", "绑定P值"]) +
            self._get_keywords(msg, "query_keywords", ["查询P值", "查看P值", "我的P值", "P值列表"]) +
            self._get_keywords(msg, "update_keywords", ["更新P值", "修改P值", "编辑P值"]) +
            self._get_keywords(msg, "delete_keywords", ["删除P值", "移除P值"]) +
            self._get_keywords(msg, "switch_keywords", ["切换P值", "使用P值", "设置P值"])
        )
        return any(keyword.lower() in text_lower for keyword in all_keywords)
    
    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        text_lower = text.lower().strip()
        
                  
        add_keywords = self._get_keywords(msg, "add_keywords", ["添加P值", "新增P值", "绑定P值"])
        query_keywords = self._get_keywords(msg, "query_keywords", ["查询P值", "查看P值", "我的P值", "P值列表"])
        update_keywords = self._get_keywords(msg, "update_keywords", ["更新P值", "修改P值", "编辑P值"])
        delete_keywords = self._get_keywords(msg, "delete_keywords", ["删除P值", "移除P值"])
        switch_keywords = self._get_keywords(msg, "switch_keywords", ["切换P值", "使用P值", "设置P值"])

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
        """
        处理添加P值请求
        
        支持格式：
        - "添加P值 123456" -> 添加P值，使用默认别名
        - "添加P值 123456 别名" -> 添加P值并设置别名
        """
        if not self._check_activation_code(msg):
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "activation_required_message", "❌ 当前还没有有效激活码\n\n请先完成激活后再使用P值管理功能。")
            return rsp
        
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 请求添加P值")
        
              
        parts = text.strip().split(maxsplit=2)
        
        if len(parts) < 2:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "add_missing_value_message", "❌ 请提供P值\n\n格式：添加P值 <P值> [别名]\n例如：添加P值 123456 别名")
            return rsp
        
        p_value = parts[1].strip()
        alias = parts[2].strip() if len(parts) > 2 else None
        
                   
        existing_id = self.p_value_storage.find_by_p_value(user_id, p_value)
        if existing_id:
            rsp = TextRspMsg(msg)
            existing_info = self.p_value_storage.get_all(user_id)[existing_id]
            existing_alias = existing_info.get('alias') or self._get_text(msg, "list_alias_fallback", "未设置")
            rsp.content = self._get_text(msg, "duplicate_value_template", "❌ 该P值已存在\n\n别名：{alias}\nP值：{p_value}").format(alias=existing_alias, p_value=p_value)
            return rsp
        
                           
        if alias:
            existing_by_alias = self.p_value_storage.find_by_alias(user_id, alias)
            if existing_by_alias:
                rsp = TextRspMsg(msg)
                rsp.content = self._get_text(msg, "duplicate_alias_template", "❌ 别名「{alias}」已被使用\n\n请使用其他别名").format(alias=alias)
                return rsp
        
              
        p_value_id = self.p_value_storage.add(user_id, p_value, alias=alias)
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 添加P值成功，ID: {p_value_id}")
        
        rsp = TextRspMsg(msg)
        if alias:
            rsp.content = self._get_text(msg, "add_success_with_alias_template", "✅ P值添加成功\n\n别名：{alias}\nP值：{p_value}\n当前已设置为使用此P值").format(alias=alias, p_value=p_value)
        else:
            rsp.content = self._get_text(msg, "add_success_without_alias_template", "✅ P值添加成功\n\nP值：{p_value}\n当前已设置为使用此P值").format(p_value=p_value)
        
        return rsp
    
    def _handle_query(self, msg: Dict[str, Any]) -> Any:
        """
        处理查询P值列表请求
        """
                               
        if not self._check_activation_code(msg):
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "activation_required_message", "❌ 当前还没有有效激活码\n\n请先完成激活后再使用P值管理功能。")
            return rsp
        
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 查询P值列表")
        
        all_p_values = self.p_value_storage.get_all(user_id)
        current_id = self.p_value_storage.get_current_id(user_id)
        
        if not all_p_values:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "empty_list_message", "您暂时没有绑定P值\n\n发送「添加P值 <P值> [别名]」可添加新的P值")
            return rsp
        
                
        rsp = TextRspMsg(msg)
        content_parts = [self._get_text(msg, "list_header_template", "📋 您的P值列表（共{count}个）：\n").format(count=len(all_p_values))]
        current_marker = self._get_text(msg, "list_current_marker", "⭐")
        alias_fallback = self._get_text(msg, "list_alias_fallback", "未设置")
        time_fallback = self._get_text(msg, "list_time_fallback", "未知")
        current_tag = self._get_text(msg, "list_current_tag", "【当前使用】")
        
        for idx, (p_value_id, p_value_info) in enumerate(all_p_values.items(), 1):
            is_current = current_marker if p_value_id == current_id else "  "
            alias = p_value_info.get("alias", alias_fallback)
            p_value = p_value_info.get("p_value", "")
            created_at = p_value_info.get("created_at", 0)
            
                   
            from datetime import datetime
            if created_at:
                time_str = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M")
            else:
                time_str = time_fallback
            
            content_parts.append(f"\n{is_current} {idx}. {alias}")
            content_parts.append(f"   P值：{p_value}")
            content_parts.append(f"   创建时间：{time_str}")
            if p_value_id == current_id:
                content_parts.append(f"   {current_tag}")

        content_parts.append(f"\n\n{self._get_text(msg, 'list_tips_header', '💡 提示:')}")
        content_parts.append(self._get_text(msg, "list_tip_switch", "• 发送「切换P值 <别名或P值>」切换当前使用的P值"))
        content_parts.append(self._get_text(msg, "list_tip_delete", "• 发送「删除P值 <别名或P值>」删除指定P值"))
        content_parts.append(self._get_text(msg, "list_tip_update", "• 发送「更新P值 <别名或P值> <新P值> [新别名]」更新P值"))
        
        rsp.content = "\n".join(content_parts)
        return rsp
    
    def _handle_update(self, msg: Dict[str, Any], text: str) -> Any:
                               
        if not self._check_activation_code(msg):
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "activation_required_message", "❌ 当前还没有有效激活码\n\n请先完成激活后再使用P值管理功能。")
            return rsp
        
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
              
        parts = text.strip().split(maxsplit=3)
        
        if len(parts) < 3:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "update_missing_args_message", "❌ 请提供别名/P值和新P值\n\n格式：更新P值 <别名或P值> <新P值> [新别名]\n例如：更新P值 别名 123456 新别名")
            return rsp
        
        identifier = parts[1].strip()
        new_p_value = parts[2].strip()
        new_alias = parts[3].strip() if len(parts) > 3 else NotImplemented
        
                
        p_value_id = None
        
                  
        found_id = self.p_value_storage.find_by_alias(user_id, identifier)
        if found_id:
            p_value_id = found_id
        else:
                      
            found_id = self.p_value_storage.find_by_p_value(user_id, identifier)
            if found_id:
                p_value_id = found_id
        
        if not p_value_id:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "not_found_template", "❌ 未找到P值：{identifier}\n\n请使用别名或P值").format(identifier=identifier)
            return rsp
        
                                  
        if new_alias:
            existing_by_alias = self.p_value_storage.find_by_alias(user_id, new_alias)
            if existing_by_alias and existing_by_alias != p_value_id:
                rsp = TextRspMsg(msg)
                rsp.content = self._get_text(msg, "duplicate_alias_template", "❌ 别名「{alias}」已被使用\n\n请使用其他别名").format(alias=new_alias)
                return rsp
        
              
        success = self.p_value_storage.update(user_id, p_value_id, p_value=new_p_value, alias=new_alias)
        
        if not success:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "update_failed_message", "❌ 更新失败")
            return rsp
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 更新P值成功，ID: {p_value_id}")
        
        rsp = TextRspMsg(msg)
        if new_alias:
            rsp.content = self._get_text(msg, "update_success_with_alias_template", "✅ P值更新成功\n\n新别名：{alias}\n新P值：{p_value}").format(alias=new_alias, p_value=new_p_value)
        else:
            rsp.content = self._get_text(msg, "update_success_without_alias_template", "✅ P值更新成功\n\n新P值：{p_value}").format(p_value=new_p_value)
        
        return rsp
    
    def _handle_delete(self, msg: Dict[str, Any], text: str) -> Any:
                               
        if not self._check_activation_code(msg):
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "activation_required_message", "❌ 当前还没有有效激活码\n\n请先完成激活后再使用P值管理功能。")
            return rsp
        
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
              
        parts = text.strip().split(maxsplit=1)
        
        if len(parts) < 2:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "delete_missing_identifier_message", "❌ 请指定要删除的P值\n\n格式：删除P值 <别名或P值>\n例如：删除P值 别名")
            return rsp
        
        identifier = parts[1].strip()
        
                                    
        p_value_id = None
        all_p_values = self.p_value_storage.get_all(user_id)
        
                  
        found_id = self.p_value_storage.find_by_alias(user_id, identifier)
        if found_id:
            p_value_id = found_id
        else:
                      
            found_id = self.p_value_storage.find_by_p_value(user_id, identifier)
            if found_id:
                p_value_id = found_id
        
        if not p_value_id:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "not_found_template", "❌ 未找到P值：{identifier}\n\n请使用别名或P值").format(identifier=identifier)
            return rsp
        
                    
        p_value_info = all_p_values.get(p_value_id, {})
        alias = p_value_info.get("alias", self._get_text(msg, "list_alias_fallback", "未设置"))
        p_value = p_value_info.get("p_value", "")
        
              
        success = self.p_value_storage.delete(user_id, p_value_id)
        
        if not success:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "delete_failed_message", "❌ 删除失败")
            return rsp
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 删除P值成功，ID: {p_value_id}")
        
        rsp = TextRspMsg(msg)
        rsp.content = self._get_text(msg, "delete_success_template", "✅ P值删除成功\n\n已删除：{alias}\nP值：{p_value}").format(alias=alias, p_value=p_value)
        
        return rsp
    
    def _handle_switch(self, msg: Dict[str, Any], text: str) -> Any:
        """
        处理切换P值请求
        
        支持格式：
        - "切换P值 <别名或P值>"
        """
                               
        if not self._check_activation_code(msg):
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "activation_required_message", "❌ 当前还没有有效激活码\n\n请先完成激活后再使用P值管理功能。")
            return rsp
        
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
              
        parts = text.strip().split(maxsplit=1)
        
        if len(parts) < 2:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "switch_missing_identifier_message", "❌ 请指定要切换的P值\n\n格式：切换P值 <别名或P值>\n例如：切换P值 别名")
            return rsp
        
        identifier = parts[1].strip()
        
                                    
        p_value_id = None
        all_p_values = self.p_value_storage.get_all(user_id)
        
                  
        found_id = self.p_value_storage.find_by_alias(user_id, identifier)
        if found_id:
            p_value_id = found_id
        else:
                      
            found_id = self.p_value_storage.find_by_p_value(user_id, identifier)
            if found_id:
                p_value_id = found_id
        
        if not p_value_id:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "not_found_template", "❌ 未找到P值：{identifier}\n\n请使用别名或P值").format(identifier=identifier)
            return rsp
        
              
        success = self.p_value_storage.set_current(user_id, p_value_id)
        
        if not success:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "switch_failed_message", "❌ 切换失败")
            return rsp
        
        p_value_info = all_p_values[p_value_id]
        alias = p_value_info.get("alias", self._get_text(msg, "list_alias_fallback", "未设置"))
        p_value = p_value_info.get("p_value", "")
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 切换P值成功，ID: {p_value_id}")
        
        rsp = TextRspMsg(msg)
        rsp.content = self._get_text(msg, "switch_success_template", "✅ 已切换P值\n\n当前使用：{alias}\nP值：{p_value}").format(alias=alias, p_value=p_value)
        
        return rsp
