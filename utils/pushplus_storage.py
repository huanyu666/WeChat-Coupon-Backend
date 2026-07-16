from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from utils.path_utils import resolve_runtime_data_path


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class PushPlusNotificationStorage:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_path = os.fspath(resolve_runtime_data_path("pushplus_notifications.db"))
        elif not os.path.isabs(db_path):
            db_path = os.fspath(resolve_runtime_data_path(db_path))
        self.db_path = db_path
        self._lock = threading.RLock()
        self._init_database()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_database(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pushplus_user_bindings (
                    user_id INTEGER PRIMARY KEY,
                    friend_id TEXT NOT NULL UNIQUE,
                    friend_token TEXT NOT NULL UNIQUE,
                    nickname TEXT NOT NULL DEFAULT '',
                    head_img_url TEXT NOT NULL DEFAULT '',
                    token_invalid_enabled INTEGER NOT NULL DEFAULT 1,
                    merchant_match_enabled INTEGER NOT NULL DEFAULT 1,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    bound_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pushplus_bind_sessions (
                    binding_id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    nonce_hash TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    qr_image_url TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    completed_at INTEGER
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pushplus_bind_sessions_user
                ON pushplus_bind_sessions(user_id, created_at DESC)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pushplus_keywords (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    normalized_keyword TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    UNIQUE(user_id, normalized_keyword)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pushplus_merchant_dedupe (
                    date_key TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    address_id TEXT NOT NULL,
                    allowance_type TEXT NOT NULL,
                    merchant_key TEXT NOT NULL,
                    merchant_name TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(date_key, user_id, address_id, allowance_type, merchant_key)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pushplus_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    user_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    link_url TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'queued',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_attempt_at INTEGER NOT NULL DEFAULT 0,
                    expires_at INTEGER,
                    short_code TEXT NOT NULL DEFAULT '',
                    response_code INTEGER NOT NULL DEFAULT 0,
                    response_message TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    accepted_at INTEGER,
                    delivered_at INTEGER,
                    last_attempt_at INTEGER
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pushplus_jobs_dispatch
                ON pushplus_jobs(status, next_attempt_at, created_at)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pushplus_jobs_short_code
                ON pushplus_jobs(short_code)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pushplus_send_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL DEFAULT 0,
                    attempted_at INTEGER NOT NULL,
                    date_key TEXT NOT NULL,
                    response_code INTEGER NOT NULL DEFAULT 0,
                    accepted INTEGER NOT NULL DEFAULT 0,
                    short_code TEXT NOT NULL DEFAULT '',
                    response_message TEXT NOT NULL DEFAULT '',
                    delivery_status INTEGER,
                    delivery_error TEXT NOT NULL DEFAULT '',
                    delivery_checked_at INTEGER,
                    delivered_at INTEGER
                )
                """
            )
            # Existing deployments created this table before delivery diagnostics existed.
            attempt_columns = {
                str(row["name"])
                for row in cursor.execute("PRAGMA table_info(pushplus_send_attempts)").fetchall()
            }
            for column_name, definition in (
                ("short_code", "TEXT NOT NULL DEFAULT ''"),
                ("response_message", "TEXT NOT NULL DEFAULT ''"),
                ("delivery_status", "INTEGER"),
                ("delivery_error", "TEXT NOT NULL DEFAULT ''"),
                ("delivery_checked_at", "INTEGER"),
                ("delivered_at", "INTEGER"),
            ):
                if column_name not in attempt_columns:
                    cursor.execute(f"ALTER TABLE pushplus_send_attempts ADD COLUMN {column_name} {definition}")
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pushplus_send_attempts_time
                ON pushplus_send_attempts(attempted_at)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS pushplus_runtime_state (
                    state_key TEXT PRIMARY KEY,
                    state_json TEXT NOT NULL DEFAULT '{}',
                    updated_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            conn.commit()

    @staticmethod
    def date_key(timestamp: int | None = None) -> str:
        value = datetime.fromtimestamp(int(timestamp or time.time()), SHANGHAI_TZ)
        return value.strftime("%Y-%m-%d")

    @staticmethod
    def next_midnight_timestamp(timestamp: int | None = None) -> int:
        current = datetime.fromtimestamp(int(timestamp or time.time()), SHANGHAI_TZ)
        next_day = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return int(next_day.timestamp())

    def cleanup_expired(self) -> None:
        now = int(time.time())
        today = self.date_key(now)
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    UPDATE pushplus_bind_sessions
                    SET status = 'expired', error_message = '二维码已过期'
                    WHERE status = 'pending' AND expires_at <= ?
                    """,
                    (now,),
                )
                conn.execute("DELETE FROM pushplus_merchant_dedupe WHERE date_key < ?", (today,))
                conn.execute(
                    """
                    UPDATE pushplus_jobs
                    SET status = 'expired', updated_at = ?, error_message = '通知已过期'
                    WHERE status IN ('queued', 'retry_wait') AND expires_at IS NOT NULL AND expires_at <= ?
                    """,
                    (now, now),
                )
                conn.commit()

    def create_bind_session(
        self,
        *,
        binding_id: str,
        user_id: int,
        nonce_hash: str,
        qr_image_url: str,
        expires_at: int,
    ) -> dict[str, Any]:
        now = int(time.time())
        with self._lock:
            with self._connection() as conn:
                conn.execute(
                    """
                    UPDATE pushplus_bind_sessions
                    SET status = 'expired', error_message = '已生成新的绑定二维码'
                    WHERE user_id = ? AND status = 'pending'
                    """,
                    (int(user_id),),
                )
                conn.execute(
                    """
                    INSERT INTO pushplus_bind_sessions (
                        binding_id, user_id, nonce_hash, status, qr_image_url,
                        error_message, created_at, expires_at
                    ) VALUES (?, ?, ?, 'pending', ?, '', ?, ?)
                    """,
                    (binding_id, int(user_id), nonce_hash, qr_image_url, now, int(expires_at)),
                )
                conn.commit()
        return self.get_bind_session(binding_id=binding_id, user_id=user_id) or {}

    def get_bind_session(self, *, binding_id: str, user_id: int) -> dict[str, Any] | None:
        self.cleanup_expired()
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT binding_id, user_id, status, qr_image_url, error_message,
                       created_at, expires_at, completed_at
                FROM pushplus_bind_sessions
                WHERE binding_id = ? AND user_id = ?
                LIMIT 1
                """,
                (str(binding_id), int(user_id)),
            ).fetchone()
        return dict(row) if row else None

    def complete_bind_session(
        self,
        *,
        nonce_hash: str,
        friend_id: str,
        friend_token: str,
        nickname: str = "",
        head_img_url: str = "",
    ) -> dict[str, Any]:
        now = int(time.time())
        with self._lock:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                session = conn.execute(
                    "SELECT * FROM pushplus_bind_sessions WHERE nonce_hash = ? LIMIT 1",
                    (nonce_hash,),
                ).fetchone()
                if session is None:
                    conn.rollback()
                    return {"success": False, "status": "unknown", "error": "绑定二维码不存在"}
                if str(session["status"] or "") == "bound":
                    conn.rollback()
                    return {"success": True, "status": "bound", "user_id": int(session["user_id"])}
                if str(session["status"] or "") != "pending" or int(session["expires_at"] or 0) <= now:
                    conn.execute(
                        "UPDATE pushplus_bind_sessions SET status = 'expired', error_message = '二维码已过期' WHERE binding_id = ?",
                        (session["binding_id"],),
                    )
                    conn.commit()
                    return {"success": False, "status": "expired", "error": "绑定二维码已过期"}

                user_id = int(session["user_id"])
                existing_friend = conn.execute(
                    """
                    SELECT user_id FROM pushplus_user_bindings
                    WHERE friend_id = ? OR friend_token = ?
                    LIMIT 1
                    """,
                    (friend_id, friend_token),
                ).fetchone()
                existing_user_binding = conn.execute(
                    "SELECT friend_id, friend_token FROM pushplus_user_bindings WHERE user_id = ? LIMIT 1",
                    (user_id,),
                ).fetchone()
                conflict = (
                    existing_friend is not None and int(existing_friend["user_id"]) != user_id
                ) or (
                    existing_user_binding is not None
                    and (
                        str(existing_user_binding["friend_id"] or "") != friend_id
                        or str(existing_user_binding["friend_token"] or "") != friend_token
                    )
                )
                if conflict:
                    conn.execute(
                        """
                        UPDATE pushplus_bind_sessions
                        SET status = 'conflict', error_message = '该微信已绑定其他平台账号', completed_at = ?
                        WHERE binding_id = ?
                        """,
                        (now, session["binding_id"]),
                    )
                    conn.commit()
                    return {"success": False, "status": "conflict", "error": "该微信已绑定其他平台账号"}

                conn.execute(
                    """
                    INSERT INTO pushplus_user_bindings (
                        user_id, friend_id, friend_token, nickname, head_img_url,
                        token_invalid_enabled, merchant_match_enabled, is_active,
                        bound_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, 1, 1, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        friend_id = excluded.friend_id,
                        friend_token = excluded.friend_token,
                        nickname = excluded.nickname,
                        head_img_url = excluded.head_img_url,
                        is_active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (user_id, friend_id, friend_token, nickname, head_img_url, now, now),
                )
                conn.execute(
                    """
                    UPDATE pushplus_bind_sessions
                    SET status = 'bound', error_message = '', completed_at = ?
                    WHERE binding_id = ?
                    """,
                    (now, session["binding_id"]),
                )
                conn.commit()
                return {"success": True, "status": "bound", "user_id": user_id}

    def get_binding(self, user_id: int) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM pushplus_user_bindings WHERE user_id = ? AND is_active = 1 LIMIT 1",
                (int(user_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_user_settings(self, user_id: int) -> dict[str, Any]:
        binding = self.get_binding(user_id)
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT keyword, normalized_keyword
                FROM pushplus_keywords
                WHERE user_id = ?
                ORDER BY id ASC
                """,
                (int(user_id),),
            ).fetchall()
        return {
            "binding": binding,
            "keywords": [dict(row) for row in rows],
        }

    def update_preferences(
        self,
        *,
        user_id: int,
        token_invalid_enabled: bool,
        merchant_match_enabled: bool,
    ) -> bool:
        now = int(time.time())
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pushplus_user_bindings
                SET token_invalid_enabled = ?, merchant_match_enabled = ?, updated_at = ?
                WHERE user_id = ? AND is_active = 1
                """,
                (int(bool(token_invalid_enabled)), int(bool(merchant_match_enabled)), now, int(user_id)),
            )
            conn.commit()
            return cursor.rowcount > 0

    def replace_keywords(self, *, user_id: int, keywords: list[tuple[str, str]]) -> None:
        now = int(time.time())
        with self._lock:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM pushplus_keywords WHERE user_id = ?", (int(user_id),))
                conn.executemany(
                    """
                    INSERT INTO pushplus_keywords (user_id, keyword, normalized_keyword, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(int(user_id), keyword, normalized, now) for keyword, normalized in keywords],
                )
                conn.commit()

    def delete_binding(self, user_id: int) -> bool:
        with self._lock:
            with self._connection() as conn:
                cursor = conn.execute("DELETE FROM pushplus_user_bindings WHERE user_id = ?", (int(user_id),))
                conn.execute("DELETE FROM pushplus_keywords WHERE user_id = ?", (int(user_id),))
                conn.execute(
                    """
                    UPDATE pushplus_jobs
                    SET status = 'expired', updated_at = ?, error_message = '用户已解绑微信推送'
                    WHERE user_id = ? AND status IN ('queued', 'retry_wait')
                    """,
                    (int(time.time()), int(user_id)),
                )
                conn.commit()
                return cursor.rowcount > 0

    def enqueue_job(
        self,
        *,
        event_key: str,
        user_id: int,
        event_type: str,
        title: str,
        content: str,
        link_url: str = "",
        expires_at: int | None = None,
    ) -> int | None:
        now = int(time.time())
        try:
            with self._connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO pushplus_jobs (
                        event_key, user_id, event_type, title, content, link_url,
                        status, next_attempt_at, expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                    """,
                    (
                        event_key,
                        int(user_id),
                        event_type,
                        title,
                        content,
                        link_url,
                        now,
                        int(expires_at) if expires_at else None,
                        now,
                        now,
                    ),
                )
                conn.commit()
                return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def reserve_and_enqueue_merchant_matches(
        self,
        *,
        event_key: str,
        user_id: int,
        date_key: str,
        address_id: str,
        allowance_type: str,
        merchants: list[dict[str, Any]],
        title: str,
        content_builder: Callable[[list[dict[str, Any]]], str],
        link_url: str,
        expires_at: int,
    ) -> tuple[int | None, list[dict[str, Any]]]:
        now = int(time.time())
        new_merchants: list[dict[str, Any]] = []
        with self._lock:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                for merchant in merchants:
                    cursor = conn.execute(
                        """
                        INSERT OR IGNORE INTO pushplus_merchant_dedupe (
                            date_key, user_id, address_id, allowance_type,
                            merchant_key, merchant_name, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            date_key,
                            int(user_id),
                            address_id,
                            allowance_type,
                            str(merchant.get("merchant_key") or ""),
                            str(merchant.get("merchant_name") or ""),
                            now,
                        ),
                    )
                    if cursor.rowcount > 0:
                        new_merchants.append(merchant)
                if not new_merchants:
                    conn.commit()
                    return None, []
                content = content_builder(new_merchants)
                try:
                    cursor = conn.execute(
                        """
                        INSERT INTO pushplus_jobs (
                            event_key, user_id, event_type, title, content, link_url,
                            status, next_attempt_at, expires_at, created_at, updated_at
                        ) VALUES (?, ?, 'merchant_match', ?, ?, ?, 'queued', ?, ?, ?, ?)
                        """,
                        (event_key, int(user_id), title, content, link_url, now, int(expires_at), now, now),
                    )
                    job_id = int(cursor.lastrowid)
                except sqlite3.IntegrityError:
                    job_id = None
                conn.commit()
                return job_id, new_merchants

    def recover_stuck_jobs(self) -> int:
        now = int(time.time())
        cutoff = now - 300
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pushplus_jobs
                SET status = 'retry_wait', next_attempt_at = ?, updated_at = ?,
                    error_message = '服务重启后恢复发送'
                WHERE status = 'sending' AND COALESCE(last_attempt_at, updated_at) <= ?
                """,
                (now, now, cutoff),
            )
            conn.commit()
            return cursor.rowcount

    def claim_next_job(self) -> dict[str, Any] | None:
        self.cleanup_expired()
        now = int(time.time())
        with self._lock:
            with self._connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """
                    SELECT * FROM pushplus_jobs
                    WHERE status IN ('queued', 'retry_wait')
                      AND next_attempt_at <= ?
                      AND (expires_at IS NULL OR expires_at > ?)
                    ORDER BY CASE event_type WHEN 'token_invalid' THEN 0 ELSE 1 END,
                             created_at ASC, id ASC
                    LIMIT 1
                    """,
                    (now, now),
                ).fetchone()
                if row is None:
                    conn.commit()
                    return None
                conn.execute(
                    """
                    UPDATE pushplus_jobs
                    SET status = 'sending', updated_at = ?
                    WHERE id = ?
                    """,
                    (now, int(row["id"])),
                )
                conn.commit()
                payload = dict(row)
                payload["status"] = "sending"
                return payload

    def begin_job_attempt(self, job_id: int) -> int:
        now = int(time.time())
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE pushplus_jobs
                SET attempt_count = attempt_count + 1, last_attempt_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, int(job_id)),
            )
            row = conn.execute("SELECT attempt_count FROM pushplus_jobs WHERE id = ?", (int(job_id),)).fetchone()
            conn.commit()
        return int(row["attempt_count"] or 0) if row else 0

    def get_job_recipient(self, user_id: int) -> dict[str, Any] | None:
        return self.get_binding(user_id)

    def defer_job(self, job_id: int, *, next_attempt_at: int, error_message: str) -> None:
        now = int(time.time())
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE pushplus_jobs
                SET status = 'retry_wait', next_attempt_at = ?, updated_at = ?, error_message = ?
                WHERE id = ?
                """,
                (int(next_attempt_at), now, str(error_message or ""), int(job_id)),
            )
            conn.commit()

    def mark_job_accepted(
        self,
        job_id: int,
        *,
        short_code: str,
        response_code: int,
        response_message: str,
    ) -> None:
        now = int(time.time())
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE pushplus_jobs
                SET status = 'accepted', short_code = ?, response_code = ?,
                    response_message = ?, error_message = '', accepted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (short_code, int(response_code), response_message, now, now, int(job_id)),
            )
            conn.commit()

    def mark_job_delivered(self, job_id: int, *, response_message: str = "") -> None:
        now = int(time.time())
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE pushplus_jobs
                SET status = 'delivered', delivered_at = ?, updated_at = ?,
                    response_message = CASE WHEN ? != '' THEN ? ELSE response_message END,
                    error_message = ''
                WHERE id = ?
                """,
                (now, now, response_message, response_message, int(job_id)),
            )
            conn.commit()

    def mark_job_failed(
        self,
        job_id: int,
        *,
        error_message: str,
        response_code: int = 0,
        response_message: str = "",
    ) -> None:
        now = int(time.time())
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE pushplus_jobs
                SET status = 'failed', error_message = ?, response_code = ?,
                    response_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (error_message, int(response_code), response_message, now, int(job_id)),
            )
            conn.commit()

    def update_job_delivery_by_short_code(
        self,
        *,
        short_code: str,
        delivered: bool,
        error_message: str = "",
    ) -> bool:
        now = int(time.time())
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pushplus_jobs
                SET status = ?, delivered_at = ?, updated_at = ?, error_message = ?
                WHERE short_code = ? AND status IN ('accepted', 'sending')
                """,
                (
                    "delivered" if delivered else "failed",
                    now if delivered else None,
                    now,
                    "" if delivered else str(error_message or "消息投递失败"),
                    short_code,
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_stale_accepted_jobs(self, *, older_than_seconds: int = 120, limit: int = 10) -> list[dict[str, Any]]:
        cutoff = int(time.time()) - max(30, int(older_than_seconds))
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pushplus_jobs
                WHERE status = 'accepted' AND short_code != '' AND updated_at <= ?
                ORDER BY updated_at ASC LIMIT ?
                """,
                (cutoff, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def touch_job(self, job_id: int) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE pushplus_jobs SET updated_at = ? WHERE id = ?",
                (int(time.time()), int(job_id)),
            )
            conn.commit()

    def record_send_attempt(self, *, job_id: int, response_code: int = 0, accepted: bool = False) -> int:
        now = int(time.time())
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO pushplus_send_attempts (job_id, attempted_at, date_key, response_code, accepted)
                VALUES (?, ?, ?, ?, ?)
                """,
                (int(job_id), now, self.date_key(now), int(response_code), int(bool(accepted))),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def update_send_attempt(
        self,
        attempt_id: int,
        *,
        response_code: int,
        accepted: bool,
        short_code: str = "",
        response_message: str = "",
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                UPDATE pushplus_send_attempts
                SET response_code = ?, accepted = ?,
                    short_code = CASE WHEN ? != '' THEN ? ELSE short_code END,
                    response_message = CASE WHEN ? != '' THEN ? ELSE response_message END
                WHERE id = ?
                """,
                (
                    int(response_code),
                    int(bool(accepted)),
                    str(short_code or ""),
                    str(short_code or ""),
                    str(response_message or ""),
                    str(response_message or ""),
                    int(attempt_id),
                ),
            )
            conn.commit()

    def update_send_attempt_delivery_by_short_code(
        self,
        *,
        short_code: str,
        delivery_status: int,
        error_message: str = "",
    ) -> bool:
        if not str(short_code or "").strip():
            return False
        now = int(time.time())
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE pushplus_send_attempts
                SET delivery_status = ?,
                    delivery_error = ?,
                    delivery_checked_at = ?,
                    delivered_at = CASE WHEN ? = 2 THEN ? ELSE delivered_at END
                WHERE short_code = ? AND accepted = 1
                """,
                (
                    int(delivery_status),
                    "" if int(delivery_status) == 2 else str(error_message or "PushPlus 投递失败"),
                    now,
                    int(delivery_status),
                    now,
                    str(short_code).strip(),
                ),
            )
            conn.commit()
            return cursor.rowcount > 0

    def list_stale_admin_test_attempts(self, *, older_than_seconds: int = 120, limit: int = 3) -> list[dict[str, Any]]:
        cutoff = int(time.time()) - max(30, int(older_than_seconds))
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM pushplus_send_attempts
                WHERE job_id = 0 AND accepted = 1 AND short_code != ''
                  AND (delivery_status IS NULL OR delivery_status NOT IN (2, 3))
                  AND attempted_at <= ?
                ORDER BY attempted_at ASC LIMIT ?
                """,
                (cutoff, max(1, min(int(limit), 100))),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_rate_limit_state(self) -> dict[str, int]:
        now = int(time.time())
        date_key = self.date_key(now)
        with self._connection() as conn:
            ten_second_count = int(
                conn.execute(
                    "SELECT COUNT(1) FROM pushplus_send_attempts WHERE attempted_at > ?",
                    (now - 10,),
                ).fetchone()[0]
                or 0
            )
            minute_count = int(
                conn.execute(
                    "SELECT COUNT(1) FROM pushplus_send_attempts WHERE attempted_at > ?",
                    (now - 60,),
                ).fetchone()[0]
                or 0
            )
            daily_count = int(
                conn.execute(
                    "SELECT COUNT(1) FROM pushplus_send_attempts WHERE date_key = ?",
                    (date_key,),
                ).fetchone()[0]
                or 0
            )
            oldest_minute = conn.execute(
                "SELECT MIN(attempted_at) FROM pushplus_send_attempts WHERE attempted_at > ?",
                (now - 60,),
            ).fetchone()[0]
            oldest_ten_second = conn.execute(
                "SELECT MIN(attempted_at) FROM pushplus_send_attempts WHERE attempted_at > ?",
                (now - 10,),
            ).fetchone()[0]
        return {
            "ten_second_count": ten_second_count,
            "minute_count": minute_count,
            "ten_second_reset_at": int(oldest_ten_second or now) + 10,
            "daily_count": daily_count,
            "minute_reset_at": int(oldest_minute or now) + 60,
            "daily_reset_at": self.next_midnight_timestamp(now),
        }

    def set_runtime_state(self, key: str, value: dict[str, Any]) -> None:
        now = int(time.time())
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO pushplus_runtime_state (state_key, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(state_key) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at
                """,
                (str(key), json.dumps(value or {}, ensure_ascii=False), now),
            )
            conn.commit()

    def get_runtime_state(self, key: str) -> dict[str, Any]:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT state_json, updated_at FROM pushplus_runtime_state WHERE state_key = ? LIMIT 1",
                (str(key),),
            ).fetchone()
        if row is None:
            return {}
        try:
            payload = json.loads(str(row["state_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["updated_at"] = int(row["updated_at"] or 0)
        return payload

    def get_stats(self) -> dict[str, Any]:
        today = self.date_key()
        with self._connection() as conn:
            bound_count = int(
                conn.execute("SELECT COUNT(1) FROM pushplus_user_bindings WHERE is_active = 1").fetchone()[0]
                or 0
            )
            status_rows = conn.execute(
                """
                SELECT status, COUNT(1) AS count
                FROM pushplus_jobs
                WHERE created_at >= ?
                GROUP BY status
                """,
                (int(datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=SHANGHAI_TZ).timestamp()),),
            ).fetchall()
            queued_count = int(
                conn.execute(
                    "SELECT COUNT(1) FROM pushplus_jobs WHERE status IN ('queued', 'retry_wait', 'sending')"
                ).fetchone()[0]
                or 0
            )
            accepted_attempt_count = int(
                conn.execute(
                    "SELECT COUNT(1) FROM pushplus_send_attempts WHERE date_key = ? AND accepted = 1",
                    (today,),
                ).fetchone()[0]
                or 0
            )
            delivery_attempt_rows = conn.execute(
                """
                SELECT delivery_status, COUNT(1) AS count
                FROM pushplus_send_attempts
                WHERE date_key = ? AND delivery_status IN (2, 3)
                GROUP BY delivery_status
                """,
                (today,),
            ).fetchall()
            failed_before_acceptance_count = int(
                conn.execute(
                    """
                    SELECT COUNT(1) FROM pushplus_jobs
                    WHERE status = 'failed' AND short_code = '' AND created_at >= ?
                    """,
                    (int(datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=SHANGHAI_TZ).timestamp()),),
                ).fetchone()[0]
                or 0
            )
            latest_admin_test_row = conn.execute(
                """
                SELECT attempted_at, accepted, response_code, response_message,
                       short_code, delivery_status, delivery_error,
                       delivery_checked_at, delivered_at
                FROM pushplus_send_attempts
                WHERE job_id = 0
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        by_status = {str(row["status"]): int(row["count"] or 0) for row in status_rows}
        delivery_by_status = {
            int(row["delivery_status"]): int(row["count"] or 0)
            for row in delivery_attempt_rows
            if row["delivery_status"] is not None
        }
        rate = self.get_rate_limit_state()
        runtime = self.get_runtime_state("dispatcher")
        return {
            "bound_user_count": bound_count,
            "queued_count": queued_count,
            "accepted_today": accepted_attempt_count,
            "delivered_today": delivery_by_status.get(2, 0),
            "failed_today": delivery_by_status.get(3, 0) + failed_before_acceptance_count,
            "expired_today": by_status.get("expired", 0),
            "send_attempts_today": rate["daily_count"],
            "send_attempts_last_minute": rate["minute_count"],
            "runtime": runtime,
            "latest_admin_test": dict(latest_admin_test_row) if latest_admin_test_row is not None else None,
        }


_storage: PushPlusNotificationStorage | None = None


def get_pushplus_notification_storage() -> PushPlusNotificationStorage:
    global _storage
    if _storage is None:
        _storage = PushPlusNotificationStorage()
    return _storage
