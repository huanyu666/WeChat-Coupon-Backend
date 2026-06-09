from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from utils.path_utils import resolve_runtime_data_path

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
ANNOUNCEMENT_TARGET_ROLES = {"all", "user", "admin"}
ANNOUNCEMENT_SURFACES = {"login", "query", "admin"}
ANNOUNCEMENT_STATUSES = {"draft", "published", "offline"}


def _now_ts() -> int:
    return int(time.time())


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_priority(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_non_negative_int(value: Any, default: int = 0) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return normalized if normalized >= 0 else default


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value).lower()
    return text if text in ANNOUNCEMENT_STATUSES else "draft"


def _normalize_target_role(value: Any) -> str:
    text = _normalize_text(value).lower()
    return text if text in ANNOUNCEMENT_TARGET_ROLES else "all"


def _normalize_surface_list(value: Any) -> list[str]:
    items: list[str]
    if isinstance(value, (list, tuple, set)):
        items = [str(item or "").strip().lower() for item in value]
    elif isinstance(value, str):
        items = [part.strip().lower() for part in value.split(",")]
    else:
        items = []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in ANNOUNCEMENT_SURFACES or item in seen:
            continue
        normalized.append(item)
        seen.add(item)
    return normalized


def _parse_local_datetime_to_ts(value: Any) -> int | None:
    text = _normalize_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(text, fmt)
            aware = dt.replace(tzinfo=SHANGHAI_TZ)
            return int(aware.timestamp())
        except ValueError:
            continue
    return None


