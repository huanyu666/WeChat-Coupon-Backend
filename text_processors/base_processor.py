"""
文本处理器基类
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from utils.response import WxRspMsg


class KeywordMatcher:
    """
    关键词匹配器 - 支持灵活的AND和OR逻辑组合
    
    匹配规则：
        - 字符串 str: 单个关键词
        - 列表 []: OR关系（满足任意一个即可）
        - 元组 (): AND关系（必须全部满足）
        - 可以任意嵌套组合
        
    示例：
        1. 简单AND: ("美团", "链接")
           → 必须同时包含"美团"和"链接"
        
        2. 简单OR: ["美团", "外卖"]
           → 包含"美团"或"外卖"之一即可
        
        3. AND + OR: ("美团", ["http://dpurl.cn", "https://dpurl.cn"])
           → 必须包含"美团"，且包含两个链接之一
        
        4. 复杂组合: (["美团", "外卖"], ["优惠", "红包"], "链接")
           → (包含"美团"或"外卖") AND (包含"优惠"或"红包") AND 包含"链接"
        
        5. 嵌套组合: (["美团", "外卖"], (["http", "https"], "dpurl.cn"))
           → (包含"美团"或"外卖") AND ((包含"http"或"https") AND 包含"dpurl.cn")
    """
    
    def __init__(self, pattern: Union[str, List, Tuple]):
        """
        初始化关键词匹配器
        
        Args:
            pattern: 匹配模式
                - str: 单个关键词
                - list: OR关系的关键词列表
                - tuple: AND关系的关键词元组
        """
        self.pattern = pattern
    
    def match(self, text: str) -> bool:
        """
        检查文本是否匹配关键词模式
        
        Args:
            text: 要检查的文本
            
        Returns:
            True: 匹配成功
            False: 匹配失败
        """
        if not text:
            return False
        
        return self._match_pattern(text, self.pattern)
    
    def _match_pattern(self, text: str, pattern: Union[str, List, Tuple]) -> bool:
        """
        递归匹配模式
        
        Args:
            text: 文本内容
            pattern: 匹配模式
            
        Returns:
            是否匹配成功
        """
                      
        if isinstance(pattern, str):
            return pattern in text
        
                           
        elif isinstance(pattern, list):
            return any(self._match_pattern(text, item) for item in pattern)
        
                          
        elif isinstance(pattern, tuple):
            return all(self._match_pattern(text, item) for item in pattern)
        
                  
        else:
            return False
    
    def __repr__(self) -> str:
        """返回匹配器的字符串表示"""
        return f"KeywordMatcher({self.pattern})"


class BaseTextProcessor(ABC):
    """文本消息处理器基类"""
    
    def __init__(self, logger, pattern: Union[str, List, Tuple] = None):
        """
        初始化文本处理器
        
        Args:
            logger: 日志记录器
            pattern: 关键词匹配模式（支持灵活的AND和OR组合）
                - None: 匹配所有消息
                - str: 单个关键词
                - list: OR关系
                - tuple: AND关系
        """
        self.logger = logger
        self.matcher = KeywordMatcher(pattern) if pattern is not None else None
    
    def can_handle(self, text: str) -> bool:
        """
        判断是否可以处理该文本
        
        Args:
            text: 文本内容
            
        Returns:
            True: 可以处理
            False: 不能处理
        """
        if self.matcher is None:
                               
            return True
        
        return self.matcher.match(text)
    
    @abstractmethod
    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional['WxRspMsg']:
        """
        处理文本消息
        
        Args:
            msg: 消息字典
            text: 文本内容
            
        Returns:
            WxRspMsg响应对象，如果不需要回复则返回None
        """
        pass
