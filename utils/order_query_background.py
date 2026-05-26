from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


WorkFactory = Callable[[], Awaitable[Any]]


@dataclass
class _BackgroundState:
    queue_limit: int
    worker_count: int
    queue: asyncio.Queue[tuple[str, WorkFactory, Any]] = field(init=False)
    workers: list[asyncio.Task] = field(default_factory=list)
    submitted: int = 0
    dropped: int = 0
    completed: int = 0
    failed: int = 0
    active: int = 0

    def __post_init__(self) -> None:
        self.queue = asyncio.Queue(maxsize=self.queue_limit)


_states: dict[asyncio.AbstractEventLoop, _BackgroundState] = {}
_blocking_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}


def _get_config() -> tuple[int, int]:
    queue_limit = _env_int("WX_ORDER_QUERY_BACKGROUND_QUEUE_LIMIT", 100, 1, 5000)
    worker_count = _env_int("WX_ORDER_QUERY_BACKGROUND_WORKERS", 2, 1, 20)
    return queue_limit, worker_count


def _get_state() -> _BackgroundState:
    loop = asyncio.get_running_loop()
    queue_limit, worker_count = _get_config()
    state = _states.get(loop)
    if state is None or state.queue_limit != queue_limit or state.worker_count != worker_count:
        state = _BackgroundState(queue_limit=queue_limit, worker_count=worker_count)
        _states[loop] = state
    while len(state.workers) < state.worker_count:
        state.workers.append(loop.create_task(_worker(state)))
    return state


async def _worker(state: _BackgroundState) -> None:
    while True:
        name, work_factory, logger = await state.queue.get()
        state.active += 1
        try:
            await work_factory()
            state.completed += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            state.failed += 1
            if logger is not None:
                try:
                    logger.warning("订单查询后台任务失败: name=%s error=%s", name, exc)
                except Exception:
                    pass
        finally:
            state.active = max(0, state.active - 1)
            state.queue.task_done()


def submit_order_query_background(name: str, work_factory: WorkFactory, *, logger: Any = None) -> bool:
    state = _get_state()
    try:
        state.queue.put_nowait((str(name or "background"), work_factory, logger))
    except asyncio.QueueFull:
        state.dropped += 1
        if logger is not None:
            try:
                logger.warning(
                    "订单查询后台任务队列已满，跳过任务: name=%s queued=%d limit=%d dropped=%d",
                    name,
                    state.queue.qsize(),
                    state.queue_limit,
                    state.dropped,
                )
            except Exception:
                pass
        return False
    state.submitted += 1
    return True


async def run_blocking_order_query_work(name: str, func: Callable[[], Any]) -> Any:
    del name
    loop = asyncio.get_running_loop()
    worker_count = _get_config()[1]
    semaphore = _blocking_semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(worker_count)
        _blocking_semaphores[loop] = semaphore
    async with semaphore:
        return await asyncio.to_thread(func)


def get_order_query_background_stats() -> dict[str, Any]:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        queue_limit, worker_count = _get_config()
        return {
            "queue_limit": queue_limit,
            "worker_count": worker_count,
            "queued": 0,
            "active": 0,
            "submitted": 0,
            "dropped": 0,
            "completed": 0,
            "failed": 0,
        }
    state = _states.get(loop)
    if state is None:
        queue_limit, worker_count = _get_config()
        return {
            "queue_limit": queue_limit,
            "worker_count": worker_count,
            "queued": 0,
            "active": 0,
            "submitted": 0,
            "dropped": 0,
            "completed": 0,
            "failed": 0,
        }
    return {
        "queue_limit": state.queue_limit,
        "worker_count": state.worker_count,
        "queued": state.queue.qsize(),
        "active": state.active,
        "submitted": state.submitted,
        "dropped": state.dropped,
        "completed": state.completed,
        "failed": state.failed,
    }
