from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.logger import setup_logger
from utils.path_utils import resolve_runtime_data_path
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_web_user_registration_config,
)

logger = setup_logger(__name__)

AUTO_APPROVE_POLL_SECONDS = 5

_auto_approve_task: asyncio.Task | None = None
_auto_approve_stop_event: asyncio.Event | None = None
_auto_approve_lock: asyncio.Lock | None = None
_runtime_state: dict[str, Any] = {
    "running": False,
    "enabled": False,
    "last_run_at": "",
    "last_approved_count": 0,
    "last_granted_count": 0,
    "total_auto_approved": 0,
    "total_initial_granted": 0,
    "last_error": "",
}

_REGISTRATION_GRANT_COLUMN = "registration_initial_grant_applied"


def _get_web_query_db_path() -> Path:
    return resolve_runtime_data_path("meituan_query.db")


def _get_config() -> dict[str, Any]:
    return normalize_web_user_registration_config(
        load_system_settings_store().get("web_user_registration_config", {})
    )


def _ensure_user_registration_grant_schema(conn: sqlite3.Connection) -> None:
    cursor = conn.cursor()
    columns = {
        str(row[1] or "").strip()
        for row in cursor.execute("PRAGMA table_info(users)").fetchall()
    }
    if _REGISTRATION_GRANT_COLUMN in columns:
        return

    cursor.execute(
        f"ALTER TABLE users ADD COLUMN {_REGISTRATION_GRANT_COLUMN} INTEGER DEFAULT 0"
    )
    cursor.execute(
        f"""
        UPDATE users
        SET {_REGISTRATION_GRANT_COLUMN} = 1
        WHERE status = 'approved'
        """
    )
    conn.commit()
    logger.info("用户表已补充注册赠送标记列: column=%s", _REGISTRATION_GRANT_COLUMN)


def ensure_user_registration_grant_schema() -> None:
    db_path = _get_web_query_db_path()
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        _ensure_user_registration_grant_schema(conn)
    finally:
        conn.close()


def _sync_pending_users_once(*, auto_approve_enabled: bool, initial_query_count: int) -> tuple[int, int]:
    db_path = _get_web_query_db_path()
    if not db_path.exists():
        return 0, 0

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        _ensure_user_registration_grant_schema(conn)
        cursor = conn.cursor()
        approved_count = 0
        if auto_approve_enabled:
            cursor.execute(
                """
                UPDATE users
                SET status = 'approved',
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'pending'
                """
            )
            approved_count = int(cursor.rowcount or 0)

        cursor.execute(
            f"""
            UPDATE users
            SET query_count = CASE
                    WHEN COALESCE(query_count, 0) < ? THEN ?
                    ELSE COALESCE(query_count, 0)
                END,
                {_REGISTRATION_GRANT_COLUMN} = 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE status = 'approved'
              AND COALESCE({_REGISTRATION_GRANT_COLUMN}, 0) = 0
            """,
            (int(initial_query_count), int(initial_query_count)),
        )
        granted_count = int(cursor.rowcount or 0)
        conn.commit()
        return approved_count, granted_count
    finally:
        conn.close()


def get_web_user_auto_approve_runtime() -> dict[str, Any]:
    config = _get_config()
    return {
        **_runtime_state,
        "running": _auto_approve_task is not None and not _auto_approve_task.done(),
        "enabled": bool(config.get("auto_approve")),
        "initial_query_count": int(config.get("initial_query_count") or 0),
        "poll_seconds": AUTO_APPROVE_POLL_SECONDS,
    }


async def _auto_approve_loop() -> None:
    global _runtime_state
    assert _auto_approve_stop_event is not None

    while not _auto_approve_stop_event.is_set():
        try:
            config = _get_config()
            enabled = bool(config.get("auto_approve"))
            initial_query_count = int(config.get("initial_query_count") or 0)
            _runtime_state["enabled"] = enabled
            _runtime_state["running"] = True
            _runtime_state["last_run_at"] = datetime.now().isoformat(timespec="seconds")

            if _auto_approve_lock is None:
                raise RuntimeError("auto_approve_lock_not_initialized")
            async with _auto_approve_lock:
                approved_count, granted_count = await asyncio.to_thread(
                    _sync_pending_users_once,
                    auto_approve_enabled=enabled,
                    initial_query_count=initial_query_count,
                )
            _runtime_state["last_approved_count"] = approved_count
            _runtime_state["last_granted_count"] = granted_count
            _runtime_state["total_auto_approved"] = int(_runtime_state.get("total_auto_approved") or 0) + approved_count
            _runtime_state["total_initial_granted"] = int(_runtime_state.get("total_initial_granted") or 0) + granted_count
            _runtime_state["last_error"] = ""
            if approved_count > 0 or granted_count > 0:
                logger.info(
                    "注册审核同步完成: approved=%s granted=%s initial_query_count=%s auto_approve=%s",
                    approved_count,
                    granted_count,
                    initial_query_count,
                    enabled,
                )

            await asyncio.wait_for(_auto_approve_stop_event.wait(), timeout=AUTO_APPROVE_POLL_SECONDS)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _runtime_state["last_error"] = f"{exc.__class__.__name__}: {exc}"
            _runtime_state["last_run_at"] = datetime.now().isoformat(timespec="seconds")
            logger.warning("自动同意新注册用户失败: %s", exc, exc_info=True)
            await asyncio.sleep(AUTO_APPROVE_POLL_SECONDS)

    _runtime_state["running"] = False


def start_web_user_auto_approve_task() -> None:
    global _auto_approve_task, _auto_approve_stop_event, _auto_approve_lock
    if _auto_approve_task is not None and not _auto_approve_task.done():
        return
    _auto_approve_stop_event = asyncio.Event()
    _auto_approve_lock = asyncio.Lock()
    _runtime_state["running"] = True
    _auto_approve_task = asyncio.create_task(_auto_approve_loop())
    logger.info("Web 注册自动同意后台任务已启动")


async def stop_web_user_auto_approve_task() -> None:
    global _auto_approve_task, _auto_approve_stop_event, _auto_approve_lock
    if _auto_approve_stop_event is not None:
        _auto_approve_stop_event.set()
    if _auto_approve_task is not None:
        try:
            await _auto_approve_task
        except asyncio.CancelledError:
            pass
    _auto_approve_task = None
    _auto_approve_stop_event = None
    _auto_approve_lock = None
    _runtime_state["running"] = False
