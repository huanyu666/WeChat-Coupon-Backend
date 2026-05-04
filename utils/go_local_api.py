from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional

from . import http_client


GO_LOCAL_API_BASE_URL = "http://localhost"
GO_LOCAL_API_SOCKET_PATH = os.getenv(
    "GO_INTERNAL_API_SOCKET_PATH",
    "/run/wx_service/meituan-query-internal.sock",
)
GO_SHORTLINK_PUBLIC_BASE_URL = os.getenv(
    "GO_SHORTLINK_PUBLIC_BASE_URL",
    "",
)


def get_go_runtime_diagnostics() -> Dict[str, Any]:
    try:
        from utils.shortlink_service import get_shortlink_config

        effective_shortlink_base_url = get_shortlink_config().public_base_url
    except Exception:
        effective_shortlink_base_url = GO_SHORTLINK_PUBLIC_BASE_URL
    return {
        "go_socket_exists": os.path.exists(GO_LOCAL_API_SOCKET_PATH),
        "go_shortlink_public_base_url": effective_shortlink_base_url,
        "go_shortlink_public_host": _normalize_shortlink_host(base_url=effective_shortlink_base_url),
    }


def _build_url(path: str) -> str:
    normalized_path = "/" + str(path or "").lstrip("/")
    return GO_LOCAL_API_BASE_URL + normalized_path


def _normalize_shortlink_host(domain_host: str = "", base_url: str = "") -> str:
    from urllib.parse import urlparse

    normalized_host = str(domain_host or "").strip().lower()
    if normalized_host:
        return normalized_host
    normalized_base_url = str(base_url or "").strip()
    if not normalized_base_url:
        return ""
    parsed = urlparse(normalized_base_url if "://" in normalized_base_url else f"http://{normalized_base_url}")
    return str(parsed.netloc or "").strip().lower()


def build_public_shortlink_path(
    path: str,
    *,
    domain_host: str = "",
    base_url: str = "",
) -> str:
    return "/" + str(path or "").lstrip("/")


def build_public_shortlink_url(
    path: str,
    *,
    domain_host: str = "",
    base_url: str = "",
) -> str:
    from utils.shortlink_service import build_public_shortlink_url as _build_public_shortlink_url

    return _build_public_shortlink_url(path, base_url=base_url)


def rewrite_public_shortlink_text(text: str, *, domain_host: str = "") -> str:
    return str(text or "")


def _raise_for_invalid_response(response: http_client.Response) -> Dict[str, Any]:
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise Exception("本地接口返回格式错误")
    if payload.get("success") is False:
        raise Exception(str(payload.get("message") or "本地接口调用失败"))
    return payload


async def post_json_async(path: str, payload: Dict[str, Any], timeout: float = 2.0) -> Dict[str, Any]:
    response = await http_client.post(
        _build_url(path),
        json=payload,
        timeout=timeout,
        uds=GO_LOCAL_API_SOCKET_PATH,
    )
    return _raise_for_invalid_response(response)


async def get_json_async(path: str, params: Dict[str, Any], timeout: float = 3.0) -> Dict[str, Any]:
    response = await http_client.get(
        _build_url(path),
        params=params,
        timeout=timeout,
        uds=GO_LOCAL_API_SOCKET_PATH,
    )
    return _raise_for_invalid_response(response)


async def query_leaderboard_async(params: Dict[str, Any], timeout: float = 180.0) -> Dict[str, Any]:
    return await get_json_async("/internal/leaderboard/query", params, timeout)


async def ingest_leaderboard_hit_async(payload: Dict[str, Any], timeout: float = 2.0) -> Dict[str, Any]:
    path = "/internal/leaderboard/ingest"
    try:
        return await post_json_async(path, payload, timeout=timeout)
    except Exception as exc:
        raise Exception(
            f"{path} 调用失败 payload={_summarize_leaderboard_payload(payload)} error={_format_exception_message(exc)}"
        ) from exc


async def resolve_random_milliseconds_async(
    service_order_id: Optional[str] = None,
    order_id: Optional[str] = None,
    timeout: float = 1.5,
) -> Dict[str, Any]:
    return await post_json_async(
        "/internal/order-random-ms/resolve",
        {
            "service_order_id": str(service_order_id or "").strip(),
            "order_id": str(order_id or "").strip(),
        },
        timeout=timeout,
    )


async def create_shortlink_async(url: str, ttl_seconds: int, timeout: float = 1.5) -> Dict[str, Any]:
    from utils.shortlink_service import create_shortlink_async as _create_shortlink_async

    return await _create_shortlink_async(url, ttl_seconds=int(ttl_seconds))


async def transform_shortlinks_in_text_async(
    text: str,
    domain_host: str,
    ttl_seconds: int,
    max_success_count: int,
    timeout: float = 6.0,
) -> Dict[str, Any]:
    from utils.shortlink_service import transform_shortlinks_in_text_async as _transform_shortlinks_in_text_async

    return await _transform_shortlinks_in_text_async(
        text,
        ttl_seconds=int(ttl_seconds),
        max_success_count=int(max_success_count),
        include_bare_urls=True,
    )


def fallback_random_millisecond(key: str) -> int:
    normalized = str(key or "").strip()
    if not normalized:
        return 0
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 1000


def _format_exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return f"{exc.__class__.__name__}: {message}"
    return exc.__class__.__name__


def _summarize_leaderboard_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "to_user_name": str(payload.get("to_user_name") or "").strip(),
        "user_id": str(payload.get("user_id") or "").strip(),
        "service_order_id": str(payload.get("service_order_id") or "").strip(),
        "order_id": str(payload.get("order_id") or "").strip(),
        "poi_id_str": str(payload.get("poi_id_str") or "").strip(),
        "poi_name": str(payload.get("poi_name") or "").strip(),
        "accept_time": payload.get("accept_time"),
    }
