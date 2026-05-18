from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.path_utils import get_log_dir


LOG_LINE_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) - "
    r"(?P<logger>[^ ]+) - (?P<level>[A-Z]+) - (?P<message>.*)$"
)
SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(secret_id=)[^&\s]+"),
    re.compile(r"(?i)(signature=)[^&\s]+"),
    re.compile(r"(?i)(token=)[^&\s]+"),
    re.compile(r"(?i)(access_token=)[^&\s]+"),
    re.compile(r"(?i)(wm_logintoken=)[^&\s]+"),
    re.compile(r"(?i)(cookie:?\s*)[^,\n]+"),
    re.compile(r"(?i)(authorization:?\s*)[^,\n]+"),
    re.compile(r"(?i)(ghp_)[A-Za-z0-9_]+"),
    re.compile(r"(openid=)[^&\s]+"),
    re.compile(r"o[A-Za-z0-9_-]{20,}"),
]


def _mask_sensitive(value: str) -> str:
    output = str(value or "")
    for pattern in SENSITIVE_PATTERNS:
        output = pattern.sub(lambda match: f"{match.group(1)}***" if match.lastindex else "***", output)
    return output


def _parse_log_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _resolve_log_path() -> Path:
    configured = os.getenv("WX_LOG_PANEL_FILE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return get_log_dir() / "app.log"


def _read_tail_lines(path: Path, max_bytes: int = 2 * 1024 * 1024) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    size = path.stat().st_size
    with path.open("rb") as file:
        if size > max_bytes:
            file.seek(-max_bytes, os.SEEK_END)
            file.readline()
        raw = file.read()
    return raw.decode("utf-8", errors="replace").splitlines()


def _parse_line(line: str, index: int) -> dict[str, Any]:
    masked = _mask_sensitive(line)
    match = LOG_LINE_RE.match(masked)
    if not match:
        return {
            "id": index,
            "timestamp": "",
            "logger": "",
            "level": "INFO",
            "message": masked,
            "raw": masked,
            "category": _classify_log(masked),
        }
    data = match.groupdict()
    message = data.get("message") or ""
    return {
        "id": index,
        "timestamp": data.get("timestamp") or "",
        "logger": data.get("logger") or "",
        "level": data.get("level") or "INFO",
        "message": message,
        "raw": masked,
        "category": _classify_log(masked),
    }


def _classify_log(text: str) -> str:
    lowered = str(text or "").lower()
    if "订单查询" in text or "美团订单查询" in text or "insurance_list" in lowered or "external_order_id" in lowered:
        return "order_query"
    if "代理" in text or "proxy" in lowered or "460" in lowered:
        return "proxy"
    if "登录" in text or "auth" in lowered:
        return "auth"
    if "短链" in text or "shortlink" in lowered:
        return "shortlink"
    if "wechat" in lowered or "微信" in text:
        return "wechat"
    return "system"


def _is_order_query_event(item: dict[str, Any]) -> bool:
    return item.get("category") == "order_query" or "查询订单失败" in str(item.get("message") or "")


def _extract_proxy_count(message: str) -> int | None:
    match = re.search(r"unique(?:_proxies|)=(\d+)", message)
    if match:
        return int(match.group(1))
    return None


def _summarize_order_event(item: dict[str, Any]) -> dict[str, Any]:
    message = str(item.get("message") or "")
    stage_match = re.search(r"stage=([A-Za-z0-9_]+)", message)
    account_match = re.search(r"\[([^\]]+)\]", message)
    elapsed_match = re.search(r"elapsed=([0-9.]+)s", message)
    proxy_summary_match = re.search(r"(proxy_attempts=\d+\s+unique_proxies=\d+(?:\s+proxies=[^\s]+)?)", message)
    failed = item.get("level") == "ERROR" or "失败" in message or "error=" in message.lower()
    return {
        "timestamp": item.get("timestamp") or "",
        "level": item.get("level") or "INFO",
        "account": account_match.group(1) if account_match else "",
        "stage": stage_match.group(1) if stage_match else "",
        "success": not failed,
        "elapsed_seconds": float(elapsed_match.group(1)) if elapsed_match else None,
        "proxy_count": _extract_proxy_count(message),
        "proxy_summary": proxy_summary_match.group(1) if proxy_summary_match else "",
        "message": message,
    }


def _extract_error_reason(message: str) -> str:
    text = str(message or "")
    for pattern in (
        r"error=([^,]+)",
        r"错误[:：]\s*([^,，]+)",
        r"查询订单失败:\s*([^,，]+)",
        r"查询失败[:：]?\s*([^,，]+)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    if "460 Proxy Authentication Invalid" in text:
        return "460 Proxy Authentication Invalid"
    return ""


def _top_counter_items(counter: Counter, limit: int = 8) -> list[dict[str, Any]]:
    return [
        {"name": str(name), "count": int(count)}
        for name, count in counter.most_common(limit)
        if str(name)
    ]


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _build_structured_order_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "order_query":
            continue
        payload = _event_payload(event)
        status = str(payload.get("status") or "unknown")
        rows.append({
            "timestamp": event.get("created_at") or "",
            "level": "INFO" if status == "success" else "ERROR",
            "account": payload.get("account_name") or "",
            "user_id": payload.get("user_id_masked") or "",
            "stage": payload.get("last_stage") or "",
            "status": status,
            "success": status == "success",
            "elapsed_seconds": payload.get("elapsed_seconds"),
            "proxy_count": payload.get("unique_proxy_count"),
            "proxy_attempts": payload.get("proxy_attempts"),
            "proxy_retries": payload.get("proxy_retry_attempts"),
            "max_proxy_switches": payload.get("max_proxy_switches"),
            "error": payload.get("error") or "",
            "failure_type": payload.get("failure_type") or "",
            "proxy_switch_effective": payload.get("proxy_switch_effective"),
            "proxy_switch_reason": payload.get("proxy_switch_reason") or "",
            "stage_summary": payload.get("stage_summary") or "",
            "proxy_summary": payload.get("proxy_summary") or "",
            "message": (
                f"{status} · account={payload.get('account_name') or '-'} "
                f"elapsed={payload.get('elapsed_seconds')}s "
                f"stage={payload.get('last_stage') or '-'} "
                f"proxy={payload.get('unique_proxy_count')}/{payload.get('proxy_attempts')} "
                f"failure_type={payload.get('failure_type') or '-'} "
                f"error={payload.get('error') or '-'}"
            ),
        })
    return list(reversed(rows[-200:]))


def _build_proxy_health(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event_type") != "proxy":
            continue
        payload = _event_payload(event)
        proxy_url = str(payload.get("proxy_url") or "").strip()
        if not proxy_url:
            continue
        row = stats.setdefault(proxy_url, {
            "proxy_url": proxy_url,
            "success_count": 0,
            "failure_count": 0,
            "last_status": "",
            "last_error": "",
            "last_seen_at": "",
            "last_failure_at": "",
            "phase": "",
        })
        status = str(payload.get("status") or "unknown")
        if status == "success":
            row["success_count"] += 1
        elif status == "failed":
            row["failure_count"] += 1
            row["last_error"] = payload.get("error") or ""
            row["last_failure_at"] = event.get("created_at") or ""
        row["last_status"] = status
        row["last_seen_at"] = event.get("created_at") or ""
        row["phase"] = payload.get("phase") or ""
    for row in stats.values():
        total_count = int(row.get("success_count") or 0) + int(row.get("failure_count") or 0)
        row["total_count"] = total_count
        row["success_rate"] = round((int(row.get("success_count") or 0) / total_count) * 100, 1) if total_count else 0.0
    return sorted(
        stats.values(),
        key=lambda item: (str(item.get("last_seen_at") or ""), int(item.get("failure_count") or 0)),
        reverse=True,
    )[:200]


def get_log_panel_data(
    *,
    minutes: int = 30,
    level: str = "",
    keyword: str = "",
    category: str = "",
    limit: int = 300,
) -> dict[str, Any]:
    minutes = min(max(int(minutes or 30), 1), 24 * 60)
    limit = min(max(int(limit or 300), 20), 1000)
    normalized_level = str(level or "").strip().upper()
    normalized_keyword = str(keyword or "").strip().lower()
    normalized_category = str(category or "").strip().lower()
    since = datetime.now() - timedelta(minutes=minutes)
    log_path = _resolve_log_path()
    rows: list[dict[str, Any]] = []
    for index, line in enumerate(_read_tail_lines(log_path)):
        item = _parse_line(line, index)
        timestamp = _parse_log_time(str(item.get("timestamp") or ""))
        if timestamp is not None and timestamp < since:
            continue
        if normalized_level and item.get("level") != normalized_level:
            continue
        if normalized_category and item.get("category") != normalized_category:
            continue
        haystack = f"{item.get('logger', '')} {item.get('level', '')} {item.get('message', '')}".lower()
        if normalized_keyword and normalized_keyword not in haystack:
            continue
        rows.append(item)

    rows = list(reversed(rows[-limit:]))
    try:
        from utils.log_event_store import get_event_store_info, load_recent_log_events

        structured_events = load_recent_log_events(minutes=minutes, limit=2000)
        event_store = get_event_store_info()
    except Exception:
        structured_events = []
        event_store = {"path": "", "exists": False, "size_bytes": 0}
    structured_order_events = _build_structured_order_events(structured_events)
    proxy_health = _build_proxy_health(structured_events)
    level_counts = Counter(str(item.get("level") or "INFO") for item in rows)
    category_counts = Counter(str(item.get("category") or "system") for item in rows)
    proxy_failures = [
        item for item in rows
        if item.get("category") == "proxy" and (item.get("level") in {"WARNING", "ERROR"} or "失败" in str(item.get("message") or ""))
    ]
    text_order_events = [_summarize_order_event(item) for item in rows if _is_order_query_event(item)][:100]
    order_events = structured_order_events or text_order_events
    order_failures = [item for item in order_events if not bool(item.get("success"))]
    stage_counter = Counter(str(item.get("stage") or "unknown") for item in order_failures)
    proxy_reason_counter = Counter(
        _extract_error_reason(str(item.get("message") or "")) or "unknown"
        for item in proxy_failures
    )
    order_reason_counter = Counter(
        _extract_error_reason(str(item.get("message") or "")) or "unknown"
        for item in order_failures
    )
    failure_type_counter = Counter(
        str(item.get("failure_type") or "unknown")
        for item in order_failures
    )
    max_unique_proxy_count = 0
    for item in order_events:
        proxy_count = item.get("proxy_count")
        if proxy_count is not None:
            max_unique_proxy_count = max(max_unique_proxy_count, int(proxy_count))

    return {
        "success": True,
        "source": str(log_path),
        "source_exists": log_path.exists(),
        "source_size_bytes": log_path.stat().st_size if log_path.exists() else 0,
        "minutes": minutes,
        "items": rows,
        "summary": {
            "total": len(rows),
            "errors": int(level_counts.get("ERROR", 0)),
            "warnings": int(level_counts.get("WARNING", 0)),
            "order_query": max(int(category_counts.get("order_query", 0)), len(order_events)),
            "order_query_failures": len(order_failures),
            "proxy": int(category_counts.get("proxy", 0)),
            "proxy_failures": max(len(proxy_failures), sum(1 for item in proxy_health if int(item.get("failure_count") or 0) > 0)),
            "max_unique_proxy_count": max_unique_proxy_count,
            "structured_events": len(structured_events),
        },
        "event_store": event_store,
        "insights": {
            "top_order_failure_stages": _top_counter_items(stage_counter),
            "top_order_failure_reasons": _top_counter_items(order_reason_counter),
            "top_order_failure_types": _top_counter_items(failure_type_counter),
            "top_proxy_failure_reasons": _top_counter_items(proxy_reason_counter),
        },
        "order_events": order_events,
        "text_order_events": text_order_events,
        "proxy_health": proxy_health,
        "proxy_failures": proxy_failures[:100],
    }
