"""
共享排行榜规则与本地排行榜记录
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse

from utils.path_utils import resolve_runtime_data_path
from utils.timezone_utils import get_timezone

GLOBAL_CONFIG_KEY = "global"
LEGACY_ACCOUNT_ID = "gh_81203cdf19a5"
DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_RULE_ID = "default"
DEFAULT_RULE_NAME = "默认排行榜"
DEFAULT_LEADERBOARD_PATH = "/order-rankings"
V2_PUBLIC_LEADERBOARD_PATH = "/order-rankings-v2"
LEGACY_DEFAULT_LEADERBOARD_URLS = {
    "http://waimaiyouhui.top/order-rankings",
    "https://waimaiyouhui.top/order-rankings",
}


def normalize_timestamp_seconds(timestamp: Any) -> Optional[int]:
    if timestamp is None:
        return None
    value = int(timestamp)
    if value > 1_000_000_000_000:
        value = int(value / 1000)
    return value


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _canonical_order_key(service_order_id: Any = "", order_id: Any = "") -> str:
    normalized_service_order_id = _normalize_text(service_order_id)
    normalized_order_id = _normalize_text(order_id)
    if normalized_service_order_id:
        return f"service:{normalized_service_order_id}"
    if normalized_order_id:
        return f"order:{normalized_order_id}"
    return ""


def _normalize_base_url(value: Any) -> str:
    text = _normalize_text(value).rstrip("/")
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        return ""
    parsed = urlparse(text)
    if not parsed.netloc:
        return ""
    return text


def _is_local_base_url(value: Any) -> bool:
    base_url = _normalize_base_url(value)
    if not base_url:
        return False
    parsed = urlparse(base_url)
    host = str(parsed.hostname or "").strip("[]").lower()
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", "host.docker.internal"}:
        return True
    return host.startswith("127.")


def _join_url(base_url: str, path: str) -> str:
    normalized_base = _normalize_base_url(base_url)
    if not normalized_base:
        return ""
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    return normalized_base + normalized_path


def _normalize_leaderboard_url(value: Any) -> str:
    url = _normalize_text(value).rstrip("/")
    if not url:
        return ""
    if url in LEGACY_DEFAULT_LEADERBOARD_URLS:
        return ""
    return url


def resolve_auto_leaderboard_base_url() -> str:
    try:
        from utils.shortlink_service import get_shortlink_config

        shortlink_base_url = _normalize_base_url(get_shortlink_config().public_base_url)
        if shortlink_base_url and not _is_local_base_url(shortlink_base_url):
            return shortlink_base_url
    except Exception:
        pass

    service_public_url = _normalize_base_url(os.getenv("WX_SERVICE_PUBLIC_URL"))
    if service_public_url:
        return service_public_url
    return ""


def resolve_auto_leaderboard_url() -> str:
    return _join_url(resolve_auto_leaderboard_base_url(), DEFAULT_LEADERBOARD_PATH) or DEFAULT_LEADERBOARD_PATH


def resolve_rule_leaderboard_url(rule: dict[str, Any] | None) -> str:
    manual_url = _normalize_leaderboard_url((rule or {}).get("leaderboard_url"))
    return manual_url or resolve_auto_leaderboard_url()


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = value.splitlines()
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _normalize_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _normalize_text(value).lower() in {"1", "true", "yes", "on"}


def _normalize_time_list(value: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in _normalize_text_list(value):
        parts = item.split(":", 1)
        if len(parts) != 2:
            continue
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except (TypeError, ValueError):
            continue
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            continue
        normalized_item = f"{hour:02d}:{minute:02d}"
        if normalized_item in seen:
            continue
        seen.add(normalized_item)
        normalized.append(normalized_item)
    return normalized


def _normalize_date_text(value: Any) -> str:
    text = _normalize_text(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return ""
    return text


def _normalize_date_overrides(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple, set)):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(value):
        if not isinstance(item, dict):
            continue
        date_text = _normalize_text(item.get("date"))
        if not date_text or date_text in seen:
            continue
        if len(date_text) != 10:
            continue
        times = _normalize_time_list(item.get("times"))
        if not times:
            continue
        seen.add(date_text)
        normalized.append({
            "date": date_text,
            "times": times,
        })
    normalized.sort(key=lambda item: str(item.get("date") or ""))
    return normalized


def _slugify_rule_id(name: str, fallback: str) -> str:
    raw = _normalize_text(name).lower()
    slug_chars: list[str] = []
    previous_dash = False
    for char in raw:
        if char.isalnum():
            slug_chars.append(char)
            previous_dash = False
            continue
        if previous_dash:
            continue
        slug_chars.append("-")
        previous_dash = True
    slug = "".join(slug_chars).strip("-")
    return slug or fallback


def _normalize_leaderboard_rule(raw_value: Any, *, index: int = 0) -> dict[str, Any]:
    if not isinstance(raw_value, dict):
        raw_value = {}
    rule_name = _normalize_text(raw_value.get("name")) or _normalize_text(raw_value.get("title")) or DEFAULT_RULE_NAME
    raw_rule_id = _normalize_text(raw_value.get("id")) or _normalize_text(raw_value.get("rule_id"))
    rule_id = raw_rule_id or _slugify_rule_id(rule_name, f"rule-{index + 1}")
    sort_order_raw = _normalize_text(raw_value.get("sort_order")) or str(index)
    try:
        sort_order = int(sort_order_raw)
    except ValueError:
        sort_order = index
    return {
        "id": rule_id,
        "name": rule_name,
        "enabled": _normalize_bool(raw_value.get("enabled", True)),
        "archived": _normalize_bool(raw_value.get("archived", False)),
        "keywords": _normalize_text_list(raw_value.get("keywords")),
        "default_times": _normalize_time_list(raw_value.get("default_times") or raw_value.get("times")),
        "date_overrides": _normalize_date_overrides(raw_value.get("date_overrides")),
        "timezone": _normalize_text(raw_value.get("timezone")) or DEFAULT_TIMEZONE,
        "leaderboard_url": _normalize_leaderboard_url(raw_value.get("leaderboard_url")),
        "description": _normalize_text(raw_value.get("description")),
        "sort_order": sort_order,
    }


def _normalize_legacy_rule(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": DEFAULT_RULE_ID,
        "name": DEFAULT_RULE_NAME,
        "enabled": True,
        "archived": False,
        "keywords": _normalize_text_list(config.get("keywords")),
        "default_times": _normalize_time_list(config.get("times")),
        "date_overrides": [],
        "timezone": _normalize_text(config.get("timezone")) or DEFAULT_TIMEZONE,
        "leaderboard_url": _normalize_leaderboard_url(config.get("leaderboard_url")),
        "description": "",
        "sort_order": 0,
    }


def _build_legacy_global_config_from_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return {
        "trigger_keyword": _normalize_text(rule.get("trigger_keyword")) or "设置排行榜",
        "times": _normalize_time_list(rule.get("default_times")),
        "keywords": _normalize_text_list(rule.get("keywords")),
        "leaderboard_url": _normalize_leaderboard_url(rule.get("leaderboard_url")),
        "timezone": _normalize_text(rule.get("timezone")) or DEFAULT_TIMEZONE,
    }


def get_shared_order_leaderboard_config() -> dict:
    from config.config import ORDER_LEADERBOARD_CONFIG

    config = ORDER_LEADERBOARD_CONFIG.get(GLOBAL_CONFIG_KEY)
    if isinstance(config, dict):
        return dict(config)
    legacy_config = ORDER_LEADERBOARD_CONFIG.get(LEGACY_ACCOUNT_ID)
    if isinstance(legacy_config, dict):
        return dict(legacy_config)
    return {}


def get_shared_leaderboard_rules() -> list[dict[str, Any]]:
    from utils.system_settings_store import load_system_settings_store

    store = load_system_settings_store()
    raw_rules = store.get("leaderboard_rules")
    rules: list[dict[str, Any]] = []
    if isinstance(raw_rules, list):
        for index, item in enumerate(raw_rules):
            rule = _normalize_leaderboard_rule(item, index=index)
            if not rule["archived"] and (not rule["keywords"] or not rule["default_times"]):
                continue
            rules.append(rule)
    if rules:
        rules.sort(key=lambda item: (int(item.get("sort_order") or 0), str(item.get("name") or "")))
        return rules

    legacy = get_shared_order_leaderboard_config()
    if legacy:
        legacy_rule = _normalize_legacy_rule(legacy)
        if legacy_rule["keywords"] and legacy_rule["default_times"]:
            return [legacy_rule]
    return []


def get_effective_rule_times(rule: dict[str, Any], record_date: str | None = None) -> list[str]:
    normalized_date = _normalize_date_text(record_date)
    if normalized_date:
        for override in _normalize_date_overrides(rule.get("date_overrides")):
            if _normalize_text(override.get("date")) == normalized_date:
                return _normalize_time_list(override.get("times"))
    return _normalize_time_list(rule.get("default_times"))


def get_rule_by_id(rule_id: str | None) -> dict[str, Any]:
    normalized_rule_id = _normalize_text(rule_id)
    if normalized_rule_id:
        for item in get_shared_leaderboard_rules():
            if _normalize_text(item.get("id")) == normalized_rule_id:
                return dict(item)
    return {}


def get_active_shared_leaderboard_rules() -> list[dict[str, Any]]:
    return [
        rule for rule in get_shared_leaderboard_rules()
        if bool(rule.get("enabled")) and not bool(rule.get("archived"))
    ]


def get_primary_shared_leaderboard_rule() -> dict[str, Any]:
    rules = get_active_shared_leaderboard_rules()
    if rules:
        return dict(rules[0])
    rules = get_shared_leaderboard_rules()
    if rules:
        return dict(rules[0])
    return {}


def resolve_primary_leaderboard_url() -> str:
    return resolve_rule_leaderboard_url(get_primary_shared_leaderboard_rule())


def get_global_leaderboard_config() -> dict[str, Any]:
    from config.config import GLOBAL_LEADERBOARD_CONFIG

    config = GLOBAL_LEADERBOARD_CONFIG
    if not isinstance(config, dict):
        return {"enabled": False, "leaderboard_url": ""}
    return {
        "enabled": _normalize_bool(config.get("enabled")),
        "leaderboard_url": _normalize_leaderboard_url(config.get("leaderboard_url")),
    }


def get_global_leaderboard_url() -> str:
    config = get_global_leaderboard_config()
    if not config.get("enabled"):
        return ""
    # The global setting remains the delivery switch. V2 only chooses the
    # destination for newly generated links, keeping the stored legacy URL
    # available for an instant rollback when the V2 public switch is off.
    try:
        from utils.order_rankings_v2 import get_ranking_v2_config

        if get_ranking_v2_config().get("public_enabled"):
            return _join_url(resolve_auto_leaderboard_base_url(), V2_PUBLIC_LEADERBOARD_PATH) or V2_PUBLIC_LEADERBOARD_PATH
    except Exception:
        # A ranking V2 configuration issue must never suppress an otherwise
        # working global leaderboard link.
        pass
    return config.get("leaderboard_url") or resolve_primary_leaderboard_url()


def _load_timezone(timezone_name: str):
    return get_timezone(_normalize_text(timezone_name) or DEFAULT_TIMEZONE)


def _parse_slot_datetime(base_dt: datetime, slot_time: str):
    parts = _normalize_text(slot_time).split(":", 1)
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return None
    return base_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _match_rule(rule: dict[str, Any], *, poi_name: str, accept_timestamp: int) -> Optional[dict[str, Any]]:
    normalized_poi_name = _normalize_text(poi_name)
    if not normalized_poi_name:
        return None

    keywords = [item for item in _normalize_text_list(rule.get("keywords")) if item]
    if not keywords:
        return None
    matched_keyword = next((keyword for keyword in keywords if keyword in normalized_poi_name), "")
    if not matched_keyword:
        return None

    timezone_name = _normalize_text(rule.get("timezone")) or DEFAULT_TIMEZONE
    timezone = _load_timezone(timezone_name)
    accept_dt = datetime.fromtimestamp(int(accept_timestamp), timezone)

    candidates: list[tuple[datetime, str]] = []
    for day_offset in (-1, 0, 1):
        base_dt = accept_dt + timedelta(days=day_offset)
        active_times = get_effective_rule_times(rule, base_dt.date().isoformat())
        for slot_time in active_times:
            slot_dt = _parse_slot_datetime(base_dt, slot_time)
            if slot_dt is not None:
                candidates.append((slot_dt, slot_time))
    candidates.sort(key=lambda item: item[0], reverse=True)

    for slot_dt, slot_time in candidates:
        slot_end = slot_dt + timedelta(minutes=30)
        if slot_dt <= accept_dt < slot_end:
            return {
                "rule_id": _normalize_text(rule.get("id")) or DEFAULT_RULE_ID,
                "rule_name": _normalize_text(rule.get("name")) or DEFAULT_RULE_NAME,
                "keyword": matched_keyword,
                "slot_time": slot_time,
                "record_date": slot_dt.date().isoformat(),
                "accept_timestamp": int(accept_timestamp),
                "timezone": timezone_name,
                "leaderboard_url": resolve_rule_leaderboard_url(rule),
            }
    return None


class LocalOrderLeaderboardStore:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = os.fspath(resolve_runtime_data_path("order_leaderboard.db"))
        elif not os.path.isabs(db_path):
            db_path = os.fspath(resolve_runtime_data_path(db_path))
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_database()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_database(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS leaderboard_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT NOT NULL,
                    rule_name TEXT NOT NULL,
                    canonical_order_key TEXT NOT NULL,
                    service_order_id TEXT,
                    order_id TEXT,
                    poi_name TEXT NOT NULL,
                    matched_keyword TEXT NOT NULL,
                    slot_time TEXT NOT NULL,
                    record_date TEXT NOT NULL,
                    accept_timestamp INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    source_user_id TEXT,
                    source_to_user_name TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            self._ensure_entry_schema(cursor)
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_leaderboard_entries_query
                ON leaderboard_entries(rule_id, record_date, slot_time, accept_timestamp, id)
                """
            )
            self._dedupe_existing_entries(cursor)
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_leaderboard_entries_unique
                ON leaderboard_entries(rule_id, canonical_order_key)
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_leaderboard_entries_rule_service_order
                ON leaderboard_entries(rule_id, service_order_id)
                WHERE service_order_id IS NOT NULL AND TRIM(service_order_id) != ''
                """
            )
            cursor.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_leaderboard_entries_rule_order
                ON leaderboard_entries(rule_id, order_id)
                WHERE order_id IS NOT NULL AND TRIM(order_id) != ''
                """
            )
            conn.commit()

    def _ensure_entry_schema(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(leaderboard_entries)")
        existing_columns = {str(row["name"] or "") for row in cursor.fetchall()}
        expected_columns = {
            "rule_id": "TEXT NOT NULL DEFAULT 'default'",
            "rule_name": "TEXT NOT NULL DEFAULT '默认排行榜'",
            "canonical_order_key": "TEXT NOT NULL DEFAULT ''",
            "service_order_id": "TEXT",
            "order_id": "TEXT",
            "poi_name": "TEXT NOT NULL DEFAULT '未知商家'",
            "matched_keyword": "TEXT NOT NULL DEFAULT ''",
            "slot_time": "TEXT NOT NULL DEFAULT ''",
            "record_date": "TEXT NOT NULL DEFAULT ''",
            "accept_timestamp": "INTEGER NOT NULL DEFAULT 0",
            "source": "TEXT NOT NULL DEFAULT 'unknown'",
            "source_user_id": "TEXT",
            "source_to_user_name": "TEXT",
            "created_at": "INTEGER NOT NULL DEFAULT 0",
            "updated_at": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_definition in expected_columns.items():
            if column_name not in existing_columns:
                cursor.execute(f"ALTER TABLE leaderboard_entries ADD COLUMN {column_name} {column_definition}")
        cursor.execute(
            """
            UPDATE leaderboard_entries
            SET canonical_order_key = CASE
                    WHEN TRIM(IFNULL(canonical_order_key, '')) != '' THEN canonical_order_key
                    WHEN TRIM(IFNULL(service_order_id, '')) != '' THEN 'service:' || TRIM(service_order_id)
                    WHEN TRIM(IFNULL(order_id, '')) != '' THEN 'order:' || TRIM(order_id)
                    ELSE 'legacy:' || id
                END,
                rule_id = CASE WHEN TRIM(IFNULL(rule_id, '')) = '' THEN ? ELSE rule_id END,
                rule_name = CASE WHEN TRIM(IFNULL(rule_name, '')) = '' THEN ? ELSE rule_name END,
                poi_name = CASE WHEN TRIM(IFNULL(poi_name, '')) = '' THEN ? ELSE poi_name END,
                source = CASE WHEN TRIM(IFNULL(source, '')) = '' THEN ? ELSE source END,
                created_at = CASE WHEN IFNULL(created_at, 0) <= 0 THEN CAST(strftime('%s','now') AS INTEGER) ELSE created_at END,
                updated_at = CASE WHEN IFNULL(updated_at, 0) <= 0 THEN CAST(strftime('%s','now') AS INTEGER) ELSE updated_at END
            """
            ,
            (DEFAULT_RULE_ID, DEFAULT_RULE_NAME, "未知商家", "unknown"),
        )

    def _dedupe_existing_entries(self, cursor: sqlite3.Cursor) -> None:
        while True:
            merged_any = False
            for field_name in ("canonical_order_key", "service_order_id", "order_id"):
                cursor.execute(
                    f"""
                    SELECT rule_id, {field_name}
                    FROM leaderboard_entries
                    WHERE {field_name} IS NOT NULL AND TRIM({field_name}) != ''
                    GROUP BY rule_id, {field_name}
                    HAVING COUNT(1) > 1
                    LIMIT 1
                    """
                )
                duplicate_group = cursor.fetchone()
                if duplicate_group is None:
                    continue
                self._merge_duplicate_group(
                    cursor,
                    rule_id=str(duplicate_group["rule_id"] or ""),
                    field_name=field_name,
                    field_value=str(duplicate_group[field_name] or ""),
                )
                merged_any = True
                break
            if not merged_any:
                break

    def _merge_duplicate_group(
        self,
        cursor: sqlite3.Cursor,
        *,
        rule_id: str,
        field_name: str,
        field_value: str,
    ) -> None:
        cursor.execute(
            f"""
            SELECT *
            FROM leaderboard_entries
            WHERE rule_id = ? AND {field_name} = ?
            ORDER BY accept_timestamp ASC, id ASC
            """,
            (rule_id, field_value),
        )
        rows = cursor.fetchall()
        if len(rows) <= 1:
            return
        keep = rows[0]
        duplicate_ids = [int(row["id"]) for row in rows[1:]]
        service_order_id = str(keep["service_order_id"] or "")
        order_id = str(keep["order_id"] or "")
        source_user_id = str(keep["source_user_id"] or "")
        source_to_user_name = str(keep["source_to_user_name"] or "")
        source = str(keep["source"] or "")
        updated_at = int(keep["updated_at"] or 0)
        for row in rows[1:]:
            service_order_id = service_order_id or str(row["service_order_id"] or "")
            order_id = order_id or str(row["order_id"] or "")
            source_user_id = source_user_id or str(row["source_user_id"] or "")
            source_to_user_name = source_to_user_name or str(row["source_to_user_name"] or "")
            source = source or str(row["source"] or "")
            updated_at = max(updated_at, int(row["updated_at"] or 0))
        canonical_order_key = _canonical_order_key(service_order_id, order_id)
        if duplicate_ids:
            cursor.executemany(
                "DELETE FROM leaderboard_entries WHERE id = ?",
                [(duplicate_id,) for duplicate_id in duplicate_ids],
            )
        cursor.execute(
            """
            UPDATE leaderboard_entries
            SET canonical_order_key = ?,
                service_order_id = ?,
                order_id = ?,
                source = ?,
                source_user_id = ?,
                source_to_user_name = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                canonical_order_key,
                service_order_id or None,
                order_id or None,
                source or "unknown",
                source_user_id or None,
                source_to_user_name or None,
                updated_at or int(time.time()),
                int(keep["id"]),
            ),
        )

    def record_hit(
        self,
        *,
        rule_match: dict[str, Any],
        source: str,
        poi_name: str,
        service_order_id: str = "",
        order_id: str = "",
        source_user_id: str = "",
        source_to_user_name: str = "",
    ) -> dict[str, Any]:
        normalized_service_order_id = _normalize_text(service_order_id)
        normalized_order_id = _normalize_text(order_id)
        canonical_order_key = _canonical_order_key(normalized_service_order_id, normalized_order_id)
        if not canonical_order_key:
            raise ValueError("missing order key")

        now_ts = int(time.time())
        payload = {
            "rule_id": _normalize_text(rule_match.get("rule_id")) or DEFAULT_RULE_ID,
            "rule_name": _normalize_text(rule_match.get("rule_name")) or DEFAULT_RULE_NAME,
            "canonical_order_key": canonical_order_key,
            "service_order_id": normalized_service_order_id,
            "order_id": normalized_order_id,
            "poi_name": _normalize_text(poi_name) or "未知商家",
            "matched_keyword": _normalize_text(rule_match.get("keyword")),
            "slot_time": _normalize_text(rule_match.get("slot_time")),
            "record_date": _normalize_text(rule_match.get("record_date")),
            "accept_timestamp": int(rule_match.get("accept_timestamp") or 0),
            "source": _normalize_text(source) or "unknown",
            "source_user_id": _normalize_text(source_user_id),
            "source_to_user_name": _normalize_text(source_to_user_name),
            "created_at": now_ts,
            "updated_at": now_ts,
        }

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                existing_rows = self._find_existing_entries(
                    cursor,
                    rule_id=payload["rule_id"],
                    canonical_order_key=payload["canonical_order_key"],
                    service_order_id=payload["service_order_id"],
                    order_id=payload["order_id"],
                )
                if len(existing_rows) > 1:
                    existing = self._merge_matching_entries(cursor, existing_rows, payload, now_ts)
                    conn.commit()
                    return {
                        "recorded": False,
                        "duplicate": True,
                        "match": self._row_to_match(existing),
                    }
                existing = existing_rows[0] if existing_rows else None
                if existing:
                    self._update_existing_entry(cursor, existing, payload, now_ts)
                    conn.commit()
                    refreshed_rows = self._find_existing_entries(
                        cursor,
                        rule_id=payload["rule_id"],
                        canonical_order_key=payload["canonical_order_key"],
                        service_order_id=payload["service_order_id"],
                        order_id=payload["order_id"],
                    )
                    refreshed = refreshed_rows[0] if refreshed_rows else existing
                    return {
                        "recorded": False,
                        "duplicate": True,
                        "match": self._row_to_match(refreshed),
                    }

                cursor.execute(
                    """
                    INSERT INTO leaderboard_entries (
                        rule_id,
                        rule_name,
                        canonical_order_key,
                        service_order_id,
                        order_id,
                        poi_name,
                        matched_keyword,
                        slot_time,
                        record_date,
                        accept_timestamp,
                        source,
                        source_user_id,
                        source_to_user_name,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["rule_id"],
                        payload["rule_name"],
                        payload["canonical_order_key"],
                        payload["service_order_id"] or None,
                        payload["order_id"] or None,
                        payload["poi_name"],
                        payload["matched_keyword"],
                        payload["slot_time"],
                        payload["record_date"],
                        payload["accept_timestamp"],
                        payload["source"],
                        payload["source_user_id"] or None,
                        payload["source_to_user_name"] or None,
                        payload["created_at"],
                        payload["updated_at"],
                    ),
                )
                entry_id = int(cursor.lastrowid or 0)
                conn.commit()
                payload["id"] = entry_id
                return {
                    "recorded": True,
                    "duplicate": False,
                    "match": self._entry_to_match(payload),
                }

    def _find_existing_entries(
        self,
        cursor: sqlite3.Cursor,
        *,
        rule_id: str,
        canonical_order_key: str,
        service_order_id: str,
        order_id: str,
    ) -> list[sqlite3.Row]:
        clauses = ["canonical_order_key = ?"]
        params: list[Any] = [rule_id, canonical_order_key]
        if service_order_id:
            clauses.append("service_order_id = ?")
            params.append(service_order_id)
        if order_id:
            clauses.append("order_id = ?")
            params.append(order_id)
        cursor.execute(
            f"""
            SELECT *
            FROM leaderboard_entries
            WHERE rule_id = ? AND ({" OR ".join(clauses)})
            ORDER BY accept_timestamp ASC, id ASC
            """,
            params,
        )
        return cursor.fetchall()

    def _merge_matching_entries(
        self,
        cursor: sqlite3.Cursor,
        rows: list[sqlite3.Row],
        payload: dict[str, Any],
        now_ts: int,
    ) -> sqlite3.Row:
        ordered_rows = sorted(rows, key=lambda row: (int(row["accept_timestamp"] or 0), int(row["id"] or 0)))
        keep = ordered_rows[0]
        service_order_id = str(payload["service_order_id"] or "")
        order_id = str(payload["order_id"] or "")
        source = str(payload["source"] or "")
        source_user_id = str(payload["source_user_id"] or "")
        source_to_user_name = str(payload["source_to_user_name"] or "")
        updated_at = now_ts
        for row in ordered_rows:
            service_order_id = service_order_id or str(row["service_order_id"] or "")
            order_id = order_id or str(row["order_id"] or "")
            source = source or str(row["source"] or "")
            source_user_id = source_user_id or str(row["source_user_id"] or "")
            source_to_user_name = source_to_user_name or str(row["source_to_user_name"] or "")
            updated_at = max(updated_at, int(row["updated_at"] or 0))
        canonical_order_key = _canonical_order_key(service_order_id, order_id)
        duplicate_ids = [int(row["id"]) for row in ordered_rows[1:]]
        if duplicate_ids:
            cursor.executemany(
                "DELETE FROM leaderboard_entries WHERE id = ?",
                [(duplicate_id,) for duplicate_id in duplicate_ids],
            )
        cursor.execute(
            """
            UPDATE leaderboard_entries
            SET canonical_order_key = ?,
                service_order_id = ?,
                order_id = ?,
                source = ?,
                source_user_id = ?,
                source_to_user_name = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                canonical_order_key,
                service_order_id or None,
                order_id or None,
                source or "unknown",
                source_user_id or None,
                source_to_user_name or None,
                updated_at,
                int(keep["id"]),
            ),
        )
        cursor.execute("SELECT * FROM leaderboard_entries WHERE id = ?", (int(keep["id"]),))
        refreshed = cursor.fetchone()
        return refreshed or keep

    def _update_existing_entry(
        self,
        cursor: sqlite3.Cursor,
        existing: sqlite3.Row,
        payload: dict[str, Any],
        now_ts: int,
    ) -> None:
        service_order_id = str(existing["service_order_id"] or "") or str(payload["service_order_id"] or "")
        order_id = str(existing["order_id"] or "") or str(payload["order_id"] or "")
        canonical_order_key = _canonical_order_key(service_order_id, order_id) or str(existing["canonical_order_key"] or "")
        cursor.execute(
            """
            UPDATE leaderboard_entries
            SET canonical_order_key = ?,
                service_order_id = ?,
                order_id = ?,
                source = CASE WHEN TRIM(IFNULL(source, '')) = '' THEN ? ELSE source END,
                source_user_id = CASE WHEN TRIM(IFNULL(source_user_id, '')) = '' THEN ? ELSE source_user_id END,
                source_to_user_name = CASE WHEN TRIM(IFNULL(source_to_user_name, '')) = '' THEN ? ELSE source_to_user_name END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                canonical_order_key,
                service_order_id or None,
                order_id or None,
                payload["source"],
                payload["source_user_id"],
                payload["source_to_user_name"],
                now_ts,
                int(existing["id"]),
            ),
        )

    def _entry_to_match(self, payload: dict[str, Any]) -> dict[str, Any]:
        match = {
            "rule_id": payload["rule_id"],
            "rule_name": payload["rule_name"],
            "keyword": payload["matched_keyword"],
            "slot_time": payload["slot_time"],
            "record_date": payload["record_date"],
            "accept_timestamp": payload["accept_timestamp"],
            "service_order_id": payload["service_order_id"],
            "order_id": payload["order_id"],
        }
        rule = get_rule_by_id(payload["rule_id"])
        match["leaderboard_url"] = resolve_rule_leaderboard_url(rule)
        return match

    def _row_to_match(self, row: sqlite3.Row) -> dict[str, Any]:
        match = {
            "rule_id": str(row["rule_id"] or ""),
            "rule_name": str(row["rule_name"] or ""),
            "keyword": str(row["matched_keyword"] or ""),
            "slot_time": str(row["slot_time"] or ""),
            "record_date": str(row["record_date"] or ""),
            "accept_timestamp": int(row["accept_timestamp"] or 0),
            "service_order_id": str(row["service_order_id"] or ""),
            "order_id": str(row["order_id"] or ""),
        }
        rule = get_rule_by_id(match["rule_id"])
        match["leaderboard_url"] = resolve_rule_leaderboard_url(rule)
        return match

    def query_rankings(
        self,
        *,
        rule_id: str,
        keyword: str = "",
        record_date: str,
        slot_time: str,
        page_size: int = 50,
        cursor: str = "",
        accept_time: Optional[int] = None,
        service_order_id: str = "",
        order_id: str = "",
    ) -> dict[str, Any]:
        normalized_rule_id = _normalize_text(rule_id)
        normalized_keyword = _normalize_text(keyword)
        normalized_record_date = _normalize_text(record_date)
        normalized_slot_time = _normalize_text(slot_time)
        limit = max(1, min(int(page_size or 50), 200))
        cursor_accept_timestamp, cursor_id = self._parse_query_cursor(cursor)
        where_keyword = " AND matched_keyword = ? " if normalized_keyword else ""
        count_params: list[Any] = [normalized_rule_id, normalized_record_date, normalized_slot_time]
        if normalized_keyword:
            count_params.append(normalized_keyword)

        rows: list[sqlite3.Row]
        total_count = 0
        with self._lock:
            with self._get_connection() as conn:
                cursor_obj = conn.cursor()
                cursor_obj.execute(
                    f"""
                    SELECT COUNT(1)
                    FROM leaderboard_entries
                    WHERE rule_id = ? AND record_date = ? AND slot_time = ? {where_keyword}
                    """,
                    count_params,
                )
                total_count = int((cursor_obj.fetchone() or [0])[0] or 0)
                query_params: list[Any] = [normalized_rule_id, normalized_record_date, normalized_slot_time]
                if normalized_keyword:
                    query_params.append(normalized_keyword)
                query_params.extend([cursor_accept_timestamp, cursor_accept_timestamp, cursor_id, limit + 1])
                cursor_obj.execute(
                    f"""
                    SELECT *
                    FROM leaderboard_entries
                    WHERE rule_id = ? AND record_date = ? AND slot_time = ? {where_keyword}
                      AND (
                        accept_timestamp > ?
                        OR (accept_timestamp = ? AND id > ?)
                      )
                    ORDER BY accept_timestamp ASC, id ASC
                    LIMIT ?
                    """,
                    query_params,
                )
                rows = cursor_obj.fetchall()

        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        next_cursor = self._build_query_cursor(visible_rows[-1]) if has_more and visible_rows else ""

        rankings: list[dict[str, Any]] = []
        user_rank: Optional[int] = None
        normalized_accept_time = normalize_timestamp_seconds(accept_time)
        normalized_service_order_id = _normalize_text(service_order_id)
        normalized_order_id = _normalize_text(order_id)
        for index, row in enumerate(visible_rows, start=1):
            rank = self._resolve_row_rank(row, normalized_rule_id, normalized_keyword, normalized_record_date, normalized_slot_time)
            if user_rank is None and self._row_matches_user_target(
                row,
                accept_timestamp=normalized_accept_time,
                service_order_id=normalized_service_order_id,
                order_id=normalized_order_id,
            ):
                user_rank = rank
            rankings.append({
                "id": int(row["id"] or 0),
                "rank": rank,
                "accept_time": self._format_accept_time(int(row["accept_timestamp"] or 0), str(row["rule_id"] or "")),
                "accept_timestamp": int(row["accept_timestamp"] or 0),
                "order_id": str(row["order_id"] or ""),
                "service_order_id": str(row["service_order_id"] or ""),
                "poi_name": str(row["poi_name"] or ""),
                "keyword": str(row["matched_keyword"] or ""),
                "rule_id": str(row["rule_id"] or ""),
                "rule_name": str(row["rule_name"] or ""),
            })
        if user_rank is None and (normalized_accept_time is not None or normalized_service_order_id or normalized_order_id):
            user_rank = self._resolve_user_rank(
                normalized_rule_id,
                normalized_keyword,
                normalized_record_date,
                normalized_slot_time,
                normalized_accept_time,
                service_order_id=normalized_service_order_id,
                order_id=normalized_order_id,
            )
        return {
            "rankings": rankings,
            "user_rank": user_rank,
            "total": total_count,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    def _parse_query_cursor(self, cursor: str) -> tuple[int, int]:
        normalized = _normalize_text(cursor)
        if not normalized:
            return -1, 0
        if ":" in normalized:
            left, right = normalized.split(":", 1)
            try:
                return max(-1, int(left)), max(0, int(right))
            except ValueError:
                return -1, 0
        try:
            legacy_id = max(0, int(normalized))
        except ValueError:
            return -1, 0
        if legacy_id <= 0:
            return -1, 0
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT accept_timestamp, id FROM leaderboard_entries WHERE id = ?",
                (legacy_id,),
            ).fetchone()
            if row:
                return int(row["accept_timestamp"] or -1), int(row["id"] or 0)
        return -1, legacy_id

    def _build_query_cursor(self, row: sqlite3.Row) -> str:
        return f"{int(row['accept_timestamp'] or 0)}:{int(row['id'] or 0)}"

    def _row_matches_user_target(
        self,
        row: sqlite3.Row,
        *,
        accept_timestamp: int | None,
        service_order_id: str,
        order_id: str,
    ) -> bool:
        if service_order_id and str(row["service_order_id"] or "") == service_order_id:
            return True
        if order_id and str(row["order_id"] or "") == order_id:
            return True
        return bool(
            accept_timestamp is not None
            and not service_order_id
            and not order_id
            and int(row["accept_timestamp"] or 0) == int(accept_timestamp)
        )

    def _resolve_row_rank(self, row: sqlite3.Row, rule_id: str, keyword: str, record_date: str, slot_time: str) -> int:
        target_accept = int(row["accept_timestamp"] or 0)
        target_id = int(row["id"] or 0)
        where_keyword = " AND matched_keyword = ? " if keyword else ""
        params: list[Any] = [rule_id, record_date, slot_time]
        if keyword:
            params.append(keyword)
        params.extend([target_accept, target_accept, target_id])
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT COUNT(1)
                FROM leaderboard_entries
                WHERE rule_id = ?
                  AND record_date = ?
                  AND slot_time = ?
                  {where_keyword}
                  AND (
                    accept_timestamp < ?
                    OR (accept_timestamp = ? AND id <= ?)
                  )
                """,
                params,
            )
            return int((cursor.fetchone() or [0])[0] or 0)

    def _resolve_user_rank(
        self,
        rule_id: str,
        keyword: str,
        record_date: str,
        slot_time: str,
        accept_timestamp: int | None,
        *,
        service_order_id: str = "",
        order_id: str = "",
    ) -> Optional[int]:
        where_keyword = " AND matched_keyword = ? " if keyword else ""
        select_params: list[Any] = [rule_id, record_date, slot_time]
        if keyword:
            select_params.append(keyword)
        target_clauses: list[str] = []
        if service_order_id:
            target_clauses.append("service_order_id = ?")
            select_params.append(service_order_id)
        if order_id:
            target_clauses.append("order_id = ?")
            select_params.append(order_id)
        if not target_clauses and accept_timestamp is not None:
            target_clauses.append("accept_timestamp = ?")
            select_params.append(int(accept_timestamp))
        if not target_clauses:
            return None
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT id AS row_id
                FROM leaderboard_entries
                WHERE rule_id = ? AND record_date = ? AND slot_time = ? {where_keyword}
                  AND ({" OR ".join(target_clauses)})
                ORDER BY accept_timestamp ASC, id ASC
                LIMIT 1
                """,
                select_params,
            )
            row = cursor.fetchone()
            if row is None or row["row_id"] is None:
                return None
            cursor.execute(
                "SELECT accept_timestamp FROM leaderboard_entries WHERE id = ?",
                (int(row["row_id"]),),
            )
            target_row = cursor.fetchone()
            if target_row is None:
                return None
            target_accept = int(target_row["accept_timestamp"] or 0)
            rank_params: list[Any] = [rule_id, record_date, slot_time]
            if keyword:
                rank_params.append(keyword)
            rank_params.extend([target_accept, target_accept, int(row["row_id"])])
            cursor.execute(
                f"""
                SELECT COUNT(1)
                FROM leaderboard_entries
                WHERE rule_id = ?
                  AND record_date = ?
                  AND slot_time = ?
                  {where_keyword}
                  AND (
                    accept_timestamp < ?
                    OR (accept_timestamp = ? AND id <= ?)
                  )
                """,
                rank_params,
            )
            return int((cursor.fetchone() or [0])[0] or 0)

    def _format_accept_time(self, accept_timestamp: int, rule_id: str) -> str:
        rule = next((item for item in get_shared_leaderboard_rules() if str(item.get("id") or "") == rule_id), None)
        timezone_name = _normalize_text((rule or {}).get("timezone")) or DEFAULT_TIMEZONE
        timezone = _load_timezone(timezone_name)
        return datetime.fromtimestamp(int(accept_timestamp), timezone).strftime("%Y-%m-%d %H:%M:%S")


