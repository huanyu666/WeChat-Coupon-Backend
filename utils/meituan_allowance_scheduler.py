from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from utils.logger import setup_logger
from utils.meituan_allowance_task_storage import get_meituan_allowance_task_storage
from utils.path_utils import resolve_runtime_data_path
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_allowance_schedule_config,
)

logger = setup_logger(__name__)

ALLOWANCE_SCHEDULER_JOB_NAME = "daily_allowance_refresh"

_scheduler_task: asyncio.Task | None = None
_scheduler_stop_event: asyncio.Event | None = None
_scheduler_refresh_event: asyncio.Event | None = None
_scheduler_lock: asyncio.Lock | None = None
_last_result: dict[str, Any] = {}


def _get_config() -> dict[str, Any]:
    return normalize_allowance_schedule_config(
        load_system_settings_store().get("allowance_schedule_config", {})
    )


def _parse_hour_minute(value: str) -> tuple[int, int]:
    hour_text, minute_text = str(value or "00:00").split(":", 1)
    return int(hour_text), int(minute_text)


def _get_schedule_times(config: dict[str, Any]) -> list[str]:
    raw_times = config.get("times")
    if isinstance(raw_times, list):
        times = [str(item or "").strip() for item in raw_times if str(item or "").strip()]
        if times:
            return times
    fallback_time = str(config.get("time") or "").strip()
    return [fallback_time] if fallback_time else []


def _get_address_scope(config: dict[str, Any]) -> str:
    value = str(config.get("address_scope") or "").strip()
    return value if value in {"all", "latest"} else "all"


def compute_next_run_at(config: dict[str, Any], now: datetime | None = None) -> datetime | None:
    if not config.get("enabled"):
        return None
    tz = ZoneInfo(str(config.get("timezone") or "Asia/Shanghai"))
    current = now.astimezone(tz) if now is not None else datetime.now(tz)
    candidates: list[datetime] = []
    for schedule_time in _get_schedule_times(config):
        hour, minute = _parse_hour_minute(schedule_time)
        candidate = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= current:
            candidate += timedelta(days=1)
        candidates.append(candidate)
    return min(candidates) if candidates else None


def _get_scheduler_run_state() -> dict[str, Any]:
    return get_meituan_allowance_task_storage().get_scheduler_run(ALLOWANCE_SCHEDULER_JOB_NAME)


def _build_run_slot_key(run_at: datetime) -> str:
    return run_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")


def get_allowance_schedule_status() -> dict[str, Any]:
    config = _get_config()
    next_run = compute_next_run_at(config)
    scheduler_run = _get_scheduler_run_state()
    return {
        "config": config,
        "running": _scheduler_task is not None and not _scheduler_task.done(),
        "next_run_at": next_run.isoformat(timespec="seconds") if next_run else "",
        "last_result": dict(_last_result or scheduler_run.get("last_result") or {}),
        "last_run_date": str(scheduler_run.get("last_run_date") or ""),
        "last_run_at": int(scheduler_run.get("last_run_at") or 0),
    }


def notify_allowance_schedule_updated() -> None:
    global _scheduler_refresh_event
    if _scheduler_refresh_event is not None:
        _scheduler_refresh_event.set()


def _get_order_query_db_path() -> str:
    return str(resolve_runtime_data_path("meituan_query.db"))


