"""
位置消息处理器
"""
from typing import Dict, Any, Optional
from .base_handler import BaseHandler


class LocationHandler(BaseHandler):
    """位置消息处理器"""
    
    async def ahandle(self, msg: Dict[str, Any]) -> Optional[str]:
        """处理位置消息"""
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        msg_info = {
            "msg_type": "location",
            "from_user_name": msg.get("FromUserName"),
            "to_user_name": to_user_name,
            "account_name": account_name,
            "create_time": msg.get("CreateTime"),
            "msg_id": msg.get("MsgId"),
            "location_x": msg.get("Location_X"),
            "location_y": msg.get("Location_Y"),
            "scale": msg.get("Scale"),
            "label": msg.get("Label")
        }
        
        self._log_message(msg, msg_info)
        
        return None
