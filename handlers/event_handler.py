"""
事件消息处理器
"""
from typing import Dict, Any, Optional
from .base_handler import BaseHandler


class EventHandler(BaseHandler):
    """事件消息处理器"""
    
    async def ahandle(self, msg: Dict[str, Any]) -> Optional[Any]:
        """处理事件消息"""
        from utils.response import TextRspMsg
        from config.config import ACCOUNT_SPECIFIC_CONFIGS
        
        event_type = msg.get("Event", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        event_info = {
            "event_type": "event",
            "from_user_name": msg.get("FromUserName"),
            "to_user_name": to_user_name,
            "account_name": account_name,
            "create_time": msg.get("CreateTime"),
            "event": event_type,
            "event_key": msg.get("EventKey"),
            "ticket": msg.get("Ticket"),
            "latitude": msg.get("Latitude"),
            "longitude": msg.get("Longitude"),
            "precision": msg.get("Precision"),
        }
        
                    
        self._log_message(msg, event_info)
        
                
        if event_type == "subscribe":
            self.logger.info(f"用户 {msg.get('FromUserName')} 关注了公众号 [{account_name}]")
            
                              
            welcome_message = "欢迎关注【每天一顿霸王餐】！回复查看最新福利~"
            
                           
            if to_user_name in ACCOUNT_SPECIFIC_CONFIGS:
                specific_config = ACCOUNT_SPECIFIC_CONFIGS[to_user_name]
                welcome_message = specific_config.get("welcome_message", welcome_message)
            
            rsp = TextRspMsg(msg)
            rsp.content = welcome_message
            return rsp
        
                  
        elif event_type == "unsubscribe":
            self.logger.warn(f"用户 {msg.get('FromUserName')} 取消关注了公众号 [{account_name}]")
            return None
        
                              
        elif event_type == "CLICK":
            return self._handle_click_event(msg, to_user_name, account_name)

                
        else:
            self.logger.info(f"收到其他事件: {event_type}")
            return None
    
    def _handle_click_event(self, msg: Dict[str, Any], to_user_name: str, account_name: str) -> Optional[Any]:
        """
        处理CLICK事件（自定义菜单点击事件）
        
        Args:
            msg: 消息字典
            to_user_name: 公众号ID
            account_name: 账号名称
            
        Returns:
            响应对象
        """
        from utils.response import TextRspMsg, ImageRspMsg
        from config.config import CLICK_EVENT_RESPONSES
        import inspect
        
        event_key = msg.get("EventKey", "")
        
        if not event_key:
            self.logger.warning(f"[{account_name}] CLICK事件缺少EventKey")
            return None
        
        self.logger.info(f"[{account_name}] 收到CLICK事件，EventKey: {event_key}")
        
                                
        click_responses = CLICK_EVENT_RESPONSES.get(to_user_name, {})
        
        if not click_responses:
            self.logger.debug(f"[{account_name}] 未配置CLICK事件响应")
            return None
        
                         
        response_config = click_responses.get(event_key)
        
        if not response_config:
            self.logger.debug(f"[{account_name}] EventKey {event_key} 未配置响应")
            return None
        
        self.logger.info(f"[{account_name}] 找到EventKey {event_key} 的响应配置")
        
                
        if callable(response_config):
                         
            try:
                                           
                sig = inspect.signature(response_config)
                params = sig.parameters
                
                if len(params) > 0:
                                   
                    req_msg = {
                        "FromUserName": msg.get("FromUserName", ""),
                        "ToUserName": msg.get("ToUserName", ""),
                        "CreateTime": msg.get("CreateTime", ""),
                        "MsgType": msg.get("MsgType", ""),
                        "Event": msg.get("Event", ""),
                        "EventKey": event_key,
                        **msg
                    }
                    result = response_config(req_msg)
                else:
                    result = response_config()
                
                                                                  
                if hasattr(result, 'dump_xml') and callable(result.dump_xml):
                    self.logger.info(f"[{account_name}] 返回响应对象: {type(result).__name__}")
                    return result
                
                                  
                if isinstance(result, str):
                    rsp = TextRspMsg(msg)
                    rsp.content = result
                    return rsp
                
                self.logger.warning(f"[{account_name}] 工厂函数返回未知类型: {type(result)}")
                return None
                
            except Exception as e:
                self.logger.error(f"[{account_name}] 调用CLICK事件工厂函数失败: {e}", exc_info=True)
                return None
        
        elif isinstance(response_config, dict):
                            
            msg_type = response_config.get("type", "text")
            
            if msg_type == "image":
                      
                media_id = response_config.get("media_id", "")
                if media_id:
                    self.logger.info(f"[{account_name}] 返回图片消息，media_id: {media_id}")
                    img_rsp = ImageRspMsg(msg)
                    img_rsp.media_id = media_id
                    return img_rsp
                else:
                    self.logger.warning(f"[{account_name}] 图片配置缺少media_id")
                    return None
            
            elif msg_type == "text":
                      
                content = response_config.get("content", "")
                rsp = TextRspMsg(msg)
                rsp.content = content
                return rsp
            
            else:
                self.logger.warning(f"[{account_name}] 不支持的响应类型: {msg_type}")
                return None
        
        elif isinstance(response_config, str):
                        
            content = '\n'.join([
                line.strip() 
                for line in response_config.split('\n') 
                if line.strip()
            ])
            rsp = TextRspMsg(msg)
            rsp.content = content
            return rsp
        
        else:
            self.logger.warning(f"[{account_name}] 未知的响应配置类型: {type(response_config)}")
            return None
