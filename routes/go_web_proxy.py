"""
Proxy the customer-facing meituan-query web app through FastAPI.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
import sqlite3
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from utils import http_client
from utils.logger import setup_logger
from utils.meituan_allowance_task_storage import get_meituan_allowance_task_storage
from utils.order_query_capacity import BUSY_MESSAGE, OrderQueryCapacityBusy, acquire_order_query_capacity
from utils.order_leaderboard_service import (
    get_global_leaderboard_url,
    normalize_timestamp_seconds,
)
from utils.order_rankings_v2 import get_order_rankings_v2_service
from utils.path_utils import resolve_runtime_data_path


logger = setup_logger(__name__)
router = APIRouter(prefix="", tags=["美团查询客户Web代理"])

GO_WEB_SOCKET_PATH = os.getenv(
    "GO_PUBLIC_WEB_SOCKET_PATH",
    "/run/wx_service/meituan-query.sock",
)
GO_WEB_BASE_URL = "http://localhost"
HEAVY_WEB_QUERY_PATHS = {
    "/web/api/query",
    "/web/api/query-ins-batch",
    "/web/api/query-ins-notify",
    "/web/api/query-ins-orders",
    "/web/api/orders/list-lookup",
}

_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
    "date",
    "server",
}
_WEB_AUTH_SENSITIVE_PATHS = {
    "/web/api/login",
    "/web/api/logout",
    "/web/api/user",
}


async def _request_go_web(
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    timeout: float,
    params: Any = None,
    content: bytes | None = None,
) -> http_client.Response:
    normalized_path = "/" + str(path or "").lstrip("/")
    return await http_client.request(
        method,
        f"{GO_WEB_BASE_URL}{normalized_path}",
        params=params,
        content=content,
        headers=headers,
        timeout=timeout,
        uds=GO_WEB_SOCKET_PATH,
        # Web 反代必须保持每次请求 Cookie 隔离，避免跨用户串用 Go Web 会话。
        stateless_cookies=True,
    )


def _get_web_query_db_path():
    return resolve_runtime_data_path("meituan_query.db")


def _current_shanghai_timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _extract_user_update_id(path: str, method: str) -> int:
    if str(method or "").upper() != "PUT":
        return 0
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    prefix = "/web/admin/api/users/"
    if not normalized_path.startswith(prefix):
        return 0
    suffix = normalized_path[len(prefix):].strip("/")
    if not suffix or "/" in suffix:
        return 0
    try:
        user_id = int(suffix)
    except (TypeError, ValueError):
        return 0
    return user_id if user_id > 0 else 0


def _ensure_web_admin_audit_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_admin_audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            actor_id INTEGER DEFAULT 0,
            actor_username TEXT DEFAULT '',
            actor_is_admin INTEGER DEFAULT 0,
            actor_is_super_admin INTEGER DEFAULT 0,
            action TEXT NOT NULL,
            target_user_id INTEGER DEFAULT 0,
            target_username TEXT DEFAULT '',
            before_json TEXT DEFAULT '',
            after_json TEXT DEFAULT '',
            request_json TEXT DEFAULT '',
            status_code INTEGER DEFAULT 0,
            error_message TEXT DEFAULT ''
        )
        """
    )
    conn.commit()


