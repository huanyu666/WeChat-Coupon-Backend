"""
Proxy the customer-facing meituan-query web app through FastAPI.
"""
from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from utils import http_client
from utils.logger import setup_logger
from utils.order_leaderboard_service import normalize_timestamp_seconds, record_leaderboard_hit


logger = setup_logger(__name__)
router = APIRouter(prefix="", tags=["美团查询客户Web代理"])

GO_WEB_SOCKET_PATH = os.getenv(
    "GO_PUBLIC_WEB_SOCKET_PATH",
    "/run/wx_service/meituan-query.sock",
)
GO_WEB_BASE_URL = "http://localhost"

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
    return candidates


def _record_web_leaderboard_hits(path: str, payload: Any) -> None:
    for item in _iter_leaderboard_candidates(path, payload):
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


async def _proxy_go_web_request(request: Request, path: str) -> Response:
    if not os.path.exists(GO_WEB_SOCKET_PATH):
        logger.warning("Go 客户 Web socket 不存在: %s", GO_WEB_SOCKET_PATH)
        return JSONResponse(
            {"detail": "客户查询 Web 服务未启动"},
            status_code=503,
        )

    normalized_path = "/" + str(path or "").lstrip("/")
    target_url = f"{GO_WEB_BASE_URL}{normalized_path}"
    try:
        upstream = await http_client.request(
            request.method,
            target_url,
            params=request.query_params,
            content=await request.body(),
            headers=_proxy_headers(request),
            timeout=60,
            uds=GO_WEB_SOCKET_PATH,
        )
    except Exception as exc:
        logger.error("Go 客户 Web 代理失败: path=%s error=%s", normalized_path, exc, exc_info=True)
        return JSONResponse(
            {"detail": "客户查询 Web 服务暂不可用"},
            status_code=502,
        )

    if upstream.status_code < 400:
        try:
            response_payload = _extract_json_payload(bytes(upstream.content or b""))
            if response_payload is not None:
                _record_web_leaderboard_hits(normalized_path, response_payload)
        except Exception as exc:
            logger.warning("解析 Web 代理返回并写入排行榜失败: path=%s error=%s", normalized_path, exc)

    response = Response(
        content=upstream.content,
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
