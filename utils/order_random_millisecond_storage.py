"""
订单随机毫秒存储
"""
from __future__ import annotations

import hashlib
import os
import random
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime

from utils.timezone_utils import get_timezone
from utils.path_utils import resolve_runtime_data_path


class OrderRandomMillisecondStorage:
    CLEANUP_INTERVAL_SECONDS = 6 * 3600
    DEFAULT_TIMEZONE = "Asia/Shanghai"
    CREATE_KIND = "create"
    ACCEPT_KIND = "accept"

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = os.fspath(resolve_runtime_data_path("order_leaderboard.db"))
        elif not os.path.isabs(db_path):
            db_path = os.fspath(resolve_runtime_data_path(db_path))
        self.db_path = db_path
        self._lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._last_cleanup_at = 0.0
        self._init_database()

    def _init_database(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if self._should_recreate_table(cursor):
                cursor.execute("DROP TABLE IF EXISTS order_random_millisecond")
                cursor.execute("DROP INDEX IF EXISTS idx_order_random_millisecond_service")
                cursor.execute("DROP INDEX IF EXISTS idx_order_random_millisecond_order")
            self._create_clean_table(cursor)
            conn.commit()

    def _should_recreate_table(self, cursor: sqlite3.Cursor) -> bool:
        cursor.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name = 'order_random_millisecond'
        """)
        if cursor.fetchone() is None:
            return False

        cursor.execute("PRAGMA table_info(order_random_millisecond)")
        columns = {row[1] for row in cursor.fetchall()}
        expected_columns = {
            "id",
            "service_order_id",
            "order_id",
            "create_random_millisecond",
            "accept_random_millisecond",
            "month_key",
            "created_at",
        }
        if "random_millisecond" in columns:
            return True
        if "updated_at" in columns:
            return True
        return columns != expected_columns

    def _create_clean_table(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS order_random_millisecond (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service_order_id TEXT,
                order_id TEXT,
                create_random_millisecond INTEGER NOT NULL,
                accept_random_millisecond INTEGER NOT NULL,
                month_key TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_order_random_millisecond_service
            ON order_random_millisecond(service_order_id, month_key)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_order_random_millisecond_order
            ON order_random_millisecond(order_id, month_key)
        """)

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def resolve_random_milliseconds(
        self,
        service_order_id: str | None = None,
        order_id: str | None = None,
        create_if_missing: bool = True,
    ) -> tuple[dict | None, str]:
        now = int(time.time())
        month_key = self._current_month_key(now)
        normalized_service_order_id = str(service_order_id or "").strip()
        normalized_order_id = str(order_id or "").strip()

        with self._lock:
            self._cleanup_if_needed_async(now, month_key)
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if normalized_service_order_id:
                    cursor.execute("""
                        SELECT *
                        FROM order_random_millisecond
                        WHERE service_order_id = ?
                          AND month_key = ?
                        ORDER BY id ASC
                        LIMIT 1
                    """, (normalized_service_order_id, month_key))
                    row = cursor.fetchone()
                    if row:
                        random_values = self._hydrate_row_random_values(row)
                        self._maybe_update_missing_order_keys(
                            cursor,
                            row_id=int(row["id"]),
                            service_order_id=normalized_service_order_id,
                            order_id=normalized_order_id,
                            now=now,
                            random_values=random_values,
                        )
                        conn.commit()
                        return random_values, "service_order_id"

                if normalized_order_id:
                    cursor.execute("""
                        SELECT *
                        FROM order_random_millisecond
                        WHERE order_id = ?
                          AND month_key = ?
                        ORDER BY id ASC
                        LIMIT 1
                    """, (normalized_order_id, month_key))
                    row = cursor.fetchone()
                    if row:
                        random_values = self._hydrate_row_random_values(row)
                        self._maybe_update_missing_order_keys(
                            cursor,
                            row_id=int(row["id"]),
                            service_order_id=normalized_service_order_id,
                            order_id=normalized_order_id,
                            now=now,
                            random_values=random_values,
                        )
                        conn.commit()
                        return random_values, "order_id"

                if not create_if_missing:
                    return None, "miss"

                create_random_millisecond = self._generate_random_millisecond()
                accept_random_millisecond = self._generate_random_millisecond(exclude=create_random_millisecond)
                cursor.execute("""
                    INSERT INTO order_random_millisecond (
                        service_order_id,
                        order_id,
                        create_random_millisecond,
                        accept_random_millisecond,
                        month_key,
                        created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    normalized_service_order_id or None,
                    normalized_order_id or None,
                    create_random_millisecond,
                    accept_random_millisecond,
                    month_key,
                    now,
                ))
                conn.commit()
                return {
                    "create_random_millisecond": int(create_random_millisecond),
                    "accept_random_millisecond": int(accept_random_millisecond),
                }, "created"

    def get_display_millisecond(
        self,
        kind: str,
        service_order_id: str | None = None,
        order_id: str | None = None,
        fallback_key: str | None = None,
        create_if_missing: bool = True,
    ) -> int:
        value, _ = self.resolve_random_milliseconds(
            service_order_id=service_order_id,
            order_id=order_id,
            create_if_missing=create_if_missing,
        )
        if value is not None:
            if kind == self.CREATE_KIND:
                return int(value["create_random_millisecond"])
            return int(value["accept_random_millisecond"])
        return self._fallback_random_millisecond(f"{kind}|{fallback_key or ''}")

    def _maybe_update_missing_order_keys(
        self,
        cursor: sqlite3.Cursor,
        row_id: int,
        service_order_id: str,
        order_id: str,
        now: int,
        random_values: dict,
    ) -> None:
        cursor.execute("""
            UPDATE order_random_millisecond
            SET service_order_id = CASE
                    WHEN TRIM(IFNULL(service_order_id, '')) = '' AND ? != '' THEN ?
                    ELSE service_order_id
                END,
                order_id = CASE
                    WHEN TRIM(IFNULL(order_id, '')) = '' AND ? != '' THEN ?
                    ELSE order_id
                END,
                create_random_millisecond = CASE
                    WHEN create_random_millisecond IS NULL THEN ?
                    ELSE create_random_millisecond
                END,
                accept_random_millisecond = CASE
                    WHEN accept_random_millisecond IS NULL THEN ?
                    ELSE accept_random_millisecond
                END
            WHERE id = ?
        """, (
            service_order_id,
            service_order_id,
            order_id,
            order_id,
            int(random_values["create_random_millisecond"]),
            int(random_values["accept_random_millisecond"]),
            row_id,
        ))

    def _hydrate_row_random_values(self, row: sqlite3.Row) -> dict:
        return {
            "create_random_millisecond": int(row["create_random_millisecond"]),
            "accept_random_millisecond": int(row["accept_random_millisecond"]),
        }

    def _cleanup_if_needed_async(self, now: int, month_key: str) -> None:
        if now - self._last_cleanup_at < self.CLEANUP_INTERVAL_SECONDS:
            return
        self._last_cleanup_at = float(now)

        def cleanup():
            with self._cleanup_lock:
                try:
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute(
                            "DELETE FROM order_random_millisecond WHERE month_key < ?",
                            (month_key,),
                        )
                        conn.commit()
                except Exception:
                    return

        thread = threading.Thread(target=cleanup, daemon=True)
        thread.start()

    def _current_month_key(self, now: int) -> str:
        zone = get_timezone(self.DEFAULT_TIMEZONE)
        return datetime.fromtimestamp(now, zone).strftime("%Y-%m")

    def _fallback_random_millisecond(self, key: str) -> int:
        normalized = str(key or "").strip()
        if not normalized:
            return 0
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return int(digest[:8], 16) % 1000

    def _generate_random_millisecond(self, exclude: int | None = None) -> int:
        generator = random.SystemRandom()
        value = generator.randrange(0, 1000)
        while exclude is not None and value == int(exclude):
            value = generator.randrange(0, 1000)
        return value


_order_random_millisecond_storage = OrderRandomMillisecondStorage()


def get_order_random_millisecond_storage() -> OrderRandomMillisecondStorage:
    return _order_random_millisecond_storage
