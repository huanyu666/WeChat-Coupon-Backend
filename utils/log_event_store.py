from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.path_utils import get_runtime_data_dir


EVENT_FILE_NAME = "log_panel_events.jsonl"
MAX_EVENT_FILE_BYTES = 5 * 1024 * 1024
MAX_EVENT_BACKUP_COUNT = 3
_EVENT_LOCK = threading.RLock()


def _event_path() -> Path:
    return get_runtime_data_dir() / EVENT_FILE_NAME


def _mask_user_id(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= 8:
        return text
    return f"{text[:4]}***{text[-4:]}"


def _rotate_if_needed(path: Path) -> None:
    if not path.exists() or path.stat().st_size < MAX_EVENT_FILE_BYTES:
        return
    for index in range(MAX_EVENT_BACKUP_COUNT, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        target = path.with_name(f"{path.name}.{index + 1}")
        if index == MAX_EVENT_BACKUP_COUNT and source.exists():
            source.unlink()
            continue
        if source.exists():
            source.replace(target)
    path.replace(path.with_name(f"{path.name}.1"))


def append_log_event(event_type: str, payload: dict[str, Any]) -> None:
    event = {
        "event_type": str(event_type or "").strip() or "unknown",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_ts": time.time(),
        "payload": payload,
    }
    path = _event_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    with _EVENT_LOCK:
        _rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")


def append_order_query_event(
    *,
    status: str,
    account_name: str,
    user_id: str,
    elapsed_seconds: float,
    last_stage: str,
    stage_summary: str,
    proxy_summary: str,
    proxy_attempts: int,
    unique_proxy_count: int,
    proxy_retry_attempts: int,
    max_proxy_switches: int,
    error: str = "",
    failure_type: str = "",
    proxy_switch_effective: bool | None = None,
    proxy_switch_reason: str = "",
) -> None:
    append_log_event(
        "order_query",
        {
            "status": str(status or "").strip() or "unknown",
            "account_name": str(account_name or "").strip(),
            "user_id_masked": _mask_user_id(user_id),
            "elapsed_seconds": round(float(elapsed_seconds or 0.0), 3),
            "last_stage": str(last_stage or "").strip(),
            "stage_summary": str(stage_summary or "").strip(),
            "proxy_summary": str(proxy_summary or "").strip(),
            "proxy_attempts": int(proxy_attempts or 0),
            "unique_proxy_count": int(unique_proxy_count or 0),
            "proxy_retry_attempts": int(proxy_retry_attempts or 0),
            "max_proxy_switches": int(max_proxy_switches or 0),
            "error": str(error or "").strip()[:500],
            "failure_type": str(failure_type or "").strip(),
            "proxy_switch_effective": proxy_switch_effective,
            "proxy_switch_reason": str(proxy_switch_reason or "").strip(),
        },
    )


def append_proxy_event(
    *,
    status: str,
    proxy_url: str,
    error: str = "",
    phase: str = "",
    valid_remaining: int | None = None,
) -> None:
    append_log_event(
        "proxy",
        {
            "status": str(status or "").strip() or "unknown",
            "proxy_url": str(proxy_url or "").strip(),
            "error": str(error or "").strip()[:500],
            "phase": str(phase or "").strip(),
            "valid_remaining": valid_remaining,
        },
    )


def load_recent_log_events(minutes: int = 1440, limit: int = 1000) -> list[dict[str, Any]]:
    minutes = min(max(int(minutes or 1440), 1), 7 * 24 * 60)
    limit = min(max(int(limit or 1000), 20), 5000)
    path = _event_path()
    if not path.exists():
        return []
    since_ts = (datetime.now() - timedelta(minutes=minutes)).timestamp()
    events: list[dict[str, Any]] = []
    with _EVENT_LOCK:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-limit * 2:]:
        try:
            event = json.loads(line)
        except Exception:
            continue
        created_ts = float(event.get("created_ts") or 0.0)
        if created_ts < since_ts:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events[-limit:]


def get_event_store_info() -> dict[str, Any]:
    path = _event_path()
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
    }
