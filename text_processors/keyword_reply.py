"""
关键词自动回复处理器

关键词配置已迁移到 config/config.py 中的 ACCOUNT_SPECIFIC_CONFIGS
每个公众号可以有独立的关键词配置
"""
import logging
import re
from typing import Dict, Any, Optional, Union, Callable
import inspect
try:
    import ahocorasick
except ImportError:  # pragma: no cover - 运行环境未装依赖时安全降级
    ahocorasick = None
from .base_processor import BaseTextProcessor


class KeywordReplyProcessor(BaseTextProcessor):
    """
    关键词自动回复处理器 - 根据关键词返回预设内容
    
    关键词配置从 config.py 的 ACCOUNT_SPECIFIC_CONFIGS 中读取
    每个公众号（ToUserName）可以有独立的关键词配置
    """
    
    def __init__(self, logger, pattern=None, multi_match: bool = False):
        """
        初始化关键词回复处理器
        
        Args:
            logger: 日志记录器
            pattern: 关键词匹配模式（在main.py中配置）
            multi_match: 是否启用多关键词匹配模式（默认False）
        """
        super().__init__(logger, pattern)
                    
        self.multi_match = multi_match
        self._keyword_matchers: Dict[str, Dict[str, Any]] = {}
        self._warned_ahocorasick_unavailable = False
        self._reload_configs()

    def _reload_configs(self) -> None:
        from config.config import KEYWORD_RESPONSES

        self._keyword_matchers = {}
        for account_id, keyword_responses in KEYWORD_RESPONSES.items():
            if not isinstance(keyword_responses, dict) or not keyword_responses:
                continue
            ordered_entries = list(keyword_responses.items())
            automaton = None
            if ahocorasick is not None:
                automaton = ahocorasick.Automaton()
                for index, (keyword, _) in enumerate(ordered_entries):
                    keyword_lower = str(keyword or "").lower()
                    if not keyword_lower:
                        continue
                    automaton.add_word(keyword_lower, (index, keyword))
                automaton.make_automaton()
            self._keyword_matchers[account_id] = {
                "ordered_entries": ordered_entries,
                "automaton": automaton,
            }

    def _get_keyword_matcher(self, to_user_name: str) -> Dict[str, Any]:
        return self._keyword_matchers.get(to_user_name, {})

    def _match_keyword_indexes(self, to_user_name: str, text: str) -> list[int]:
        matcher = self._get_keyword_matcher(to_user_name)
        ordered_entries = matcher.get("ordered_entries", [])
        if not ordered_entries or not text:
            return []

        text_lower = text.lower()
        automaton = matcher.get("automaton")
        matched_indexes = set()

        if automaton is not None:
            for _, (index, _) in automaton.iter(text_lower):
                matched_indexes.add(index)
        else:
            if not self._warned_ahocorasick_unavailable:
                self.logger.warning("pyahocorasick 未安装，KeywordReplyProcessor 回退到逐项扫描")
                self._warned_ahocorasick_unavailable = True
            for index, (keyword, _) in enumerate(ordered_entries):
                keyword_lower = str(keyword or "").lower()
                if keyword_lower and keyword_lower in text_lower:
                    matched_indexes.add(index)

        return sorted(matched_indexes)
    
    def _call_response_function(self, func: Callable, msg: Dict[str, Any]) -> Any:
        """
        智能调用响应函数，自动判断是否需要传入参数
        
        Args:
            func: 响应函数（可能是无参数的 lambda 或需要 req_msg 的 lambda）
            msg: 微信消息对象
            
        Returns:
            函数返回的内容（可能是字符串或响应对象）
        """
        try:
                    
            sig = inspect.signature(func)
            params = sig.parameters
            
                                 
            if len(params) > 0:
                                                             
                req_msg = {
                    "FromUserName": msg.get("FromUserName", ""),
                    "ToUserName": msg.get("ToUserName", ""),
                    "CreateTime": msg.get("CreateTime", ""),
                    "MsgType": msg.get("MsgType", ""),
                    "Content": msg.get("Content", ""),
                    "MsgId": msg.get("MsgId", ""),
                               
                    **msg
                }
                return func(req_msg)
            else:
                            
                return func()
        except Exception as e:
            self.logger.error("调用响应函数失败: %s", e)
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    @staticmethod
    def _substitute_variables(content: str, msg: Dict[str, Any]) -> str:
        """替换回复内容中的 $变量名$ 占位符。

        支持的变量：$FromUserName$, $ToUserName$, $CreateTime$, $MsgType$, $Content$, $MsgId$
        以及 msg 中的任意字段（$字段名$）。
        """
        if '$' not in content:
            return content

        def _replacer(m):
            key = m.group(1)
            val = msg.get(key, '')
            return str(val) if val is not None else ''

        return re.sub(r'\$([A-Za-z_][A-Za-z0-9_]*)\$', _replacer, content)

    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        """
        处理关键词回复
        
        Args:
            msg: 消息字典
            text: 文本内容
            
        Returns:
            WxRspMsg响应对象
        """
        from utils.response import TextRspMsg
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)

        matcher = self._get_keyword_matcher(to_user_name)
        ordered_entries = matcher.get("ordered_entries", [])

        if ordered_entries and self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug("[%s] 加载关键词配置，共 %s 个关键词", account_name, len(ordered_entries))

        if not ordered_entries:
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug("[%s] 未配置关键词回复", account_name)
            return None

        matched_indexes = self._match_keyword_indexes(to_user_name, text)
        if not matched_indexes:
            return None

        if self.multi_match:
            matched_contents = []
            matched_keywords = []
            for index in matched_indexes:
                keyword, response = ordered_entries[index]
                matched_keywords.append(keyword)

                if callable(response):
                    result = self._call_response_function(response, msg)
                    if hasattr(result, 'dump_xml') and callable(result.dump_xml):
                        if not hasattr(result, 'content'):
                            self.logger.info(
                                "[%s] 检测到非文本响应 (%s)，忽略多匹配模式直接返回",
                                account_name,
                                type(result).__name__,
                            )
                            return result

                    if hasattr(result, 'content'):
                        content = result.content
                    elif isinstance(result, str):
                        content = result
                    else:
                        content = str(result) if result is not None else ""
                else:
                    content = '\n'.join([
                        line.strip()
                        for line in response.split('\n')
                        if line.strip()
                    ])
                matched_contents.append(content)

            if matched_contents:
                self.logger.info("[%s] 匹配到多个关键词: %s", account_name, ", ".join(matched_keywords))
                rsp = TextRspMsg(msg)
                rsp.content = self._substitute_variables('\n\n'.join(matched_contents), msg)
                return rsp
            return None

        first_index = matched_indexes[0]
        keyword, response = ordered_entries[first_index]
        self.logger.info("[%s] 匹配到关键词: %s", account_name, keyword)

        if callable(response):
            result = self._call_response_function(response, msg)
            if self.logger.isEnabledFor(logging.DEBUG):
                self.logger.debug("[%s] 工厂函数返回类型: %s", account_name, type(result).__name__)

            if hasattr(result, 'dump_xml') and callable(result.dump_xml):
                self.logger.info("[%s] 返回响应对象: %s", account_name, type(result).__name__)
                return result

            if isinstance(result, str):
                content = result
            else:
                self.logger.warning("[%s] 未知响应类型，转换为字符串: %s", account_name, type(result))
                content = str(result) if result is not None else ""
        else:
            content = '\n'.join([
                line.strip()
                for line in response.split('\n')
                if line.strip()
            ])

        rsp = TextRspMsg(msg)
        rsp.content = self._substitute_variables(content, msg)
        return rsp
