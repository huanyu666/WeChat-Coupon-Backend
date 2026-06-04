from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

from utils.logger import setup_logger
from utils.runtime_migration import create_migration_archive, get_migration_archive_dir
from utils.system_settings_store import load_system_settings_store, normalize_backup_schedule_config

logger = setup_logger(__name__)

SCHEDULED_BACKUP_PREFIX = "wx-coupon-scheduled-"
_scheduler_task: asyncio.Task | None = None
_scheduler_stop_event: asyncio.Event | None = None
_scheduler_lock: asyncio.Lock | None = None
_last_result: dict[str, Any] = {}
_next_run_at: str = ""


def _get_config() -> dict[str, Any]:
    return normalize_backup_schedule_config(
        load_system_settings_store().get("backup_schedule_config", {})
    )


def _parse_hour_minute(value: str) -> tuple[int, int]:
    hour_text, minute_text = str(value or "03:00").split(":", 1)
    return int(hour_text), int(minute_text)


def compute_next_run_at(config: dict[str, Any], now: datetime | None = None) -> datetime | None:
    if not config.get("enabled"):
        return None
    tz = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    frequency = str(config.get("frequency") or "weekly")
    hour, minute = _parse_hour_minute(str(config.get("time") or "03:00"))

    if frequency == "hourly":
        candidate = current.replace(minute=minute, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(hours=1)
        return candidate

    candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if frequency == "daily":
        if candidate <= current:
            candidate += timedelta(days=1)
        return candidate

    target_weekday = int(str(config.get("weekday") or "6"))
    days_ahead = (target_weekday - current.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= current:
        candidate += timedelta(days=7)
    return candidate


def get_backup_schedule_status() -> dict[str, Any]:
    config = _get_config()
    next_run = compute_next_run_at(config)
    return {
        "config": config,
        "running": _scheduler_task is not None and not _scheduler_task.done(),
        "next_run_at": next_run.isoformat(timespec="seconds") if next_run else "",
        "last_result": dict(_last_result),
    }


def _prune_scheduled_backups(retention_count: int) -> int:
    archive_dir = get_migration_archive_dir("scheduled")
    archives = sorted(
        [
            path
            for pattern in ("*.tar.gz", "*.tgz")
            for path in archive_dir.glob(pattern)
            if path.is_file() and path.name.startswith(SCHEDULED_BACKUP_PREFIX)
        ],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    deleted = 0
    for path in archives[max(1, retention_count) :]:
        try:
            path.unlink()
            deleted += 1
        except OSError:
            logger.warning("自动备份清理失败: path=%s", path, exc_info=True)
    return deleted


def _create_scheduled_backup(config: dict[str, Any]) -> dict[str, Any]:
    archive_dir = get_migration_archive_dir("scheduled")
    archive_path = archive_dir / f"{SCHEDULED_BACKUP_PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}.tar.gz"
    result = create_migration_archive(
        archive_path,
        include_env=bool(config.get("include_env")),
        include_redis_shortlinks=bool(config.get("include_redis_shortlinks")),
        include_redis_runtime=bool(config.get("include_redis_runtime")),
    )
    pruned_count = _prune_scheduled_backups(int(str(config.get("retention_count") or "30")))
    return {
        "success": True,
        "archive_path": result.get("archive_path", ""),
        "archive_name": result.get("archive_name", Path(str(result.get("archive_path", ""))).name),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "pruned_count": pruned_count,
        "include_env": bool(config.get("include_env")),
        "include_redis_shortlinks": bool(config.get("include_redis_shortlinks")),
        "include_redis_runtime": bool(config.get("include_redis_runtime")),
        "redis_shortlink_key_count": (result.get("redis_shortlink_stats") or {}).get("key_count", 0),
        "redis_runtime_key_count": (result.get("redis_runtime_stats") or {}).get("key_count", 0),
    }


async def run_scheduled_backup_once() -> dict[str, Any]:
    global _last_result, _scheduler_lock
    if _scheduler_lock is None:
        _scheduler_lock = asyncio.Lock()
    async with _scheduler_lock:
        config = _get_config()
        result = await asyncio.to_thread(_create_scheduled_backup, config)
        _last_result = result
        logger.info("自动备份完成: %s", result)
        return result


async def _scheduler_loop() -> None:
    global _last_result, _next_run_at
    assert _scheduler_stop_event is not None
    while not _scheduler_stop_event.is_set():
        try:
            config = _get_config()
            next_run = compute_next_run_at(config)
            _next_run_at = next_run.isoformat(timespec="seconds") if next_run else ""
            if next_run is None:
                await asyncio.wait_for(_scheduler_stop_event.wait(), timeout=60)
                continue
            now = datetime.now(next_run.tzinfo)
            sleep_seconds = max(1.0, min(300.0, (next_run - now).total_seconds()))
            await asyncio.wait_for(_scheduler_stop_event.wait(), timeout=sleep_seconds)
            if _scheduler_stop_event.is_set():
                break
            if datetime.now(next_run.tzinfo) >= next_run:
                await run_scheduled_backup_once()
                await asyncio.sleep(1)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _last_result = {
                "success": False,
                "error": str(exc),
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            logger.warning("自动备份调度失败: %s", exc, exc_info=True)
            await asyncio.sleep(60)


def start_backup_scheduler() -> None:
    global _scheduler_task, _scheduler_stop_event, _scheduler_lock
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_stop_event = asyncio.Event()
    _scheduler_lock = asyncio.Lock()
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("自动备份调度已启动")


async def stop_backup_scheduler() -> None:
    global _scheduler_task, _scheduler_stop_event
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    if _scheduler_task is not None:
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
    _scheduler_stop_event = None
