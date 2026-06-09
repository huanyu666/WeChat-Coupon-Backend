"""
认证相关路由
"""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from typing import Any
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import config as config_package
import config.config as app_config
from config import get_config
from utils import http_client
from utils.auth_utils import (
    SESSION_COOKIE_NAME,
    create_session_token,
    verify_credentials,
    get_current_user,
    clear_session,
    clear_all_sessions,
)
from utils.go_local_api import GO_LOCAL_API_SOCKET_PATH
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path, resolve_runtime_data_path
from utils.proxy_utils import get_proxy_runtime_state
from utils.runtime_identity import build_runtime_identity
from utils.system_settings_store import (
    ALLOWANCE_SCHEDULE_TYPES,
    load_system_settings_store,
    normalize_allowance_relay_pool_config,
    normalize_allowance_schedule_config,
    normalize_web_user_registration_config,
    save_system_settings_store,
)
from utils.wechat_utils import access_token_cache
from utils.inflight_request_store import get_inflight_request_store
from utils.web_user_auto_approve import get_web_user_auto_approve_runtime
from utils.web_announcement_storage import get_web_announcement_storage
from wechat_account_store import load_wechat_account_store

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))
web_templates = Jinja2Templates(directory=str(resolve_project_path("web", "templates")))

router = APIRouter(prefix="", tags=["认证"])
GO_WEB_AUTH_SOCKET_PATH = os.getenv(
    "GO_PUBLIC_WEB_SOCKET_PATH",
    "/run/wx_service/meituan-query.sock",
)
GO_WEB_AUTH_BASE_URL = "http://localhost"
LOCAL_WEB_AUTH_BASE_URL = os.getenv("WX_LOCAL_WEB_AUTH_BASE_URL", "http://127.0.0.1")
_WEB_AUTH_HOP_BY_HOP_HEADERS = {
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


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


LOGIN_FAILURE_WINDOW_SECONDS = _get_env_int("WX_LOGIN_FAILURE_WINDOW_SECONDS", 900)
LOGIN_FAILURE_MAX_ATTEMPTS = _get_env_int("WX_LOGIN_FAILURE_MAX_ATTEMPTS", 8)
LOGIN_LOCK_SECONDS = _get_env_int("WX_LOGIN_LOCK_SECONDS", 900)
_login_failures: dict[str, list[float]] = {}
_login_blocked_until: dict[str, float] = {}
_ADMIN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
_ADMIN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TOML_SECTION_RE = re.compile(r"^\s*\[([^\]]+)]\s*$")
_ADMIN_LINE_RE = re.compile(r"^(\s*)([^\s=#][^=]*?)(\s*=\s*)([\"'])([0-9a-fA-F]{64})([\"'])(.*)$")
_web_order_query_processor = None


def _client_ip_from_request(request: Request) -> str:
    client_host = request.client.host if request.client else ""
    x_forwarded_for = str(request.headers.get("x-forwarded-for", "") or "").split(",", 1)[0].strip()
    x_real_ip = str(request.headers.get("x-real-ip", "") or "").strip()
    return x_forwarded_for or x_real_ip or client_host or "unknown"


def _login_rate_keys(username: str, client_ip: str) -> list[str]:
    normalized_username = str(username or "").strip().lower() or "unknown"
    normalized_ip = str(client_ip or "").strip() or "unknown"
    return [f"user:{normalized_username}", f"ip:{normalized_ip}"]


def _prune_login_rate_state(now: float) -> None:
    cutoff = now - LOGIN_FAILURE_WINDOW_SECONDS
    for key in list(_login_failures.keys()):
        values = [ts for ts in _login_failures.get(key, []) if ts >= cutoff]
        if values:
            _login_failures[key] = values
        else:
            _login_failures.pop(key, None)
    for key, blocked_until in list(_login_blocked_until.items()):
        if blocked_until <= now:
            _login_blocked_until.pop(key, None)


def _is_login_rate_limited(username: str, client_ip: str) -> bool:
    now = time.time()
    _prune_login_rate_state(now)
    return any(_login_blocked_until.get(key, 0) > now for key in _login_rate_keys(username, client_ip))


def _record_login_failure(username: str, client_ip: str) -> None:
    now = time.time()
    _prune_login_rate_state(now)
    for key in _login_rate_keys(username, client_ip):
        attempts = _login_failures.setdefault(key, [])
        attempts.append(now)
        if len(attempts) >= LOGIN_FAILURE_MAX_ATTEMPTS:
            _login_blocked_until[key] = now + LOGIN_LOCK_SECONDS


def _clear_login_failures(username: str, client_ip: str) -> None:
    for key in _login_rate_keys(username, client_ip):
        _login_failures.pop(key, None)
        _login_blocked_until.pop(key, None)


def _auth_cookie_secure(request: Request) -> bool:
    configured = str(os.getenv("WX_AUTH_COOKIE_SECURE", "") or "").strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    return request.url.scheme == "https"


def _require_admin_user(username: str) -> None:
    user_map = load_users()
    user = user_map.get(str(username or "").strip())
    if not user or not bool(user.get("is_admin")):
        raise PermissionError("需要管理员权限")


def _build_web_auth_proxy_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lowered = key.lower()
        if lowered in _WEB_AUTH_HOP_BY_HOP_HEADERS or lowered == "host":
            continue
        headers[key] = value
    headers["host"] = request.headers.get("host", "localhost")
    return headers


async def _request_web_owned_json(
    request: Request,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = _build_web_auth_proxy_headers(request)
    targets: list[dict[str, Any]] = []
    if os.path.exists(GO_WEB_AUTH_SOCKET_PATH):
        targets.append({
            "url": f"{GO_WEB_AUTH_BASE_URL}{path}",
            "uds": GO_WEB_AUTH_SOCKET_PATH,
        })
    targets.append({
        "url": f"{LOCAL_WEB_AUTH_BASE_URL}{path}",
    })

    last_error: Exception | None = None
    for target in targets:
        try:
            response = await http_client.request(
                "GET",
                target["url"],
                headers=headers,
                params=params,
                timeout=10,
                uds=target.get("uds"),
            )
        except Exception as exc:
            last_error = exc
            continue

        try:
            payload = json.loads(response.content.decode("utf-8")) if response.content else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return int(response.status_code), payload

    raise RuntimeError(f"读取 Web 登录态失败: {last_error or 'upstream_unavailable'}")


async def _get_current_web_admin_user(request: Request) -> dict[str, Any]:
    status_code, payload = await _request_web_owned_json(request, "/web/api/user")
    if status_code == 200 and bool(payload.get("is_admin")):
        return payload
    if status_code in {302, 401}:
        raise PermissionError("Web 登录已过期，请重新登录")
    if status_code == 200 and isinstance(payload, dict):
        username = str(payload.get("username") or "").strip()
        if username:
            db_path = _get_web_query_db_path()
            if db_path.exists():
                conn = sqlite3.connect(str(db_path), timeout=10.0)
                conn.row_factory = sqlite3.Row
                try:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT id, is_admin, is_super_admin, status
                        FROM users
                        WHERE username = ?
                        LIMIT 1
                        """,
                        (username,),
                    )
                    row = cursor.fetchone()
                    if row and str(row["status"] or "").strip().lower() == "approved" and (
                        bool(row["is_admin"]) or bool(row["is_super_admin"])
                    ):
                        return {
                            **payload,
                            "id": int(row["id"] or payload.get("id") or 0),
                            "is_admin": True,
                            "is_super_admin": bool(row["is_super_admin"]),
                            "admin_probe": "local_db",
                        }
                finally:
                    conn.close()

    if status_code == 200:
        raise PermissionError("需要管理员权限")
    raise RuntimeError(f"读取 Web 登录态失败: user_http={status_code} user_payload={payload}")


async def _get_current_web_query_user(request: Request) -> dict[str, Any]:
    status_code, payload = await _request_web_owned_json(request, "/web/api/user")
    if status_code == 200 and isinstance(payload, dict):
        return payload
    if status_code in {302, 401}:
        raise PermissionError("Web 登录已过期，请重新登录")
    raise PermissionError("需要先登录 Web 查询系统")


def _get_web_query_db_path() -> Path:
    return resolve_runtime_data_path("meituan_query.db")


def _get_allowance_db_path() -> Path:
    return resolve_runtime_data_path("meituan_allowance") / "meituan_allowance_tasks.db"


def _get_web_order_query_processor():
    global _web_order_query_processor
    if _web_order_query_processor is None:
        from text_processors.meituan_order_query_processor import MeituanOrderQueryProcessor

        _web_order_query_processor = MeituanOrderQueryProcessor(logger)
    return _web_order_query_processor


def _resolve_web_query_user_id(user_payload: dict[str, Any]) -> int:
    try:
        resolved_id = int(user_payload.get("id") or 0)
    except (TypeError, ValueError, AttributeError):
        resolved_id = 0
    if resolved_id > 0:
        return resolved_id

    username = str(user_payload.get("username") or "").strip()
    if not username:
        return 0
    conn = sqlite3.connect(str(_get_web_query_db_path()), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ? LIMIT 1", (username,))
        row = cursor.fetchone()
        return int(row["id"] or 0) if row else 0
    finally:
        conn.close()


def _load_web_token_record_for_user(*, user_id: int, token_id: int) -> dict[str, Any] | None:
    normalized_user_id = int(user_id or 0)
    normalized_token_id = int(token_id or 0)
    if normalized_user_id <= 0 or normalized_token_id <= 0:
        return None

    conn = sqlite3.connect(str(_get_web_query_db_path()), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, user_id, name, token, meituan_user_id, is_active, created_at, updated_at
            FROM tokens
            WHERE id = ? AND user_id = ?
            LIMIT 1
            """,
            (normalized_token_id, normalized_user_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"] or 0),
            "user_id": int(row["user_id"] or 0),
            "name": str(row["name"] or "").strip(),
            "token": str(row["token"] or "").strip(),
            "meituan_user_id": str(row["meituan_user_id"] or "").strip(),
            "is_active": bool(int(row["is_active"] or 0)),
            "created_at": str(row["created_at"] or "").strip(),
            "updated_at": str(row["updated_at"] or "").strip(),
        }
    finally:
        conn.close()


class WebInsuranceBatchQueryRequest(BaseModel):
    token_id: str
    max_count: int = 1


class WebInsuranceNotifyQueryRequest(BaseModel):
    token_id: str


class WebLegacyOrderQueryRequest(BaseModel):
    token_id: str
    order_ids: list[str]


async def _resolve_web_random_milliseconds(
    *,
    service_order_id: str = "",
    order_id: str = "",
) -> dict[str, int | None]:
    try:
        from utils.go_local_api import resolve_random_milliseconds_async

        payload = await resolve_random_milliseconds_async(
            service_order_id=service_order_id,
            order_id=order_id,
        )
        data = payload.get("data") if isinstance(payload, dict) else {}
        if not isinstance(data, dict):
            data = {}
        create_value = data.get("create_random_millisecond")
        accept_value = data.get("accept_random_millisecond")
        return {
            "create_random_millisecond": int(create_value) if create_value is not None else None,
            "accept_random_millisecond": int(accept_value) if accept_value is not None else None,
        }
    except Exception:
        return {
            "create_random_millisecond": None,
            "accept_random_millisecond": None,
        }


def _mark_web_token_inactive(token_id: int) -> bool:
    normalized_token_id = int(token_id or 0)
    if normalized_token_id <= 0:
        return False
    conn = sqlite3.connect(str(_get_web_query_db_path()), timeout=10.0)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE tokens
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (normalized_token_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


async def _query_single_web_insurance_candidate(
    *,
    processor: Any,
    token_record: dict[str, Any],
    service_order_id: str,
    accept_time: int | None,
    poi_name: str,
    query_context: dict[str, Any],
) -> dict[str, Any]:
    token = str(token_record.get("token") or "").strip()
    meituan_user_id = str(token_record.get("meituan_user_id") or "").strip()
    if not token or not meituan_user_id or not service_order_id:
        return {
            "service_order_id": service_order_id,
            "error": "订单信息不完整",
        }

    result: dict[str, Any] = {
        "channel": "insurance_orders",
        "service_order_id": service_order_id,
        "accept_time": int(accept_time) if accept_time is not None else None,
        "poi_name": poi_name or "未知商家",
    }

    external_order_id = await processor._afetch_external_order_id_with_retry(  # noqa: SLF001
        token,
        meituan_user_id,
        service_order_id,
        query_context=query_context,
    )
    if external_order_id:
        result["order_id"] = external_order_id

    order_detail_map = {}
    if external_order_id:
        order_detail_map = await processor._alookup_order_create_times_with_retry(  # noqa: SLF001
            meituan_user_id,
            token,
            [external_order_id],
            query_context=query_context,
            retry_stage="order_center_lookup_batch",
        )
    order_detail = order_detail_map.get(external_order_id) if isinstance(order_detail_map, dict) else {}
    if isinstance(order_detail, dict):
        create_time = order_detail.get("createTime")
        if create_time is not None:
            try:
                result["create_time"] = int(create_time)
            except (TypeError, ValueError):
                pass
        shop_link = str(order_detail.get("shopLink") or "").strip()
        if shop_link:
            coupon_url, poi_id_str = processor._build_merchant_coupon_url(shop_link)  # noqa: SLF001
            if coupon_url:
                result["coupon_url"] = coupon_url
            if poi_id_str:
                result["poi_id_str"] = poi_id_str

    random_ms = await _resolve_web_random_milliseconds(
        service_order_id=service_order_id,
        order_id=str(result.get("order_id") or ""),
    )
    result.update(random_ms)
    return result


def _consume_web_user_query_count(user_id: int, consume_count: int = 1) -> tuple[bool, int, str]:
    normalized_user_id = int(user_id or 0)
    normalized_consume_count = max(1, int(consume_count or 1))
    if normalized_user_id <= 0:
        return False, 0, "用户信息无效"

    conn = sqlite3.connect(str(_get_web_query_db_path()), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT query_count, status FROM users WHERE id = ? LIMIT 1",
            (normalized_user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return False, 0, "用户不存在"
        status = str(row["status"] or "").strip()
        current_count = int(row["query_count"] or 0)
        if status not in {"approved", ""}:
            return False, current_count, "账号尚未通过审核"
        if current_count < normalized_consume_count:
            return False, current_count, "剩余查询次数不足"

        remaining_count = current_count - normalized_consume_count
        cursor.execute(
            """
            UPDATE users
            SET query_count = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (remaining_count, normalized_user_id),
        )
        conn.commit()
        return True, remaining_count, ""
    finally:
        conn.close()


def _normalize_web_insurance_query_result(item: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "channel": "insurance_orders",
        "service_order_id": str(item.get("serviceOrderId") or item.get("service_order_id") or "").strip(),
        "order_id": str(item.get("orderId") or item.get("order_id") or "").strip(),
        "poi_name": str(item.get("poi_name") or item.get("title") or "").strip() or "未知商家",
        "coupon_url": str(item.get("merchantCouponUrl") or item.get("coupon_url") or "").strip(),
        "poi_id_str": str(item.get("poiIdStr") or item.get("poi_id_str") or "").strip(),
    }
    for source_key, target_key in (
        ("acceptTime", "accept_time"),
        ("accept_time", "accept_time"),
        ("createTime", "create_time"),
        ("create_time", "create_time"),
        ("create_random_millisecond", "create_random_millisecond"),
        ("accept_random_millisecond", "accept_random_millisecond"),
    ):
        value = item.get(source_key)
        if value in (None, ""):
            continue
        try:
            normalized[target_key] = int(value)
        except (TypeError, ValueError):
            normalized[target_key] = value
    if item.get("error"):
        normalized["error"] = str(item.get("error") or "").strip()
    return normalized


def _decode_json_response_payload(response: JSONResponse) -> dict[str, Any]:
    try:
        raw_body = bytes(getattr(response, "body", b"") or b"")
        payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


async def _prepare_web_query_token_context(request: Request, raw_token_id: Any) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        current_user = await _get_current_web_query_user(request)
    except PermissionError as exc:
        return None, JSONResponse({"success": False, "error": str(exc)}, status_code=401)
    except Exception as exc:
        logger.warning("读取 Web 查询登录态失败: %s", exc, exc_info=True)
        return None, JSONResponse({"success": False, "error": "登录状态异常，请重新登录"}, status_code=500)

    web_user_id = _resolve_web_query_user_id(current_user)
    if web_user_id <= 0:
        return None, JSONResponse({"success": False, "error": "无法识别当前用户"}, status_code=400)

    try:
        token_id = int(str(raw_token_id or "").strip())
    except (TypeError, ValueError):
        token_id = 0
    if token_id <= 0:
        return None, _build_web_query_error_response("请选择有效 Token", status_code=400, error_code="token_not_found")

    token_record = _load_web_token_record_for_user(user_id=web_user_id, token_id=token_id)
    if token_record is None:
        return None, _build_web_query_error_response("Token 不存在或无权访问", status_code=404, error_code="token_not_found")
    if not bool(token_record.get("is_active")):
        return None, _build_web_query_error_response("当前 Token 已失效，请重新添加", status_code=400, error_code="token_inactive")

    token = str(token_record.get("token") or "").strip()
    meituan_user_id = str(token_record.get("meituan_user_id") or "").strip()
    if not token or not meituan_user_id:
        return None, _build_web_query_error_response("Token 信息不完整，请重新添加", status_code=400, error_code="token_incomplete")

    return {
        "current_user": current_user,
        "web_user_id": web_user_id,
        "token_id": token_id,
        "token_record": token_record,
        "token": token,
        "meituan_user_id": meituan_user_id,
    }, None


def _remaining_web_query_count(current_user: dict[str, Any]) -> int:
    try:
        return int(current_user.get("query_count") or 0) if isinstance(current_user, dict) else 0
    except (TypeError, ValueError, AttributeError):
        return 0


def _normalize_web_legacy_order_result(item: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "channel": "legacy",
        "orderId": str(item.get("orderId") or item.get("order_id") or "").strip(),
        "poi_name": str(item.get("poi_name") or item.get("title") or "").strip() or "未知商家",
        "accepted": bool(item.get("accepted")),
    }
    accept_time = item.get("acceptTime", item.get("accept_time"))
    if accept_time not in (None, ""):
        try:
            normalized["acceptTime"] = int(accept_time)
        except (TypeError, ValueError):
            normalized["acceptTime"] = accept_time
    create_time = item.get("createTime", item.get("create_time"))
    if create_time not in (None, ""):
        try:
            normalized["create_time"] = int(create_time)
        except (TypeError, ValueError):
            normalized["create_time"] = create_time
    return normalized


def _normalize_web_query_error_code(message: str, explicit_code: str = "") -> str:
    if explicit_code:
        return str(explicit_code or "").strip()
    normalized = str(message or "").strip()
    lowered = normalized.lower()
    if "登录已过期" in normalized:
        return "login_expired"
    if "登录状态已失效" in normalized or "认证失败" in normalized:
        return "token_expired"
    if "当前 Token 已失效" in normalized:
        return "token_inactive"
    if "Token 不存在" in normalized or "无权访问" in normalized:
        return "token_not_found"
    if "Token 信息不完整" in normalized:
        return "token_incomplete"
    if "剩余查询次数不足" in normalized:
        return "insufficient_query_count"
    if "当前查询较多" in normalized:
        return "queue_busy"
    if "订单ID格式不正确" in normalized:
        return "invalid_order_id"
    if "请输入至少一个订单ID" in normalized:
        return "missing_order_id"
    if "网络繁忙" in normalized:
        return "proxy_unavailable"
    if "超时" in normalized or "timeout" in lowered or "timed out" in lowered:
        return "network_timeout"
    if "当前用户没有可查询的订单" in normalized:
        return "no_order"
    return "query_failed"


def _web_query_error_hint(error_code: str) -> str:
    normalized = str(error_code or "").strip()
    return {
        "login_expired": "请重新登录 Web 后再试。",
        "token_expired": "请刷新 Token 状态，必要时重新粘贴登录后的完整美团链接。",
        "token_inactive": "当前 Token 已被停用，请重新添加或更换账号。",
        "token_not_found": "请重新选择当前账号下可用的 Token。",
        "token_incomplete": "这个 Token 缺少必要字段，请删除后重新添加。",
        "insufficient_query_count": "请先在后台为当前用户补充查询次数。",
        "queue_busy": "当前还有其他查单任务在排队，稍等几秒再试。",
        "invalid_order_id": "订单号需要是 15-22 位数字，可以一行一个。",
        "missing_order_id": "请先输入至少一个订单号后再提交。",
        "proxy_unavailable": "如果连续失败，请到后台查看代理状态和查单运行概览。",
        "network_timeout": "建议稍等几秒重试；如果持续超时，请检查后台代理状态。",
        "no_order": "当前账号暂时没有可查询的订单，可以换个账号或稍后再试。",
        "query_failed": "如果连续失败，请到后台查看最近失败原因和代理状态。",
    }.get(normalized, "")


def _build_web_query_error_response(
    message: str,
    *,
    status_code: int,
    error_code: str = "",
    login_expired: bool = False,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    normalized_message = str(message or "").strip() or "查询失败"
    normalized_code = _normalize_web_query_error_code(normalized_message, explicit_code=error_code)
    payload: dict[str, Any] = {
        "success": False,
        "error": normalized_message,
        "error_code": normalized_code,
    }
    hint = _web_query_error_hint(normalized_code)
    if hint:
        payload["error_hint"] = hint
    if login_expired:
        payload["login_expired"] = True
    if isinstance(extra, dict):
        payload.update(extra)
    return JSONResponse(payload, status_code=status_code)


def _mask_meituan_user_id(value: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) <= 5:
        return normalized
    return f"{normalized[:3]}***{normalized[-2:]}"


def _extract_web_query_runtime_metrics(processor: Any | None, query_context: dict[str, Any] | None) -> dict[str, Any]:
    if processor is None or not isinstance(query_context, dict):
        return {
            "last_stage": "",
            "stage_summary": "",
            "proxy_summary": "",
            "proxy_attempts": 0,
            "unique_proxy_count": 0,
            "proxy_retry_attempts": 0,
        }
    try:
        proxy_attempts, unique_proxy_count = processor._get_proxy_usage_counts(query_context)  # noqa: SLF001
    except Exception:
        proxy_attempts, unique_proxy_count = 0, 0
    try:
        proxy_summary = processor._format_proxy_usage_summary(query_context)  # noqa: SLF001
    except Exception:
        proxy_summary = ""
    try:
        stage_summary = processor._format_query_stage_summary(query_context)  # noqa: SLF001
    except Exception:
        stage_summary = ""
    return {
        "last_stage": str(query_context.get("last_stage") or "").strip(),
        "stage_summary": stage_summary,
        "proxy_summary": proxy_summary,
        "proxy_attempts": int(proxy_attempts or 0),
        "unique_proxy_count": int(unique_proxy_count or 0),
        "proxy_retry_attempts": int(query_context.get("proxy_retry_attempts") or 0),
    }


def _record_web_order_query_runtime_event(
    *,
    source: str,
    status: str,
    success: bool,
    web_user_id: int = 0,
    token_id: int = 0,
    meituan_user_id: str = "",
    result_count: int = 0,
    query_count_consumed: int = 0,
    error_message: str = "",
    error_code: str = "",
    processor: Any | None = None,
    query_context: dict[str, Any] | None = None,
    started_at: float | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    try:
        from utils.log_event_store import append_log_event

        metrics = _extract_web_query_runtime_metrics(processor, query_context)
        append_log_event(
            "web_order_query",
            {
                "source": str(source or "").strip() or "unknown",
                "status": str(status or "").strip() or ("success" if success else "failed"),
                "success": bool(success),
                "date_key": _current_shanghai_date_key(),
                "web_user_id": int(web_user_id or 0),
                "token_id": int(token_id or 0),
                "meituan_user_id_masked": _mask_meituan_user_id(meituan_user_id),
                "result_count": int(result_count or 0),
                "query_count_consumed": int(query_count_consumed or 0),
                "error_message": str(error_message or "").strip()[:500],
                "error_code": _normalize_web_query_error_code(error_message, explicit_code=error_code),
                "elapsed_seconds": round(max(0.0, time.time() - float(started_at or time.time())), 3),
                **metrics,
                **(extra if isinstance(extra, dict) else {}),
            },
        )
    except Exception as exc:
        logger.warning("Web 查单运行事件写入失败: %s", exc)


def _load_web_order_query_overview() -> dict[str, Any]:
    from utils.log_event_store import load_recent_log_events
    from utils.order_query_background import get_order_query_background_stats
    from utils.order_query_capacity import get_order_query_capacity_stats

    today_date_key = _current_shanghai_date_key()
    overview = {
        "today_request_count": 0,
        "today_success_count": 0,
        "today_failed_count": 0,
        "today_consumed_count": 0,
        "today_result_count": 0,
        "today_timeout_count": 0,
        "today_login_expired_count": 0,
        "today_busy_count": 0,
        "success_rate": 0.0,
        "last_event_at": "",
        "last_success_at": "",
        "last_failure_at": "",
        "last_error_message": "",
        "last_error_code": "",
        "top_failure_reasons": [],
        "recent_failures": [],
        "capacity": get_order_query_capacity_stats(),
        "background": get_order_query_background_stats(),
        "proxy": get_proxy_runtime_state(),
    }
    try:
        recent_events = load_recent_log_events(minutes=72 * 60, limit=4000)
    except Exception:
        recent_events = []

    web_events = []
    for item in recent_events:
        if not isinstance(item, dict) or str(item.get("event_type") or "").strip() != "web_order_query":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        payload["created_at"] = str(item.get("created_at") or "")
        web_events.append(payload)

    today_events = [item for item in web_events if str(item.get("date_key") or "") == today_date_key]
    if not today_events:
        return overview

    success_events = [item for item in today_events if bool(item.get("success"))]
    failed_events = [item for item in today_events if not bool(item.get("success"))]
    overview["today_request_count"] = len(today_events)
    overview["today_success_count"] = len(success_events)
    overview["today_failed_count"] = len(failed_events)
    overview["today_consumed_count"] = sum(int(item.get("query_count_consumed") or 0) for item in today_events)
    overview["today_result_count"] = sum(int(item.get("result_count") or 0) for item in today_events)
    overview["today_timeout_count"] = sum(
        1 for item in failed_events if str(item.get("error_code") or "") == "network_timeout"
    )
    overview["today_login_expired_count"] = sum(
        1 for item in failed_events if str(item.get("error_code") or "") in {"login_expired", "token_expired", "token_inactive"}
    )
    overview["today_busy_count"] = sum(
        1 for item in failed_events if str(item.get("error_code") or "") == "queue_busy"
    )
    overview["success_rate"] = round((len(success_events) * 100.0) / len(today_events), 1) if today_events else 0.0

    latest_event = today_events[-1]
    latest_success = success_events[-1] if success_events else None
    latest_failure = failed_events[-1] if failed_events else None
    overview["last_event_at"] = str(latest_event.get("created_at") or "")
    overview["last_success_at"] = str(latest_success.get("created_at") or "") if latest_success else ""
    overview["last_failure_at"] = str(latest_failure.get("created_at") or "") if latest_failure else ""
    overview["last_error_message"] = str(latest_failure.get("error_message") or "") if latest_failure else ""
    overview["last_error_code"] = str(latest_failure.get("error_code") or "") if latest_failure else ""

    failure_counts: dict[str, int] = {}
    for item in failed_events:
        code = str(item.get("error_code") or "").strip()
        message = str(item.get("error_message") or "").strip()
        label = code or message or "query_failed"
        failure_counts[label] = failure_counts.get(label, 0) + 1

    overview["top_failure_reasons"] = [
        {"label": key, "count": value}
        for key, value in sorted(failure_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:5]
    ]
    overview["recent_failures"] = [
        {
            "created_at": str(item.get("created_at") or ""),
            "source": str(item.get("source") or ""),
            "error_code": str(item.get("error_code") or ""),
            "error_message": str(item.get("error_message") or ""),
        }
        for item in failed_events[-5:]
    ][::-1]
    return overview


async def _execute_web_query_insurance_batch(
    request: Request,
    request_data: WebInsuranceBatchQueryRequest,
    *,
    source: str,
):
    context, error_response = await _prepare_web_query_token_context(request, request_data.token_id)
    if error_response is not None or context is None:
        return error_response

    current_user = context["current_user"]
    web_user_id = int(context["web_user_id"])
    token_id = int(context["token_id"])
    token = str(context["token"])
    meituan_user_id = str(context["meituan_user_id"])
    started_at = time.time()

    remaining_count_hint = _remaining_web_query_count(current_user)
    if remaining_count_hint <= 0:
        return _build_web_query_error_response("剩余查询次数不足", status_code=400)

    processor = _get_web_order_query_processor()
    max_count = max(1, min(int(request_data.max_count or 1), 5))
    query_context = processor._build_order_query_context("web", str(web_user_id), None)  # noqa: SLF001

    try:
        from utils.order_query_capacity import OrderQueryCapacityBusy, acquire_order_query_capacity

        async with acquire_order_query_capacity(
            "web",
            identity=f"{web_user_id}:{token_id}",
            timeout=processor.ORDER_QUERY_QUEUE_TIMEOUT_SECONDS,  # noqa: SLF001
        ):
            results, success_steps, query_error = await processor._aquery_top_orders(  # noqa: SLF001
                token,
                meituan_user_id,
                max_count,
                "",
                query_context=query_context,
            )
    except OrderQueryCapacityBusy:
        _record_web_order_query_runtime_event(
            source=source,
            status="queue_busy",
            success=False,
            web_user_id=web_user_id,
            token_id=token_id,
            meituan_user_id=meituan_user_id,
            error_message=processor.ORDER_QUERY_QUEUE_BUSY_MESSAGE,  # noqa: SLF001
            error_code="queue_busy",
            processor=processor,
            query_context=query_context,
            started_at=started_at,
        )
        return _build_web_query_error_response(
            processor.ORDER_QUERY_QUEUE_BUSY_MESSAGE,  # noqa: SLF001
            status_code=429,
            error_code="queue_busy",
        )
    except Exception as exc:
        error_text = processor._format_exception_message(exc)  # noqa: SLF001
        normalized_error = processor._normalize_user_error(error_text)  # noqa: SLF001
        if processor._is_token_expired_error(error_text):  # noqa: SLF001
            _record_web_order_query_runtime_event(
                source=source,
                status="token_expired",
                success=False,
                web_user_id=web_user_id,
                token_id=token_id,
                meituan_user_id=meituan_user_id,
                error_message="美团登录状态已失效",
                error_code="token_expired",
                processor=processor,
                query_context=query_context,
                started_at=started_at,
            )
            _mark_web_token_inactive(token_id)
            return _build_web_query_error_response(
                "美团登录状态已失效",
                status_code=401,
                error_code="token_expired",
                login_expired=True,
            )
        _record_web_order_query_runtime_event(
            source=source,
            status="failed",
            success=False,
            web_user_id=web_user_id,
            token_id=token_id,
            meituan_user_id=meituan_user_id,
            error_message=normalized_error,
            processor=processor,
            query_context=query_context,
            started_at=started_at,
        )
        logger.warning(
            "Web 批量查单失败: user_id=%s token_id=%s error=%s stages=%s",
            web_user_id,
            token_id,
            error_text,
            processor._format_query_stage_summary(query_context),  # noqa: SLF001
            exc_info=True,
        )
        return _build_web_query_error_response(normalized_error, status_code=502)

    consume_ok, remaining_count, consume_error = _consume_web_user_query_count(web_user_id, 1)
    if not consume_ok:
        return _build_web_query_error_response(consume_error or "剩余查询次数不足", status_code=400)

    if query_error:
        normalized_query_error = processor._normalize_user_error(query_error)  # noqa: SLF001
        if normalized_query_error == "当前用户没有可查询的订单":
            _record_web_order_query_runtime_event(
                source=source,
                status="no_order",
                success=True,
                web_user_id=web_user_id,
                token_id=token_id,
                meituan_user_id=meituan_user_id,
                query_count_consumed=1,
                processor=processor,
                query_context=query_context,
                started_at=started_at,
            )
            return JSONResponse({
                "success": True,
                "data": [],
                "remaining_query_count": remaining_count,
                "query_count_consumed": 1,
            })
        _record_web_order_query_runtime_event(
            source=source,
            status="failed",
            success=False,
            web_user_id=web_user_id,
            token_id=token_id,
            meituan_user_id=meituan_user_id,
            error_message=normalized_query_error,
            processor=processor,
            query_context=query_context,
            started_at=started_at,
        )
        return _build_web_query_error_response(normalized_query_error, status_code=400)

    normalized_results = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        normalized_item = _normalize_web_insurance_query_result(item)
        if "create_random_millisecond" not in normalized_item and "accept_random_millisecond" not in normalized_item:
            random_ms = await _resolve_web_random_milliseconds(
                service_order_id=str(normalized_item.get("service_order_id") or ""),
                order_id=str(normalized_item.get("order_id") or ""),
            )
            normalized_item.update(random_ms)
        normalized_results.append(normalized_item)

    _record_web_order_query_runtime_event(
        source=source,
        status="success",
        success=True,
        web_user_id=web_user_id,
        token_id=token_id,
        meituan_user_id=meituan_user_id,
        result_count=len(normalized_results),
        query_count_consumed=1,
        processor=processor,
        query_context=query_context,
        started_at=started_at,
    )
    logger.info(
        "Web 批量查单完成: user_id=%s token_id=%s count=%s success_steps=%s remaining_query_count=%s stages=%s",
        web_user_id,
        token_id,
        len(normalized_results),
        success_steps,
        remaining_count,
        processor._format_query_stage_summary(query_context),  # noqa: SLF001
    )
    return JSONResponse({
        "success": True,
        "data": normalized_results,
        "remaining_query_count": remaining_count,
        "query_count_consumed": 1,
    })


@router.post("/web/api/query-ins-batch")
async def web_query_insurance_batch(request: Request, request_data: WebInsuranceBatchQueryRequest):
    return await _execute_web_query_insurance_batch(request, request_data, source="insurance_batch")


@router.post("/web/api/query-ins-notify")
async def web_query_insurance_notify(request: Request, request_data: WebInsuranceNotifyQueryRequest):
    return await _execute_web_query_insurance_batch(
        request,
        WebInsuranceBatchQueryRequest(token_id=request_data.token_id, max_count=1),
        source="insurance_notify",
    )


@router.post("/web/api/orders/list-lookup")
async def web_lookup_order_create_times(request: Request, request_data: WebLegacyOrderQueryRequest):
    context, error_response = await _prepare_web_query_token_context(request, request_data.token_id)
    if error_response is not None or context is None:
        return error_response

    web_user_id = int(context["web_user_id"])
    token_id = int(context["token_id"])
    token = str(context["token"])
    meituan_user_id = str(context["meituan_user_id"])
    processor = _get_web_order_query_processor()
    started_at = time.time()

    order_ids = []
    seen_order_ids: set[str] = set()
    for raw_order_id in request_data.order_ids or []:
        order_id = str(raw_order_id or "").strip()
        if not order_id or order_id in seen_order_ids:
            continue
        seen_order_ids.add(order_id)
        order_ids.append(order_id)
    if not order_ids:
        return _build_web_query_error_response("请输入至少一个订单ID", status_code=400)

    query_context = processor._build_order_query_context("web", str(web_user_id), None)  # noqa: SLF001
    try:
        from utils.order_query_capacity import OrderQueryCapacityBusy, acquire_order_query_capacity

        async with acquire_order_query_capacity(
            "web",
            identity=f"{web_user_id}:{token_id}:lookup",
            timeout=processor.ORDER_QUERY_QUEUE_TIMEOUT_SECONDS,  # noqa: SLF001
        ):
            order_details = await processor._alookup_order_create_times_with_retry(  # noqa: SLF001
                meituan_user_id,
                token,
                order_ids,
                query_context=query_context,
                retry_stage="order_center_lookup_batch",
            )
    except OrderQueryCapacityBusy:
        _record_web_order_query_runtime_event(
            source="create_time_lookup",
            status="queue_busy",
            success=False,
            web_user_id=web_user_id,
            token_id=token_id,
            meituan_user_id=meituan_user_id,
            error_message=processor.ORDER_QUERY_QUEUE_BUSY_MESSAGE,  # noqa: SLF001
            error_code="queue_busy",
            processor=processor,
            query_context=query_context,
            started_at=started_at,
        )
        return _build_web_query_error_response(
            processor.ORDER_QUERY_QUEUE_BUSY_MESSAGE,  # noqa: SLF001
            status_code=429,
            error_code="queue_busy",
        )
    except Exception as exc:
        error_text = processor._format_exception_message(exc)  # noqa: SLF001
        normalized_error = processor._normalize_user_error(error_text)  # noqa: SLF001
        if processor._is_token_expired_error(error_text):  # noqa: SLF001
            _record_web_order_query_runtime_event(
                source="create_time_lookup",
                status="token_expired",
                success=False,
                web_user_id=web_user_id,
                token_id=token_id,
                meituan_user_id=meituan_user_id,
                error_message="美团登录状态已失效",
                error_code="token_expired",
                processor=processor,
                query_context=query_context,
                started_at=started_at,
            )
            _mark_web_token_inactive(token_id)
            return _build_web_query_error_response(
                "美团登录状态已失效",
                status_code=401,
                error_code="token_expired",
                login_expired=True,
            )
        _record_web_order_query_runtime_event(
            source="create_time_lookup",
            status="failed",
            success=False,
            web_user_id=web_user_id,
            token_id=token_id,
            meituan_user_id=meituan_user_id,
            error_message=normalized_error,
            processor=processor,
            query_context=query_context,
            started_at=started_at,
        )
        logger.warning(
            "Web 订单创建时间补查失败: user_id=%s token_id=%s error=%s stages=%s",
            web_user_id,
            token_id,
            error_text,
            processor._format_query_stage_summary(query_context),  # noqa: SLF001
            exc_info=True,
        )
        return _build_web_query_error_response(normalized_error, status_code=502)

    normalized_results = []
    for order_id in order_ids:
        order_detail = order_details.get(order_id) if isinstance(order_details, dict) else None
        if not isinstance(order_detail, dict):
            continue
        normalized_item: dict[str, Any] = {"orderId": order_id}
        create_time = order_detail.get("createTime")
        if create_time is not None:
            try:
                normalized_item["create_time"] = int(create_time)
            except (TypeError, ValueError):
                normalized_item["create_time"] = create_time
        shop_link = str(order_detail.get("shopLink") or "").strip()
        if shop_link:
            normalized_item["shop_link"] = shop_link
        normalized_results.append(normalized_item)

    _record_web_order_query_runtime_event(
        source="create_time_lookup",
        status="success",
        success=True,
        web_user_id=web_user_id,
        token_id=token_id,
        meituan_user_id=meituan_user_id,
        result_count=len(normalized_results),
        processor=processor,
        query_context=query_context,
        started_at=started_at,
    )
    return JSONResponse({"success": True, "data": normalized_results})


@router.post("/web/api/query")
async def web_query_order_detail(request: Request, request_data: WebLegacyOrderQueryRequest):
    context, error_response = await _prepare_web_query_token_context(request, request_data.token_id)
    if error_response is not None or context is None:
        return error_response

    current_user = context["current_user"]
    web_user_id = int(context["web_user_id"])
    token_id = int(context["token_id"])
    token = str(context["token"])
    meituan_user_id = str(context["meituan_user_id"])
    started_at = time.time()
    remaining_count_hint = _remaining_web_query_count(current_user)
    if remaining_count_hint <= 0:
        return _build_web_query_error_response("剩余查询次数不足", status_code=400)

    normalized_order_ids = []
    seen_order_ids: set[str] = set()
    for raw_order_id in request_data.order_ids or []:
        order_id = str(raw_order_id or "").strip()
        if not order_id or order_id in seen_order_ids:
            continue
        seen_order_ids.add(order_id)
        normalized_order_ids.append(order_id)
    if not normalized_order_ids:
        return _build_web_query_error_response("请输入至少一个订单ID", status_code=400)

    for order_id in normalized_order_ids:
        if not order_id.isdigit() or len(order_id) < 15 or len(order_id) > 22:
            return _build_web_query_error_response("订单ID格式不正确，应为15-22位数字", status_code=400)

    from routes.wechat import MeituanOrderQueryRequest, query_meituan_order

    results = []
    for order_id in normalized_order_ids:
        upstream_response = await query_meituan_order(
            MeituanOrderQueryRequest(token=token, order_id=order_id),
        )
        payload = _decode_json_response_payload(upstream_response)
        if int(upstream_response.status_code) == 401:
            _record_web_order_query_runtime_event(
                source="manual_order_query",
                status="token_expired",
                success=False,
                web_user_id=web_user_id,
                token_id=token_id,
                meituan_user_id=meituan_user_id,
                error_message="美团登录状态已失效",
                error_code="token_expired",
                started_at=started_at,
            )
            _mark_web_token_inactive(token_id)
            return _build_web_query_error_response(
                "美团登录状态已失效",
                status_code=401,
                error_code="token_expired",
                login_expired=True,
            )
        if not bool(payload.get("success")):
            error_text = str(payload.get("error") or "查询失败").strip()
            if "认证失败" in error_text or "登录状态已失效" in error_text or "token" in error_text.lower():
                _record_web_order_query_runtime_event(
                    source="manual_order_query",
                    status="token_expired",
                    success=False,
                    web_user_id=web_user_id,
                    token_id=token_id,
                    meituan_user_id=meituan_user_id,
                    error_message="美团登录状态已失效",
                    error_code="token_expired",
                    started_at=started_at,
                )
                _mark_web_token_inactive(token_id)
                return _build_web_query_error_response(
                    "美团登录状态已失效",
                    status_code=401,
                    error_code="token_expired",
                    login_expired=True,
                )
            status_code = int(upstream_response.status_code or 400)
            if status_code < 400:
                status_code = 400
            _record_web_order_query_runtime_event(
                source="manual_order_query",
                status="failed",
                success=False,
                web_user_id=web_user_id,
                token_id=token_id,
                meituan_user_id=meituan_user_id,
                error_message=error_text,
                started_at=started_at,
                extra={"order_id_count": len(normalized_order_ids)},
            )
            logger.warning(
                "Web 手动查单失败: user_id=%s token_id=%s order_id=%s status=%s error=%s",
                web_user_id,
                token_id,
                order_id,
                status_code,
                error_text,
            )
            return _build_web_query_error_response(error_text, status_code=status_code)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        results.append(_normalize_web_legacy_order_result(data))

    consume_ok, remaining_count, consume_error = _consume_web_user_query_count(web_user_id, 1)
    if not consume_ok:
        return _build_web_query_error_response(consume_error or "剩余查询次数不足", status_code=400)

    _record_web_order_query_runtime_event(
        source="manual_order_query",
        status="success",
        success=True,
        web_user_id=web_user_id,
        token_id=token_id,
        meituan_user_id=meituan_user_id,
        result_count=len(results),
        query_count_consumed=1,
        started_at=started_at,
        extra={"order_id_count": len(normalized_order_ids)},
    )
    logger.info(
        "Web 手动查单完成: user_id=%s token_id=%s count=%s remaining_query_count=%s",
        web_user_id,
        token_id,
        len(results),
        remaining_count,
    )
    return JSONResponse({
        "success": True,
        "data": results,
        "remaining_query_count": remaining_count,
        "query_count_consumed": 1,
    })


def _current_shanghai_date_key() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def _empty_allowance_type_overview(
    allowance_type: str,
    *,
    schedule: dict[str, Any],
    active_meituan_user_count: int,
    refresh_target_count: int,
    refresh_target_user_count: int,
    today_date_key: str,
) -> dict[str, Any]:
    normalized_type = str(allowance_type or "").strip()
    if normalized_type not in ALLOWANCE_SCHEDULE_TYPES:
        normalized_type = "large"

    schedule_types = schedule.get("types") if isinstance(schedule, dict) else {}
    if not isinstance(schedule_types, dict):
        schedule_types = {}
    runtime = schedule_types.get(normalized_type) if isinstance(schedule_types.get(normalized_type), dict) else {}
    config = runtime.get("config") if isinstance(runtime.get("config"), dict) else {}
    address_scope = str(config.get("address_scope") or "all").strip()
    if address_scope not in {"all", "latest"}:
        address_scope = "all"

    estimated_refresh_count = active_meituan_user_count if address_scope == "latest" else refresh_target_count
    last_result = runtime.get("last_result") if isinstance(runtime.get("last_result"), dict) else {}
    discovery_failed_targets = last_result.get("discovery_failed_targets") if isinstance(last_result, dict) else []
    skipped_targets = last_result.get("skipped_targets") if isinstance(last_result, dict) else []
    if not isinstance(discovery_failed_targets, list):
        discovery_failed_targets = []
    if not isinstance(skipped_targets, list):
        skipped_targets = []
    return {
        "allowance_type": normalized_type,
        "config": config,
        "runtime": runtime,
        "address_scope": address_scope,
        "refresh_target_count": int(refresh_target_count or 0),
        "refresh_target_user_count": int(refresh_target_user_count or 0),
        "active_meituan_user_count": int(active_meituan_user_count or 0),
        "estimated_refresh_count": int(estimated_refresh_count or 0),
        "today_result_count": 0,
        "today_merchant_total": 0,
        "running_task_count": 0,
        "queued_task_count": 0,
        "today_task_count": 0,
        "today_date_key": today_date_key,
        "last_result": last_result,
        "last_discovery_failed_count": int(len(discovery_failed_targets)),
        "last_skipped_count": int(len(skipped_targets)),
        "last_failed_account_count": int(len(discovery_failed_targets)),
        "last_failed_reasons": [
            {
                "meituan_user_id": str(item.get("meituan_user_id") or ""),
                "reason": str(item.get("error_message") or item.get("reason") or ""),
                "token_deactivated": bool(item.get("token_deactivated")),
            }
            for item in discovery_failed_targets
            if isinstance(item, dict)
        ],
        "next_run_at": str(runtime.get("next_run_at") or ""),
        "last_run_date": str(runtime.get("last_run_date") or ""),
        "last_run_at": int(runtime.get("last_run_at") or 0),
    }


def _load_web_admin_stats() -> dict[str, Any]:
    db_path = _get_web_query_db_path()
    stats = {
        "total_users": 0,
        "active_users": 0,
        "total_queries": 0,
        "total_query_count": 0,
        "total_tokens": 0,
        "active_tokens": 0,
        "meituan_user_count": 0,
    }
    if not db_path.exists():
        return stats

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_users,
                COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0) AS active_users,
                COALESCE(SUM(query_count), 0) AS total_query_count
            FROM users
            """
        )
        row = cursor.fetchone() or (0, 0, 0)
        stats["total_users"] = int(row[0] or 0)
        stats["active_users"] = int(row[1] or 0)
        stats["total_query_count"] = int(row[2] or 0)

        cursor.execute("SELECT COUNT(*) FROM query_records")
        row = cursor.fetchone() or (0,)
        stats["total_queries"] = int(row[0] or 0)

        cursor.execute(
            """
            SELECT
                COUNT(*) AS total_tokens,
                COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) AS active_tokens,
                COUNT(DISTINCT CASE WHEN meituan_user_id IS NOT NULL AND meituan_user_id != '' THEN meituan_user_id END) AS meituan_user_count
            FROM tokens
            """
        )
        row = cursor.fetchone() or (0, 0, 0)
        stats["total_tokens"] = int(row[0] or 0)
        stats["active_tokens"] = int(row[1] or 0)
        stats["meituan_user_count"] = int(row[2] or 0)
    finally:
        conn.close()
    return stats


def _build_web_admin_user_list_payload(
    *,
    search: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    db_path = _get_web_query_db_path()
    safe_page = max(1, int(page or 1))
    safe_page_size = min(200, max(1, int(page_size or 20)))
    offset = (safe_page - 1) * safe_page_size
    search_value = str(search or "").strip()
    status_value = str(status or "").strip().lower()
    allowed_statuses = {"pending", "approved", "rejected"}

    payload = {
        "users": [],
        "total": 0,
        "page": safe_page,
        "page_size": safe_page_size,
    }
    if not db_path.exists():
        return payload

    where_clauses: list[str] = []
    params: list[Any] = []
    if search_value:
        where_clauses.append("username LIKE ?")
        params.append(f"%{search_value}%")
    if status_value in allowed_statuses:
        where_clauses.append("status = ?")
        params.append(status_value)
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM users {where_sql}", tuple(params))
        row = cursor.fetchone() or (0,)
        payload["total"] = int(row[0] or 0)

        cursor.execute(
            f"""
            SELECT
                id,
                username,
                is_admin,
                is_super_admin,
                query_count,
                status,
                created_at,
                updated_at
            FROM users
            {where_sql}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            tuple([*params, safe_page_size, offset]),
        )
        user_rows = cursor.fetchall() or []
        if not user_rows:
            return payload

        user_ids = [int(row["id"]) for row in user_rows]
        token_counts_by_user: dict[int, dict[str, int]] = {}
        placeholders = ",".join("?" for _ in user_ids)

        cursor.execute(
            f"""
            SELECT
                user_id,
                COUNT(*) AS token_count,
                COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) AS active_token_count
            FROM tokens
            WHERE user_id IN ({placeholders})
            GROUP BY user_id
            """,
            tuple(user_ids),
        )
        for token_row in cursor.fetchall() or []:
            user_id = int(token_row["user_id"] or 0)
            token_counts_by_user[user_id] = {
                "token_count": int(token_row["token_count"] or 0),
                "active_token_count": int(token_row["active_token_count"] or 0),
            }

        running_task_count_by_user: dict[int, int] = {}
        cursor.execute(
            f"""
            SELECT DISTINCT user_id, meituan_user_id
            FROM tokens
            WHERE user_id IN ({placeholders})
              AND meituan_user_id IS NOT NULL
              AND TRIM(meituan_user_id) != ''
            """,
            tuple(user_ids),
        )
        token_bind_rows = cursor.fetchall() or []
        meituan_user_to_user_ids: dict[str, set[int]] = {}
        for token_bind_row in token_bind_rows:
            user_id = int(token_bind_row["user_id"] or 0)
            meituan_user_id = str(token_bind_row["meituan_user_id"] or "").strip()
            if user_id <= 0 or not meituan_user_id:
                continue
            meituan_user_to_user_ids.setdefault(meituan_user_id, set()).add(user_id)

        allowance_db_path = _get_allowance_db_path()
        if allowance_db_path.exists() and meituan_user_to_user_ids:
            allowance_conn = sqlite3.connect(str(allowance_db_path), timeout=10.0)
            allowance_conn.row_factory = sqlite3.Row
            try:
                allowance_cursor = allowance_conn.cursor()
                meituan_user_ids = list(meituan_user_to_user_ids.keys())
                allowance_placeholders = ",".join("?" for _ in meituan_user_ids)
                allowance_cursor.execute(
                    f"""
                    SELECT meituan_user_id, COUNT(DISTINCT task_id) AS running_task_count
                    FROM allowance_tasks
                    WHERE status = 'running'
                      AND meituan_user_id IN ({allowance_placeholders})
                    GROUP BY meituan_user_id
                    """,
                    tuple(meituan_user_ids),
                )
                for allowance_row in allowance_cursor.fetchall() or []:
                    meituan_user_id = str(allowance_row["meituan_user_id"] or "").strip()
                    task_count = int(allowance_row["running_task_count"] or 0)
                    if not meituan_user_id or task_count <= 0:
                        continue
                    for user_id in meituan_user_to_user_ids.get(meituan_user_id, set()):
                        running_task_count_by_user[user_id] = (
                            int(running_task_count_by_user.get(user_id) or 0) + task_count
                        )
            finally:
                allowance_conn.close()

        users: list[dict[str, Any]] = []
        for row in user_rows:
            user_id = int(row["id"] or 0)
            token_meta = token_counts_by_user.get(user_id) or {}
            users.append({
                "id": user_id,
                "username": str(row["username"] or ""),
                "is_admin": bool(row["is_admin"]),
                "is_super_admin": bool(row["is_super_admin"]),
                "query_count": int(row["query_count"] or 0),
                "status": str(row["status"] or ""),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "token_count": int(token_meta.get("token_count") or 0),
                "active_token_count": int(token_meta.get("active_token_count") or 0),
                "running_task_count": int(running_task_count_by_user.get(user_id) or 0),
            })
        payload["users"] = users
    finally:
        conn.close()
    return payload


def _load_allowance_admin_overview() -> dict[str, Any]:
    from utils.meituan_allowance_scheduler import get_allowance_schedule_status
    from utils.meituan_allowance_relay_pool import get_allowance_relay_pool_runtime

    web_stats = _load_web_admin_stats()
    schedule = get_allowance_schedule_status()
    relay_pool = get_allowance_relay_pool_runtime()
    today_date_key = _current_shanghai_date_key()
    active_meituan_user_count = int(web_stats.get("meituan_user_count") or 0)
    overview = {
        "schedule": schedule,
        "relay_pool": relay_pool,
        "active_meituan_user_count": active_meituan_user_count,
        "refresh_target_count": 0,
        "refresh_target_user_count": 0,
        "today_result_count": 0,
        "today_merchant_total": 0,
        "running_task_count": 0,
        "queued_task_count": 0,
        "today_task_count": 0,
        "today_date_key": today_date_key,
        "types": {},
    }
    db_path = _get_allowance_db_path()
    if not db_path.exists():
        for allowance_type in ALLOWANCE_SCHEDULE_TYPES:
            overview["types"][allowance_type] = _empty_allowance_type_overview(
                allowance_type,
                schedule=schedule,
                active_meituan_user_count=active_meituan_user_count,
                refresh_target_count=0,
                refresh_target_user_count=0,
                today_date_key=today_date_key,
            )
        return overview

    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM allowance_refresh_targets")
        row = cursor.fetchone() or (0,)
        overview["refresh_target_count"] = int(row[0] or 0)

        cursor.execute("SELECT COUNT(DISTINCT meituan_user_id) FROM allowance_refresh_targets")
        row = cursor.fetchone() or (0,)
        overview["refresh_target_user_count"] = int(row[0] or 0)

        start_of_day_ts = int(
            datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        for allowance_type in ALLOWANCE_SCHEDULE_TYPES:
            type_overview = _empty_allowance_type_overview(
                allowance_type,
                schedule=schedule,
                active_meituan_user_count=active_meituan_user_count,
                refresh_target_count=int(overview["refresh_target_count"] or 0),
                refresh_target_user_count=int(overview["refresh_target_user_count"] or 0),
                today_date_key=today_date_key,
            )

            cursor.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(merchant_count), 0)
                FROM allowance_daily_results
                WHERE date_key = ? AND allowance_type = ?
                """,
                (today_date_key, allowance_type),
            )
            row = cursor.fetchone() or (0, 0)
            type_overview["today_result_count"] = int(row[0] or 0)
            type_overview["today_merchant_total"] = int(row[1] or 0)

            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0)
                FROM allowance_tasks
                WHERE allowance_type = ?
                """,
                (allowance_type,),
            )
            row = cursor.fetchone() or (0, 0)
            type_overview["running_task_count"] = int(row[0] or 0)
            type_overview["queued_task_count"] = int(row[1] or 0)

            cursor.execute(
                """
                SELECT COUNT(*)
                FROM allowance_tasks
                WHERE allowance_type = ? AND created_at >= ?
                """,
                (allowance_type, start_of_day_ts),
            )
            row = cursor.fetchone() or (0,)
            type_overview["today_task_count"] = int(row[0] or 0)
            overview["types"][allowance_type] = type_overview
    finally:
        conn.close()

    for allowance_type in ALLOWANCE_SCHEDULE_TYPES:
        type_overview = overview["types"].get(allowance_type) or {}
        overview["today_result_count"] += int(type_overview.get("today_result_count") or 0)
        overview["today_merchant_total"] += int(type_overview.get("today_merchant_total") or 0)
        overview["running_task_count"] += int(type_overview.get("running_task_count") or 0)
        overview["queued_task_count"] += int(type_overview.get("queued_task_count") or 0)
        overview["today_task_count"] += int(type_overview.get("today_task_count") or 0)
    return overview


def _normalize_allowance_admin_settings_payload(raw_payload: Any) -> dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    raw_schedule_config = payload.get("schedule_config")
    if not isinstance(raw_schedule_config, dict):
        if isinstance(payload.get("types"), dict):
            raw_schedule_config = {
                "types": payload.get("types"),
            }
        else:
            raw_schedule_config = payload
    return {
        "schedule_config": normalize_allowance_schedule_config(raw_schedule_config),
        "relay_pool_config": normalize_allowance_relay_pool_config(
            payload.get("relay_pool")
            if "relay_pool" in payload
            else payload.get("allowance_relay_pool_config")
        ),
    }


def _normalize_web_user_registration_settings_payload(raw_payload: Any) -> dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    config_payload = payload.get("config") if isinstance(payload.get("config"), dict) else payload
    return {
        "config": normalize_web_user_registration_config(config_payload),
    }


def _admin_setup_required() -> bool:
    admin_users = getattr(app_config, "ADMIN_USERS", {}) or {}
    return not isinstance(admin_users, dict) or not bool(admin_users)


def _admin_config_path() -> Path:
    raw_path = str(os.getenv("WX_SERVICE_CONFIG_FILE") or os.getenv("CONFIG_FILE") or "").strip()
    if raw_path:
        return Path(raw_path).expanduser()

    runtime_data_dir = str(os.getenv("WX_SERVICE_DATA_DIR") or "").strip()
    if runtime_data_dir:
        return Path(runtime_data_dir).expanduser() / "config.toml"

    return resolve_runtime_data_path("config.toml")


def _quote_toml_key(username: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", username):
        return username
    return json.dumps(username, ensure_ascii=False)


def _toml_line_key(line: str) -> str | None:
    match = _ADMIN_LINE_RE.match(line)
    if not match:
        return None
    raw_key = match.group(2).strip()
    if (raw_key.startswith('"') and raw_key.endswith('"')) or (raw_key.startswith("'") and raw_key.endswith("'")):
        return raw_key[1:-1]
    return raw_key


def _find_admin_section(lines: list[str]) -> tuple[int | None, int | None]:
    start: int | None = None
    end: int | None = None
    for index, line in enumerate(lines):
        match = _TOML_SECTION_RE.match(line)
        if not match:
            continue
        if start is None:
            if match.group(1).strip() == "admin_users":
                start = index
            continue
        end = index
        break
    return start, end


def _upsert_admin_user(lines: list[str], username: str, password_hash: str) -> tuple[list[str], str]:
    start, end = _find_admin_section(lines)
    new_line = f'{_quote_toml_key(username)} = "{password_hash}"'
    if start is None:
        output = list(lines)
        if output and output[-1].strip():
            output.append("")
        output.extend(["[admin_users]", new_line])
        return output, "created_section"

    section_end = end if end is not None else len(lines)
    output = list(lines)
    for index in range(start + 1, section_end):
        if _toml_line_key(output[index]) == username:
            output[index] = new_line
            return output, "updated_user"

    insert_at = section_end
    while insert_at > start + 1 and not output[insert_at - 1].strip():
        insert_at -= 1
    output.insert(insert_at, new_line)
    return output, "created_user"


def _replace_admin_users(lines: list[str], username: str, password_hash: str) -> list[str]:
    start, end = _find_admin_section(lines)
    new_line = f'{_quote_toml_key(username)} = "{password_hash}"'
    if start is None:
        output = list(lines)
        if output and output[-1].strip():
            output.append("")
        output.extend(["[admin_users]", new_line])
        return output

    section_end = end if end is not None else len(lines)
    return list(lines[: start + 1]) + [new_line] + list(lines[section_end:])


def get_admin_usernames() -> list[str]:
    admin_users = getattr(app_config, "ADMIN_USERS", {}) or {}
    if not isinstance(admin_users, dict):
        return []
    return sorted(str(username) for username in admin_users.keys())


def set_admin_password(username: str, password_hash: str) -> str:
    if not _ADMIN_USERNAME_RE.fullmatch(username):
        raise ValueError("管理员用户名只能包含字母、数字、下划线、点、@ 和短横线，长度 1-64")
    if not _ADMIN_HASH_RE.fullmatch(password_hash):
        raise ValueError("密码摘要格式不正确")

    config_path = _admin_config_path().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.exists() else []
    output_lines, action = _upsert_admin_user(lines, username, password_hash)

    if config_path.exists():
        backup_path = config_path.with_name(f"{config_path.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(f".{config_path.name}.tmp")
    temp_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    os.replace(temp_path, config_path)
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass

    app_config.reload_config()
    config_package.ADMIN_USERS = app_config.ADMIN_USERS
    if _admin_setup_required():
        raise RuntimeError("管理员账号写入后未能加载，请检查配置路径")
    return action


def set_single_admin_credentials(username: str, password_hash: str) -> str:
    if not _ADMIN_USERNAME_RE.fullmatch(username):
        raise ValueError("管理员用户名只能包含字母、数字、下划线、点、@ 和短横线，长度 1-64")
    if not _ADMIN_HASH_RE.fullmatch(password_hash):
        raise ValueError("密码摘要格式不正确")

    config_path = _admin_config_path().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.exists() else []
    output_lines = _replace_admin_users(lines, username, password_hash)

    if config_path.exists():
        backup_path = config_path.with_name(f"{config_path.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(f".{config_path.name}.tmp")
    temp_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    os.replace(temp_path, config_path)
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass

    app_config.reload_config()
    config_package.ADMIN_USERS = app_config.ADMIN_USERS
    if _admin_setup_required():
        raise RuntimeError("管理员账号写入后未能加载，请检查配置路径")
    clear_all_sessions()
    return "replaced_single_admin"


def _create_initial_admin(username: str, password_hash: str) -> None:
    if not _admin_setup_required():
        raise PermissionError("管理员账号已存在，首次设置入口已关闭")
    set_admin_password(username, password_hash)


def _build_dashboard_overview(username: str) -> dict:
    store_data = load_wechat_account_store()
    account_specific_configs = store_data.get("account_specific_configs", {})
    keyword_responses = store_data.get("keyword_responses", {})
    default_account_id = str(store_data.get("default_account_id", "") or "")
    accounts = store_data.get("accounts", {})
    if not isinstance(account_specific_configs, dict):
        account_specific_configs = {}
    if not isinstance(keyword_responses, dict):
        keyword_responses = {}
    if not isinstance(accounts, dict):
        accounts = {}

    url_mode_labels = {
        "all": "美团 + 大众点评",
        "meituan": "仅美团",
        "dianping": "仅大众点评",
    }

    activity_items = []
    keyword_rule_count = 0
    processor_binding_count = 0
    authorized_user_count = 0

    for account_id, account_config in accounts.items():
        base_config = account_config if isinstance(account_config, dict) else {}
        specific_config = account_specific_configs.get(account_id, {})
        if not isinstance(specific_config, dict):
            specific_config = {}
        account_keywords = keyword_responses.get(account_id, {})
        if not isinstance(account_keywords, dict):
            account_keywords = {}

        processors = [str(item).strip() for item in specific_config.get("enabled_text_processors", []) if str(item).strip()]
        authorized_users = [str(item).strip() for item in specific_config.get("authorized_users", []) if str(item).strip()]
        keyword_count = len(account_keywords)
        processor_count = len(processors)
        keyword_rule_count += keyword_count
        processor_binding_count += processor_count
        authorized_user_count += len(authorized_users)

        missing_fields = [
            field_name
            for field_name in ("appid", "app_secret", "token")
            if not str(base_config.get(field_name, "") or "").strip()
        ]
        if missing_fields:
            status = "warning"
            status_text = "配置待补齐"
        elif processor_count or keyword_count or str(specific_config.get("default_reply", "") or "").strip() or str(specific_config.get("welcome_message", "") or "").strip():
            status = "online"
            status_text = "运行就绪"
        else:
            status = "processing"
            status_text = "基础配置"

        primary_module = ", ".join(processors[:2]) if processors else "默认文本链路"
        if processor_count > 2:
            primary_module = f"{primary_module} 等 {processor_count} 项"

        mode_label = url_mode_labels.get(str(specific_config.get("url_mode", "all") or "all"), "自定义模式")
        detail_parts = [f"模式：{mode_label}"]
        if authorized_users:
            detail_parts.append(f"授权用户 {len(authorized_users)} 人")
        if keyword_count:
            detail_parts.append(f"关键词 {keyword_count} 条")
        elif str(specific_config.get("default_reply", "") or "").strip():
            detail_parts.append("已配置默认回复")
        if account_id == default_account_id:
            detail_parts.insert(0, "默认账号")

        activity_items.append({
            "account_id": str(account_id),
            "name": str(base_config.get("name", account_id) or account_id),
            "module": primary_module,
            "rule_scale": f"关键词 {keyword_count} · 处理器 {processor_count}",
            "status": status,
            "status_text": status_text,
            "detail": " · ".join(detail_parts),
            "is_default": account_id == default_account_id,
        })

    activity_items.sort(key=lambda item: (not item["is_default"], item["name"], item["account_id"]))

    inflight_store = get_inflight_request_store()
    inflight_entries = inflight_store.get_entries_snapshot()
    if not isinstance(inflight_entries, dict):
        inflight_entries = {}
    inflight_values = list(inflight_entries.values())
    completed_entries = [entry for entry in inflight_values if entry.get("completed_at")]
    successful_entries = [entry for entry in completed_entries if entry.get("response") is not None and not entry.get("error")]
    pending_count = sum(1 for entry in inflight_values if not entry.get("completed_at"))
    avg_latency_seconds = None
    if completed_entries:
        durations = [float(entry.get("duration") or 0.0) for entry in completed_entries]
        avg_latency_seconds = round(sum(durations) / len(durations), 2)
    success_rate = round(len(successful_entries) * 100 / len(completed_entries), 1) if completed_entries else None

    proxy_state = get_proxy_runtime_state()
    proxy_pool_size = int(proxy_state.get("pool_size") or 0) if isinstance(proxy_state, dict) else 0
    proxy_pool_valid_size = int(proxy_state.get("pool_valid_size") or 0) if isinstance(proxy_state, dict) else 0
    client_pool_size = 0
    watcher_count = 0
    try:
        from utils import http_client
        client_pool_size = int(http_client.get_client_stats().get("client_pool_size") or 0)
    except Exception:
        client_pool_size = 0
    try:
        from utils.p_value_storage import get_p_value_storage
        from utils.verification_code import (
            get_link_verification_manager,
            get_mt_order_verification_manager,
            get_verification_manager,
        )
        watcher_count += get_verification_manager().get_watcher_count()
        watcher_count += get_link_verification_manager().get_watcher_count()
        watcher_count += get_mt_order_verification_manager().get_watcher_count()
        watcher_count += get_p_value_storage().get_watcher_count()
    except Exception:
        watcher_count = watcher_count or 0

    active_token_count = 0
    try:
        active_token_count = len(access_token_cache)
    except Exception:
        active_token_count = 0

    go_socket_exists = bool(GO_LOCAL_API_SOCKET_PATH and os.path.exists(GO_LOCAL_API_SOCKET_PATH))
    nodes = [
        {
            "name": "Nginx / OpenResty",
            "meta": "入口路由在线",
            "status": "online",
            "status_text": "正常",
        },
        {
            "name": "Python Backend API",
            "meta": f"账号 {len(activity_items)} 个 · Watcher {watcher_count} 个",
            "status": "online",
            "status_text": "正常",
        },
        {
            "name": "代理池 / HTTP 客户端",
            "meta": f"有效代理 {proxy_pool_valid_size}/{proxy_pool_size} · 连接池 {client_pool_size}",
            "status": "online" if proxy_pool_valid_size > 0 or proxy_pool_size == 0 else "warning",
            "status_text": "可用" if proxy_pool_valid_size > 0 or proxy_pool_size == 0 else "待关注",
        },
        {
            "name": "Go Shortlink Server",
            "meta": GO_LOCAL_API_SOCKET_PATH or "未配置 socket",
            "status": "online" if go_socket_exists else "warning",
            "status_text": "在线" if go_socket_exists else "缺失 / 降级",
        },
    ]

    config_payload = get_config()
    miniprogram_count = 0
    if isinstance(config_payload, dict):
        miniprogram_appids = config_payload.get("miniprogram_appids", {})
        if isinstance(miniprogram_appids, dict):
            miniprogram_count = len(miniprogram_appids)

    return {
        "success": True,
        "username": username,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "account_count": len(activity_items),
            "default_account_id": default_account_id,
            "keyword_rule_count": keyword_rule_count,
            "processor_binding_count": processor_binding_count,
            "authorized_user_count": authorized_user_count,
            "miniprogram_count": miniprogram_count,
            "recent_request_count": len(inflight_values),
            "pending_request_count": pending_count,
            "avg_latency_seconds": avg_latency_seconds,
            "success_rate": success_rate,
            "watcher_count": watcher_count,
            "client_pool_size": client_pool_size,
            "proxy_pool_size": proxy_pool_size,
            "proxy_pool_valid_size": proxy_pool_valid_size,
            "active_token_count": active_token_count,
            "passive_reply_timeout_budget": 3.5,
        },
        "activity": activity_items,
        "nodes": nodes,
        "environment": build_runtime_identity(),
    }


@router.get("/", response_class=HTMLResponse)
async def root():
    """
    根路径重定向到登录页面
    """
    return RedirectResponse(url="/login")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """
    返回登录页面
    """
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "setup_required": _admin_setup_required()},
    )


@router.get("/index", response_class=HTMLResponse)
async def index_page(request: Request):
    """
    返回系统首页
    注意：页面本身不验证会话，而是在前端 JavaScript 中验证 Cookie 会话
    如果未登录，前端会自动跳转到 /login
    """
    return templates.TemplateResponse(request, "dashboard.html", {"request": request})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"request": request})


@router.get("/web/login", response_class=HTMLResponse)
async def web_login_page(request: Request):
    registration_runtime = get_web_user_auto_approve_runtime()
    return web_templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "registration_auto_approve": bool(registration_runtime.get("enabled")),
        },
    )


@router.get("/web/query", response_class=HTMLResponse)
async def web_query_page(request: Request):
    return web_templates.TemplateResponse(request, "query.html", {"request": request})


@router.get("/web/admin", response_class=HTMLResponse)
async def web_admin_page(request: Request):
    return web_templates.TemplateResponse(request, "admin.html", {"request": request})


@router.get("/web/admin/api/allowance-settings")
async def get_allowance_settings(request: Request):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)
    except Exception as exc:
        logger.warning("读取津贴定时设置失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "读取津贴设置失败"}, status_code=500)

    store = load_system_settings_store()
    config = normalize_allowance_schedule_config(store.get("allowance_schedule_config", {}))
    relay_pool_config = normalize_allowance_relay_pool_config(store.get("allowance_relay_pool_config", {}))
    from utils.meituan_allowance_scheduler import get_allowance_schedule_status
    from utils.meituan_allowance_relay_pool import get_allowance_relay_pool_runtime

    return JSONResponse({
        "success": True,
        "config": config,
        "relay_pool": {
            "config": relay_pool_config,
            "runtime": get_allowance_relay_pool_runtime(),
        },
        "runtime": get_allowance_schedule_status(),
        "overview": _load_allowance_admin_overview(),
    })


@router.post("/web/admin/api/allowance-settings")
async def save_allowance_settings(request: Request):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        payload = await request.json()
        normalized_payload = _normalize_allowance_admin_settings_payload(payload)
        store = load_system_settings_store()
        store["allowance_schedule_config"] = normalized_payload["schedule_config"]
        store["allowance_relay_pool_config"] = normalized_payload["relay_pool_config"]
        save_system_settings_store(store)
        from utils.meituan_allowance_scheduler import (
            get_allowance_schedule_status,
            notify_allowance_schedule_updated,
        )
        from utils.meituan_allowance_relay_pool import get_allowance_relay_pool_runtime

        notify_allowance_schedule_updated()

        return JSONResponse({
            "success": True,
            "message": "津贴定时设置已保存",
            "config": normalized_payload["schedule_config"],
            "relay_pool": {
                "config": normalized_payload["relay_pool_config"],
                "runtime": get_allowance_relay_pool_runtime(),
            },
            "runtime": get_allowance_schedule_status(),
            "overview": _load_allowance_admin_overview(),
        })
    except Exception as exc:
        logger.warning("保存津贴定时设置失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "保存津贴定时设置失败"}, status_code=500)


@router.post("/web/admin/api/allowance-settings/run")
async def run_allowance_settings_now(request: Request):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        payload = await request.json()
        allowance_type = str((payload or {}).get("allowance_type") or "").strip()
        if allowance_type not in ALLOWANCE_SCHEDULE_TYPES:
            return JSONResponse({"success": False, "error": "allowance_type 不合法"}, status_code=400)

        from utils.meituan_allowance_scheduler import (
            get_allowance_schedule_status,
            notify_allowance_schedule_updated,
            run_allowance_schedule_once,
        )

        result = await run_allowance_schedule_once(allowance_type)
        notify_allowance_schedule_updated()

        return JSONResponse({
            "success": True,
            "message": "立即执行已完成",
            "allowance_type": allowance_type,
            "result": result,
            "runtime": get_allowance_schedule_status(),
            "overview": _load_allowance_admin_overview(),
        })
    except Exception as exc:
        logger.warning("手动执行津贴自动更新失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "手动执行津贴自动更新失败"}, status_code=500)


@router.get("/web/admin/api/registration-settings")
async def get_web_registration_settings(request: Request):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)
    except Exception as exc:
        logger.warning("读取注册设置失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "读取注册设置失败"}, status_code=500)

    store = load_system_settings_store()
    config = normalize_web_user_registration_config(store.get("web_user_registration_config", {}))
    return JSONResponse({
        "success": True,
        "config": config,
        "runtime": get_web_user_auto_approve_runtime(),
    })


@router.post("/web/admin/api/registration-settings")
async def save_web_registration_settings(request: Request):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        payload = await request.json()
        normalized_payload = _normalize_web_user_registration_settings_payload(payload)
        store = load_system_settings_store()
        store["web_user_registration_config"] = normalized_payload["config"]
        save_system_settings_store(store)
        return JSONResponse({
            "success": True,
            "message": "注册设置已保存",
            "config": normalized_payload["config"],
            "runtime": get_web_user_auto_approve_runtime(),
        })
    except Exception as exc:
        logger.warning("保存注册设置失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "保存注册设置失败"}, status_code=500)


@router.get("/web/admin/api/stats")
async def get_web_admin_stats(request: Request):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)
    except Exception as exc:
        logger.warning("读取 Web 管理后台统计失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "读取统计失败"}, status_code=500)

    web_stats = _load_web_admin_stats()
    allowance_overview = _load_allowance_admin_overview()
    order_query_overview = _load_web_order_query_overview()
    payload = {
        **web_stats,
        "allowance": allowance_overview,
        "order_query": order_query_overview,
        "registration": {
            "config": normalize_web_user_registration_config(
                load_system_settings_store().get("web_user_registration_config", {})
            ),
            "runtime": get_web_user_auto_approve_runtime(),
        },
    }
    return JSONResponse(payload)


@router.get("/web/admin/api/users")
async def get_web_admin_users(request: Request):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)
    except Exception as exc:
        logger.warning("读取 Web 管理后台用户列表失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "读取用户列表失败"}, status_code=500)

    params = request.query_params
    search = str(params.get("search") or "").strip()
    status = str(params.get("status") or "").strip()
    try:
        page = max(1, int(str(params.get("page") or "1").strip() or "1"))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(200, max(1, int(str(params.get("page_size") or "20").strip() or "20")))
    except (TypeError, ValueError):
        page_size = 20

    try:
        payload = _build_web_admin_user_list_payload(
            search=search,
            status=status,
            page=page,
            page_size=page_size,
        )
        return JSONResponse(payload)
    except Exception as exc:
        logger.warning("查询 Web 管理后台用户列表失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "查询用户列表失败"}, status_code=500)


@router.get("/web/admin/api/announcements")
async def get_web_admin_announcements(request: Request):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    params = request.query_params
    try:
        page = max(1, int(str(params.get("page") or "1").strip() or "1"))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(200, max(1, int(str(params.get("page_size") or "20").strip() or "20")))
    except (TypeError, ValueError):
        page_size = 20

    try:
        storage = get_web_announcement_storage()
        payload = storage.list_announcements(
            search=str(params.get("search") or "").strip(),
            status=str(params.get("status") or "").strip(),
            target_role=str(params.get("target_role") or "").strip(),
            surface=str(params.get("surface") or "").strip(),
            page=page,
            page_size=page_size,
        )
        return JSONResponse({"success": True, **payload})
    except Exception as exc:
        logger.warning("读取公告列表失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "读取公告列表失败"}, status_code=500)


@router.post("/web/admin/api/announcements")
async def create_web_admin_announcement(request: Request):
    try:
        current_admin = await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        payload = await request.json()
        storage = get_web_announcement_storage()
        item = storage.create_announcement(payload if isinstance(payload, dict) else {}, str(current_admin.get("username") or ""))
        return JSONResponse({"success": True, "message": "公告已创建", "item": item})
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.warning("创建公告失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "创建公告失败"}, status_code=500)


@router.get("/web/admin/api/announcements/{announcement_id}")
async def get_web_admin_announcement_detail(request: Request, announcement_id: int):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        storage = get_web_announcement_storage()
        item = storage.get_announcement(announcement_id)
        if not item:
            return JSONResponse({"success": False, "error": "公告不存在"}, status_code=404)
        return JSONResponse({"success": True, "item": item})
    except Exception as exc:
        logger.warning("读取公告详情失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "读取公告详情失败"}, status_code=500)


@router.put("/web/admin/api/announcements/{announcement_id}")
async def update_web_admin_announcement(request: Request, announcement_id: int):
    try:
        current_admin = await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        payload = await request.json()
        storage = get_web_announcement_storage()
        item = storage.update_announcement(
            announcement_id,
            payload if isinstance(payload, dict) else {},
            str(current_admin.get("username") or ""),
        )
        if not item:
            return JSONResponse({"success": False, "error": "公告不存在"}, status_code=404)
        return JSONResponse({"success": True, "message": "公告已更新", "item": item})
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        logger.warning("更新公告失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "更新公告失败"}, status_code=500)


@router.post("/web/admin/api/announcements/{announcement_id}/publish")
async def publish_web_admin_announcement(request: Request, announcement_id: int):
    try:
        current_admin = await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        storage = get_web_announcement_storage()
        item = storage.set_status(announcement_id, "published", str(current_admin.get("username") or ""))
        if not item:
            return JSONResponse({"success": False, "error": "公告不存在"}, status_code=404)
        return JSONResponse({"success": True, "message": "公告已发布", "item": item})
    except Exception as exc:
        logger.warning("发布公告失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "发布公告失败"}, status_code=500)


@router.post("/web/admin/api/announcements/{announcement_id}/offline")
async def offline_web_admin_announcement(request: Request, announcement_id: int):
    try:
        current_admin = await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        storage = get_web_announcement_storage()
        item = storage.set_status(announcement_id, "offline", str(current_admin.get("username") or ""))
        if not item:
            return JSONResponse({"success": False, "error": "公告不存在"}, status_code=404)
        return JSONResponse({"success": True, "message": "公告已下线", "item": item})
    except Exception as exc:
        logger.warning("下线公告失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "下线公告失败"}, status_code=500)


@router.delete("/web/admin/api/announcements/{announcement_id}")
async def delete_web_admin_announcement(request: Request, announcement_id: int):
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        storage = get_web_announcement_storage()
        deleted = storage.delete_announcement(announcement_id)
        if not deleted:
            return JSONResponse({"success": False, "error": "公告不存在"}, status_code=404)
        return JSONResponse({"success": True, "message": "公告已删除"})
    except Exception as exc:
        logger.warning("删除公告失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "删除公告失败"}, status_code=500)


@router.post("/web/admin/api/announcements/{announcement_id}/move")
async def move_web_admin_announcement(request: Request, announcement_id: int):
    try:
        current_admin = await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        payload = await request.json()
        direction = ""
        if isinstance(payload, dict):
            direction = str(payload.get("direction") or "").strip().lower()
        if direction not in {"up", "down"}:
            return JSONResponse({"success": False, "error": "direction 只支持 up 或 down"}, status_code=400)
        storage = get_web_announcement_storage()
        item = storage.move_announcement(announcement_id, direction, str(current_admin.get("username") or ""))
        if not item:
            return JSONResponse({"success": False, "error": "公告不存在"}, status_code=404)
        return JSONResponse({"success": True, "message": "公告排序已更新", "item": item})
    except Exception as exc:
        logger.warning("调整公告排序失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "调整公告排序失败"}, status_code=500)


@router.get("/web/api/announcements/current")
async def get_current_web_announcements(request: Request):
    surface = str(request.query_params.get("surface") or "").strip().lower()
    storage = get_web_announcement_storage()

    try:
        if surface == "login":
            payload = storage.get_current_announcements(surface="login", viewer_role="user", user_id=None)
            return JSONResponse({"success": True, "surface": "login", **payload})

        if surface == "admin":
            await _get_current_web_admin_user(request)
            payload = storage.get_current_announcements(surface="admin", viewer_role="admin", user_id=None)
            payload["popup_item"] = None
            return JSONResponse({"success": True, "surface": "admin", **payload})

        current_user = await _get_current_web_query_user(request)
        if bool(current_user.get("is_admin")):
            return JSONResponse({
                "success": True,
                "surface": "query",
                "banner_items": [],
                "popup_item": None,
            })
        user_id = _resolve_web_query_user_id(current_user)
        payload = storage.get_current_announcements(surface="query", viewer_role="user", user_id=user_id)
        return JSONResponse({"success": True, "surface": "query", **payload})
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) or "需要先登录" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)
    except Exception as exc:
        logger.warning("读取当前公告失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "读取当前公告失败"}, status_code=500)


@router.post("/web/api/announcements/{announcement_id}/read")
async def mark_web_announcement_read(request: Request, announcement_id: int):
    try:
        current_user = await _get_current_web_query_user(request)
    except PermissionError as exc:
        status_code = 401 if "登录已过期" in str(exc) or "需要先登录" in str(exc) else 403
        return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)

    try:
        if bool(current_user.get("is_admin")):
            return JSONResponse({"success": True, "message": "管理员无需记录公告已读"})
        user_id = _resolve_web_query_user_id(current_user)
        if user_id <= 0:
            return JSONResponse({"success": False, "error": "无法识别当前用户"}, status_code=400)
        storage = get_web_announcement_storage()
        item = storage.get_announcement(announcement_id)
        if not item:
            return JSONResponse({"success": False, "error": "公告不存在"}, status_code=404)
        storage.mark_read(announcement_id, user_id)
        return JSONResponse({"success": True, "message": "已记录公告已读"})
    except Exception as exc:
        logger.warning("记录公告已读失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "记录公告已读失败"}, status_code=500)


@router.post("/api/auth/login")
async def login(request: Request):
    """
    用户登录接口
    
    """
    try:
        client_ip = _client_ip_from_request(request)
        data = await request.json()
        username = data.get("username", "").strip()
        password_hash = data.get("password_hash", "")
        timestamp = data.get("timestamp")
        nonce = data.get("nonce")
        remember_me = data.get("remember_me", False)
        
        if not username or not password_hash:
            return JSONResponse({
                "success": False,
                "error": "用户名和密码不能为空"
            }, status_code=400)
        
        if timestamp is None or nonce is None:
            return JSONResponse({
                "success": False,
                "error": "请求参数不完整"
            }, status_code=400)

        if _is_login_rate_limited(username, client_ip):
            logger.warning("登录请求被限流: username=%s client_ip=%s", username, client_ip)
            return JSONResponse({
                "success": False,
                "error": "登录失败次数过多，请稍后再试"
            }, status_code=429)
        
                            
        if not verify_credentials(username, password_hash, timestamp, nonce):
            _record_login_failure(username, client_ip)
            return JSONResponse({
                "success": False,
                "error": "用户名或密码错误"
            }, status_code=401)
        
                   
        token = create_session_token(username, remember_me)
        _clear_login_failures(username, client_ip)

        response = JSONResponse({
            "success": True,
            "message": "登录成功"
        })
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=7 * 24 * 60 * 60 if remember_me else 24 * 60 * 60,
            httponly=True,
            samesite="lax",
            secure=_auth_cookie_secure(request),
            path="/",
        )
        return response
        
    except Exception as e:
        logger.error(f"登录异常: {e}")
        return JSONResponse({
            "success": False,
            "error": "登录失败，请稍后重试"
        }, status_code=500)


@router.get("/api/auth/setup/status")
async def setup_status():
    return JSONResponse({
        "success": True,
        "setup_required": _admin_setup_required()
    })


@router.post("/api/auth/setup")
async def setup_admin(request: Request):
    """
    首次部署时通过浏览器创建第一个管理员。
    只有当前没有任何管理员账号时允许调用；创建后立即关闭入口。
    """
    try:
        if not _admin_setup_required():
            return JSONResponse({
                "success": False,
                "error": "管理员账号已存在，请直接登录"
            }, status_code=409)

        client_ip = _client_ip_from_request(request)
        data = await request.json()
        username = str(data.get("username", "") or "").strip()
        password_hash = str(data.get("password_hash", "") or "").strip().lower()
        if not username or not password_hash:
            return JSONResponse({
                "success": False,
                "error": "用户名和密码不能为空"
            }, status_code=400)
        if _is_login_rate_limited(username, client_ip):
            return JSONResponse({
                "success": False,
                "error": "请求次数过多，请稍后再试"
            }, status_code=429)

        try:
            _create_initial_admin(username, password_hash)
        except ValueError as exc:
            _record_login_failure(username, client_ip)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
        except PermissionError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=409)

        token = create_session_token(username, remember_me=False)
        _clear_login_failures(username, client_ip)
        response = JSONResponse({
            "success": True,
            "message": "管理员账号已创建"
        })
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=24 * 60 * 60,
            httponly=True,
            samesite="lax",
            secure=_auth_cookie_secure(request),
            path="/",
        )
        return response
    except Exception as exc:
        logger.error("首次管理员设置失败: %s", exc)
        return JSONResponse({
            "success": False,
            "error": "初始化失败，请稍后重试"
        }, status_code=500)


@router.get("/api/auth/verify")
async def verify_token(current_user: str = Depends(get_current_user)):
    """
    验证当前 Cookie 会话是否有效
    """
    return JSONResponse({
        "success": True,
        "username": current_user
    })


@router.get("/api/dashboard/overview")
async def dashboard_overview(current_user: str = Depends(get_current_user)):
    return JSONResponse(_build_dashboard_overview(current_user))


@router.post("/api/auth/logout")
async def logout(request: Request):
    """
    用户登出
    """
    cookie_token = str(request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if cookie_token:
        clear_session(cookie_token)
    
    response = JSONResponse({
        "success": True,
        "message": "登出成功"
    })
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
