"""
小程序处理器基类
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from utils.response import WxRspMsg


class BaseMiniprogramProcessor(ABC):
    """小程序处理器基类"""
    
    def __init__(self, logger):
        self.logger = logger
    
    @abstractmethod
    async def aprocess(self, msg: Dict[str, Any]) -> Optional['WxRspMsg']:
        """
        处理小程序消息
        
        Args:
            msg: 消息字典
            
        Returns:
            WxRspMsg响应对象，如果不需要回复则返回None
        """
        pass
