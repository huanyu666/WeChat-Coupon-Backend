"""
请求缓存工具

按 msg_id 或 FromUserName+CreateTime 缓存微信消息响应，用于重试排重。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional, Dict, Any, Tuple

from utils.logger import setup_logger

logger = setup_logger(__name__)


class RequestCache:
    def __init__(self, cache_ttl: int = 15, slow_request_threshold: float = 5.0, cleanup_interval: int = 30):
        self.cache_ttl = cache_ttl
        self.slow_request_threshold = slow_request_threshold
        self._cleanup_interval = cleanup_interval
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()

    def _msg_cache_key(self, msg_id: str = "", from_user: str = "", create_time: str = "") -> str:
        """生成消息缓存键：有 MsgId 用 msg_id，事件用 FromUserName+CreateTime（微信推荐）"""
        if msg_id:
            return f"msg:{msg_id}"
        return f"evt:{from_user}_{create_time}"

    async def get_by_msg_id_async(
        self,
        msg_id: str = "",
        from_user: str = "",
        create_time: str = "",
    ) -> Optional[Tuple[bytes, float]]:
        """按 msg_id 或 FromUserName+CreateTime 获取缓存（用于重试消息排重，返回原始响应字节）"""
        cache_key = self._msg_cache_key(msg_id, from_user, create_time)
        async with self._lock:
            self._cleanup_expired()
            cache_entry = self._cache.get(cache_key)
            if not cache_entry:
                return None

            if time.time() - cache_entry["timestamp"] <= self.cache_ttl:
                logger.info(f"命中请求缓存: {cache_key[:8]}... (耗时: {cache_entry['duration']:.2f}秒)")
                return cache_entry["response"], cache_entry["duration"]

            del self._cache[cache_key]
            logger.debug(f"缓存已过期: {cache_key[:8]}...")
            return None

    async def set_by_msg_id_async(
        self,
        msg_id: str,
        response: bytes,
        duration: float,
        from_user: str = "",
        create_time: str = "",
    ) -> None:
        """按 msg_id 或 FromUserName+CreateTime 设置缓存（仅慢请求，存原始响应便于重试时按新 nonce 加密）"""
        if duration < self.slow_request_threshold:
            return
        cache_key = self._msg_cache_key(msg_id, from_user, create_time)
        async with self._lock:
            self._cleanup_expired()
            self._cache[cache_key] = {
                "response": response,
                "timestamp": time.time(),
                "duration": duration,
            }
            logger.info(f"缓存慢请求: {cache_key[:8]}... (耗时: {duration:.2f}秒)")

    async def clear_async(self) -> None:
        async with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"清空了 {count} 个缓存项")

    async def get_stats_async(self) -> Dict[str, Any]:
        async with self._lock:
            self._cleanup_expired()
            return {
                "cache_size": len(self._cache),
                "cache_ttl": self.cache_ttl,
                "slow_request_threshold": self.slow_request_threshold,
            }

    def _cleanup_expired(self, force: bool = False) -> None:
        current_time = time.time()
        if not force and current_time - self._last_cleanup < self._cleanup_interval:
            return

        self._last_cleanup = current_time
        expired_keys = [
            key for key, entry in self._cache.items()
            if current_time - entry["timestamp"] > self.cache_ttl
        ]

        for key in expired_keys:
            del self._cache[key]

        if expired_keys:
            logger.debug(f"清理了 {len(expired_keys)} 个过期缓存项")


_request_cache = RequestCache()


def get_request_cache() -> RequestCache:
    """获取全局请求缓存实例"""
    return _request_cache