_shared_store: Optional[LocalOrderLeaderboardStore] = None


def get_local_order_leaderboard_store() -> LocalOrderLeaderboardStore:
    global _shared_store
    if _shared_store is None:
        _shared_store = LocalOrderLeaderboardStore()
    return _shared_store


def record_leaderboard_hit(
    *,
    source: str,
    poi_name: str,
    accept_timestamp: int,
    service_order_id: str = "",
    order_id: str = "",
    source_user_id: str = "",
    source_to_user_name: str = "",
) -> Optional[dict[str, Any]]:
    for rule in get_active_shared_leaderboard_rules():
        match = _match_rule(rule, poi_name=poi_name, accept_timestamp=int(accept_timestamp))
        if match is None:
            continue
        result = get_local_order_leaderboard_store().record_hit(
            rule_match=match,
            source=source,
            poi_name=poi_name,
            service_order_id=service_order_id,
            order_id=order_id,
            source_user_id=source_user_id,
            source_to_user_name=source_to_user_name,
        )
        return dict(result.get("match") or {})
    return None


def normalize_leaderboard_rules_for_runtime(raw_rules: Any) -> list[dict[str, Any]]:
    normalized_rules: list[dict[str, Any]] = []
    if isinstance(raw_rules, list):
        for index, item in enumerate(raw_rules):
            rule = _normalize_leaderboard_rule(item, index=index)
            if not rule["archived"] and (not rule["keywords"] or not rule["default_times"]):
                continue
            normalized_rules.append(rule)
    normalized_rules.sort(key=lambda item: (int(item.get("sort_order") or 0), str(item.get("name") or "")))
    return normalized_rules


