from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from utils.path_utils import resolve_runtime_data_path


class MeituanAllowanceTaskStorage:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            storage_dir = resolve_runtime_data_path("meituan_allowance")
            storage_dir.mkdir(parents=True, exist_ok=True)
            db_path = os.fspath(storage_dir / "meituan_allowance_tasks.db")
        elif not os.path.isabs(db_path):
            db_path = os.fspath(resolve_runtime_data_path(db_path))

        self.db_path = db_path
        self._lock = threading.RLock()
        print(f"[INFO] 美团津贴任务数据库路径: active={self.db_path}", flush=True)
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
                CREATE TABLE IF NOT EXISTS allowance_tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    allowance_type TEXT NOT NULL DEFAULT 'large',
                    meituan_user_id TEXT NOT NULL DEFAULT '',
                    address_id TEXT NOT NULL DEFAULT '',
                    token_masked TEXT NOT NULL,
                    token_fingerprint TEXT NOT NULL,
                    input_latitude TEXT NOT NULL,
                    input_longitude TEXT NOT NULL,
                    normalized_latitude TEXT NOT NULL,
                    normalized_longitude TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    finished_at INTEGER,
                    pages_requested INTEGER NOT NULL DEFAULT 0,
                    merchant_count INTEGER NOT NULL DEFAULT 0,
                    stop_reason TEXT NOT NULL DEFAULT '',
                    consecutive_empty_pages INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT NOT NULL DEFAULT '',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    progress_json TEXT NOT NULL DEFAULT '[]',
                    merchants_json TEXT NOT NULL DEFAULT '[]',
                    web_user_id INTEGER,
                    web_token_id INTEGER,
                    task_source TEXT NOT NULL DEFAULT 'legacy',
                    address_name TEXT NOT NULL DEFAULT '',
                    notification_status TEXT NOT NULL DEFAULT 'not_applicable',
                    notification_processed_at INTEGER
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS allowance_refresh_targets (
                    meituan_user_id TEXT NOT NULL,
                    address_id TEXT NOT NULL,
                    resolved_address_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    last_task_id TEXT NOT NULL DEFAULT '',
                    last_refresh_at INTEGER,
                    PRIMARY KEY (meituan_user_id, address_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_allowance_refresh_targets_updated_at
                ON allowance_refresh_targets(updated_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS allowance_scheduler_runs (
                    job_name TEXT PRIMARY KEY,
                    last_run_date TEXT NOT NULL DEFAULT '',
                    last_run_at INTEGER NOT NULL DEFAULT 0,
                    last_result_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS allowance_daily_results (
                    date_key TEXT NOT NULL,
                    meituan_user_id TEXT NOT NULL,
                    address_id TEXT NOT NULL,
                    allowance_type TEXT NOT NULL DEFAULT 'large',
                    resolved_address_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    first_task_id TEXT NOT NULL DEFAULT '',
                    last_task_id TEXT NOT NULL DEFAULT '',
                    last_task_status TEXT NOT NULL DEFAULT '',
                    merchant_count INTEGER NOT NULL DEFAULT 0,
                    merchants_json TEXT NOT NULL DEFAULT '[]',
                    task_ids_json TEXT NOT NULL DEFAULT '[]',
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (date_key, meituan_user_id, address_id, allowance_type)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS allowance_daily_task_stats (
                    date_key TEXT NOT NULL,
                    allowance_type TEXT NOT NULL DEFAULT 'large',
                    task_count INTEGER NOT NULL DEFAULT 0,
                    queued_task_count INTEGER NOT NULL DEFAULT 0,
                    running_task_count INTEGER NOT NULL DEFAULT 0,
                    succeeded_task_count INTEGER NOT NULL DEFAULT 0,
                    failed_task_count INTEGER NOT NULL DEFAULT 0,
                    interrupted_task_count INTEGER NOT NULL DEFAULT 0,
                    relay_success_task_count INTEGER NOT NULL DEFAULT 0,
                    relay_switched_task_count INTEGER NOT NULL DEFAULT 0,
                    relay_fallback_task_count INTEGER NOT NULL DEFAULT 0,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (date_key, allowance_type)
                )
                """
            )
            self._migrate_allowance_daily_results_table(cursor)
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_allowance_daily_results_lookup
                ON allowance_daily_results(date_key, meituan_user_id, address_id, allowance_type)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_allowance_tasks_created_at
                ON allowance_tasks(created_at DESC)
                """
            )
            self._ensure_column_exists(cursor, "allowance_tasks", "allowance_type", "TEXT NOT NULL DEFAULT 'large'")
            self._ensure_column_exists(cursor, "allowance_tasks", "meituan_user_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column_exists(cursor, "allowance_tasks", "address_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column_exists(cursor, "allowance_tasks", "web_user_id", "INTEGER")
            self._ensure_column_exists(cursor, "allowance_tasks", "web_token_id", "INTEGER")
            self._ensure_column_exists(cursor, "allowance_tasks", "task_source", "TEXT NOT NULL DEFAULT 'legacy'")
            self._ensure_column_exists(cursor, "allowance_tasks", "address_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column_exists(cursor, "allowance_tasks", "notification_status", "TEXT NOT NULL DEFAULT 'not_applicable'")
            self._ensure_column_exists(cursor, "allowance_tasks", "notification_processed_at", "INTEGER")
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_allowance_tasks_user_address_created_at
                ON allowance_tasks(meituan_user_id, address_id, created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_allowance_tasks_user_address_type_created_at
                ON allowance_tasks(meituan_user_id, address_id, allowance_type, created_at DESC)
                """
            )
            self._backfill_current_daily_task_stats(cursor)
            conn.commit()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            conn.commit()

    def _current_date_key(self, now_ts: int | None = None) -> str:
        dt = time.time() if now_ts is None else int(now_ts)
        return time.strftime("%Y-%m-%d", time.gmtime(dt + 8 * 3600))

    def _start_of_date_timestamp(self, date_key: str) -> int:
        try:
            return int(datetime.strptime(date_key, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        except (TypeError, ValueError):
            return 0

    def _write_daily_task_stats(
        self,
        cursor: sqlite3.Cursor,
        *,
        date_key: str,
        allowance_type: str,
        deltas: Dict[str, int],
    ) -> None:
        normalized_date_key = str(date_key or "").strip()
        normalized_allowance_type = str(allowance_type or "large").strip() or "large"
        if not normalized_date_key:
            return

        fields = (
            "task_count",
            "queued_task_count",
            "running_task_count",
            "succeeded_task_count",
            "failed_task_count",
            "interrupted_task_count",
            "relay_success_task_count",
            "relay_switched_task_count",
            "relay_fallback_task_count",
        )
        values = [int(deltas.get(field) or 0) for field in fields]
        now = int(time.time())
        cursor.execute(
            f"""
            INSERT INTO allowance_daily_task_stats (
                date_key, allowance_type, {', '.join(fields)}, updated_at
            ) VALUES (?, ?, {', '.join('?' for _ in fields)}, ?)
            ON CONFLICT(date_key, allowance_type) DO UPDATE SET
                {', '.join(f'{field} = {field} + excluded.{field}' for field in fields)},
                updated_at = excluded.updated_at
            """,
            (normalized_date_key, normalized_allowance_type, *values, now),
        )

    def _backfill_current_daily_task_stats(self, cursor: sqlite3.Cursor) -> None:
        """Migrate today's existing task history once into the compact dashboard table."""
        today_date_key = self._current_date_key()
        cursor.execute(
            "SELECT COUNT(1) FROM allowance_daily_task_stats WHERE date_key = ?",
            (today_date_key,),
        )
        if int((cursor.fetchone() or (0,))[0] or 0) > 0:
            return

        start_of_day_ts = self._start_of_date_timestamp(today_date_key)
        cursor.execute(
            """
            SELECT allowance_type, status, summary_json
            FROM allowance_tasks
            WHERE created_at >= ?
            """,
            (start_of_day_ts,),
        )
        stats_by_type: Dict[str, Dict[str, int]] = {}
        for allowance_type, status, summary_json in cursor.fetchall():
            normalized_type = str(allowance_type or "large").strip() or "large"
            metrics = stats_by_type.setdefault(normalized_type, {})
            metrics["task_count"] = int(metrics.get("task_count") or 0) + 1
            status_field = {
                "queued": "queued_task_count",
                "running": "running_task_count",
                "succeeded": "succeeded_task_count",
                "failed": "failed_task_count",
                "interrupted": "interrupted_task_count",
            }.get(str(status or "").strip())
            if status_field:
                metrics[status_field] = int(metrics.get(status_field) or 0) + 1
            try:
                summary = json.loads(summary_json or "{}")
            except Exception:
                summary = {}
            if not isinstance(summary, dict):
                continue
            if isinstance(summary.get("relay_attempts"), list) and summary.get("relay_attempts"):
                metrics["relay_success_task_count"] = int(metrics.get("relay_success_task_count") or 0) + 1
            if bool(summary.get("relay_switched")):
                metrics["relay_switched_task_count"] = int(metrics.get("relay_switched_task_count") or 0) + 1
            if bool(summary.get("fallback_used")):
                metrics["relay_fallback_task_count"] = int(metrics.get("relay_fallback_task_count") or 0) + 1

        for allowance_type in {"large", "small_free_order", *stats_by_type.keys()}:
            self._write_daily_task_stats(
                cursor,
                date_key=today_date_key,
                allowance_type=allowance_type,
                deltas=stats_by_type.get(allowance_type) or {},
            )

    def _record_task_status_transition(
        self,
        cursor: sqlite3.Cursor,
        *,
        created_at: int,
        allowance_type: str,
        previous_status: str,
        next_status: str,
        summary: Dict[str, Any] | None = None,
    ) -> None:
        normalized_previous = str(previous_status or "").strip()
        normalized_next = str(next_status or "").strip()
        if not normalized_next or normalized_next == normalized_previous:
            return

        status_fields = {
            "queued": "queued_task_count",
            "running": "running_task_count",
            "succeeded": "succeeded_task_count",
            "failed": "failed_task_count",
            "interrupted": "interrupted_task_count",
        }
        deltas: Dict[str, int] = {}
        previous_field = status_fields.get(normalized_previous)
        next_field = status_fields.get(normalized_next)
        if previous_field:
            deltas[previous_field] = -1
        if next_field:
            deltas[next_field] = 1

        if normalized_next in {"succeeded", "failed", "interrupted"}:
            normalized_summary = summary if isinstance(summary, dict) else {}
            if isinstance(normalized_summary.get("relay_attempts"), list) and normalized_summary.get("relay_attempts"):
                deltas["relay_success_task_count"] = 1
            if bool(normalized_summary.get("relay_switched")):
                deltas["relay_switched_task_count"] = 1
            if bool(normalized_summary.get("fallback_used")):
                deltas["relay_fallback_task_count"] = 1

        self._write_daily_task_stats(
            cursor,
            date_key=self._current_date_key(created_at),
            allowance_type=allowance_type,
            deltas=deltas,
        )

    def _normalize_merchant_identity(self, merchant: Dict[str, Any]) -> str:
        return str(
            merchant.get("poi_id_str")
            or merchant.get("poi_id")
            or merchant.get("wm_poi_id_str")
            or ""
        ).strip()

    def _merge_unique_dict_list(self, current: Any, incoming: Any, *, key_fields: tuple[str, ...]) -> list[Dict[str, Any]]:
        items: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for source in (current, incoming):
            if not isinstance(source, list):
                continue
            for raw_item in source:
                if not isinstance(raw_item, dict):
                    continue
                key = "|".join(str(raw_item.get(field) or "").strip() for field in key_fields)
                if not key:
                    key = json.dumps(raw_item, ensure_ascii=False, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                items.append(dict(raw_item))
        return items

    def _merge_merchant_item(self, existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(existing)
        for key, value in incoming.items():
            if key in {"activities", "sku_allowance_items"}:
                continue
            if key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value

        merged["activities"] = self._merge_unique_dict_list(
            existing.get("activities"),
            incoming.get("activities"),
            key_fields=("type", "name", "amount"),
        )
        merged["sku_allowance_items"] = self._merge_unique_dict_list(
            existing.get("sku_allowance_items"),
            incoming.get("sku_allowance_items"),
            key_fields=("sku_id", "name", "allowance_amount"),
        )
        if merged.get("sku_allowance_items") and not merged.get("sku_allowance_note"):
            merged["sku_allowance_note"] = incoming.get("sku_allowance_note") or existing.get("sku_allowance_note") or ""
        return merged

    def _merge_merchant_lists(self, existing: Any, incoming: Any) -> list[Dict[str, Any]]:
        merged_list: list[Dict[str, Any]] = []
        index_by_id: dict[str, int] = {}

        def append_or_merge(raw_item: Any) -> None:
            if not isinstance(raw_item, dict):
                return
            item = dict(raw_item)
            merchant_id = self._normalize_merchant_identity(item)
            if not merchant_id:
                return
            if merchant_id in index_by_id:
                existing_index = index_by_id[merchant_id]
                merged_list[existing_index] = self._merge_merchant_item(merged_list[existing_index], item)
                return
            index_by_id[merchant_id] = len(merged_list)
            merged_list.append(item)

        if isinstance(existing, list):
            for item in existing:
                append_or_merge(item)
        if isinstance(incoming, list):
            for item in incoming:
                append_or_merge(item)
        return merged_list

    def _ensure_column_exists(
        self,
        cursor: sqlite3.Cursor,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_columns = {str(row[1] or "") for row in cursor.fetchall()}
        if column_name in existing_columns:
            return
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    def _migrate_allowance_daily_results_table(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("PRAGMA table_info(allowance_daily_results)")
        table_info = cursor.fetchall()
        if not table_info:
            return

        existing_columns = [str(row[1] or "") for row in table_info]
        primary_key_columns = [
            str(row[1] or "")
            for row in sorted(table_info, key=lambda item: int(item[5] or 0))
            if int(row[5] or 0) > 0
        ]
        expected_primary_key = ["date_key", "meituan_user_id", "address_id", "allowance_type"]
        if "allowance_type" in existing_columns and primary_key_columns == expected_primary_key:
            return

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS allowance_daily_results__new (
                date_key TEXT NOT NULL,
                meituan_user_id TEXT NOT NULL,
                address_id TEXT NOT NULL,
                allowance_type TEXT NOT NULL DEFAULT 'large',
                resolved_address_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                first_task_id TEXT NOT NULL DEFAULT '',
                last_task_id TEXT NOT NULL DEFAULT '',
                last_task_status TEXT NOT NULL DEFAULT '',
                merchant_count INTEGER NOT NULL DEFAULT 0,
                merchants_json TEXT NOT NULL DEFAULT '[]',
                task_ids_json TEXT NOT NULL DEFAULT '[]',
                summary_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (date_key, meituan_user_id, address_id, allowance_type)
            )
            """
        )
        allowance_type_select = (
            "COALESCE(NULLIF(allowance_type, ''), 'large')"
            if "allowance_type" in existing_columns
            else "'large'"
        )
        cursor.execute(
            f"""
            INSERT OR REPLACE INTO allowance_daily_results__new (
                date_key, meituan_user_id, address_id, allowance_type,
                resolved_address_json, created_at, updated_at,
                first_task_id, last_task_id, last_task_status,
                merchant_count, merchants_json, task_ids_json, summary_json
            )
            SELECT
                date_key, meituan_user_id, address_id, {allowance_type_select},
                resolved_address_json, created_at, updated_at,
                first_task_id, last_task_id, last_task_status,
                merchant_count, merchants_json, task_ids_json, summary_json
            FROM allowance_daily_results
            """
        )
        cursor.execute("DROP TABLE allowance_daily_results")
        cursor.execute("ALTER TABLE allowance_daily_results__new RENAME TO allowance_daily_results")

    def create_task(
        self,
        *,
        task_id: str,
        status: str,
        allowance_type: str,
        meituan_user_id: str,
        address_id: str,
        token_masked: str,
        token_fingerprint: str,
        input_latitude: str,
        input_longitude: str,
        normalized_latitude: str,
        normalized_longitude: str,
        relay_node_name: str = "",
        relay_node_url: str = "",
        relay_strategy: str = "healthy_round_robin",
        web_user_id: int | None = None,
        web_token_id: int | None = None,
        task_source: str = "legacy",
        address_name: str = "",
        created_at: int | None = None,
    ) -> None:
        now = int(created_at or time.time())
        summary = {
            "allowance_type": str(allowance_type or "large").strip() or "large",
            "pages_requested": 0,
            "merchant_count": 0,
            "stop_reason": "",
            "consecutive_empty_pages": 0,
            "duration_seconds": 0,
            "page_size": 10,
            "request_delay_ms": 300,
            "empty_page_stop_threshold": 3,
            "max_pages": 100,
            "input_coordinates": {
                "latitude": input_latitude,
                "longitude": input_longitude,
            },
            "normalized_coordinates": {
                "latitude": normalized_latitude,
                "longitude": normalized_longitude,
            },
            "relay_strategy": str(relay_strategy or "healthy_round_robin").strip() or "healthy_round_robin",
            "relay_node_name": str(relay_node_name or "").strip(),
            "relay_node_url": str(relay_node_url or "").strip(),
            "preferred_relay_node_name": str(relay_node_name or "").strip(),
            "preferred_relay_node_url": str(relay_node_url or "").strip(),
            "active_relay_node_name": str(relay_node_name or "").strip(),
            "active_relay_node_url": str(relay_node_url or "").strip(),
            "successful_relay_node_name": "",
            "successful_relay_node_url": "",
            "relay_nodes_tried": [],
            "relay_attempts": [],
            "relay_switched": False,
            "fallback_used": False,
            "fallback_mode": "",
        }
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO allowance_tasks (
                        task_id, status, allowance_type, meituan_user_id, address_id, token_masked, token_fingerprint,
                        input_latitude, input_longitude, normalized_latitude, normalized_longitude,
                        created_at, pages_requested, merchant_count, stop_reason,
                        consecutive_empty_pages, error_message, summary_json, progress_json, merchants_json,
                        web_user_id, web_token_id, task_source, address_name, notification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, '', 0, '', ?, '[]', '[]', ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        status,
                        str(allowance_type or "large").strip() or "large",
                        str(meituan_user_id or "").strip(),
                        str(address_id or "").strip(),
                        token_masked,
                        token_fingerprint,
                        input_latitude,
                        input_longitude,
                        normalized_latitude,
                        normalized_longitude,
                        now,
                        json.dumps(summary, ensure_ascii=False),
                        int(web_user_id) if web_user_id else None,
                        int(web_token_id) if web_token_id else None,
                        str(task_source or "legacy").strip() or "legacy",
                        str(address_name or "").strip(),
                        "pending" if web_user_id else "not_applicable",
                    ),
                )
                self._write_daily_task_stats(
                    conn.cursor(),
                    date_key=self._current_date_key(now),
                    allowance_type=allowance_type,
                    deltas={
                        "task_count": 1,
                        "queued_task_count": 1 if str(status or "").strip() == "queued" else 0,
                        "running_task_count": 1 if str(status or "").strip() == "running" else 0,
                    },
                )
                conn.commit()

    def upsert_refresh_target(
        self,
        *,
        meituan_user_id: str,
        address_id: str,
        resolved_address: Dict[str, Any] | None,
        last_task_id: str = "",
        last_refresh_at: int | None = None,
    ) -> None:
        normalized_user_id = str(meituan_user_id or "").strip()
        normalized_address_id = str(address_id or "").strip()
        if not normalized_user_id or not normalized_address_id:
            return
        now = int(time.time())
        resolved_address_payload = resolved_address if isinstance(resolved_address, dict) else {}
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO allowance_refresh_targets (
                        meituan_user_id, address_id, resolved_address_json,
                        created_at, updated_at, last_task_id, last_refresh_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(meituan_user_id, address_id) DO UPDATE SET
                        resolved_address_json = excluded.resolved_address_json,
                        updated_at = excluded.updated_at,
                        last_task_id = CASE
                            WHEN excluded.last_task_id != '' THEN excluded.last_task_id
                            ELSE allowance_refresh_targets.last_task_id
                        END,
                        last_refresh_at = COALESCE(excluded.last_refresh_at, allowance_refresh_targets.last_refresh_at)
                    """,
                    (
                        normalized_user_id,
                        normalized_address_id,
                        json.dumps(resolved_address_payload, ensure_ascii=False),
                        now,
                        now,
                        str(last_task_id or "").strip(),
                        int(last_refresh_at) if last_refresh_at else None,
                    ),
                )
                conn.commit()

    def list_refresh_targets(self) -> list[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT meituan_user_id, address_id, resolved_address_json,
                           created_at, updated_at, last_task_id, last_refresh_at
                    FROM allowance_refresh_targets
                    ORDER BY updated_at DESC, meituan_user_id ASC, address_id ASC
                    """
                )
                rows = cursor.fetchall()
        targets: list[Dict[str, Any]] = []
        for row in rows:
            targets.append(
                {
                    "meituan_user_id": str(row["meituan_user_id"] or ""),
                    "address_id": str(row["address_id"] or ""),
                    "resolved_address": self._parse_json_dict(row["resolved_address_json"]),
                    "created_at": int(row["created_at"] or 0),
                    "updated_at": int(row["updated_at"] or 0),
                    "last_task_id": str(row["last_task_id"] or ""),
                    "last_refresh_at": int(row["last_refresh_at"] or 0) or None,
                }
            )
        return targets

    def mark_refresh_target_refreshed(
        self,
        *,
        meituan_user_id: str,
        address_id: str,
        task_id: str,
        refreshed_at: int | None = None,
    ) -> None:
        normalized_user_id = str(meituan_user_id or "").strip()
        normalized_address_id = str(address_id or "").strip()
        if not normalized_user_id or not normalized_address_id:
            return
        now = int(refreshed_at or time.time())
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE allowance_refresh_targets
                    SET updated_at = ?, last_task_id = ?, last_refresh_at = ?
                    WHERE meituan_user_id = ? AND address_id = ?
                    """,
                    (
                        now,
                        str(task_id or "").strip(),
                        now,
                        normalized_user_id,
                        normalized_address_id,
                    ),
                )
                conn.commit()

    def get_scheduler_run(self, job_name: str) -> Dict[str, Any]:
        normalized_job_name = str(job_name or "").strip()
        if not normalized_job_name:
            return {"job_name": "", "last_run_date": "", "last_run_at": 0, "last_result": {}}
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT job_name, last_run_date, last_run_at, last_result_json
                    FROM allowance_scheduler_runs
                    WHERE job_name = ?
                    """,
                    (normalized_job_name,),
                )
                row = cursor.fetchone()
        if row is None:
            return {"job_name": normalized_job_name, "last_run_date": "", "last_run_at": 0, "last_result": {}}
        return {
            "job_name": str(row["job_name"] or ""),
            "last_run_date": str(row["last_run_date"] or ""),
            "last_run_at": int(row["last_run_at"] or 0),
            "last_result": self._parse_json_dict(row["last_result_json"]),
        }

    def set_scheduler_run(
        self,
        *,
        job_name: str,
        last_run_date: str,
        last_run_at: int,
        last_result: Dict[str, Any] | None = None,
    ) -> None:
        normalized_job_name = str(job_name or "").strip()
        if not normalized_job_name:
            return
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO allowance_scheduler_runs (job_name, last_run_date, last_run_at, last_result_json)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(job_name) DO UPDATE SET
                        last_run_date = excluded.last_run_date,
                        last_run_at = excluded.last_run_at,
                        last_result_json = excluded.last_result_json
                    """,
                    (
                        normalized_job_name,
                        str(last_run_date or "").strip(),
                        int(last_run_at or 0),
                        json.dumps(last_result or {}, ensure_ascii=False),
                    ),
                )
                conn.commit()

    def update_daily_aggregate(
        self,
        *,
        meituan_user_id: str,
        address_id: str,
        allowance_type: str,
        resolved_address: Dict[str, Any] | None,
        task_id: str,
        task_status: str,
        merchants: list[Dict[str, Any]] | None,
        summary: Dict[str, Any] | None,
        date_key: str | None = None,
    ) -> None:
        normalized_user_id = str(meituan_user_id or "").strip()
        normalized_address_id = str(address_id or "").strip()
        normalized_allowance_type = str(allowance_type or "large").strip() or "large"
        if not normalized_user_id or not normalized_address_id:
            return

        effective_date_key = str(date_key or self._current_date_key()).strip()
        if not effective_date_key:
            return

        now = int(time.time())
        resolved_address_payload = dict(resolved_address or {}) if isinstance(resolved_address, dict) else {}
        incoming_merchants = list(merchants or []) if isinstance(merchants, list) else []
        incoming_summary = dict(summary or {}) if isinstance(summary, dict) else {}

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT *
                    FROM allowance_daily_results
                    WHERE date_key = ? AND meituan_user_id = ? AND address_id = ? AND allowance_type = ?
                    """,
                    (effective_date_key, normalized_user_id, normalized_address_id, normalized_allowance_type),
                )
                row = cursor.fetchone()

                existing_merchants: list[Dict[str, Any]] = []
                existing_task_ids: list[str] = []
                first_task_id = str(task_id or "").strip()
                created_at = now
                if row is not None:
                    existing_merchants = self._parse_json_list(row["merchants_json"])
                    existing_task_ids = [
                        str(item).strip()
                        for item in self._parse_json_scalar_list(row["task_ids_json"])
                        if str(item).strip()
                    ]
                    first_task_id = str(row["first_task_id"] or "").strip() or first_task_id
                    created_at = int(row["created_at"] or now)

                merged_merchants = self._merge_merchant_lists(existing_merchants, incoming_merchants)
                merged_task_ids = []
                seen_task_ids: set[str] = set()
                for item in existing_task_ids + ([str(task_id or "").strip()] if str(task_id or "").strip() else []):
                    normalized_task_id = str(item or "").strip()
                    if not normalized_task_id or normalized_task_id in seen_task_ids:
                        continue
                    seen_task_ids.add(normalized_task_id)
                    merged_task_ids.append(normalized_task_id)

                aggregate_summary = {
                    "date_key": effective_date_key,
                    "allowance_type": normalized_allowance_type,
                    "merchant_count": len(merged_merchants),
                    "task_count": len(merged_task_ids),
                    "last_task_id": str(task_id or "").strip(),
                    "last_task_status": str(task_status or "").strip(),
                    "resolved_address": resolved_address_payload,
                }
                if incoming_summary:
                    aggregate_summary["latest_task_summary"] = incoming_summary

                conn.execute(
                    """
                    INSERT INTO allowance_daily_results (
                        date_key, meituan_user_id, address_id, allowance_type, resolved_address_json,
                        created_at, updated_at, first_task_id, last_task_id, last_task_status,
                        merchant_count, merchants_json, task_ids_json, summary_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date_key, meituan_user_id, address_id, allowance_type) DO UPDATE SET
                        resolved_address_json = excluded.resolved_address_json,
                        updated_at = excluded.updated_at,
                        last_task_id = excluded.last_task_id,
                        last_task_status = excluded.last_task_status,
                        merchant_count = excluded.merchant_count,
                        merchants_json = excluded.merchants_json,
                        task_ids_json = excluded.task_ids_json,
                        summary_json = excluded.summary_json
                    """,
                    (
                        effective_date_key,
                        normalized_user_id,
                        normalized_address_id,
                        normalized_allowance_type,
                        json.dumps(resolved_address_payload, ensure_ascii=False),
                        created_at,
                        now,
                        first_task_id,
                        str(task_id or "").strip(),
                        str(task_status or "").strip(),
                        len(merged_merchants),
                        json.dumps(merged_merchants, ensure_ascii=False),
                        json.dumps(merged_task_ids, ensure_ascii=False),
                        json.dumps(aggregate_summary, ensure_ascii=False),
                    ),
                )
                conn.commit()

    def get_daily_aggregate(
        self,
        *,
        meituan_user_id: str,
        address_id: str,
        allowance_type: str,
        date_key: str | None = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_user_id = str(meituan_user_id or "").strip()
        normalized_address_id = str(address_id or "").strip()
        normalized_allowance_type = str(allowance_type or "large").strip() or "large"
        effective_date_key = str(date_key or self._current_date_key()).strip()
        if not normalized_user_id or not normalized_address_id or not effective_date_key:
            return None
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT *
                    FROM allowance_daily_results
                    WHERE date_key = ? AND meituan_user_id = ? AND address_id = ? AND allowance_type = ?
                    LIMIT 1
                    """,
                    (effective_date_key, normalized_user_id, normalized_address_id, normalized_allowance_type),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return {
            "date_key": str(row["date_key"] or ""),
            "meituan_user_id": str(row["meituan_user_id"] or ""),
            "address_id": str(row["address_id"] or ""),
            "allowance_type": str(row["allowance_type"] or "large"),
            "resolved_address": self._parse_json_dict(row["resolved_address_json"]),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
            "first_task_id": str(row["first_task_id"] or ""),
            "last_task_id": str(row["last_task_id"] or ""),
            "last_task_status": str(row["last_task_status"] or ""),
            "merchant_count": int(row["merchant_count"] or 0),
            "merchants": self._parse_json_list(row["merchants_json"]),
            "task_ids": self._parse_json_scalar_list(row["task_ids_json"]),
            "summary": self._parse_json_dict(row["summary_json"]),
        }

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        meituan_user_id: str | None = None,
        address_id: str | None = None,
        started_at: int | None = None,
        finished_at: int | None = None,
        pages_requested: int | None = None,
        merchant_count: int | None = None,
        stop_reason: str | None = None,
        consecutive_empty_pages: int | None = None,
        error_message: str | None = None,
        summary: Dict[str, Any] | None = None,
        progress: list[Dict[str, Any]] | None = None,
        merchants: list[Dict[str, Any]] | None = None,
    ) -> None:
        fields: list[str] = []
        values: list[Any] = []

        def append(name: str, value: Any) -> None:
            fields.append(f"{name} = ?")
            values.append(value)

        if status is not None:
            append("status", status)
        if meituan_user_id is not None:
            append("meituan_user_id", str(meituan_user_id or "").strip())
        if address_id is not None:
            append("address_id", str(address_id or "").strip())
        if started_at is not None:
            append("started_at", int(started_at))
        if finished_at is not None:
            append("finished_at", int(finished_at))
        if pages_requested is not None:
            append("pages_requested", int(pages_requested))
        if merchant_count is not None:
            append("merchant_count", int(merchant_count))
        if stop_reason is not None:
            append("stop_reason", str(stop_reason))
        if consecutive_empty_pages is not None:
            append("consecutive_empty_pages", int(consecutive_empty_pages))
        if error_message is not None:
            append("error_message", str(error_message))
        if summary is not None:
            append("summary_json", json.dumps(summary, ensure_ascii=False))
        if progress is not None:
            append("progress_json", json.dumps(progress, ensure_ascii=False))
        if merchants is not None:
            append("merchants_json", json.dumps(merchants, ensure_ascii=False))

        if not fields:
            return

        values.append(task_id)
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                existing_task = None
                if status is not None:
                    cursor.execute(
                        """
                        SELECT created_at, allowance_type, status, summary_json
                        FROM allowance_tasks
                        WHERE task_id = ?
                        """,
                        (task_id,),
                    )
                    existing_task = cursor.fetchone()
                conn.execute(
                    f"UPDATE allowance_tasks SET {', '.join(fields)} WHERE task_id = ?",
                    values,
                )
                if existing_task is not None:
                    transition_summary = summary
                    if not isinstance(transition_summary, dict):
                        transition_summary = self._parse_json_dict(existing_task["summary_json"])
                    self._record_task_status_transition(
                        cursor,
                        created_at=int(existing_task["created_at"] or 0),
                        allowance_type=str(existing_task["allowance_type"] or "large"),
                        previous_status=str(existing_task["status"] or ""),
                        next_status=str(status or ""),
                        summary=transition_summary,
                    )
                conn.commit()

    def update_task_notification(self, task_id: str, *, status: str) -> None:
        normalized_status = str(status or "").strip() or "processed"
        processed_at = None if normalized_status in {"pending", "retry"} else int(time.time())
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE allowance_tasks
                    SET notification_status = ?, notification_processed_at = ?
                    WHERE task_id = ?
                    """,
                    (normalized_status, processed_at, str(task_id or "")),
                )
                conn.commit()

    def list_pending_notification_tasks(self, limit: int = 100) -> list[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM allowance_tasks
                    WHERE status = 'succeeded'
                      AND web_user_id IS NOT NULL
                      AND web_user_id > 0
                      AND notification_status IN ('pending', 'retry')
                    ORDER BY finished_at ASC, created_at ASC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit or 100), 1000)),),
                ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM allowance_tasks WHERE task_id = ?", (task_id,))
                row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def get_latest_task_by_meituan_user_id_and_address_id(
        self,
        meituan_user_id: str,
        address_id: str,
        allowance_type: str,
    ) -> Optional[Dict[str, Any]]:
        normalized_user_id = str(meituan_user_id or "").strip()
        normalized_address_id = str(address_id or "").strip()
        normalized_allowance_type = str(allowance_type or "large").strip() or "large"
        if not normalized_user_id or not normalized_address_id:
            return None
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT *
                    FROM allowance_tasks
                    WHERE meituan_user_id = ? AND address_id = ? AND allowance_type = ?
                    ORDER BY created_at DESC, task_id DESC
                    LIMIT 1
                    """,
                    (normalized_user_id, normalized_address_id, normalized_allowance_type),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def get_latest_task_by_token_fingerprint(self, token_fingerprint: str) -> Optional[Dict[str, Any]]:
        normalized_fingerprint = str(token_fingerprint or "").strip()
        if not normalized_fingerprint:
            return None
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT *
                    FROM allowance_tasks
                    WHERE token_fingerprint = ?
                    ORDER BY created_at DESC, task_id DESC
                    LIMIT 1
                    """,
                    (normalized_fingerprint,),
                )
                row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_task(row)

    def mark_incomplete_tasks_interrupted(self) -> int:
        interrupted_at = int(time.time())
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT task_id, started_at, created_at, allowance_type, status, summary_json
                    FROM allowance_tasks
                    WHERE status IN ('queued', 'running')
                    """
                )
                rows = cursor.fetchall()
                for row in rows:
                    summary = self._parse_json_dict(row["summary_json"])
                    summary["stop_reason"] = "interrupted_on_startup"
                    started_at = int(row["started_at"] or summary.get("started_at") or interrupted_at)
                    summary["started_at"] = started_at
                    summary["finished_at"] = interrupted_at
                    summary["duration_seconds"] = round(max(0.0, interrupted_at - started_at), 3)
                    cursor.execute(
                        """
                        UPDATE allowance_tasks
                        SET status = 'interrupted',
                            finished_at = ?,
                            stop_reason = 'interrupted_on_startup',
                            error_message = ?,
                            summary_json = ?
                        WHERE task_id = ?
                        """,
                        (
                            interrupted_at,
                            "服务重启，任务已中断",
                            json.dumps(summary, ensure_ascii=False),
                            row["task_id"],
                        ),
                    )
                    self._record_task_status_transition(
                        cursor,
                        created_at=int(row["created_at"] or interrupted_at),
                        allowance_type=str(row["allowance_type"] or "large"),
                        previous_status=str(row["status"] or ""),
                        next_status="interrupted",
                        summary=summary,
                    )
                conn.commit()
                return len(rows)

    def clear_all_tasks(self) -> int:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(1) FROM allowance_tasks")
                row = cursor.fetchone()
                deleted_count = int(row[0] or 0) if row else 0
                cursor.execute("DELETE FROM allowance_tasks")
                conn.commit()
                return deleted_count

    def clear_tasks_by_allowance_type(self, allowance_type: str) -> int:
        normalized_allowance_type = str(allowance_type or "large").strip() or "large"
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(1) FROM allowance_tasks WHERE allowance_type = ?",
                    (normalized_allowance_type,),
                )
                row = cursor.fetchone()
                deleted_count = int(row[0] or 0) if row else 0
                cursor.execute(
                    "DELETE FROM allowance_tasks WHERE allowance_type = ?",
                    (normalized_allowance_type,),
                )
                conn.commit()
                return deleted_count

    def clear_daily_results_by_allowance_type(self, allowance_type: str) -> int:
        normalized_allowance_type = str(allowance_type or "large").strip() or "large"
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(1) FROM allowance_daily_results WHERE allowance_type = ?",
                    (normalized_allowance_type,),
                )
                row = cursor.fetchone()
                deleted_count = int(row[0] or 0) if row else 0
                cursor.execute(
                    "DELETE FROM allowance_daily_results WHERE allowance_type = ?",
                    (normalized_allowance_type,),
                )
                conn.commit()
                return deleted_count

    def clear_daily_results_before_date(self, date_key: str) -> int:
        normalized_date_key = str(date_key or "").strip()
        if not normalized_date_key:
            return 0
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(1) FROM allowance_daily_results WHERE date_key < ?",
                    (normalized_date_key,),
                )
                row = cursor.fetchone()
                deleted_count = int(row[0] or 0) if row else 0
                cursor.execute(
                    "DELETE FROM allowance_daily_results WHERE date_key < ?",
                    (normalized_date_key,),
                )
                conn.commit()
                return deleted_count

    def get_daily_task_stats(self, date_key: str | None = None) -> list[Dict[str, Any]]:
        effective_date_key = str(date_key or self._current_date_key()).strip()
        if not effective_date_key:
            return []
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT date_key, allowance_type, task_count, queued_task_count,
                           running_task_count, succeeded_task_count, failed_task_count,
                           interrupted_task_count, relay_success_task_count,
                           relay_switched_task_count, relay_fallback_task_count, updated_at
                    FROM allowance_daily_task_stats
                    WHERE date_key = ?
                    """,
                    (effective_date_key,),
                )
                rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def clear_daily_task_stats_before_date(self, date_key: str) -> int:
        normalized_date_key = str(date_key or "").strip()
        if not normalized_date_key:
            return 0
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(1) FROM allowance_daily_task_stats WHERE date_key < ?",
                    (normalized_date_key,),
                )
                row = cursor.fetchone() or (0,)
                deleted_count = int(row[0] or 0)
                cursor.execute(
                    "DELETE FROM allowance_daily_task_stats WHERE date_key < ?",
                    (normalized_date_key,),
                )
                conn.commit()
                return deleted_count

    def clear_finished_tasks_before_timestamp(self, cutoff_ts: int) -> int:
        normalized_cutoff_ts = int(cutoff_ts or 0)
        if normalized_cutoff_ts <= 0:
            return 0
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT COUNT(1)
                    FROM allowance_tasks
                    WHERE created_at < ? AND status NOT IN ('queued', 'running')
                    """,
                    (normalized_cutoff_ts,),
                )
                row = cursor.fetchone()
                deleted_count = int(row[0] or 0) if row else 0
                cursor.execute(
                    """
                    DELETE FROM allowance_tasks
                    WHERE created_at < ? AND status NOT IN ('queued', 'running')
                    """,
                    (normalized_cutoff_ts,),
                )
                conn.commit()
                return deleted_count

    def clear_all_by_meituan_user_id(self, meituan_user_id: str) -> Dict[str, int]:
        normalized_user_id = str(meituan_user_id or "").strip()
        deleted = {
            "tasks": 0,
            "refresh_targets": 0,
            "daily_results": 0,
        }
        if not normalized_user_id:
            return deleted

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(1) FROM allowance_tasks WHERE meituan_user_id = ?",
                    (normalized_user_id,),
                )
                row = cursor.fetchone()
                deleted["tasks"] = int(row[0] or 0) if row else 0

                cursor.execute(
                    "SELECT COUNT(1) FROM allowance_refresh_targets WHERE meituan_user_id = ?",
                    (normalized_user_id,),
                )
                row = cursor.fetchone()
                deleted["refresh_targets"] = int(row[0] or 0) if row else 0

                cursor.execute(
                    "SELECT COUNT(1) FROM allowance_daily_results WHERE meituan_user_id = ?",
                    (normalized_user_id,),
                )
                row = cursor.fetchone()
                deleted["daily_results"] = int(row[0] or 0) if row else 0

                cursor.execute(
                    "DELETE FROM allowance_tasks WHERE meituan_user_id = ?",
                    (normalized_user_id,),
                )
                cursor.execute(
                    "DELETE FROM allowance_refresh_targets WHERE meituan_user_id = ?",
                    (normalized_user_id,),
                )
                cursor.execute(
                    "DELETE FROM allowance_daily_results WHERE meituan_user_id = ?",
                    (normalized_user_id,),
                )
                conn.commit()
        return deleted

    def _row_to_task(self, row: sqlite3.Row) -> Dict[str, Any]:
        summary = self._parse_json_dict(row["summary_json"])
        progress = self._parse_json_list(row["progress_json"])
        merchants = self._parse_json_list(row["merchants_json"])
        return {
            "task_id": str(row["task_id"]),
            "status": str(row["status"]),
            "allowance_type": str(row["allowance_type"] or "large"),
            "meituan_user_id": str(row["meituan_user_id"] or ""),
            "address_id": str(row["address_id"] or ""),
            "token_masked": str(row["token_masked"] or ""),
            "token_fingerprint": str(row["token_fingerprint"] or ""),
            "input_latitude": str(row["input_latitude"] or ""),
            "input_longitude": str(row["input_longitude"] or ""),
            "normalized_latitude": str(row["normalized_latitude"] or ""),
            "normalized_longitude": str(row["normalized_longitude"] or ""),
            "created_at": int(row["created_at"] or 0),
            "started_at": int(row["started_at"] or 0) or None,
            "finished_at": int(row["finished_at"] or 0) or None,
            "pages_requested": int(row["pages_requested"] or 0),
            "merchant_count": int(row["merchant_count"] or 0),
            "stop_reason": str(row["stop_reason"] or ""),
            "consecutive_empty_pages": int(row["consecutive_empty_pages"] or 0),
            "error_message": str(row["error_message"] or ""),
            "summary": summary,
            "progress": progress,
            "merchants": merchants,
            "web_user_id": int(row["web_user_id"] or 0) if "web_user_id" in row.keys() else 0,
            "web_token_id": int(row["web_token_id"] or 0) if "web_token_id" in row.keys() else 0,
            "task_source": str(row["task_source"] or "legacy") if "task_source" in row.keys() else "legacy",
            "address_name": str(row["address_name"] or "") if "address_name" in row.keys() else "",
            "notification_status": str(row["notification_status"] or "not_applicable") if "notification_status" in row.keys() else "not_applicable",
            "notification_processed_at": (
                int(row["notification_processed_at"] or 0) or None
                if "notification_processed_at" in row.keys()
                else None
            ),
        }

    def _parse_json_dict(self, value: Any) -> Dict[str, Any]:
        try:
            parsed = json.loads(str(value or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _parse_json_list(self, value: Any) -> list[Dict[str, Any]]:
        try:
            parsed = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def _parse_json_scalar_list(self, value: Any) -> list[Any]:
        try:
            parsed = json.loads(str(value or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []


_storage: MeituanAllowanceTaskStorage | None = None


def get_meituan_allowance_task_storage() -> MeituanAllowanceTaskStorage:
    global _storage
    if _storage is None:
        _storage = MeituanAllowanceTaskStorage()
    return _storage
