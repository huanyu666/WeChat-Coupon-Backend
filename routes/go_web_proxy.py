"""
Proxy the customer-facing meituan-query web app through FastAPI.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from utils import http_client
from utils.logger import setup_logger
from utils.order_query_background import submit_order_query_background
from utils.order_query_capacity import BUSY_MESSAGE, OrderQueryCapacityBusy, acquire_order_query_capacity
from utils.order_leaderboard_service import (
    get_global_leaderboard_config,
    normalize_timestamp_seconds,
    record_leaderboard_hit,
    resolve_primary_leaderboard_url,
)


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


def _extract_json_payload(response_content: bytes) -> Any:
    if not response_content:
        return None
    try:
        return json.loads(response_content.decode("utf-8"))
    except Exception:
        return None


def _iter_leaderboard_candidates(path: str, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not payload.get("success"):
        return []
    data = payload.get("data")
    candidates: list[dict[str, Any]] = []
    normalized_path = str(path or "")

    def append_candidate(item: Any, source_hint: str) -> None:
        if not isinstance(item, dict):
            return
        poi_name = str(item.get("poi_name") or item.get("title") or "").strip()
        accept_timestamp = normalize_timestamp_seconds(item.get("accept_time", item.get("acceptTime")))
        service_order_id = str(item.get("service_order_id") or item.get("serviceOrderId") or "").strip()
        order_id = str(item.get("order_id") or item.get("orderId") or "").strip()
        if not poi_name or accept_timestamp is None or (not service_order_id and not order_id):
            return
        candidates.append({
            "source": source_hint,
            "poi_name": poi_name,
            "accept_timestamp": int(accept_timestamp),
            "service_order_id": service_order_id,
            "order_id": order_id,
        })

    if normalized_path.endswith("/web/api/query"):
        if isinstance(data, list):
            for item in data:
                append_candidate(item, "web")
    elif normalized_path.endswith("/web/api/query-ins-batch"):
        if isinstance(data, list):
            for item in data:
                append_candidate(item, "web")
    elif normalized_path.endswith("/web/api/query-ins-notify"):
        if isinstance(data, list):
            for item in data:
                append_candidate(item, "web")
        elif isinstance(data, dict):
            append_candidate(data, "web")
    elif normalized_path.endswith("/web/api/query-ins-orders") or normalized_path.endswith("/web/api/orders/list-lookup"):
        if isinstance(data, list):
            for item in data:
                append_candidate(item, "web")
        elif isinstance(data, dict):
            for key in ("orders", "items", "records", "list"):
                nested = data.get(key)
                if isinstance(nested, list):
                    for item in nested:
                        append_candidate(item, "web")
    return candidates


def _record_web_leaderboard_candidates(path: str, candidates: list[dict[str, Any]]) -> None:
    for item in candidates:
        try:
            record_leaderboard_hit(
                source=str(item.get("source") or "web"),
                poi_name=str(item.get("poi_name") or ""),
                accept_timestamp=int(item.get("accept_timestamp") or 0),
                service_order_id=str(item.get("service_order_id") or ""),
                order_id=str(item.get("order_id") or ""),
            )
        except Exception as exc:
            logger.warning("Web 排行榜写入失败: path=%s error=%s item=%s", path, exc, item)


def _enqueue_web_leaderboard_hits(path: str, payload: Any) -> None:
    candidates = _iter_leaderboard_candidates(path, payload)
    if not candidates:
        return
    submit_order_query_background(
        "web_leaderboard",
        lambda: asyncio.to_thread(_record_web_leaderboard_candidates, path, candidates),
        logger=logger,
    )


def _inject_global_leaderboard_url(payload: Any) -> Any:
    """If global leaderboard is enabled, inject leaderboard_url into the JSON response."""
    config = get_global_leaderboard_config()
    if not config.get("enabled"):
        return payload
    leaderboard_url = config.get("leaderboard_url") or resolve_primary_leaderboard_url()
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
    target_url = f"{GO_WEB_BASE_URL}{normalized_path}"

    async def fetch_upstream() -> http_client.Response:
        return await http_client.request(
            request.method,
            target_url,
            params=request.query_params,
            content=await request.body(),
            headers=_proxy_headers(request),
            timeout=60,
            uds=GO_WEB_SOCKET_PATH,
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
                _enqueue_web_leaderboard_hits(normalized_path, response_payload)
                injected = _inject_global_leaderboard_url(response_payload)
                if injected is not response_payload:
                    modified_content = json.dumps(injected, ensure_ascii=False).encode("utf-8")
        except Exception as exc:
            logger.warning("解析 Web 代理返回并写入排行榜失败: path=%s error=%s", normalized_path, exc)

    response = Response(
        content=modified_content if modified_content is not None else upstream.content,
        status_code=upstream.status_code,
    )
    _copy_response_headers(upstream, response)
    return response


@router.api_route("/web", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_go_web_root(request: Request):
    return await _proxy_go_web_request(request, "/web")


@router.api_route("/web/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy_go_web_path(request: Request, path: str):
    return await _proxy_go_web_request(request, f"/web/{path}")