def _load_active_tokens_for_allowance_refresh() -> dict[str, dict[str, Any]]:
    conn = sqlite3.connect(_get_order_query_db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, name, token, meituan_user_id, is_active, updated_at
            FROM tokens
            WHERE is_active = 1 AND token IS NOT NULL AND token != '' AND meituan_user_id IS NOT NULL AND meituan_user_id != ''
            ORDER BY updated_at DESC, id DESC
            """
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    tokens_by_user_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        meituan_user_id = str(row["meituan_user_id"] or "").strip()
        if not meituan_user_id or meituan_user_id in tokens_by_user_id:
            continue
        tokens_by_user_id[meituan_user_id] = {
            "id": int(row["id"] or 0),
            "user_id": int(row["user_id"] or 0),
            "name": str(row["name"] or "").strip(),
            "token": str(row["token"] or "").strip(),
            "meituan_user_id": meituan_user_id,
        }
    return tokens_by_user_id


async def _refresh_allowance_targets_after_clear() -> dict[str, Any]:
    from routes.wechat import (
        _create_meituan_allowance_task_internal,
        _extract_meituan_user_id,
        _load_meituan_history_address_options,
        _safe_text,
    )

    storage = get_meituan_allowance_task_storage()
    token_map = await asyncio.to_thread(_load_active_tokens_for_allowance_refresh)
    config = _get_config()
    address_scope = _get_address_scope(config)
    discovered_target_count = 0
    discovery_failed_targets: list[dict[str, Any]] = []
    selected_targets: dict[tuple[str, str], dict[str, Any]] = {}

    for raw_user_id, token_record in token_map.items():
        meituan_user_id = _extract_meituan_user_id(raw_user_id)
        token = str((token_record or {}).get("token") or "").strip()
        if not meituan_user_id or not token:
            continue
        try:
            addresses = await _load_meituan_history_address_options(token=token, user_id=meituan_user_id)
        except Exception as exc:
            logger.warning(
                "津贴自动更新读取账号历史地址失败: meituan_user_id=%s error=%s",
                meituan_user_id,
                exc,
                exc_info=True,
            )
            discovery_failed_targets.append({
                "meituan_user_id": meituan_user_id,
                "reason": f"load_addresses_failed:{exc.__class__.__name__}",
            })
            continue

        selected_addresses = addresses[:1] if address_scope == "latest" else addresses

        for item in selected_addresses:
            if not isinstance(item, dict):
                continue
            resolved_address = item.get("resolved_address")
            if not isinstance(resolved_address, dict) or not resolved_address:
                continue
            address_id = _safe_text(resolved_address.get("address_id"))
            if not address_id:
                continue
            storage.upsert_refresh_target(
                meituan_user_id=meituan_user_id,
                address_id=address_id,
                resolved_address=resolved_address,
            )
            selected_targets[(meituan_user_id, address_id)] = {
                "meituan_user_id": meituan_user_id,
                "address_id": address_id,
                "resolved_address": resolved_address,
            }
            discovered_target_count += 1

    targets = list(selected_targets.values())
    refreshed_targets: list[dict[str, Any]] = []
    skipped_targets: list[dict[str, Any]] = []

    for target in targets:
        meituan_user_id = str(target.get("meituan_user_id") or "").strip()
        address_id = str(target.get("address_id") or "").strip()
        resolved_address = target.get("resolved_address") or {}
        token_record = token_map.get(meituan_user_id)

        if not token_record:
            skipped_targets.append({
                "meituan_user_id": meituan_user_id,
                "address_id": address_id,
                "reason": "missing_active_token",
            })
            continue
        if not isinstance(resolved_address, dict) or not resolved_address:
            skipped_targets.append({
                "meituan_user_id": meituan_user_id,
                "address_id": address_id,
                "reason": "missing_resolved_address",
            })
            continue

        try:
            task_payload = await _create_meituan_allowance_task_internal(
                token=token_record["token"],
                meituan_user_id=meituan_user_id,
                resolved_address=resolved_address,
            )
            storage.mark_refresh_target_refreshed(
                meituan_user_id=meituan_user_id,
                address_id=address_id,
                task_id=str(task_payload.get("task_id") or ""),
            )
            refreshed_targets.append({
                "meituan_user_id": meituan_user_id,
                "address_id": address_id,
                "task_id": str(task_payload.get("task_id") or ""),
            })
        except Exception as exc:
            logger.warning(
                "津贴自动更新创建任务失败: meituan_user_id=%s address_id=%s error=%s",
                meituan_user_id,
                address_id,
                exc,
                exc_info=True,
            )
            skipped_targets.append({
                "meituan_user_id": meituan_user_id,
                "address_id": address_id,
                "reason": f"create_task_failed:{exc.__class__.__name__}",
            })

    return {
        "refreshed_count": len(refreshed_targets),
        "refreshed_targets": refreshed_targets,
        "skipped_count": len(skipped_targets),
        "skipped_targets": skipped_targets,
        "registered_target_count": len(targets),
        "discovered_target_count": discovered_target_count,
        "discovery_failed_count": len(discovery_failed_targets),
        "discovery_failed_targets": discovery_failed_targets,
        "address_scope": address_scope,
    }


async def _run_allowance_refresh_job() -> dict[str, Any]:
    storage = get_meituan_allowance_task_storage()
    deleted_count = await asyncio.to_thread(storage.clear_all_tasks)
    refresh_result = await _refresh_allowance_targets_after_clear()
    return {
        "success": True,
        "deleted_count": deleted_count,
        "executed_at": datetime.now().isoformat(timespec="seconds"),
        "reason": "scheduled_daily_refresh",
        **refresh_result,
    }


async def run_allowance_schedule_once() -> dict[str, Any]:
    global _last_result, _scheduler_lock
    if _scheduler_lock is None:
        _scheduler_lock = asyncio.Lock()
    async with _scheduler_lock:
        result = await _run_allowance_refresh_job()
        shanghai_now = datetime.now(ZoneInfo("Asia/Shanghai"))
        get_meituan_allowance_task_storage().set_scheduler_run(
            job_name=ALLOWANCE_SCHEDULER_JOB_NAME,
            last_run_date=_build_run_slot_key(shanghai_now),
            last_run_at=int(shanghai_now.timestamp()),
            last_result=result,
        )
        _last_result = result
        logger.info("津贴列表定时任务完成: %s", result)
        return result


async def _wait_for_scheduler_signal(timeout: float | None) -> str:
    assert _scheduler_stop_event is not None
    stop_task = asyncio.create_task(_scheduler_stop_event.wait())
    refresh_task = (
        asyncio.create_task(_scheduler_refresh_event.wait())
        if _scheduler_refresh_event is not None
        else None
    )
    tasks = [stop_task] + ([refresh_task] if refresh_task is not None else [])
    try:
        done, pending = await asyncio.wait(
            tasks,
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if not done:
            return "timeout"
        if stop_task in done and _scheduler_stop_event.is_set():
            return "stop"
        if refresh_task is not None and refresh_task in done:
            if _scheduler_refresh_event is not None:
                _scheduler_refresh_event.clear()
            return "refresh"
        return "timeout"
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


async def _scheduler_loop() -> None:
    global _last_result
    assert _scheduler_stop_event is not None
    while not _scheduler_stop_event.is_set():
        try:
            config = _get_config()
            next_run = compute_next_run_at(config)
            if next_run is None:
                signal = await _wait_for_scheduler_signal(60)
                if signal == "stop":
                    break
                continue

            now = datetime.now(next_run.tzinfo)
            sleep_seconds = max(1.0, min(300.0, (next_run - now).total_seconds()))
            signal = await _wait_for_scheduler_signal(sleep_seconds)
            if signal == "stop":
                break
            if signal == "refresh":
                continue

            current_time = datetime.now(next_run.tzinfo)
            if current_time >= next_run:
                run_state = _get_scheduler_run_state()
                slot_key = _build_run_slot_key(next_run)
                if str(run_state.get("last_run_date") or "") != slot_key:
                    await run_allowance_schedule_once()
                await asyncio.sleep(1)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _last_result = {
                "success": False,
                "error": str(exc),
                "executed_at": datetime.now().isoformat(timespec="seconds"),
            }
            logger.warning("津贴列表定时任务失败: %s", exc, exc_info=True)
            await asyncio.sleep(60)


def start_allowance_scheduler() -> None:
    global _scheduler_task, _scheduler_stop_event, _scheduler_refresh_event, _scheduler_lock
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_stop_event = asyncio.Event()
    _scheduler_refresh_event = asyncio.Event()
    _scheduler_lock = asyncio.Lock()
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    logger.info("津贴列表定时任务已启动")


async def stop_allowance_scheduler() -> None:
    global _scheduler_task, _scheduler_stop_event, _scheduler_refresh_event
    if _scheduler_stop_event is not None:
        _scheduler_stop_event.set()
    if _scheduler_refresh_event is not None:
        _scheduler_refresh_event.set()
    if _scheduler_task is not None:
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass
    _scheduler_task = None
    _scheduler_stop_event = None
    _scheduler_refresh_event = None
