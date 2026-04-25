"""
视频消息处理器
"""
from typing import Dict, Any, Optional
from .base_handler import BaseHandler


class VideoHandler(BaseHandler):
    """视频消息处理器"""
    
    async def ahandle(self, msg: Dict[str, Any]) -> Optional[str]:
        """处理视频消息"""
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        msg_info = {
            "msg_type": "video",
            "from_user_name": msg.get("FromUserName"),
            "to_user_name": to_user_name,
            "account_name": account_name,
            "create_time": msg.get("CreateTime"),
            "msg_id": msg.get("MsgId"),
            "media_id": msg.get("MediaId"),
            "thumb_media_id": msg.get("ThumbMediaId")
        }
        
        self._log_message(msg, msg_info)
        
        return None
