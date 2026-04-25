"""
语音消息处理器
"""
from typing import Dict, Any, Optional
from .base_handler import BaseHandler


class VoiceHandler(BaseHandler):
    """语音消息处理器"""
    
    async def ahandle(self, msg: Dict[str, Any]) -> Optional[str]:
        """处理语音消息"""
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        msg_info = {
            "msg_type": "voice",
            "from_user_name": msg.get("FromUserName"),
            "to_user_name": to_user_name,
            "account_name": account_name,
            "create_time": msg.get("CreateTime"),
            "msg_id": msg.get("MsgId"),
            "media_id": msg.get("MediaId"),
            "format": msg.get("Format")
        }
        
        self._log_message(msg, msg_info)
        
        return None
