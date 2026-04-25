"""
微信请求处理中结果池

用于同一条消息在处理过程中被微信重试时，后续请求可以等待首次处理结果，
避免重复执行业务逻辑，也避免在结果未生成前直接返回 success。
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional, Dict, Any

from utils.logger import setup_logger

logger = setup_logger(__name__)


class InflightRequestStore:
    def __init__(self, ttl: int = 180, cleanup_interval: int = 30):
        self._ttl = ttl
        self._cleanup_interval = cleanup_interval
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._async_lock = None
        self._last_cleanup = time.time()

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    async def get_or_create(self, key: str) -> tuple[Dict[str, Any], bool]:
        async with self._get_async_lock():
            await self._cleanup_if_needed()
            entry = self._entries.get(key)
            if entry:
                return dict(entry), False

            entry = {
                "event": asyncio.Event(),
                "started_at": time.time(),
                "completed_at": None,
                "response": None,
                "duration": 0.0,
                "error": None,
            }
            self._entries[key] = entry
            return dict(entry), True

    async def get_completed(self, key: str) -> Optional[Dict[str, Any]]:
        async with self._get_async_lock():
            await self._cleanup_if_needed()
            entry = self._entries.get(key)
            if not entry:
                return None
            if entry.get("completed_at"):
                return dict(entry)
            return None

    async def wait_result(self, key: str, timeout: float) -> Optional[Dict[str, Any]]:
        async with self._get_async_lock():
            entry = self._entries.get(key)
            if not entry:
                return None
            event = entry["event"]

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

        async with self._get_async_lock():
            current = self._entries.get(key)
            if not current:
                return None
            return dict(current)

    async def set_result(self, key: str, response: bytes, duration: float) -> None:
        async with self._get_async_lock():
            entry = self._entries.get(key)
            if not entry:
                return
            entry["response"] = response
            entry["duration"] = duration
            entry["completed_at"] = time.time()
            entry["error"] = None
            entry["event"].set()

    async def set_error(self, key: str, error: str) -> None:
        async with self._get_async_lock():
            entry = self._entries.get(key)
            if not entry:
                return
            entry["error"] = error
            entry["completed_at"] = time.time()
            entry["event"].set()

    async def _cleanup_if_needed(self) -> None:
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        expired_keys = []
        for key, entry in self._entries.items():
            completed_at = entry.get("completed_at")
            base_time = completed_at or entry.get("started_at", now)
            if now - base_time > self._ttl:
                expired_keys.append(key)

        for key in expired_keys:
            del self._entries[key]

        if expired_keys:
            logger.debug(f"清理了 {len(expired_keys)} 个处理中结果池条目")


_inflight_request_store = InflightRequestStore()


def get_inflight_request_store() -> InflightRequestStore:
    return _inflight_request_store
