"""
小程序卡片消息处理器
"""
from typing import Dict, Any, Optional
from .base_handler import BaseHandler


class MiniprogramHandler(BaseHandler):
    """小程序卡片消息处理器"""
    
    def __init__(self, logger, miniprogram_processors: Dict[str, Any]):
        """
        初始化小程序消息处理器
        
        Args:
            logger: 日志记录器
            miniprogram_processors: 小程序处理器字典 {appid: processor}
        """
        super().__init__(logger)
        self.miniprogram_processors = miniprogram_processors
    
    async def ahandle(self, msg: Dict[str, Any]) -> Optional[Any]:
        """异步处理小程序卡片消息"""
        from config.config import ACCOUNT_SPECIFIC_CONFIGS
        from text_processors.stateful_processor import USER_STATES

        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        app_id = msg.get("AppId")
        user_id = msg.get("FromUserName", "")

        msg_info = {
            "msg_type": "miniprogrampage",
            "from_user_name": user_id,
            "to_user_name": to_user_name,
            "account_name": account_name,
            "create_time": msg.get("CreateTime"),
            "msg_id": msg.get("MsgId"),
            "title": msg.get("Title"),
            "app_id": app_id,
            "page_path": msg.get("PagePath"),
            "thumb_url": msg.get("ThumbUrl"),
            "thumb_media_id": msg.get("ThumbMediaId")
        }

        self._log_message(msg, msg_info)

        user_state = USER_STATES.get(user_id)
        if user_state:
            processor_name = user_state.get("processor", "")
            state_name = user_state.get("state", "")
            if processor_name == "MeituanShopQueryProcessor" and state_name == "waiting_miniprogram":
                self.logger.info(f"[{account_name}] 用户 {user_id} 处于小程序监听状态，转发给文本处理器")
                from handlers.text_handler import TextHandler
                try:
                    from routes.wechat import text_processors
                    text_handler = TextHandler(self.logger, text_processors)
                    return await text_handler.ahandle(msg)
                except ImportError:
                    self.logger.error(f"[{account_name}] 无法导入text_processors，无法处理小程序消息")
                    return None

        account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
        enabled_appids = account_config.get("enabled_miniprogram_appids", [])
        if enabled_appids and app_id not in enabled_appids:
            self.logger.info(f"[{account_name}] 小程序 {app_id} 未在该公众号启用")
            return None

        if app_id in self.miniprogram_processors:
            processor = self.miniprogram_processors[app_id]
            self.logger.info(f"[{account_name}] 使用小程序处理器: {processor.__class__.__name__}")
            return await processor.aprocess(msg)

        self.logger.debug(f"[{account_name}] 未找到小程序 {app_id} 的处理器")
        return None
