"""Two-source aggregated order-ranking collector and storage."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from utils.logger import setup_logger
from utils.path_utils import resolve_runtime_data_path
from utils.system_settings_store import load_system_settings_store, save_system_settings_store


logger = setup_logger(__name__)
TIMEZONE = ZoneInfo("Asia/Shanghai")
SOURCE1_BASE = "https://naiba666.com/md"
SOURCE2_URL = "https://mt.liliabc.fun/page/wm/md"
POLL_SECONDS = 5
WINDOW_SECONDS = 600
RETENTION_DAYS = 3
SOURCE1_TIMEOUT = 4.0
SOURCE2_TIMEOUT = 4.0
SOURCE1_CONCURRENCY = 8
SOURCE2_CONCURRENCY_CAP = 16
SOURCE1_SESSION_CHECK_SECONDS = 60
SOURCE1_RELAY_TIMEOUT = 12.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    raw = _text(value).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def normalize_ranking_v2_config(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    return {
        "collection_enabled": _bool(value.get("collection_enabled"), False),
        "public_enabled": _bool(value.get("public_enabled"), False),
        "rank_text_enabled": _bool(value.get("rank_text_enabled"), False),
        "source1_enabled": _bool(value.get("source1_enabled"), False),
        "source1_username": _text(value.get("source1_username")),
        "source1_password": _text(value.get("source1_password")),
        "source1_relay_url": _text(value.get("source1_relay_url")).rstrip("/"),
        "source1_relay_secret": _text(value.get("source1_relay_secret")),
        "poll_seconds": POLL_SECONDS,
        "window_seconds": WINDOW_SECONDS,
        "retention_days": RETENTION_DAYS,
        "announcement_enabled": _bool(value.get("announcement_enabled"), False),
        "announcement_title": _text(value.get("announcement_title")),
        "announcement_body": _text(value.get("announcement_body")),
        "announcement_image_url": _text(value.get("announcement_image_url")),
        "announcement_link_url": _text(value.get("announcement_link_url")),
    }


def get_ranking_v2_config() -> dict[str, Any]:
    return normalize_ranking_v2_config(load_system_settings_store().get("order_rankings_v2_config", {}))


def is_ranking_rank_text_enabled() -> bool:
    """Return the single switch shared by Web and WeChat order results."""
    return bool(get_ranking_v2_config().get("rank_text_enabled"))


def save_ranking_v2_config(payload: dict[str, Any]) -> dict[str, Any]:
    current = get_ranking_v2_config()
    value = payload if isinstance(payload, dict) else {}
    for key in ("collection_enabled", "public_enabled", "rank_text_enabled", "source1_enabled"):
        if key in value:
            current[key] = _bool(value.get(key))
    if "announcement_enabled" in value:
        current["announcement_enabled"] = _bool(value.get("announcement_enabled"))
    for key in ("announcement_title", "announcement_body", "announcement_image_url", "announcement_link_url"):
        if key in value:
            current[key] = _text(value.get(key))[:2000]
    # The administrator API deliberately never returns the source account.
    # Therefore an empty form field means "keep", not "erase".
    if _bool(value.get("clear_source1_credentials")):
        current["source1_username"] = ""
        current["source1_password"] = ""
    else:
        if _text(value.get("source1_username")):
            current["source1_username"] = _text(value.get("source1_username"))
        if _bool(value.get("clear_source1_password")):
            current["source1_password"] = ""
        elif _text(value.get("source1_password")):
            current["source1_password"] = _text(value.get("source1_password"))
    if _bool(value.get("clear_source1_relay_secret")):
        current["source1_relay_secret"] = ""
    elif _text(value.get("source1_relay_secret")):
        current["source1_relay_secret"] = _text(value.get("source1_relay_secret"))
    if _bool(value.get("clear_source1_relay_url")):
        current["source1_relay_url"] = ""
    elif _text(value.get("source1_relay_url")):
        candidate = _text(value.get("source1_relay_url")).rstrip("/")
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("来源1 Relay 地址必须是完整 HTTP/HTTPS 地址")
        current["source1_relay_url"] = candidate
    store = load_system_settings_store()
    store["order_rankings_v2_config"] = current
    save_system_settings_store(store)
    return current


def serialize_ranking_v2_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    value = normalize_ranking_v2_config(config if config is not None else get_ranking_v2_config())
    return {
        "collection_enabled": value["collection_enabled"],
        "public_enabled": value["public_enabled"],
        "rank_text_enabled": value["rank_text_enabled"],
        "source1_enabled": value["source1_enabled"],
        "source1_username": value["source1_username"][:2] + "***" if value["source1_username"] else "",
        "source1_username_configured": bool(value["source1_username"]),
        "source1_password_configured": bool(value["source1_password"]),
        "source1_relay_url": value["source1_relay_url"],
        "source1_relay_configured": bool(value["source1_relay_url"]),
        "source1_relay_secret_configured": bool(value["source1_relay_secret"]),
        "announcement_enabled": value["announcement_enabled"],
        "announcement_title": value["announcement_title"],
        "announcement_body": value["announcement_body"],
        "announcement_image_url": value["announcement_image_url"],
        "announcement_link_url": value["announcement_link_url"],
        "poll_seconds": POLL_SECONDS,
        "window_seconds": WINDOW_SECONDS,
        "retention_days": RETENTION_DAYS,
    }


def normalize_merchant_name(value: Any) -> str:
    raw = unicodedata.normalize("NFKC", _text(value)).lower()
    return re.sub(r"[\s\-_.，,。！!？?、:：;；()（）\[\]【】{}]+", "", raw)


def _hash_payload(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class Source1RankingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.brand = ""
        self.slot_time = ""
        self.date = ""
        self.participant_count = 0
        self.items: list[dict[str, Any]] = []
        self._stack: list[tuple[str, str]] = []
        self._capture = ""
        self._values: dict[str, list[str]] = {}
        self._item_depth = 0
        self._item_values: dict[str, list[str]] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = set((_text(dict(attrs).get("class"))).split())
        previous = self._capture
        label = ""
        if "rank-item" in classes:
            self._item_depth = len(self._stack) + 1
            self._item_values = {}
        if "brand" in classes:
            label = "brand"
        elif "time" in classes:
            label = "time"
        elif "date" in classes:
            label = "date"
        elif "num" in classes:
            label = "total"
        elif self._item_values is not None:
            if "sec" in classes:
                label = "sec"
            elif "cnt" in classes:
                label = "count"
            elif "pct" in classes:
                label = "percent"
            elif "cum-line" in classes:
                label = "cumulative"
        self._stack.append((tag, previous))
        if label:
            self._capture = label

    def handle_data(self, data: str) -> None:
        text = _text(data)
        if not text or not self._capture:
            return
        target = self._item_values if self._item_values is not None and self._capture in {"sec", "count", "percent", "cumulative"} else self._values
        if target is not None:
            target.setdefault(self._capture, []).append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._item_values is not None and len(self._stack) == self._item_depth and tag == "div":
            values = self._item_values
            second_match = re.search(r"(\d+)", " ".join(values.get("sec", [])))
            count_match = re.search(r"(\d+)", " ".join(values.get("count", [])))
            if second_match and count_match:
                self.items.append({"second": int(second_match.group(1)), "count": int(count_match.group(1))})
            self._item_values = None
            self._item_depth = 0
        if self._stack:
            _, previous = self._stack.pop()
            self._capture = previous

    def result(self) -> dict[str, Any]:
        def one(name: str) -> str:
            return " ".join(self._values.get(name, [])).strip()
        total_match = re.search(r"(\d+)", one("total"))
        buckets: dict[int, int] = {}
        for item in self.items:
            buckets[int(item["second"])] = int(item["count"])
        return {
            "brand": one("brand"),
            "slot_time": one("time"),
            "date": one("date"),
            "participant_count": int(total_match.group(1)) if total_match else sum(buckets.values()),
            "buckets": buckets,
        }


def parse_source1_ranking_html(html: str) -> dict[str, Any]:
    parser = Source1RankingParser()
    parser.feed(html)
    parser.close()
    result = parser.result()
    if not result["brand"] or not result["buckets"]:
        raise ValueError("来源1排行榜页面结构不完整或登录已失效")
    if result["participant_count"] and sum(result["buckets"].values()) != result["participant_count"]:
        raise ValueError("来源1参与人数与秒桶人数不一致")
    return result


def _extract_json_assignment(html: str, variable_name: str) -> Any:
    match = re.search(rf"\bvar\s+{re.escape(variable_name)}\s*=\s*", html)
    if not match:
        raise ValueError(f"未找到来源2 {variable_name}")
    start = match.end()
    while start < len(html) and html[start].isspace():
        start += 1
    if start >= len(html) or html[start] not in "[{":
        raise ValueError(f"来源2 {variable_name} 不是JSON对象")
    opening = html[start]
    closing = "}" if opening == "{" else "]"
    depth = 0
    quote = ""
    escaped = False
    for index in range(start, len(html)):
        char = html[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return json.loads(html[start:index + 1])
    raise ValueError(f"来源2 {variable_name} JSON不完整")


def parse_source2_shop_data(html: str, slot_start: datetime) -> dict[str, dict[int, int]]:
    raw = _extract_json_assignment(html, "shopData")
    if not isinstance(raw, dict):
        raise ValueError("来源2 shopData结构异常")
    output: dict[str, dict[int, int]] = {}
    start_seconds = int(slot_start.timestamp())
    for merchant, values in raw.items():
        if not isinstance(values, list):
            continue
        bucket = Counter()
        for value in values:
            try:
                offset = int(value) - start_seconds
            except (TypeError, ValueError):
                continue
            if offset >= 1:
                bucket[offset] += 1
        output[_text(merchant)] = dict(sorted(bucket.items()))
    return output


class OrderRankingsV2Storage:
    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = os.fspath(resolve_runtime_data_path("order_rankings_v2.db") if db_path is None else db_path)
        self._lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _conn(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS ranking_v2_sessions (
                    source TEXT PRIMARY KEY, cookies_json TEXT NOT NULL DEFAULT '', is_valid INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0, last_verified_at INTEGER NOT NULL DEFAULT 0,
                    last_login_at INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS ranking_v2_activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, record_date TEXT NOT NULL, merchant_name TEXT NOT NULL,
                    merchant_key TEXT NOT NULL, slot_time TEXT NOT NULL, source_activity_json TEXT NOT NULL DEFAULT '{}',
                    quantity_per_slot INTEGER NOT NULL DEFAULT 0, max_discount TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1, updated_at INTEGER NOT NULL,
                    UNIQUE(record_date, merchant_key, slot_time)
                );
                CREATE TABLE IF NOT EXISTS ranking_v2_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'running',
                    window_start_at INTEGER NOT NULL, window_end_at INTEGER NOT NULL, source1_last_poll_at INTEGER NOT NULL DEFAULT 0,
                    source2_last_poll_at INTEGER NOT NULL DEFAULT 0, source1_poll_count INTEGER NOT NULL DEFAULT 0,
                    source2_poll_count INTEGER NOT NULL DEFAULT 0, skipped_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    UNIQUE(activity_id, window_start_at)
                );
                CREATE TABLE IF NOT EXISTS ranking_v2_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, activity_id INTEGER NOT NULL,
                    payload_hash TEXT NOT NULL, payload_json TEXT NOT NULL, first_seen_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL, response_ms INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(source, activity_id, payload_hash)
                );
                CREATE TABLE IF NOT EXISTS ranking_v2_snapshot_buckets (
                    snapshot_id INTEGER NOT NULL, bucket_second INTEGER NOT NULL, bucket_count INTEGER NOT NULL,
                    PRIMARY KEY(snapshot_id, bucket_second)
                );
                CREATE TABLE IF NOT EXISTS ranking_v2_matches (
                    activity_id INTEGER PRIMARY KEY, source2_merchant_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', message TEXT NOT NULL DEFAULT '', updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ranking_v2_merged_buckets (
                    activity_id INTEGER NOT NULL, bucket_second INTEGER NOT NULL, source1_count INTEGER NOT NULL DEFAULT 0,
                    source2_count INTEGER NOT NULL DEFAULT 0, merged_count INTEGER NOT NULL DEFAULT 0,
                    cumulative_count INTEGER NOT NULL DEFAULT 0, ratio REAL NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL, PRIMARY KEY(activity_id, bucket_second)
                );
                CREATE INDEX IF NOT EXISTS idx_ranking_v2_activities_date ON ranking_v2_activities(record_date, slot_time);
                CREATE INDEX IF NOT EXISTS idx_ranking_v2_runs_status ON ranking_v2_runs(status, window_end_at);
                CREATE INDEX IF NOT EXISTS idx_ranking_v2_snapshots_latest ON ranking_v2_snapshots(source, activity_id, last_seen_at DESC);
            """)
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(ranking_v2_activities)").fetchall()}
            if "quantity_per_slot" not in columns:
                conn.execute("ALTER TABLE ranking_v2_activities ADD COLUMN quantity_per_slot INTEGER NOT NULL DEFAULT 0")
            if "max_discount" not in columns:
                conn.execute("ALTER TABLE ranking_v2_activities ADD COLUMN max_discount TEXT NOT NULL DEFAULT ''")
            # Existing V2 rows already retain the complete source-1 activity
            # payload. Populate the explicit display fields during upgrade.
            for row in conn.execute("SELECT id, source_activity_json, quantity_per_slot, max_discount FROM ranking_v2_activities").fetchall():
                if int(row["quantity_per_slot"] or 0) > 0 or _text(row["max_discount"]):
                    continue
                try:
                    payload = json.loads(str(row["source_activity_json"] or "{}"))
                    activity = payload.get("activity") if isinstance(payload, dict) else {}
                    activity = activity if isinstance(activity, dict) else {}
                    quantity = max(0, int(activity.get("quantity_per_slot") or 0))
                    discount = _text(activity.get("max_discount"))[:32]
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if quantity or discount:
                    conn.execute(
                        "UPDATE ranking_v2_activities SET quantity_per_slot=?, max_discount=? WHERE id=?",
                        (quantity, discount, int(row["id"])),
                    )

    def upsert_activities(self, items: list[dict[str, Any]]) -> int:
        now = int(time.time())
        count = 0
        with self._lock, self._conn() as conn:
            for item in items:
                merchant = _text(item.get("merchant_name"))
                date = _text(item.get("record_date"))
                slot = _text(item.get("slot_time"))
                if not merchant or not date or not slot:
                    continue
                source_activity = item.get("activity") if isinstance(item.get("activity"), dict) else {}
                quantity_raw = source_activity.get("quantity_per_slot", item.get("quantity_per_slot", 0))
                try:
                    quantity = max(0, int(quantity_raw or 0))
                except (TypeError, ValueError):
                    quantity = 0
                discount = _text(source_activity.get("max_discount", item.get("max_discount", "")))[:32]
                conn.execute("""
                    INSERT INTO ranking_v2_activities(record_date, merchant_name, merchant_key, slot_time, source_activity_json, quantity_per_slot, max_discount, enabled, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(record_date, merchant_key, slot_time) DO UPDATE SET
                        merchant_name=excluded.merchant_name, source_activity_json=excluded.source_activity_json,
                        quantity_per_slot=excluded.quantity_per_slot, max_discount=excluded.max_discount,
                        enabled=1, updated_at=excluded.updated_at
                """, (date, merchant, normalize_merchant_name(merchant), slot, json.dumps(item, ensure_ascii=False), quantity, discount, now))
                count += 1
        return count

    def list_activities(self, record_date: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ranking_v2_activities WHERE enabled=1"
        params: list[Any] = []
        if record_date:
            sql += " AND record_date=?"
            params.append(record_date)
        sql += " ORDER BY slot_time, merchant_name"
        with self._lock, self._conn() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]

    def get_activity(self, activity_id: int) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM ranking_v2_activities WHERE id=?", (activity_id,)).fetchone()
            return dict(row) if row else None

    def ensure_run(self, activity_id: int, start_at: int, end_at: int) -> dict[str, Any]:
        now = int(time.time())
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT INTO ranking_v2_runs(activity_id, status, window_start_at, window_end_at, created_at, updated_at)
                VALUES (?, 'running', ?, ?, ?, ?)
                ON CONFLICT(activity_id, window_start_at) DO UPDATE SET
                    status=CASE WHEN ranking_v2_runs.status='finished' AND ranking_v2_runs.window_end_at>? THEN 'running' ELSE ranking_v2_runs.status END,
                    window_end_at=MAX(ranking_v2_runs.window_end_at, excluded.window_end_at), updated_at=excluded.updated_at
            """, (activity_id, start_at, end_at, now, now, now))
            row = conn.execute("SELECT * FROM ranking_v2_runs WHERE activity_id=? AND window_start_at=?", (activity_id, start_at)).fetchone()
            return dict(row)

    def get_running_runs(self, now: int | None = None) -> list[dict[str, Any]]:
        current = int(now or time.time())
        with self._lock, self._conn() as conn:
            conn.execute("UPDATE ranking_v2_runs SET status='finished', updated_at=? WHERE status='running' AND window_end_at<=?", (current, current))
            rows = conn.execute("""
                SELECT r.*, a.record_date, a.merchant_name, a.merchant_key, a.slot_time
                FROM ranking_v2_runs r JOIN ranking_v2_activities a ON a.id=r.activity_id
                WHERE r.status='running' AND r.window_start_at<=? AND r.window_end_at>?
                ORDER BY a.slot_time, a.merchant_name
            """, (current, current)).fetchall()
            return [dict(row) for row in rows]

    def mark_poll(self, run_ids: list[int], source: str, *, success: bool, response_ms: int = 0, error: str = "", skipped: bool = False) -> None:
        if not run_ids:
            return
        now = int(time.time())
        field = "source1" if source == "source1" else "source2"
        with self._lock, self._conn() as conn:
            for run_id in run_ids:
                conn.execute(f"""
                    UPDATE ranking_v2_runs SET {field}_last_poll_at=?, {field}_poll_count={field}_poll_count+?,
                        skipped_count=skipped_count+?, last_error=?, updated_at=? WHERE id=?
                """, (now, 0 if skipped else 1, 1 if skipped else 0, _text(error)[:500], now, run_id))

    def latest_poll_at(self, run: dict[str, Any], source: str) -> int:
        return int(run.get("source1_last_poll_at" if source == "source1" else "source2_last_poll_at") or 0)

    def write_snapshot(self, source: str, activity_id: int, buckets: dict[int, int], metadata: dict[str, Any], response_ms: int) -> tuple[int, bool]:
        normalized = {str(int(second)): int(count) for second, count in sorted(buckets.items()) if int(second) >= 1 and int(count) >= 0}
        payload = {"metadata": metadata, "buckets": normalized}
        digest = _hash_payload(payload)
        now = int(time.time())
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT id FROM ranking_v2_snapshots WHERE source=? AND activity_id=? AND payload_hash=?", (source, activity_id, digest)).fetchone()
            if row:
                conn.execute("UPDATE ranking_v2_snapshots SET last_seen_at=?, response_ms=? WHERE id=?", (now, response_ms, int(row["id"])))
                return int(row["id"]), False
            cursor = conn.execute("""
                INSERT INTO ranking_v2_snapshots(source, activity_id, payload_hash, payload_json, first_seen_at, last_seen_at, response_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (source, activity_id, digest, json.dumps(payload, ensure_ascii=False), now, now, response_ms))
            snapshot_id = int(cursor.lastrowid)
            conn.executemany("INSERT INTO ranking_v2_snapshot_buckets(snapshot_id, bucket_second, bucket_count) VALUES (?, ?, ?)", [(snapshot_id, int(second), int(count)) for second, count in normalized.items()])
            return snapshot_id, True

    def set_match(self, activity_id: int, status: str, merchant_name: str = "", message: str = "") -> None:
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT INTO ranking_v2_matches(activity_id, source2_merchant_name, status, message, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(activity_id) DO UPDATE SET source2_merchant_name=excluded.source2_merchant_name,
                    status=excluded.status, message=excluded.message, updated_at=excluded.updated_at
            """, (activity_id, _text(merchant_name), _text(status), _text(message)[:500], int(time.time())))

    def _latest_source_buckets(self, conn: sqlite3.Connection, source: str, activity_id: int) -> dict[int, int]:
        row = conn.execute("SELECT id FROM ranking_v2_snapshots WHERE source=? AND activity_id=? ORDER BY last_seen_at DESC, id DESC LIMIT 1", (source, activity_id)).fetchone()
        if not row:
            return {}
        rows = conn.execute("SELECT bucket_second, bucket_count FROM ranking_v2_snapshot_buckets WHERE snapshot_id=?", (int(row["id"]),)).fetchall()
        return {int(item["bucket_second"]): int(item["bucket_count"]) for item in rows}

    def rebuild_merged_buckets(self, activity_id: int) -> None:
        now = int(time.time())
        with self._lock, self._conn() as conn:
            one = self._latest_source_buckets(conn, "source1", activity_id)
            two = self._latest_source_buckets(conn, "source2", activity_id)
            max_second = max([0, *one.keys(), *two.keys()])
            conn.execute("DELETE FROM ranking_v2_merged_buckets WHERE activity_id=?", (activity_id,))
            cumulative = 0
            total = sum(one.values()) + sum(two.values())
            rows: list[tuple[Any, ...]] = []
            for second in range(1, max_second + 1):
                source1_count = int(one.get(second, 0))
                source2_count = int(two.get(second, 0))
                merged = source1_count + source2_count
                cumulative += merged
                ratio = (cumulative / total) if total else 0.0
                rows.append((activity_id, second, source1_count, source2_count, merged, cumulative, ratio, now))
            if rows:
                conn.executemany("""
                    INSERT INTO ranking_v2_merged_buckets(activity_id, bucket_second, source1_count, source2_count, merged_count, cumulative_count, ratio, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, rows)

    def get_public_payload(self, activity_id: int) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            activity = conn.execute("SELECT * FROM ranking_v2_activities WHERE id=?", (activity_id,)).fetchone()
            if not activity:
                return None
            buckets = [dict(row) for row in conn.execute("SELECT * FROM ranking_v2_merged_buckets WHERE activity_id=? ORDER BY bucket_second", (activity_id,)).fetchall()]
            last = conn.execute("SELECT MAX(last_seen_at) AS value FROM ranking_v2_snapshots WHERE activity_id=?", (activity_id,)).fetchone()
            match = conn.execute("SELECT * FROM ranking_v2_matches WHERE activity_id=?", (activity_id,)).fetchone()
            return {
                "activity": dict(activity), "buckets": buckets,
                "total": int(buckets[-1]["cumulative_count"]) if buckets else 0,
                "last_updated_at": int((last or {}).get("value") or 0) if isinstance(last, dict) else int(last["value"] or 0),
                "source2_match": dict(match) if match else {"status": "pending"},
            }

    def find_activity(self, merchant: str, record_date: str, slot_time: str) -> dict[str, Any] | None:
        key = normalize_merchant_name(merchant)
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM ranking_v2_activities WHERE record_date=? AND slot_time=? AND merchant_key=?", (record_date, slot_time, key)).fetchone()
            return dict(row) if row else None

    def cleanup(self) -> int:
        cutoff = (datetime.now(TIMEZONE).date() - timedelta(days=RETENTION_DAYS - 1)).isoformat()
        with self._lock, self._conn() as conn:
            rows = conn.execute("SELECT id FROM ranking_v2_activities WHERE record_date<?", (cutoff,)).fetchall()
            ids = [int(row["id"]) for row in rows]
            if not ids:
                return 0
            marks = ",".join("?" for _ in ids)
            snapshot_rows = conn.execute(f"SELECT id FROM ranking_v2_snapshots WHERE activity_id IN ({marks})", ids).fetchall()
            snapshot_ids = [int(row["id"]) for row in snapshot_rows]
            if snapshot_ids:
                snapshot_marks = ",".join("?" for _ in snapshot_ids)
                conn.execute(f"DELETE FROM ranking_v2_snapshot_buckets WHERE snapshot_id IN ({snapshot_marks})", snapshot_ids)
            conn.execute(f"DELETE FROM ranking_v2_snapshots WHERE activity_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM ranking_v2_merged_buckets WHERE activity_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM ranking_v2_matches WHERE activity_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM ranking_v2_runs WHERE activity_id IN ({marks})", ids)
            conn.execute(f"DELETE FROM ranking_v2_activities WHERE id IN ({marks})", ids)
            return len(ids)

    def get_session(self, source: str) -> dict[str, Any] | None:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM ranking_v2_sessions WHERE source=?", (source,)).fetchone()
            return dict(row) if row else None

    def save_session(self, source: str, cookies_json: str, *, valid: bool, error: str = "", login: bool = False) -> None:
        now = int(time.time())
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT INTO ranking_v2_sessions(source, cookies_json, is_valid, updated_at, last_verified_at, last_login_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET cookies_json=excluded.cookies_json, is_valid=excluded.is_valid,
                    updated_at=excluded.updated_at, last_verified_at=excluded.last_verified_at,
                    last_login_at=CASE WHEN excluded.last_login_at>0 THEN excluded.last_login_at ELSE ranking_v2_sessions.last_login_at END,
                    last_error=excluded.last_error
            """, (source, cookies_json, 1 if valid else 0, now, now, now if login else 0, _text(error)[:500]))

    def clear_session(self, source: str = "source1") -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM ranking_v2_sessions WHERE source=?", (source,))

    def status(self) -> dict[str, Any]:
        now = int(time.time())
        with self._lock, self._conn() as conn:
            activity_count = int(conn.execute("SELECT COUNT(*) AS value FROM ranking_v2_activities WHERE record_date=?", (datetime.now(TIMEZONE).date().isoformat(),)).fetchone()["value"])
            running = int(conn.execute("SELECT COUNT(*) AS value FROM ranking_v2_runs WHERE status='running' AND window_end_at>?", (now,)).fetchone()["value"])
            matches = conn.execute("SELECT status, COUNT(*) AS count FROM ranking_v2_matches GROUP BY status").fetchall()
            session = conn.execute("SELECT * FROM ranking_v2_sessions WHERE source='source1'").fetchone()
            latest = conn.execute("SELECT MAX(last_seen_at) AS value FROM ranking_v2_snapshots").fetchone()
            session_payload = {
                "is_valid": bool(session["is_valid"]) if session else False,
                "updated_at": int(session["updated_at"] or 0) if session else 0,
                "last_verified_at": int(session["last_verified_at"] or 0) if session else 0,
                "last_login_at": int(session["last_login_at"] or 0) if session else 0,
                "last_error": _text(session["last_error"]) if session else "",
            }
            return {
                "today_activity_count": activity_count, "running_task_count": running,
                "matches": {str(row["status"]): int(row["count"]) for row in matches},
                # Never expose cookies_json through an API response, including
                # administrator status endpoints.
                "source1_session": session_payload,
                "last_updated_at": int(latest["value"] or 0),
            }


class OrderRankingsV2Service:
    def __init__(self) -> None:
        self.storage = OrderRankingsV2Storage()
        self._client: httpx.AsyncClient | None = None
        self._session_loaded = False
        self._login_lock = asyncio.Lock()
        self._scheduler_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._source1_inflight: set[int] = set()
        self._source2_inflight: set[tuple[str, str]] = set()
        self._source1_semaphore = asyncio.Semaphore(SOURCE1_CONCURRENCY)
        self._last_activity_refresh = 0.0
        self._last_cleanup_date = ""
        self._source1_last_check_at = 0.0
        self._source1_session_confirmed = False

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
        if not self._session_loaded:
            self._session_loaded = True
            stored = self.storage.get_session("source1") or {}
            try:
                for item in json.loads(str(stored.get("cookies_json") or "[]")):
                    self._client.cookies.set(str(item["name"]), str(item["value"]), domain=str(item.get("domain") or "naiba666.com"), path=str(item.get("path") or "/"))
            except Exception:
                pass
        return self._client

    def _dump_cookies(self) -> str:
        if self._client is None:
            return "[]"
        output: list[dict[str, Any]] = []
        for cookie in self._client.cookies.jar:
            if "naiba666.com" not in str(cookie.domain or ""):
                continue
            output.append({"name": cookie.name, "value": cookie.value, "domain": cookie.domain, "path": cookie.path, "expires": cookie.expires})
        return json.dumps(output, ensure_ascii=False, separators=(",", ":"))

    def _source1_relay_url(self, config: dict[str, Any] | None = None) -> str:
        return _text((config or get_ranking_v2_config()).get("source1_relay_url")).rstrip("/")

    async def _source1_relay_request(self, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        config = get_ranking_v2_config()
        relay_url = self._source1_relay_url(config)
        if not relay_url:
            raise RuntimeError("来源1国内 Relay 未配置")
        headers = {"Accept": "application/json"}
        if config.get("source1_relay_secret"):
            headers["X-Order-Rankings-Relay-Secret"] = str(config["source1_relay_secret"])
        body = {"operation": operation, **(payload or {})}
        try:
            client = await self._http()
            response = await client.post(
                f"{relay_url}/relay/order-rankings/source1",
                json=body,
                headers=headers,
                timeout=SOURCE1_RELAY_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException as exc:
            raise RuntimeError("来源1国内 Relay 超时") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                raise RuntimeError("来源1国内 Relay 密钥错误") from exc
            raise RuntimeError(f"来源1国内 Relay 请求失败（HTTP {exc.response.status_code}）") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError("来源1国内 Relay 不可用") from exc
        if not isinstance(data, dict) or not data.get("success"):
            raise RuntimeError("来源1国内 Relay 返回异常")
        return data

    async def _source1_ranking_html(self, merchant: str, record_date: str, slot_time: str) -> str:
        if self._source1_relay_url():
            data = await self._source1_relay_request("ranking", {
                "merchant_name": merchant, "record_date": record_date, "slot_time": slot_time,
            })
            html = str(data.get("html") or "")
            if not html:
                raise RuntimeError("来源1国内 Relay 未返回榜单页面")
            return html
        client = await self._http()
        response = await client.get(
            f"{SOURCE1_BASE}/ranking.php",
            params={"brand": merchant, "time": f"{record_date} {slot_time}:00", "_": str(int(time.time() * 1000))},
            timeout=SOURCE1_TIMEOUT,
        )
        response.raise_for_status()
        return response.text

    async def _check_login(self) -> str:
        """Return valid/invalid/unknown without treating network faults as logout."""
        client = await self._http()
        try:
            if self._source1_relay_url():
                payload = await self._source1_relay_request("check_login")
                valid = bool(payload.get("session_valid"))
                self.storage.save_session("source1", "[]", valid=valid, error="" if valid else "未登录")
                self._source1_session_confirmed = valid
                if valid:
                    self._source1_last_check_at = time.monotonic()
                return "valid" if valid else "invalid"
            response = await client.get(f"{SOURCE1_BASE}/api.php?action=check_login", timeout=SOURCE1_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            code = str(payload.get("code")) if isinstance(payload, dict) else ""
            if code in {"1", "200"}:
                self.storage.save_session("source1", self._dump_cookies(), valid=True)
                self._source1_session_confirmed = True
                self._source1_last_check_at = time.monotonic()
                return "valid"
            if code == "0":
                self.storage.save_session("source1", self._dump_cookies(), valid=False, error=_text(payload.get("msg") if isinstance(payload, dict) else "未登录"))
                self._source1_session_confirmed = False
                return "invalid"
            raise RuntimeError("来源1会话检测响应异常")
        except Exception as exc:
            # Keep the persisted session intact. A temporary timeout or 5xx must
            # not create a burst of password logins from parallel collectors.
            logger.warning("排行榜来源1会话检测暂不可用: %s", exc.__class__.__name__)
            return "unknown"

    async def ensure_source1_login(self, *, force: bool = False) -> bool:
        config = get_ranking_v2_config()
        if not config["source1_enabled"] or not config["source1_username"] or not config["source1_password"]:
            raise RuntimeError("请先在后台配置并启用来源1账号")
        async with self._login_lock:
            if not force and self._source1_session_confirmed and time.monotonic() - self._source1_last_check_at < SOURCE1_SESSION_CHECK_SECONDS:
                return True
            if not force:
                session_state = await self._check_login()
                if session_state == "valid":
                    return True
                if session_state == "unknown":
                    raise RuntimeError("来源1会话检测暂不可用")
            try:
                if self._source1_relay_url(config):
                    await self._source1_relay_request("login", {
                        "username": config["source1_username"], "password": config["source1_password"],
                    })
                else:
                    client = await self._http()
                    response = await client.post(
                        f"{SOURCE1_BASE}/api.php?action=login",
                        data={"username": config["source1_username"], "password": config["source1_password"]},
                        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                        timeout=SOURCE1_TIMEOUT,
                    )
                    payload = response.json()
                    ok = isinstance(payload, dict) and str(payload.get("code")) in {"1", "200"}
                    if not ok:
                        raise RuntimeError(_text(payload.get("msg") if isinstance(payload, dict) else "来源1登录失败") or "来源1登录失败")
                session_state = await self._check_login()
                if session_state != "valid":
                    raise RuntimeError("来源1登录后会话验证失败")
                self.storage.save_session("source1", self._dump_cookies(), valid=True, login=True)
                return True
            except Exception as exc:
                self.storage.save_session("source1", self._dump_cookies(), valid=False, error=_text(exc))
                raise

    def _is_source1_login_page(self, html: str) -> bool:
        body = str(html or "").lower()
        return "login.html" in body or "请先登录" in body or "未登录" in body

    async def reset_source1_session(self) -> None:
        self.storage.clear_session("source1")
        self._session_loaded = False
        self._source1_session_confirmed = False
        self._source1_last_check_at = 0.0
        if self._client is not None:
            self._client.cookies.clear()

    async def refresh_activities(self) -> int:
        if self._source1_relay_url():
            payload = (await self._source1_relay_request("activities")).get("data")
        else:
            client = await self._http()
            response = await client.get(f"{SOURCE1_BASE}/api.php?action=get_activities", headers={"Accept": "application/json"}, timeout=SOURCE1_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict) or str(payload.get("code")) not in {"1", "200"}:
            raise RuntimeError(_text(payload.get("msg") if isinstance(payload, dict) else "来源1活动接口异常") or "来源1活动接口异常")
        today = datetime.now(TIMEZONE).date()
        items: list[dict[str, Any]] = []
        for activity in payload.get("data") or []:
            if not isinstance(activity, dict) or not _bool(activity.get("status"), True):
                continue
            try:
                start = datetime.strptime(_text(activity.get("start_date")), "%Y-%m-%d").date()
                end = datetime.strptime(_text(activity.get("end_date")), "%Y-%m-%d").date()
            except ValueError:
                continue
            if not start <= today <= end:
                continue
            for slot in _text(activity.get("time_slots")).split(","):
                slot = _text(slot)
                if re.fullmatch(r"\d{2}:\d{2}", slot):
                    items.append({"record_date": today.isoformat(), "merchant_name": _text(activity.get("brand")), "slot_time": slot, "activity": activity})
        self._last_activity_refresh = time.monotonic()
        return self.storage.upsert_activities(items)

    async def _fetch_source1(self, run: dict[str, Any]) -> None:
        run_id = int(run["id"])
        if run_id in self._source1_inflight:
            self.storage.mark_poll([run_id], "source1", success=False, skipped=True)
            return
        self._source1_inflight.add(run_id)
        started = time.monotonic()
        try:
            async with self._source1_semaphore:
                await self.ensure_source1_login()
                html = await self._source1_ranking_html(run["merchant_name"], run["record_date"], run["slot_time"])
                try:
                    parsed = parse_source1_ranking_html(html)
                except ValueError:
                    if not self._is_source1_login_page(html):
                        raise
                    await self.ensure_source1_login(force=True)
                    parsed = parse_source1_ranking_html(
                        await self._source1_ranking_html(run["merchant_name"], run["record_date"], run["slot_time"])
                    )
                duration = int((time.monotonic() - started) * 1000)
                activity_id = int(run["activity_id"])
                self.storage.write_snapshot("source1", activity_id, parsed["buckets"], {"participant_count": parsed["participant_count"]}, duration)
                self.storage.rebuild_merged_buckets(activity_id)
                self.storage.mark_poll([run_id], "source1", success=True, response_ms=duration)
        except Exception as exc:
            self.storage.mark_poll([run_id], "source1", success=False, response_ms=int((time.monotonic() - started) * 1000), error=exc.__class__.__name__)
            logger.warning("排行榜来源1采集失败: activity=%s error=%s", run_id, exc.__class__.__name__)
        finally:
            self._source1_inflight.discard(run_id)

    async def _fetch_source2_slot(self, runs: list[dict[str, Any]]) -> None:
        if not runs:
            return
        key = (str(runs[0]["record_date"]), str(runs[0]["slot_time"]))
        run_ids = [int(item["id"]) for item in runs]
        if key in self._source2_inflight:
            self.storage.mark_poll(run_ids, "source2", success=False, skipped=True)
            return
        self._source2_inflight.add(key)
        started = time.monotonic()
        try:
            client = await self._http()
            slot_dt = datetime.strptime(f"{key[0]} {key[1]}", "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
            compact = slot_dt.strftime("%m%d%H%M")
            response = await client.get(SOURCE2_URL, params={"key": f"mt-time-{compact}"}, timeout=SOURCE2_TIMEOUT)
            response.raise_for_status()
            source_data = parse_source2_shop_data(response.text, slot_dt)
            indexed: dict[str, list[str]] = {}
            for name in source_data:
                indexed.setdefault(normalize_merchant_name(name), []).append(name)
            duration = int((time.monotonic() - started) * 1000)
            for run in runs:
                names = indexed.get(str(run["merchant_key"]), [])
                activity_id = int(run["activity_id"])
                if len(names) != 1:
                    self.storage.set_match(activity_id, "unmatched" if not names else "ambiguous", message="来源2未找到唯一同名商家")
                    continue
                matched_name = names[0]
                self.storage.set_match(activity_id, "matched", merchant_name=matched_name)
                self.storage.write_snapshot("source2", activity_id, source_data[matched_name], {"merchant_name": matched_name}, duration)
                self.storage.rebuild_merged_buckets(activity_id)
            self.storage.mark_poll(run_ids, "source2", success=True, response_ms=duration)
        except Exception as exc:
            self.storage.mark_poll(run_ids, "source2", success=False, response_ms=int((time.monotonic() - started) * 1000), error=exc.__class__.__name__)
            logger.warning("排行榜来源2采集失败: slot=%s %s error=%s", key[0], key[1], exc.__class__.__name__)
        finally:
            self._source2_inflight.discard(key)

    def _track_task(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                config = get_ranking_v2_config()
                now = datetime.now(TIMEZONE)
                now_ts = int(now.timestamp())
                if config["collection_enabled"]:
                    if time.monotonic() - self._last_activity_refresh >= 300:
                        # Back off for the complete refresh interval even when
                        # the upstream rejects this host. Do not hammer the
                        # source once per scheduler tick after a failure.
                        self._last_activity_refresh = time.monotonic()
                        try:
                            await self.refresh_activities()
                        except Exception as exc:
                            logger.warning("排行榜活动刷新失败: %s", exc.__class__.__name__)
                    for activity in self.storage.list_activities(now.date().isoformat()):
                        slot_dt = datetime.strptime(f"{activity['record_date']} {activity['slot_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
                        start_at = int(slot_dt.timestamp())
                        if start_at <= now_ts < start_at + WINDOW_SECONDS:
                            self.storage.ensure_run(int(activity["id"]), start_at, start_at + WINDOW_SECONDS)
                # Existing manual tasks keep running even when the automatic
                # discovery switch is off. This makes one-off gray validation
                # usable without enabling production collection.
                runs = self.storage.get_running_runs(now_ts)
                source2_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
                for run in runs:
                    if now_ts - self.storage.latest_poll_at(run, "source1") >= POLL_SECONDS:
                        self._track_task(self._fetch_source1(run))
                    source2_groups.setdefault((str(run["record_date"]), str(run["slot_time"])), []).append(run)
                launched = 0
                for group in source2_groups.values():
                    if launched >= SOURCE2_CONCURRENCY_CAP:
                        break
                    if now_ts - min(self.storage.latest_poll_at(item, "source2") for item in group) >= POLL_SECONDS:
                        self._track_task(self._fetch_source2_slot(group))
                        launched += 1
                date_key = now.date().isoformat()
                if self._last_cleanup_date != date_key:
                    self.storage.cleanup()
                    self._last_cleanup_date = date_key
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("排行榜V2调度循环异常: %s", exc.__class__.__name__)
            await asyncio.sleep(1)

    def start(self) -> None:
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            self._scheduler_task = None
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def test_source1_login(self) -> dict[str, Any]:
        await self.ensure_source1_login(force=False)
        return self.storage.get_session("source1") or {}

    async def refresh_source1_activities_for_admin(self) -> list[dict[str, Any]]:
        """Verify the configured source session, then fetch today's selectable catalog."""
        await self.ensure_source1_login()
        await self.refresh_activities()
        return self.storage.list_activities(datetime.now(TIMEZONE).date().isoformat())

    async def test_source1_query(self, merchant: str, record_date: str, slot_time: str) -> dict[str, Any]:
        await self.ensure_source1_login()
        return parse_source1_ranking_html(await self._source1_ranking_html(merchant, record_date, slot_time))

    async def test_source2_query(self, record_date: str, slot_time: str) -> dict[str, Any]:
        client = await self._http()
        slot_dt = datetime.strptime(f"{record_date} {slot_time}", "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
        response = await client.get(SOURCE2_URL, params={"key": f"mt-time-{slot_dt.strftime('%m%d%H%M')}"}, timeout=SOURCE2_TIMEOUT)
        response.raise_for_status()
        data = parse_source2_shop_data(response.text, slot_dt)
        return {"merchant_count": len(data), "merchants": sorted(data), "bucket_counts": {name: sum(bucket.values()) for name, bucket in data.items()}}

    def start_manual(self, merchant: str, record_date: str, slot_time: str) -> dict[str, Any]:
        activity = self.storage.find_activity(merchant, record_date, slot_time)
        if not activity:
            self.storage.upsert_activities([{"merchant_name": merchant, "record_date": record_date, "slot_time": slot_time, "manual": True}])
            activity = self.storage.find_activity(merchant, record_date, slot_time)
        if not activity:
            raise RuntimeError("无法创建测试活动")
        now = int(time.time())
        return self.storage.ensure_run(int(activity["id"]), now, now + WINDOW_SECONDS)

    def rank_for_order(self, poi_name: str, accept_time: Any) -> dict[str, Any] | None:
        try:
            timestamp = int(accept_time)
            if timestamp > 1_000_000_000_000:
                timestamp //= 1000
        except (TypeError, ValueError):
            return None
        accepted_at = datetime.fromtimestamp(timestamp, TIMEZONE)
        normalized_poi = normalize_merchant_name(poi_name)
        candidates: list[tuple[int, dict[str, Any], int]] = []
        for activity in self.storage.list_activities(accepted_at.date().isoformat()):
            key = str(activity["merchant_key"])
            if not key or key not in normalized_poi:
                continue
            slot_dt = datetime.strptime(f"{activity['record_date']} {activity['slot_time']}", "%Y-%m-%d %H:%M").replace(tzinfo=TIMEZONE)
            offset = int(timestamp - slot_dt.timestamp())
            if 1 <= offset < 1800:
                candidates.append((len(key), activity, offset))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return None
        _, activity, offset = candidates[0]
        payload = self.storage.get_public_payload(int(activity["id"]))
        if not payload:
            return None
        target = next((item for item in payload["buckets"] if int(item["bucket_second"]) == offset), None)
        if not target or int(target["merged_count"]) <= 0:
            return None
        return {
            "merchant_name": activity["merchant_name"], "record_date": activity["record_date"], "slot_time": activity["slot_time"],
            "bucket_second": offset, "rank": int(target["cumulative_count"]) - int(target["merged_count"]) + 1,
            "tie_count": int(target["merged_count"]), "total": int(payload["total"]),
        }

    def rank_text_for_order(self, poi_name: str, accept_time: Any) -> str:
        if not is_ranking_rank_text_enabled():
            return ""
        try:
            result = self.rank_for_order(poi_name, accept_time)
        except Exception as exc:
            logger.warning("排行榜 V2 名次读取失败: %s", exc.__class__.__name__)
            return ""
        if not result:
            return ""
        return "并列第 {rank} 名（{second:02d}s，{tie} 人同秒，合计 {total} 人）".format(
            rank=int(result["rank"]), second=int(result["bucket_second"]),
            tie=int(result["tie_count"]), total=int(result["total"]),
        )


_service: OrderRankingsV2Service | None = None


def get_order_rankings_v2_service() -> OrderRankingsV2Service:
    global _service
    if _service is None:
        _service = OrderRankingsV2Service()
    return _service


def start_order_rankings_v2_scheduler() -> None:
    get_order_rankings_v2_service().start()


async def stop_order_rankings_v2_scheduler() -> None:
    await get_order_rankings_v2_service().stop()
