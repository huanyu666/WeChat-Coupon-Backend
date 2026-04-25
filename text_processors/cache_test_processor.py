"""
缓存测试处理器

用于测试请求缓存功能，处理时长固定为10秒
"""
import asyncio
import time
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor
from utils.response import TextRspMsg


class CacheTestProcessor(BaseTextProcessor):
    """
    缓存测试处理器
    
    处理关键词："测试缓存" 或 "cache_test"
    处理时长：固定10秒（用于测试请求缓存功能）
    """
    
    def __init__(self, logger, pattern=["测试缓存", "cache_test"]):
        """
        初始化缓存测试处理器
        
        Args:
            logger: 日志记录器
            pattern: 关键词匹配模式，默认匹配"测试缓存"或"cache_test"（OR关系）
        """
        super().__init__(logger, pattern)
        self.process_duration = 10             
    
    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[TextRspMsg]:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        self.logger.info(f"[{account_name}] 用户 {user_id} 触发缓存测试，开始处理（预计耗时 {self.process_duration} 秒）")
        start_time = time.time()
        await asyncio.sleep(self.process_duration)
        actual_duration = time.time() - start_time
        self.logger.info(f"[{account_name}] 缓存测试处理完成，实际耗时: {actual_duration:.2f} 秒")
        rsp = TextRspMsg(msg)
        rsp.content = (
            f"✅ 缓存测试完成\n\n"
            f"处理时长: {actual_duration:.2f} 秒\n"
            f"如果处理时长超过5秒，此请求会被缓存\n"
            f"15秒内相同请求将直接返回缓存结果\n\n"
            f"当前时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}"
        )
        return rsp
