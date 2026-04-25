"""
用户ID回显处理器
"""
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor
from utils.response import TextRspMsg


class EchoUserIdProcessor(BaseTextProcessor):
    """
    关键词触发，返回用户的 FromUserName
    """

    def __init__(self, logger):
        super().__init__(logger)
        self.keywords = ["获取id", "获取ID", "查询id", "查询ID", "我的id", "我的ID"]

    def is_trigger(self, text: str) -> bool:
        t = text.strip().lower()
        return any(k.lower() in t for k in self.keywords)

    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        if not self.is_trigger(text):
            return None

        user_id = msg.get("FromUserName", "")
        rsp = TextRspMsg(msg)
        rsp.content = f"您的ID：{user_id}"
        return rsp