def _format_ts_for_display(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")


def _format_ts_for_form(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=SHANGHAI_TZ).strftime("%Y-%m-%dT%H:%M")


class WebAnnouncementStorage:
    def __init__(self, db_path: str | None = None):
        if db_path is None:
            storage_dir = resolve_runtime_data_path("web_announcements")
            storage_dir.mkdir(parents=True, exist_ok=True)
            db_path = os.fspath(storage_dir / "web_announcements.db")
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
                CREATE TABLE IF NOT EXISTS announcements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL DEFAULT '',
                    body TEXT NOT NULL DEFAULT '',
                    link_url TEXT NOT NULL DEFAULT '',
                    link_text TEXT NOT NULL DEFAULT '',
                    target_role TEXT NOT NULL DEFAULT 'all',
                    surfaces_json TEXT NOT NULL DEFAULT '[]',
                    priority INTEGER NOT NULL DEFAULT 0,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    show_as_popup INTEGER NOT NULL DEFAULT 0,
                    popup_close_delay_seconds INTEGER NOT NULL DEFAULT 0,
                    popup_repeat_always INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'draft',
                    start_at INTEGER,
                    end_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    created_by TEXT NOT NULL DEFAULT '',
                    updated_by TEXT NOT NULL DEFAULT ''
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS announcement_reads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    announcement_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    read_at INTEGER NOT NULL,
                    UNIQUE(announcement_id, user_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_announcements_status_time
                ON announcements(status, start_at, end_at)
                """
            )
            self._ensure_column_exists(
                conn,
                "announcements",
                "sort_order",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column_exists(
                conn,
                "announcements",
                "popup_close_delay_seconds",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column_exists(
                conn,
                "announcements",
                "popup_repeat_always",
                "INTEGER NOT NULL DEFAULT 0",
            )
            cursor.execute(
                """
                UPDATE announcements
                SET sort_order = id
                WHERE COALESCE(sort_order, 0) = 0
                """
            )
            cursor.execute(
                """
                DROP INDEX IF EXISTS idx_announcements_priority
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_announcements_priority
                ON announcements(sort_order DESC, priority DESC, start_at DESC, id DESC)
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_announcement_reads_user
                ON announcement_reads(user_id, announcement_id)
                """
            )
            conn.commit()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            conn.commit()

    def _ensure_column_exists(self, conn: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> None:
        cursor = conn.cursor()
        cursor.execute(f"PRAGMA table_info({table_name})")
        existing_columns = {str(row["name"] or "").strip() for row in (cursor.fetchall() or [])}
        if column_name in existing_columns:
            return
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
        conn.commit()

    def _normalize_sort_orders_in_connection(self, conn: sqlite3.Connection, operator: str = "") -> None:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id
            FROM announcements
            ORDER BY sort_order DESC, priority DESC, COALESCE(start_at, 0) DESC, id DESC
            """
        )
        rows = cursor.fetchall() or []
        if not rows:
            return
        now_ts = _now_ts()
        total = len(rows)
        operator_name = str(operator or "").strip()
        for index, row in enumerate(rows):
            announcement_id = int(row["id"] or 0)
            normalized_sort = total - index
            cursor.execute(
                """
                UPDATE announcements
                SET sort_order = ?, updated_at = ?, updated_by = COALESCE(NULLIF(?, ''), updated_by)
                WHERE id = ?
                """,
                (normalized_sort, now_ts, operator_name, announcement_id),
            )
        conn.commit()

    def _serialize_row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        surfaces = []
        try:
            decoded = json.loads(str(row["surfaces_json"] or "[]"))
            if isinstance(decoded, list):
                surfaces = _normalize_surface_list(decoded)
        except Exception:
            surfaces = []
        item = {
            "id": int(row["id"] or 0),
            "title": str(row["title"] or ""),
            "body": str(row["body"] or ""),
            "link_url": str(row["link_url"] or ""),
            "link_text": str(row["link_text"] or ""),
            "target_role": str(row["target_role"] or "all"),
            "surfaces": surfaces,
            "priority": int(row["priority"] or 0),
            "sort_order": int(row["sort_order"] or 0),
            "show_as_popup": bool(int(row["show_as_popup"] or 0)),
            "popup_close_delay_seconds": int(row["popup_close_delay_seconds"] or 0),
            "popup_repeat_always": bool(int(row["popup_repeat_always"] or 0)),
            "status": str(row["status"] or "draft"),
            "start_at": int(row["start_at"] or 0) if row["start_at"] is not None else None,
            "end_at": int(row["end_at"] or 0) if row["end_at"] is not None else None,
            "start_at_text": _format_ts_for_display(row["start_at"]),
            "end_at_text": _format_ts_for_display(row["end_at"]),
            "start_at_form": _format_ts_for_form(row["start_at"]),
            "end_at_form": _format_ts_for_form(row["end_at"]),
            "created_at": int(row["created_at"] or 0),
            "updated_at": int(row["updated_at"] or 0),
            "created_at_text": _format_ts_for_display(row["created_at"]),
            "updated_at_text": _format_ts_for_display(row["updated_at"]),
            "created_by": str(row["created_by"] or ""),
            "updated_by": str(row["updated_by"] or ""),
        }
        item["is_active_now"] = self.is_active_item(item)
        item["surface_labels"] = ",".join(item["surfaces"])
        return item

    def is_active_item(self, item: dict[str, Any], now_ts: int | None = None) -> bool:
        current_ts = _now_ts() if now_ts is None else int(now_ts)
        if str(item.get("status") or "") != "published":
            return False
        start_at = item.get("start_at")
        end_at = item.get("end_at")
        if start_at not in (None, 0, "") and current_ts < int(start_at):
            return False
        if end_at not in (None, 0, "") and current_ts > int(end_at):
            return False
        return True

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        title = _normalize_text(payload.get("title"))
        body = _normalize_text(payload.get("body"))
        link_url = _normalize_text(payload.get("link_url"))
        link_text = _normalize_text(payload.get("link_text"))
        target_role = _normalize_target_role(payload.get("target_role"))
        surfaces = _normalize_surface_list(payload.get("surfaces"))
        priority = _normalize_priority(payload.get("priority"))
        sort_order = _normalize_priority(payload.get("sort_order"))
        show_as_popup = _normalize_bool(payload.get("show_as_popup"))
        popup_close_delay_seconds = _normalize_non_negative_int(payload.get("popup_close_delay_seconds"), 0)
        popup_repeat_always = _normalize_bool(payload.get("popup_repeat_always"))
        status = _normalize_status(payload.get("status"))
        start_at = _parse_local_datetime_to_ts(payload.get("start_at"))
        end_at = _parse_local_datetime_to_ts(payload.get("end_at"))

        if not title:
            raise ValueError("公告标题不能为空")
        if not body:
            raise ValueError("公告正文不能为空")
        if not surfaces:
            raise ValueError("至少选择一个展示页面")
        if end_at is not None and start_at is not None and end_at < start_at:
            raise ValueError("结束时间不能早于开始时间")
        if link_url and not link_text:
            link_text = "查看详情"

        return {
            "title": title,
            "body": body,
            "link_url": link_url,
            "link_text": link_text,
            "target_role": target_role,
            "surfaces": surfaces,
            "priority": priority,
            "sort_order": sort_order,
            "show_as_popup": show_as_popup,
            "popup_close_delay_seconds": popup_close_delay_seconds,
            "popup_repeat_always": popup_repeat_always,
            "status": status,
            "start_at": start_at,
            "end_at": end_at,
        }

    def create_announcement(self, payload: dict[str, Any], operator: str) -> dict[str, Any]:
        normalized = self.validate_payload(payload)
        now_ts = _now_ts()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            if int(normalized["sort_order"]) == 0:
                cursor.execute("SELECT COALESCE(MAX(sort_order), 0) FROM announcements")
                row = cursor.fetchone()
                current_max_sort = int(row[0] or 0) if row else 0
                normalized["sort_order"] = current_max_sort + 1
            cursor.execute(
                """
                INSERT INTO announcements (
                    title, body, link_url, link_text, target_role, surfaces_json,
                    priority, sort_order, show_as_popup, popup_close_delay_seconds, popup_repeat_always,
                    status, start_at, end_at,
                    created_at, updated_at, created_by, updated_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["title"],
                    normalized["body"],
                    normalized["link_url"],
                    normalized["link_text"],
                    normalized["target_role"],
                    json.dumps(normalized["surfaces"], ensure_ascii=False),
                    int(normalized["priority"]),
                    int(normalized["sort_order"]),
                    1 if normalized["show_as_popup"] else 0,
                    int(normalized["popup_close_delay_seconds"]),
                    1 if normalized["popup_repeat_always"] else 0,
                    normalized["status"],
                    normalized["start_at"],
                    normalized["end_at"],
                    now_ts,
                    now_ts,
                    str(operator or "").strip(),
                    str(operator or "").strip(),
                ),
            )
            announcement_id = int(cursor.lastrowid or 0)
            conn.commit()
            self._normalize_sort_orders_in_connection(conn, operator)
        return self.get_announcement(announcement_id) or {}

    def update_announcement(self, announcement_id: int, payload: dict[str, Any], operator: str) -> dict[str, Any] | None:
        normalized = self.validate_payload(payload)
        now_ts = _now_ts()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE announcements
                SET title = ?, body = ?, link_url = ?, link_text = ?, target_role = ?, surfaces_json = ?,
                    priority = ?, sort_order = ?, show_as_popup = ?, popup_close_delay_seconds = ?,
                    popup_repeat_always = ?, status = ?, start_at = ?, end_at = ?, updated_at = ?, updated_by = ?
                WHERE id = ?
                """,
                (
                    normalized["title"],
                    normalized["body"],
                    normalized["link_url"],
                    normalized["link_text"],
                    normalized["target_role"],
                    json.dumps(normalized["surfaces"], ensure_ascii=False),
                    int(normalized["priority"]),
                    int(normalized["sort_order"]),
                    1 if normalized["show_as_popup"] else 0,
                    int(normalized["popup_close_delay_seconds"]),
                    1 if normalized["popup_repeat_always"] else 0,
                    normalized["status"],
                    normalized["start_at"],
                    normalized["end_at"],
                    now_ts,
                    str(operator or "").strip(),
                    int(announcement_id),
                ),
            )
            conn.commit()
            if cursor.rowcount <= 0:
                return None
            self._normalize_sort_orders_in_connection(conn, operator)
        return self.get_announcement(announcement_id)

    def get_announcement(self, announcement_id: int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM announcements WHERE id = ? LIMIT 1", (int(announcement_id),))
            return self._serialize_row(cursor.fetchone())

    def delete_announcement(self, announcement_id: int) -> bool:
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM announcement_reads WHERE announcement_id = ?", (int(announcement_id),))
            cursor.execute("DELETE FROM announcements WHERE id = ?", (int(announcement_id),))
            conn.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                self._normalize_sort_orders_in_connection(conn)
            return deleted

    def set_status(self, announcement_id: int, status: str, operator: str) -> dict[str, Any] | None:
        normalized_status = _normalize_status(status)
        now_ts = _now_ts()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE announcements
                SET status = ?, updated_at = ?, updated_by = ?
                WHERE id = ?
                """,
                (normalized_status, now_ts, str(operator or "").strip(), int(announcement_id)),
            )
            conn.commit()
            if cursor.rowcount <= 0:
                return None
        return self.get_announcement(announcement_id)

    def list_announcements(
        self,
        *,
        search: str = "",
        status: str = "",
        target_role: str = "",
        surface: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        safe_page = max(1, int(page or 1))
        safe_page_size = min(200, max(1, int(page_size or 20)))
        offset = (safe_page - 1) * safe_page_size
        where_clauses: list[str] = []
        params: list[Any] = []

        search_value = _normalize_text(search)
        status_value = _normalize_status(status) if _normalize_text(status) else ""
        target_value = _normalize_target_role(target_role) if _normalize_text(target_role) else ""
        surface_value = _normalize_text(surface).lower()

        if search_value:
            where_clauses.append("(title LIKE ? OR body LIKE ?)")
            params.extend([f"%{search_value}%", f"%{search_value}%"])
        if status_value:
            where_clauses.append("status = ?")
            params.append(status_value)
        if target_value:
            where_clauses.append("target_role = ?")
            params.append(target_value)
        if surface_value in ANNOUNCEMENT_SURFACES:
            where_clauses.append("surfaces_json LIKE ?")
            params.append(f'%"{surface_value}"%')

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT COUNT(*) FROM announcements {where_sql}", tuple(params))
            row = cursor.fetchone() or (0,)
            total = int(row[0] or 0)
            cursor.execute(
                f"""
                SELECT *
                FROM announcements
                {where_sql}
                ORDER BY sort_order DESC, priority DESC, COALESCE(start_at, 0) DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                tuple([*params, safe_page_size, offset]),
            )
            items = [self._serialize_row(item) for item in (cursor.fetchall() or [])]
        return {
            "items": [item for item in items if item],
            "total": total,
            "page": safe_page,
            "page_size": safe_page_size,
        }

    def _match_target_role(self, item_target_role: str, viewer_role: str) -> bool:
        if item_target_role == "all":
            return True
        return item_target_role == viewer_role

    def get_current_announcements(
        self,
        *,
        surface: str,
        viewer_role: str,
        user_id: int | None = None,
    ) -> dict[str, Any]:
        normalized_surface = _normalize_text(surface).lower()
        if normalized_surface not in ANNOUNCEMENT_SURFACES:
            normalized_surface = "query"
        normalized_viewer_role = "admin" if str(viewer_role or "").strip().lower() == "admin" else "user"
        now_ts = _now_ts()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT *
                FROM announcements
                WHERE status = 'published'
                ORDER BY sort_order DESC, priority DESC, COALESCE(start_at, 0) DESC, id DESC
                """
            )
            rows = cursor.fetchall() or []
            read_ids: set[int] = set()
            if normalized_surface == "query" and normalized_viewer_role == "user" and int(user_id or 0) > 0:
                cursor.execute(
                    "SELECT announcement_id FROM announcement_reads WHERE user_id = ?",
                    (int(user_id),),
                )
                read_ids = {int(item[0] or 0) for item in (cursor.fetchall() or [])}

        banner_items: list[dict[str, Any]] = []
        popup_item: dict[str, Any] | None = None
        for row in rows:
            item = self._serialize_row(row)
            if not item:
                continue
            if normalized_surface not in set(item.get("surfaces") or []):
                continue
            if not self._match_target_role(str(item.get("target_role") or "all"), normalized_viewer_role):
                continue
            if not self.is_active_item(item, now_ts=now_ts):
                continue
            banner_items.append(item)
            if normalized_surface == "admin":
                continue
            if normalized_viewer_role != "user":
                continue
            if not bool(item.get("show_as_popup")):
                continue
            if not bool(item.get("popup_repeat_always")) and int(item.get("id") or 0) in read_ids:
                continue
            if popup_item is None:
                popup_item = item

        return {
            "banner_items": banner_items,
            "popup_item": None if normalized_surface == "admin" else popup_item,
        }

    def mark_read(self, announcement_id: int, user_id: int) -> bool:
        normalized_announcement_id = int(announcement_id or 0)
        normalized_user_id = int(user_id or 0)
        if normalized_announcement_id <= 0 or normalized_user_id <= 0:
            return False
        now_ts = _now_ts()
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO announcement_reads (announcement_id, user_id, read_at)
                VALUES (?, ?, ?)
                ON CONFLICT(announcement_id, user_id)
                DO UPDATE SET read_at = excluded.read_at
                """,
                (normalized_announcement_id, normalized_user_id, now_ts),
            )
            conn.commit()
        return True

    def move_announcement(self, announcement_id: int, direction: str, operator: str) -> dict[str, Any] | None:
        normalized_announcement_id = int(announcement_id or 0)
        normalized_direction = str(direction or "").strip().lower()
        if normalized_announcement_id <= 0 or normalized_direction not in {"up", "down"}:
            return None
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, sort_order
                FROM announcements
                ORDER BY sort_order DESC, priority DESC, COALESCE(start_at, 0) DESC, id DESC
                """
            )
            rows = cursor.fetchall() or []
            ids = [int(row["id"] or 0) for row in rows]
            if normalized_announcement_id not in ids:
                return None
            current_index = ids.index(normalized_announcement_id)
            swap_index = current_index - 1 if normalized_direction == "up" else current_index + 1
            if swap_index < 0 or swap_index >= len(rows):
                return self.get_announcement(normalized_announcement_id)
            current_row = rows[current_index]
            swap_row = rows[swap_index]
            current_sort = int(current_row["sort_order"] or 0)
            swap_sort = int(swap_row["sort_order"] or 0)
            now_ts = _now_ts()
            operator_name = str(operator or "").strip()
            cursor.execute(
                "UPDATE announcements SET sort_order = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (swap_sort, now_ts, operator_name, normalized_announcement_id),
            )
            cursor.execute(
                "UPDATE announcements SET sort_order = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                (current_sort, now_ts, operator_name, int(swap_row["id"] or 0)),
            )
            conn.commit()
            self._normalize_sort_orders_in_connection(conn, operator)
        return self.get_announcement(normalized_announcement_id)

    def normalize_sort_orders(self, operator: str) -> dict[str, Any]:
        with self._lock, self._get_connection() as conn:
            self._normalize_sort_orders_in_connection(conn, operator)
        return self.list_announcements(page=1, page_size=200)


_storage_instance: WebAnnouncementStorage | None = None


def get_web_announcement_storage() -> WebAnnouncementStorage:
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = WebAnnouncementStorage()
    return _storage_instance
