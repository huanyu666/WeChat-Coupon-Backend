"""
消息处理器基类
"""
import logging
from typing import Dict, Any, Optional
from abc import ABC, abstractmethod


class BaseHandler(ABC):
    """消息处理器基类"""
    
    def __init__(self, logger):
        self.logger = logger
    
    @abstractmethod
    async def ahandle(self, msg: Dict[str, Any]) -> Optional[str]:
        """
        处理消息
        
        Args:
            msg: 解析后的消息字典
            
        Returns:
            返回给用户的响应内容，如果不需要响应则返回None
        """
        pass
    
    def _log_message(self, msg: Dict[str, Any], msg_info: Dict[str, Any]) -> None:
        """记录消息日志"""
        if not self.logger.isEnabledFor(logging.INFO):
            return
        import json
        self.logger.info("收到消息: %s", json.dumps(msg_info, ensure_ascii=False, indent=2))