def _load_web_user_snapshot(user_id: int) -> dict[str, Any]:
    normalized_user_id = int(user_id or 0)
    if normalized_user_id <= 0:
        return {}
    db_path = _get_web_query_db_path()
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, username, status, is_admin, is_super_admin, query_count, created_at, updated_at
            FROM users
            WHERE id = ?
            LIMIT 1
            """,
            (normalized_user_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {}
        return {
            "id": int(row["id"] or 0),
            "username": str(row["username"] or ""),
            "status": str(row["status"] or ""),
            "is_admin": bool(row["is_admin"]),
            "is_super_admin": bool(row["is_super_admin"]),
            "query_count": int(row["query_count"] or 0),
            "created_at": str(row["created_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }
    finally:
        conn.close()


def _write_web_admin_audit_log(
    *,
    actor: dict[str, Any] | None,
    action: str,
    target_user_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    request_payload: dict[str, Any] | None,
    status_code: int,
    error_message: str = "",
) -> None:
    db_path = _get_web_query_db_path()
    if not db_path.exists():
        return
    actor_payload = actor if isinstance(actor, dict) else {}
    before_payload = before if isinstance(before, dict) else {}
    after_payload = after if isinstance(after, dict) else {}
    request_data = request_payload if isinstance(request_payload, dict) else {}
    target_username = str((after_payload or before_payload).get("username") or "")
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        _ensure_web_admin_audit_schema(conn)
        conn.execute(
            """
            INSERT INTO web_admin_audit_logs (
                created_at,
                actor_id,
                actor_username,
                actor_is_admin,
                actor_is_super_admin,
                action,
                target_user_id,
                target_username,
                before_json,
                after_json,
                request_json,
                status_code,
                error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _current_shanghai_timestamp(),
                int(actor_payload.get("id") or 0),
                str(actor_payload.get("username") or ""),
                1 if bool(actor_payload.get("is_admin")) else 0,
                1 if bool(actor_payload.get("is_super_admin")) else 0,
                str(action or ""),
                int(target_user_id or 0),
                target_username,
                json.dumps(before_payload, ensure_ascii=False, sort_keys=True),
                json.dumps(after_payload, ensure_ascii=False, sort_keys=True),
                json.dumps(request_data, ensure_ascii=False, sort_keys=True),
                int(status_code or 0),
                str(error_message or ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()


async def _load_current_web_actor(request: Request) -> dict[str, Any]:
    if not os.path.exists(GO_WEB_SOCKET_PATH):
        return {}
    response = await _request_go_web(
        "GET",
        "/web/api/user",
        headers=_proxy_headers(request),
        timeout=10,
    )
    if int(response.status_code) != 200:
        return {}
    try:
        payload = json.loads(response.content.decode("utf-8")) if response.content else {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_token_delete_id(path: str, method: str) -> int:
    if str(method or "").upper() != "DELETE":
        return 0
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    prefix = "/web/api/tokens/"
    if not normalized_path.startswith(prefix):
        return 0
    suffix = normalized_path[len(prefix):].strip("/")
    if not suffix or "/" in suffix:
        return 0
    try:
        token_id = int(suffix)
    except (TypeError, ValueError):
        token_id = 0
    return token_id if token_id > 0 else 0


def _load_meituan_user_id_by_token_id(token_id: int) -> str:
    normalized_token_id = int(token_id or 0)
    if normalized_token_id <= 0:
        return ""
    db_path = _get_web_query_db_path()
    if not db_path.exists():
        return ""
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT meituan_user_id
            FROM tokens
            WHERE id = ?
            LIMIT 1
            """,
            (normalized_token_id,),
        )
        row = cursor.fetchone()
        return str(row["meituan_user_id"] or "").strip() if row else ""
    finally:
        conn.close()


def _cleanup_allowance_data_for_meituan_user_id(meituan_user_id: str) -> dict[str, int]:
    normalized_user_id = str(meituan_user_id or "").strip()
    if not normalized_user_id:
        return {"tasks": 0, "refresh_targets": 0, "daily_results": 0, "cancelled_runtime_tasks": 0}

    cancelled_runtime_tasks = 0
    try:
        from routes import wechat as wechat_routes

        background_tasks = getattr(wechat_routes, "_meituan_allowance_background_tasks", None)
        storage = get_meituan_allowance_task_storage()
        if isinstance(background_tasks, dict):
            task_ids_to_cancel: list[str] = []
            for task_id in list(background_tasks.keys()):
                task = storage.get_task(str(task_id))
                if not isinstance(task, dict):
                    continue
                if str(task.get("meituan_user_id") or "").strip() != normalized_user_id:
                    continue
                runtime_task = background_tasks.get(str(task_id))
                if runtime_task is not None and not runtime_task.done():
                    runtime_task.cancel()
                    cancelled_runtime_tasks += 1
                task_ids_to_cancel.append(str(task_id))
            for task_id in task_ids_to_cancel:
                background_tasks.pop(task_id, None)
    except Exception as exc:
        logger.warning("停止津贴运行时任务失败: meituan_user_id=%s error=%s", normalized_user_id, exc, exc_info=True)

    deleted = get_meituan_allowance_task_storage().clear_all_by_meituan_user_id(normalized_user_id)
    deleted["cancelled_runtime_tasks"] = cancelled_runtime_tasks
    return deleted


def _proxy_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in _HOP_BY_HOP_HEADERS or lowered == "host":
            continue
        headers[key] = value
    headers["host"] = request.headers.get("host", "localhost")
    return headers


def _copy_response_headers(source: http_client.Response, target: Response) -> None:
    for key, value in source.headers.multi_items():
        if key.lower() in _HOP_BY_HOP_HEADERS:
            continue
        target.headers.append(key, value)


def _apply_no_store_headers(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    vary_parts = {
        str(item).strip()
        for item in str(response.headers.get("Vary") or "").split(",")
        if str(item).strip()
    }
    vary_parts.add("Cookie")
    response.headers["Vary"] = ", ".join(sorted(vary_parts))
    return response


def _is_web_auth_sensitive_path(path: str) -> bool:
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    return normalized_path in _WEB_AUTH_SENSITIVE_PATHS


def _extract_json_payload(response_content: bytes) -> Any:
    if not response_content:
        return None
    try:
        return json.loads(response_content.decode("utf-8"))
    except Exception:
        return None


def _iter_ranking_v2_candidates(path: str, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not payload.get("success"):
        return []
    data = payload.get("data")
    candidates: list[dict[str, Any]] = []
    normalized_path = str(path or "")

    def append_candidate(item: Any) -> None:
        if not isinstance(item, dict):
            return
        poi_name = str(item.get("poi_name") or item.get("title") or "").strip()
        accept_timestamp = normalize_timestamp_seconds(item.get("accept_time", item.get("acceptTime")))
        if not poi_name or accept_timestamp is None:
            return
        candidates.append({
            "item": item,
            "poi_name": poi_name,
            "accept_timestamp": int(accept_timestamp),
        })

    if normalized_path.endswith("/web/api/query"):
        if isinstance(data, list):
            for item in data:
                append_candidate(item)
    elif normalized_path.endswith("/web/api/query-ins-batch"):
        if isinstance(data, list):
            for item in data:
                append_candidate(item)
    elif normalized_path.endswith("/web/api/query-ins-notify"):
        if isinstance(data, list):
            for item in data:
                append_candidate(item)
        elif isinstance(data, dict):
            append_candidate(data)
    elif normalized_path.endswith("/web/api/query-ins-orders") or normalized_path.endswith("/web/api/orders/list-lookup"):
        if isinstance(data, list):
            for item in data:
                append_candidate(item)
        elif isinstance(data, dict):
            for key in ("orders", "items", "records", "list"):
                nested = data.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        append_candidate(item)
    return candidates


def _inject_ranking_v2_text(path: str, payload: Any) -> Any:
    """Enrich completed order results from collected V2 data only.

    This intentionally has no write path: Web orders are no longer an input to
    any local leaderboard, because V2 is sourced only from the two collectors.
    """
    service = get_order_rankings_v2_service()
    for candidate in _iter_ranking_v2_candidates(path, payload):
        rank_text = service.rank_text_for_order(candidate["poi_name"], candidate["accept_timestamp"])
        if rank_text:
            candidate["item"]["ranking_v2_rank_text"] = rank_text
    return payload


def _inject_global_leaderboard_url(payload: Any) -> Any:
    """If global leaderboard is enabled, inject leaderboard_url into the JSON response."""
    leaderboard_url = get_global_leaderboard_url()
    if not leaderboard_url or not isinstance(payload, dict):
        return payload
    payload["leaderboard_url"] = leaderboard_url
    return payload


def _is_heavy_web_query(path: str, method: str) -> bool:
    if str(method or "").upper() in {"OPTIONS", "HEAD"}:
        return False
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    return normalized_path in HEAVY_WEB_QUERY_PATHS


async def _proxy_go_web_request(request: Request, path: str) -> Response:
    if not os.path.exists(GO_WEB_SOCKET_PATH):
        logger.warning("Go 客户 Web socket 不存在: %s", GO_WEB_SOCKET_PATH)
        return JSONResponse(
            {"detail": "客户查询 Web 服务未启动"},
            status_code=503,
        )

    normalized_path = "/" + str(path or "").lstrip("/")
    request_body = await request.body()
    deleted_token_id = _extract_token_delete_id(normalized_path, request.method)
    deleted_meituan_user_id = _load_meituan_user_id_by_token_id(deleted_token_id) if deleted_token_id > 0 else ""
    updated_user_id = _extract_user_update_id(normalized_path, request.method)
    admin_request_payload: dict[str, Any] | None = None
    admin_actor: dict[str, Any] = {}
    admin_before_snapshot: dict[str, Any] = {}

    if updated_user_id > 0:
        try:
            parsed_payload = json.loads(request_body.decode("utf-8")) if request_body else {}
        except Exception:
            parsed_payload = {}
        admin_request_payload = parsed_payload if isinstance(parsed_payload, dict) else {}
        admin_before_snapshot = _load_web_user_snapshot(updated_user_id)
        if "is_admin" in admin_request_payload:
            try:
                admin_actor = await _load_current_web_actor(request)
            except Exception as exc:
                logger.warning("读取 Web 管理操作人失败: path=%s error=%s", normalized_path, exc, exc_info=True)
                admin_actor = {}
            if not bool(admin_actor.get("is_super_admin")):
                _write_web_admin_audit_log(
                    actor=admin_actor,
                    action="update_user_admin_denied",
                    target_user_id=updated_user_id,
                    before=admin_before_snapshot,
                    after=admin_before_snapshot,
                    request_payload=admin_request_payload,
                    status_code=403,
                    error_message="只有超级管理员可以修改 Web 用户管理员权限",
                )
                return JSONResponse(
                    {"success": False, "error": "只有超级管理员可以修改管理员权限"},
                    status_code=403,
                )

    async def fetch_upstream() -> http_client.Response:
        return await _request_go_web(
            request.method,
            normalized_path,
            params=request.query_params,
            content=request_body,
            headers=_proxy_headers(request),
            timeout=60,
        )

    try:
        if _is_heavy_web_query(normalized_path, request.method):
            try:
                async with acquire_order_query_capacity(
                    "web",
                    identity=f"{request.client.host if request.client else ''}:{normalized_path}",
                ):
                    upstream = await fetch_upstream()
            except OrderQueryCapacityBusy as exc:
                global_stats = (exc.stats or {}).get("global") or {}
                source_stats = ((exc.stats or {}).get("sources") or {}).get("web") or {}
                logger.warning(
                    "Web 订单查询过载返回: path=%s active=%s/%s waiting=%s global_active=%s/%s",
                    normalized_path,
                    source_stats.get("active"),
                    source_stats.get("limit"),
                    source_stats.get("waiting"),
                    global_stats.get("active"),
                    global_stats.get("limit"),
                )
                return JSONResponse(
                    {"success": False, "message": BUSY_MESSAGE, "detail": BUSY_MESSAGE},
                    status_code=429,
                )
        else:
            upstream = await fetch_upstream()
    except Exception as exc:
        logger.error("Go 客户 Web 代理失败: path=%s error=%s", normalized_path, exc, exc_info=True)
        return JSONResponse(
            {"detail": "客户查询 Web 服务暂不可用"},
            status_code=502,
        )

    modified_content = None
    if upstream.status_code < 400:
        try:
            response_payload = _extract_json_payload(bytes(upstream.content or b""))
            if response_payload is not None:
                enriched = _inject_ranking_v2_text(normalized_path, response_payload)
                injected = _inject_global_leaderboard_url(enriched)
                if injected is not response_payload:
                    modified_content = json.dumps(injected, ensure_ascii=False).encode("utf-8")
        except Exception as exc:
            logger.warning("解析 Web 代理返回并写入排行榜失败: path=%s error=%s", normalized_path, exc)

    response = Response(
        content=modified_content if modified_content is not None else upstream.content,
        status_code=upstream.status_code,
    )
    _copy_response_headers(upstream, response)
    if _is_web_auth_sensitive_path(normalized_path) or bool(response.headers.get("set-cookie")):
        _apply_no_store_headers(response)

    if updated_user_id > 0:
        if not admin_actor:
            try:
                admin_actor = await _load_current_web_actor(request)
            except Exception:
                admin_actor = {}
        admin_after_snapshot = _load_web_user_snapshot(updated_user_id)
        action = "update_user_admin" if admin_request_payload and "is_admin" in admin_request_payload else "update_user"
        _write_web_admin_audit_log(
            actor=admin_actor,
            action=action,
            target_user_id=updated_user_id,
            before=admin_before_snapshot,
            after=admin_after_snapshot,
            request_payload=admin_request_payload,
            status_code=int(upstream.status_code),
            error_message="" if int(upstream.status_code) < 400 else "upstream_update_failed",
        )

    if deleted_token_id > 0 and upstream.status_code < 400 and deleted_meituan_user_id:
        try:
            cleanup_result = _cleanup_allowance_data_for_meituan_user_id(deleted_meituan_user_id)
            logger.info(
                "删除 Web token 后已清理津贴数据: token_id=%s meituan_user_id=%s cleanup=%s",
                deleted_token_id,
                deleted_meituan_user_id,
                cleanup_result,
            )
        except Exception as exc:
            logger.warning(
                "删除 Web token 后清理津贴数据失败: token_id=%s meituan_user_id=%s error=%s",
                deleted_token_id,
                deleted_meituan_user_id,
                exc,
                exc_info=True,
            )
    return response


@router.api_route("/web", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_go_web_root(request: Request):
    return await _proxy_go_web_request(request, "/web")


@router.api_route("/web/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_go_web_path(request: Request, path: str):
    return await _proxy_go_web_request(request, f"/web/{path}")
