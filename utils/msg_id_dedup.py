"""
微信消息 MsgId 去重工具

用于路由层对已处理的 MsgId 进行去重，避免微信重试导致的重复处理。
"""
import time
import threading
from typing import Optional

                                       
MSG_ID_TTL = 180


class MsgIdDedup:
    """MsgId 去重缓存"""

    def __init__(self, ttl: int = MSG_ID_TTL, cleanup_interval: int = 60):
        self._ttl = ttl
        self._cleanup_interval = cleanup_interval
        self._processed: dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.time()

    def is_duplicate(self, msg_id: str) -> bool:
        """检查 MsgId 是否已处理过（重复）"""
        if not msg_id:
            return False
        with self._lock:
            self._cleanup_if_needed()
            return msg_id in self._processed

    def mark_processed(self, msg_id: str) -> None:
        """标记 MsgId 为已处理"""
        if not msg_id:
            return
        with self._lock:
            self._processed[msg_id] = time.time()

    def _cleanup_if_needed(self) -> None:
        """清理过期条目"""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now
        expired = [k for k, v in self._processed.items() if now - v > self._ttl]
        for k in expired:
            del self._processed[k]


_msg_id_dedup = MsgIdDedup()


def get_msg_id_dedup() -> MsgIdDedup:
    """获取全局 MsgId 去重实例"""
    return _msg_id_dedup
