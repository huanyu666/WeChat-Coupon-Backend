from __future__ import annotations

import hashlib
import os
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from . import http_client


GO_LOCAL_API_BASE_URL = "http://localhost"
GO_LOCAL_API_SOCKET_PATH = os.getenv(
    "GO_INTERNAL_API_SOCKET_PATH",
    "/run/wx_service/meituan-query-internal.sock",
)
GO_SHORTLINK_PUBLIC_BASE_URL = os.getenv(
    "GO_SHORTLINK_PUBLIC_BASE_URL",
    "http://jd2.top",
)
JD2_SHORTLINK_HOST = "jd2.top"


def _build_url(path: str) -> str:
    normalized_path = "/" + str(path or "").lstrip("/")
    return GO_LOCAL_API_BASE_URL + normalized_path


def _normalize_shortlink_host(domain_host: str = "", base_url: str = "") -> str:
    normalized_host = str(domain_host or "").strip().lower()
    if normalized_host:
        return normalized_host

    normalized_base_url = str(base_url or "").strip()
    if not normalized_base_url:
        return ""
    parsed = urlparse(
        normalized_base_url
        if "://" in normalized_base_url
        else f"http://{normalized_base_url}"
    )
    return str(parsed.netloc or "").strip().lower()


def build_public_shortlink_path(
    path: str,
    *,
    domain_host: str = "",
    base_url: str = "",
) -> str:
    normalized_path = "/" + str(path or "").lstrip("/")
    normalized_host = _normalize_shortlink_host(domain_host=domain_host, base_url=base_url)
    if normalized_host == JD2_SHORTLINK_HOST and normalized_path.startswith("/key/"):
        short_key = normalized_path[len("/key/") :].lstrip("/")
        return f"/{short_key}" if short_key else "/"
    return normalized_path


def build_public_shortlink_url(
    path: str,
    *,
    domain_host: str = "",
    base_url: str = "",
) -> str:
    normalized_base_url = str(base_url or GO_SHORTLINK_PUBLIC_BASE_URL).strip().rstrip("/")
    return normalized_base_url + build_public_shortlink_path(
        path,
        domain_host=domain_host,
        base_url=normalized_base_url,
    )


def rewrite_public_shortlink_text(text: str, *, domain_host: str = "") -> str:
    normalized_text = str(text or "")
    normalized_host = _normalize_shortlink_host(
        domain_host=domain_host,
        base_url=GO_SHORTLINK_PUBLIC_BASE_URL,
    )
    if normalized_host != JD2_SHORTLINK_HOST or not normalized_text:
        return normalized_text
    return (
        normalized_text.replace(f"http://{JD2_SHORTLINK_HOST}/key/", f"http://{JD2_SHORTLINK_HOST}/")
        .replace(f"https://{JD2_SHORTLINK_HOST}/key/", f"https://{JD2_SHORTLINK_HOST}/")
    )


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
    return await post_json_async(
        "/internal/shortlink/create",
        {
            "url": str(url or "").strip(),
            "ttl_seconds": int(ttl_seconds),
        },
        timeout=timeout,
    )


async def transform_shortlinks_in_text_async(
    text: str,
    domain_host: str,
    ttl_seconds: int,
    max_success_count: int,
    timeout: float = 6.0,
) -> Dict[str, Any]:
    return await post_json_async(
        "/internal/shortlink/transform-text",
        {
            "text": str(text or ""),
            "domain_host": str(domain_host or "").strip(),
            "ttl_seconds": int(ttl_seconds),
            "max_success_count": int(max_success_count),
        },
        timeout=timeout,
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
