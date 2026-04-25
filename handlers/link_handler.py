"""
链接消息处理器
"""
from typing import Dict, Any, Optional
from .base_handler import BaseHandler


class LinkHandler(BaseHandler):
    """链接消息处理器"""
    
    async def ahandle(self, msg: Dict[str, Any]) -> Optional[str]:
        """处理链接消息"""
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        msg_info = {
            "msg_type": "link",
            "from_user_name": msg.get("FromUserName"),
            "to_user_name": to_user_name,
            "account_name": account_name,
            "create_time": msg.get("CreateTime"),
            "msg_id": msg.get("MsgId"),
            "title": msg.get("Title"),
            "description": msg.get("Description"),
            "url": msg.get("Url")
        }
        
        self._log_message(msg, msg_info)
        
        return None
