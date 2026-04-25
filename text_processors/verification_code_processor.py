"""
激活码获取处理器

"""
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor
from utils.response import TextRspMsg
from utils.verification_code import (
    get_verification_manager,
    get_mt_order_verification_manager,
    get_link_verification_manager,
    parse_duration_string,
    INFINITE_USES_THRESHOLD,
)
from config.config import ACCOUNT_SPECIFIC_CONFIGS


class ActivationCodeProcessor(BaseTextProcessor):
    INFINITE_HOURS_THRESHOLD = 88888888               
    
    def __init__(self, logger):
        super().__init__(logger)
        self.activation_manager = get_verification_manager()
        self.mt_order_activation_manager = get_mt_order_verification_manager()
        self.activation_manager_link = get_link_verification_manager()
        
                            
        self.trigger_keywords = ["获取激活码"]
        self.query_keywords = ["查询激活码", "查看激活码", "我的激活码"]
        self.delete_keywords = ["1+"]
    
    def _reload_configs(self):
        pass
    
    def is_trigger(self, text: str) -> bool:
        text_lower = text.lower().strip()
        for keyword in self.trigger_keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def is_query(self, text: str) -> bool:
        text_lower = text.lower().strip()
        for keyword in self.query_keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def is_delete(self, text: str) -> bool:
        text_lower = text.lower().strip()
        for keyword in self.delete_keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    def _select_manager(self, tokens):
        """
        查询/删除时选择号池：
        - 默认（无关键词）查询三池：未绑定池、链接池、美团订单池
        - 含“订单”关键词 -> 美团订单池
        - 含“链接”/“link”关键词 -> 链接池
        - 否则 -> 未绑定池
        """
        type_keywords_mt = {"订单", "美团订单", "mt订单", "订单码", "美团订单码"}
        type_keywords_link = {"链接", "获取链接", "link"}
        manager = self.activation_manager
        label = "未绑定"
        type_selected = False
        remaining = []
        for token in tokens:
            if token in type_keywords_mt:
                manager = self.mt_order_activation_manager
                label = "美团订单"
                type_selected = True
                continue
            if token in type_keywords_link:
                manager = self.activation_manager                                    
                manager = self.activation_manager                    
                manager = self.activation_manager
                label = "链接"
                type_selected = True
                continue
            remaining.append(token)
               
        if type_selected and label == "链接":
            manager = self.activation_manager_link
        return manager, label, remaining, type_selected
    
    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
                  
        if self.is_query(text):
            return self._handle_query(msg, text)
        
                  
        if self.is_delete(text):
            return self._handle_delete(msg, text)
        
                  
        if not self.is_trigger(text):
            return None
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 请求获取激活码")
        
                  
        authorized_users = []
        if to_user_name in ACCOUNT_SPECIFIC_CONFIGS:
            specific_config = ACCOUNT_SPECIFIC_CONFIGS[to_user_name]
            authorized_users = specific_config.get("authorized_users", [])
        
                  
        if user_id not in authorized_users:
            self.logger.warning(f"[{account_name}] 用户 {user_id} 未授权")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 您没有权限获取激活码"
            return rsp
        
                                   
        parts = text.strip().split()
        manager = self.activation_manager
        manager_label = "通用"
        arg_tokens = parts[1:] if len(parts) > 1 else []
        
                    
        duration_hours = 24          
        max_uses = 1        
        num_codes = 1          
        infinite_time = False
        infinite_uses = False
        
                         
               
                             
                                
                                
                                   
                                             
                                
        if arg_tokens:
                  
            for part in arg_tokens:
                      
                if part in {"∞", "无限", "无限次", "无限制", "永久"}:
                    infinite_time = True
                    infinite_uses = True
                    self.logger.info(f"[{account_name}] 用户请求无限激活码")
                    continue
                
                                                
                import re
                count_match = re.match(r'^(?:x?(\d+)|(\d+)\s*(?:个|份|张|码))$', part, re.IGNORECASE)
                if count_match:
                    raw_count = count_match.group(1) or count_match.group(2)
                    num_codes = int(raw_count)
                    if num_codes < 1:
                        num_codes = 1
                    elif num_codes > 50:
                        num_codes = 50
                    self.logger.info(f"[{account_name}] 用户指定生成数量: {num_codes}个")
                    continue
                
                        
                try:
                    duration_hours = parse_duration_string(part)
                    self.logger.info(f"[{account_name}] 用户指定有效期: {part} ({duration_hours}小时)")
                    continue
                except ValueError:
                    pass
                
                                                  
                if not infinite_uses:
                    match = re.match(r'^(\d+)次?$', part)
                    if match:
                        max_uses = int(match.group(1))
                        if max_uses < 1:
                            max_uses = 1
                        elif max_uses > 10000:
                            max_uses = 10000
                        self.logger.info(f"[{account_name}] 用户指定使用次数: {max_uses}次")
                        continue

                
        if infinite_time:
            duration_hours = self.INFINITE_HOURS_THRESHOLD
        if infinite_uses:
            max_uses = INFINITE_USES_THRESHOLD
        
                            
        if duration_hours == 24 and not arg_tokens:
            if to_user_name in ACCOUNT_SPECIFIC_CONFIGS:
                default_duration = ACCOUNT_SPECIFIC_CONFIGS[to_user_name].get("default_code_duration", "24h")
                try:
                    duration_hours = parse_duration_string(default_duration)
                except ValueError:
                    duration_hours = 24
        
        
                
        codes = []
        for _ in range(num_codes):
            code = manager.generate_code(user_id, duration_hours, max_uses=max_uses)
            codes.append(code)
        
        self.logger.info(f"[{account_name}] 为用户 {user_id} 生成 {manager_label} 激活码 {num_codes} 个: {', '.join(codes)}, 有效期: {duration_hours}小时, 使用次数: {max_uses}次")
        
               
        rsp = TextRspMsg(msg)
        uses_text = "1次（一次性激活码）" if max_uses == 1 else f"{max_uses}次"
        if max_uses >= INFINITE_USES_THRESHOLD:
            uses_text = "无限次"
        
        expire_text = f"{duration_hours}小时"
        if duration_hours >= self.INFINITE_HOURS_THRESHOLD:
            expire_text = "永久"
        
        if num_codes == 1:
            rsp.content = f"""✅ 激活码生成成功！

激活码: {codes[0]}
有效期: {expire_text}
使用次数: {uses_text}

类型: {manager_label}

请在使用"获取链接"或"美团订单查询"功能时输入此激活码。"""
        else:
            code_lines = "\n".join([f"{idx}. {c}" for idx, c in enumerate(codes, 1)])
            rsp.content = f"""✅ 已生成 {num_codes} 个激活码！

有效期: {expire_text}
使用次数: {uses_text}
类型: {manager_label}

激活码列表：
{code_lines}

使用任意一个激活码即可进行「获取链接」或「美团订单查询」等操作。"""
        
        return rsp
    
    def _handle_query(self, msg: Dict[str, Any], text: str) -> Any:
        """
        处理查询激活码请求
        
        Args:
            msg: 消息字典
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
                                          
        parts = text.strip().split()
        manager, manager_label, _, type_selected = self._select_manager(parts[1:] if len(parts) > 1 else [])
        
        managers = []
        if type_selected:
            managers.append((manager_label, manager))
        else:
            managers.append(("未绑定", self.activation_manager))
            managers.append(("链接", self.activation_manager_link))
            managers.append(("美团订单", self.mt_order_activation_manager))
        
                  
        authorized_users = []
        if to_user_name in ACCOUNT_SPECIFIC_CONFIGS:
            specific_config = ACCOUNT_SPECIFIC_CONFIGS[to_user_name]
            authorized_users = specific_config.get("authorized_users", [])
        
                  
        if user_id not in authorized_users:
            self.logger.warning(f"[{account_name}] 用户 {user_id} 未授权")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 您没有权限查询激活码"
            return rsp
        
        all_sections = []
        total_count = 0
        
        for label, mgr in managers:
            codes = mgr.get_all_codes_for_user(user_id)
            total_count += len(codes)
            all_sections.append((label, codes))
        
        if total_count == 0:
            rsp = TextRspMsg(msg)
            rsp.content = "您暂时没有激活码\n\n发送「获取激活码」可生成新的激活码"
            return rsp
        
        rsp = TextRspMsg(msg)
        content_parts = [f"📋 您的激活码列表（共{total_count}个）："]
        
        for label, codes in all_sections:
            content_parts.append(f"\n【{label}】共{len(codes)}个")
            if not codes:
                content_parts.append("  （无）")
                continue
            for idx, code_info in enumerate(codes, 1):
                status_emoji = "✅" if code_info["status"] == "有效" else "❌"
                content_parts.append(f"\n{idx}. {status_emoji} {code_info['code']}")
                content_parts.append(f"   状态: {code_info['status']}")
                
                expires_at_text = code_info["expires_at"]
                content_parts.append(f"   到期: {expires_at_text}")
                
                if code_info.get("bound_user"):
                    content_parts.append(f"   绑定用户: {code_info['bound_user']}")
                
                if code_info["status"] == "有效":
                    if code_info["remaining_hours"] is not None:
                        content_parts.append(f"   剩余: {code_info['remaining_hours']}小时{code_info['remaining_minutes']}分钟")
                    else:
                        content_parts.append("   剩余: 永久")
                    
                    max_uses_display = code_info["max_uses"]
                    if isinstance(max_uses_display, str) or max_uses_display >= INFINITE_USES_THRESHOLD:
                        max_uses_display = "无限"
                        remaining_uses_display = "无限"
                    else:
                        remaining_uses_display = code_info["remaining_uses"]
                    
                    content_parts.append(f"   次数: {code_info['used_count']}/{max_uses_display}次，剩余{remaining_uses_display}次")
        
        content_parts.append("\n\n💡 提示:")
        content_parts.append("• 发送「删除激活码 XXX」删除指定激活码（默认通用池，添加“订单”删除订单池）")
        content_parts.append("• 发送「获取激活码」生成新激活码")
        
        rsp.content = "\n".join(content_parts)
        return rsp
    
    def _handle_delete(self, msg: Dict[str, Any], text: str) -> Any:
        """
        处理删除激活码请求
        
        Args:
            msg: 消息字典
            text: 文本内容
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
                 
        parts = text.strip().split()
        manager, manager_label, arg_tokens, _ = self._select_manager(parts[1:] if len(parts) > 1 else [])
        
                  
        authorized_users = []
        if to_user_name in ACCOUNT_SPECIFIC_CONFIGS:
            specific_config = ACCOUNT_SPECIFIC_CONFIGS[to_user_name]
            authorized_users = specific_config.get("authorized_users", [])
        
                  
        if user_id not in authorized_users:
            self.logger.warning(f"[{account_name}] 用户 {user_id} 未授权")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 您没有权限删除激活码"
            return rsp
        
        if not arg_tokens:
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 请指定要删除的激活码\n\n格式: 删除激活码 XXX"
            return rsp
        
        code_to_delete = arg_tokens[0].strip()
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 请求删除{manager_label}激活码: {code_to_delete}")
        
               
        success, message = manager.revoke_code(user_id, code_to_delete)
        
        rsp = TextRspMsg(msg)
        if success:
            rsp.content = f"✅ {message}"
            self.logger.info(f"[{account_name}] {message}")
        else:
            rsp.content = f"❌ {message}"
            self.logger.warning(f"[{account_name}] {message}")
        
        return rsp