def derive_legacy_order_leaderboard_config(rules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not rules:
        return {}
    primary_rule = dict(next((item for item in rules if not bool(item.get("archived"))), rules[0]))
    return {GLOBAL_CONFIG_KEY: _build_legacy_global_config_from_rule(primary_rule)}


async def arecord_leaderboard_hits(
    logger,
    results: Iterable[Dict[str, Any]],
    to_user_name: str,
    user_id: str,
    account_config: Optional[dict] = None,
    ingest_timeout_seconds: float = 0.8,
    deadline_at: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    del account_config, ingest_timeout_seconds, deadline_at
    from utils.order_query_background import run_blocking_order_query_work

    return await run_blocking_order_query_work(
        "wechat_leaderboard",
        lambda: _record_leaderboard_hits_sync(logger, results, to_user_name, user_id),
    )


def _record_leaderboard_hits_sync(
    logger,
    results: Iterable[Dict[str, Any]],
    to_user_name: str,
    user_id: str,
) -> Optional[Dict[str, Any]]:
    matches: list[Dict[str, Any]] = []
    for index, item in enumerate(list(results), start=1):
        if not isinstance(item, dict):
            continue
        if item.get("error"):
            continue
        poi_name = _normalize_text(item.get("poi_name"))
        accept_timestamp = normalize_timestamp_seconds(item.get("acceptTime", item.get("accept_time")))
        if not poi_name or accept_timestamp is None:
            continue
        match = record_leaderboard_hit(
            source="wechat",
            poi_name=poi_name,
            accept_timestamp=int(accept_timestamp),
            service_order_id=_normalize_text(item.get("serviceOrderId", item.get("service_order_id"))),
            order_id=_normalize_text(item.get("orderId", item.get("order_id"))),
            source_user_id=user_id,
            source_to_user_name=to_user_name,
        )
        if match:
            logger.info("排行榜写入命中: index=%d rule=%s keyword=%s slot=%s", index, match.get("rule_id"), match.get("keyword"), match.get("slot_time"))
            matches.append(match)
    if not matches:
        return None
    matches.sort(key=lambda item: (int(item.get("accept_timestamp") or 0), str(item.get("rule_id") or "")))
    return matches[0]
