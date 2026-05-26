from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict


BUSY_MESSAGE = "当前查询较多，请稍后再试"


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, "")).strip())
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


def _config() -> dict[str, Any]:
    return {
        "global_limit": _env_int("WX_ORDER_QUERY_GLOBAL_CONCURRENCY", 24, 1, 500),
        "wechat_limit": _env_int("WX_ORDER_QUERY_WECHAT_CONCURRENCY", 16, 1, 500),
        "web_limit": _env_int("WX_ORDER_QUERY_WEB_CONCURRENCY", 16, 1, 500),
        "queue_timeout_seconds": _env_float("WX_ORDER_QUERY_QUEUE_TIMEOUT_SECONDS", 2.0, 0.05, 30.0),
    }


@dataclass(frozen=True)
class OrderQueryCapacitySlot:
    source: str
    identity: str
    waited_seconds: float
    active_global: int
    active_source: int


class OrderQueryCapacityBusy(RuntimeError):
    def __init__(self, message: str = BUSY_MESSAGE, *, source: str = "", stats: dict[str, Any] | None = None):
        super().__init__(message)
        self.source = source
        self.stats = stats or {}


class _LoopCapacityState:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.global_semaphore = asyncio.Semaphore(int(config["global_limit"]))
        self.source_semaphores = {
            "wechat": asyncio.Semaphore(int(config["wechat_limit"])),
            "web": asyncio.Semaphore(int(config["web_limit"])),
        }
        self.active_by_source = {"wechat": 0, "web": 0}
        self.waiting_by_source = {"wechat": 0, "web": 0}
        self.rejected_by_source = {"wechat": 0, "web": 0}
        self.acquired_by_source = {"wechat": 0, "web": 0}
        self.wait_samples_by_source: dict[str, list[float]] = {"wechat": [], "web": []}

    def _source_limit(self, source: str) -> int:
        if source == "web":
            return int(self.config["web_limit"])
        return int(self.config["wechat_limit"])

    def snapshot(self) -> dict[str, Any]:
        global_limit = int(self.config["global_limit"])
        global_active = max(0, global_limit - int(getattr(self.global_semaphore, "_value", global_limit)))
        source_payload: dict[str, Any] = {}
        for source in ("wechat", "web"):
            samples = self.wait_samples_by_source.get(source) or []
            avg_wait = sum(samples) / len(samples) if samples else 0.0
            source_payload[source] = {
                "limit": self._source_limit(source),
                "active": int(self.active_by_source.get(source) or 0),
                "waiting": int(self.waiting_by_source.get(source) or 0),
                "rejected": int(self.rejected_by_source.get(source) or 0),
                "acquired": int(self.acquired_by_source.get(source) or 0),
                "recent_avg_wait_seconds": round(avg_wait, 3),
            }
        return {
            "global": {
                "limit": global_limit,
                "active": global_active,
                "waiting": int(sum(self.waiting_by_source.values())),
            },
            "sources": source_payload,
            "queue_timeout_seconds": float(self.config["queue_timeout_seconds"]),
        }


_states: dict[asyncio.AbstractEventLoop, _LoopCapacityState] = {}


def _normalize_source(source: str) -> str:
    normalized = str(source or "").strip().lower()
    return normalized if normalized in {"wechat", "web"} else "wechat"


def _get_state() -> _LoopCapacityState:
    loop = asyncio.get_running_loop()
    state = _states.get(loop)
    config = _config()
    if state is None or state.config != config:
        state = _LoopCapacityState(config)
        _states[loop] = state
    return state


async def _acquire_one(semaphore: asyncio.Semaphore, timeout: float) -> bool:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False


@asynccontextmanager
async def acquire_order_query_capacity(
    source: str,
    *,
    identity: str = "",
    timeout: float | None = None,
) -> AsyncIterator[OrderQueryCapacitySlot]:
    normalized_source = _normalize_source(source)
    state = _get_state()
    timeout_seconds = float(timeout if timeout is not None else state.config["queue_timeout_seconds"])
    source_semaphore = state.source_semaphores[normalized_source]
    started_at = time.time()
    source_acquired = False
    global_acquired = False
    waiting_decremented = False
    state.waiting_by_source[normalized_source] += 1
    try:
        source_acquired = await _acquire_one(source_semaphore, timeout_seconds)
        remaining = max(0.001, timeout_seconds - (time.time() - started_at))
        if source_acquired:
            global_acquired = await _acquire_one(state.global_semaphore, remaining)
        state.waiting_by_source[normalized_source] = max(0, state.waiting_by_source[normalized_source] - 1)
        waiting_decremented = True
        if not source_acquired or not global_acquired:
            if source_acquired and not global_acquired:
                source_semaphore.release()
                source_acquired = False
            state.rejected_by_source[normalized_source] += 1
            raise OrderQueryCapacityBusy(source=normalized_source, stats=state.snapshot())

        waited = max(0.0, time.time() - started_at)
        samples = state.wait_samples_by_source[normalized_source]
        samples.append(waited)
        del samples[:-200]
        state.active_by_source[normalized_source] += 1
        state.acquired_by_source[normalized_source] += 1
        snapshot = state.snapshot()
        yield OrderQueryCapacitySlot(
            source=normalized_source,
            identity=str(identity or "").strip(),
            waited_seconds=waited,
            active_global=int(snapshot["global"]["active"]),
            active_source=int(snapshot["sources"][normalized_source]["active"]),
        )
    finally:
        if not waiting_decremented:
            state.waiting_by_source[normalized_source] = max(0, state.waiting_by_source[normalized_source] - 1)
        if global_acquired:
            state.global_semaphore.release()
        if source_acquired:
            source_semaphore.release()
            state.active_by_source[normalized_source] = max(0, state.active_by_source[normalized_source] - 1)


def get_order_query_capacity_stats() -> dict[str, Any]:
    try:
        state = _get_state()
    except RuntimeError:
        config = _config()
        return {
            "global": {"limit": int(config["global_limit"]), "active": 0, "waiting": 0},
            "sources": {
                "wechat": {"limit": int(config["wechat_limit"]), "active": 0, "waiting": 0, "rejected": 0, "acquired": 0, "recent_avg_wait_seconds": 0.0},
                "web": {"limit": int(config["web_limit"]), "active": 0, "waiting": 0, "rejected": 0, "acquired": 0, "recent_avg_wait_seconds": 0.0},
            },
            "queue_timeout_seconds": float(config["queue_timeout_seconds"]),
        }
    return state.snapshot()
