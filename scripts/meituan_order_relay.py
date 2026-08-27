from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


LISTEN_HOST = os.getenv("MEITUAN_ORDER_RELAY_HOST", "0.0.0.0").strip() or "0.0.0.0"
LISTEN_PORT = int(os.getenv("MEITUAN_ORDER_RELAY_PORT", "18080") or "18080")
RELAY_SECRET = str(os.getenv("MEITUAN_ORDER_RELAY_SECRET") or "").strip()
PROXY_API_URL = str(os.getenv("MEITUAN_ORDER_PROXY_API_URL") or "").strip()
PROXY_API_TIMEOUT_SECONDS = max(1.0, float(os.getenv("MEITUAN_ORDER_PROXY_API_TIMEOUT_SECONDS", "2") or "2"))
UPSTREAM_TIMEOUT_SECONDS = max(3.0, float(os.getenv("MEITUAN_ORDER_RELAY_TIMEOUT_SECONDS", "10") or "10"))
PROXY_POOL_ENABLED = str(os.getenv("MEITUAN_ORDER_PROXY_POOL_ENABLED", "false")).strip().lower() not in {"0", "false", "no", "off"}
PROXY_POOL_SIZE = min(20, max(1, int(os.getenv("MEITUAN_ORDER_PROXY_POOL_SIZE", "3") or "3")))
PROXY_MAX_USE_COUNT = min(500, max(1, int(os.getenv("MEITUAN_ORDER_PROXY_MAX_USE_COUNT", "30") or "30")))
PROXY_MAX_AGE_SECONDS = min(3600, max(10, int(os.getenv("MEITUAN_ORDER_PROXY_MAX_AGE_SECONDS", "60") or "60")))
MAX_CONCURRENCY = min(64, max(1, int(os.getenv("MEITUAN_ORDER_RELAY_MAX_CONCURRENCY", "12") or "12")))

OPERATIONS = {
    # The target URL for this operation is supplied by the authenticated main
    # service so changing the upstream does not require redeploying the Relay.
    "third_party_order": ("POST", ""),
    "order_history_status": ("POST", "https://wx.waimai.meituan.com/weapp/v2/order/historystatus"),
    "order_center_orders": ("GET", "https://ordercenter.meituan.com/ordercenter/user/orders"),
    "insurance_list_orders": ("GET", "https://insurance.meituan.com/access-api/center/listPage/orders"),
    "insurance_notify_infos": ("GET", "https://insurance.meituan.com/access-api/center/homepage/notify"),
    "insurance_order_detail": ("GET", "https://insurance.meituan.com/access-api/center/homepage/orders"),
    "external_order_lookup": ("GET", "https://www.jchunuo.com/accessapi/access-api/queryOrderInfoNeedToken"),
}
THIRD_PARTY_ALLOWED_HOSTS = {"mt.ssss66.xyz", "mt.liliabc.fun"}
_IP_PORT_RE = re.compile(r"(?<![\w.-])((?:\d{1,3}\.){3}\d{1,3}:\d{2,5})(?![\w.-])")


class OrderRelayRequest(BaseModel):
    operation: str
    params: dict[str, Any] = Field(default_factory=dict)
    form: dict[str, Any] = Field(default_factory=dict)
    json_body: dict[str, Any] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    relay_options: dict[str, Any] = Field(default_factory=dict)


class OrderRelayProbeRequest(BaseModel):
    relay_options: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ProxyItem:
    url: str
    selected_at: float
    use_count: int = 0
    invalid: bool = False


def _proxy_api_request_url(api_url: str, number: int) -> str:
    parsed = urllib.parse.urlparse(str(api_url or "").strip())
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    # Preserve provider-specific parameters. Some APIs (notably IPzan) reject
    # unknown aliases such as ``number``/``QTY``; only update a count key that
    # the configured URL already declares.
    count_text = str(max(1, number))
    if "num" in query:
        query["num"] = count_text
    elif "number" in query:
        query["number"] = count_text
    if "QTY" in query:
        query["QTY"] = count_text
    elif "qty" in query:
        query["qty"] = count_text
    for key in ("city", "ISP", "province"):
        query.pop(key, None)
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))


def _normalize_proxy(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("\\r", "").replace("\\n", "").strip()
    if "://" in text:
        parsed = urllib.parse.urlparse(text)
        if parsed.hostname and parsed.port:
            return f"http://{parsed.hostname}:{parsed.port}"
    match = _IP_PORT_RE.search(text)
    if not match:
        return ""
    return f"http://{match.group(1)}"


def _collect_proxy_candidates(value: Any, results: list[str]) -> None:
    if isinstance(value, str):
        candidate = _normalize_proxy(value)
        if candidate:
            results.append(candidate)
        for match in _IP_PORT_RE.findall(value):
            results.append(f"http://{match}")
        return
    if isinstance(value, dict):
        ip = value.get("ip") or value.get("host") or value.get("server")
        port = value.get("port")
        if ip and port:
            results.append(f"http://{ip}:{port}")
        for child in value.values():
            _collect_proxy_candidates(child, results)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _collect_proxy_candidates(child, results)


def _parse_proxy_candidates(response: httpx.Response) -> list[str]:
    values: list[str] = []
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if payload is not None:
        _collect_proxy_candidates(payload, values)
    else:
        _collect_proxy_candidates(response.text, values)
    seen: set[str] = set()
    return [item for item in values if item and not (item in seen or seen.add(item))]


def _safe_headers(headers: dict[str, str]) -> dict[str, str]:
    blocked = {"host", "content-length", "connection", "proxy-authorization"}
    return {
        str(key): str(value)
        for key, value in dict(headers or {}).items()
        if str(key).strip().lower() not in blocked and str(key).strip()
    }


class ProxyPool:
    def __init__(self) -> None:
        self._items: list[ProxyItem] = []
        self._lock = asyncio.Lock()
        self._api_url = ""
        self._last_error = ""
        self._success_count = 0
        self._failure_count = 0

    def _valid_items(self, now: float) -> list[ProxyItem]:
        return [
            item for item in self._items
            if not item.invalid
            and (now - item.selected_at) < PROXY_MAX_AGE_SECONDS
            and item.use_count < PROXY_MAX_USE_COUNT
        ]

    async def _fetch(self, count: int, api_url: str) -> list[ProxyItem]:
        if not api_url:
            self._last_error = "订单 Relay 未配置代理 IP 接口"
            return []
        try:
            async with httpx.AsyncClient(follow_redirects=False) as client:
                response = await client.get(
                    _proxy_api_request_url(api_url, count),
                    timeout=httpx.Timeout(PROXY_API_TIMEOUT_SECONDS),
                    headers={"Accept": "application/json,text/plain,*/*", "Accept-Encoding": "identity", "User-Agent": "meituan-order-relay/1.0"},
                )
                response.raise_for_status()
            proxies = _parse_proxy_candidates(response)
            if not proxies:
                self._last_error = "代理接口返回成功，但没有可用代理"
                return []
            now = time.time()
            self._last_error = ""
            return [ProxyItem(url=item, selected_at=now) for item in proxies]
        except Exception as exc:
            self._last_error = f"代理接口获取失败: {exc.__class__.__name__}"
            return []

    async def acquire(self, *, use_pool: bool, api_url: str = "") -> str:
        if not api_url:
            self._last_error = "订单 Relay 未配置代理 IP 接口"
            return ""
        if not use_pool:
            # Direct mode intentionally does not cache or serialize proxy
            # acquisition. Each upstream attempt receives a fresh IP.
            items = await self._fetch(1, api_url)
            return items[0].url if items else ""
        async with self._lock:
            if self._api_url != api_url:
                self._api_url = api_url
                self._items.clear()
            now = time.time()
            self._items = self._valid_items(now)
            if not self._items:
                self._items.extend(await self._fetch(PROXY_POOL_SIZE, api_url))
            if not self._items:
                return ""
            item = min(self._items, key=lambda current: (current.use_count, current.selected_at))
            item.use_count += 1
            return item.url

    async def report_success(self, proxy_url: str) -> None:
        del proxy_url
        async with self._lock:
            self._success_count += 1

    async def report_failure(self, proxy_url: str) -> None:
        async with self._lock:
            self._failure_count += 1
            for item in self._items:
                if item.url == proxy_url:
                    item.invalid = True

    async def probe(self, *, use_pool: bool, api_url: str = "") -> dict[str, Any]:
        proxy_url = await self.acquire(use_pool=use_pool, api_url=api_url)
        if not proxy_url:
            return {"success": False, "error_code": "proxy_api_unavailable", "message": self._last_error or "无法获取代理"}
        return {
            "success": True,
            "message": "订单 Relay 代理接口正常",
            "proxy_mode": "pool" if use_pool else "direct",
            "proxy_pool_size": len(self._items),
        }

    async def runtime(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "configured": bool(PROXY_API_URL or self._api_url),
                "pool_enabled": PROXY_POOL_ENABLED,
                "pool_size": len(self._items),
                "valid_count": len(self._valid_items(time.time())),
                "success_count": self._success_count,
                "failure_count": self._failure_count,
                "last_error": self._last_error,
            }


proxy_pool = ProxyPool()
request_limiter = asyncio.Semaphore(MAX_CONCURRENCY)
app = FastAPI(title="meituan-order-relay", version="1.0.0")


def _assert_secret(value: str | None) -> None:
    if RELAY_SECRET and value != RELAY_SECRET:
        raise HTTPException(status_code=401, detail="relay secret invalid")


def _relay_options(raw_options: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw_options if isinstance(raw_options, dict) else {}
    mode = str(raw.get("proxy_mode") or "").strip().lower()
    if mode not in {"direct", "pool"}:
        mode = "pool" if PROXY_POOL_ENABLED else "direct"
    try:
        retry_count = int(raw.get("proxy_retry_count", 2))
    except (TypeError, ValueError):
        retry_count = 2
    try:
        queue_wait_seconds = float(raw.get("queue_wait_seconds", 3))
    except (TypeError, ValueError):
        queue_wait_seconds = 3
    proxy_api_url = str(raw.get("proxy_api_url") or PROXY_API_URL).strip()
    proxy_fallback_enabled = bool(raw.get("proxy_fallback_enabled", bool(proxy_api_url)))
    try:
        third_party_timeout = float(raw.get("third_party_timeout_seconds", UPSTREAM_TIMEOUT_SECONDS))
    except (TypeError, ValueError):
        third_party_timeout = UPSTREAM_TIMEOUT_SECONDS
    try:
        third_party_direct_timeout = float(raw.get("third_party_direct_timeout_seconds", min(5.0, third_party_timeout)))
    except (TypeError, ValueError):
        third_party_direct_timeout = min(5.0, third_party_timeout)
    return {
        "use_pool": mode == "pool",
        "proxy_mode": mode,
        "proxy_retry_count": min(3, max(0, retry_count)),
        "queue_wait_seconds": min(10.0, max(0.5, queue_wait_seconds)),
        "proxy_api_url": proxy_api_url if proxy_fallback_enabled else "",
        "proxy_fallback_enabled": proxy_fallback_enabled,
        "third_party_timeout_seconds": min(60.0, max(3.0, third_party_timeout)),
        "third_party_direct_timeout_seconds": min(30.0, max(1.0, third_party_direct_timeout)),
    }


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "meituan-order-relay",
        "order_relay_secret_enabled": bool(RELAY_SECRET),
        "order_proxy_configured": bool(PROXY_API_URL or proxy_pool._api_url),
        "max_concurrency": MAX_CONCURRENCY,
        "proxy": await proxy_pool.runtime(),
    }


@app.post("/relay/meituan/order-query/probe")
async def probe_order_relay(
    payload: OrderRelayProbeRequest,
    x_order_relay_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    _assert_secret(x_order_relay_secret)
    options = _relay_options(payload.relay_options)
    return await proxy_pool.probe(
        use_pool=bool(options["use_pool"]),
        api_url=str(options.get("proxy_api_url") or ""),
    )


def _third_party_endpoint(payload: OrderRelayRequest) -> str:
    value = str((payload.params or {}).get("third_party_url") or os.getenv("MEITUAN_ORDER_THIRD_PARTY_URL") or "").strip()
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.hostname not in THIRD_PARTY_ALLOWED_HOSTS
        or parsed.port not in {None, 443}
        or parsed.hostname in {"localhost", "localhost.localdomain", "::1"}
    ):
        raise HTTPException(status_code=400, detail="第三方订单接口地址无效")
    return value.rstrip("/")


async def _request_third_party_direct(
    endpoint: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout_seconds: float,
    proxy_url: str | None = None,
) -> tuple[httpx.Response, Any]:
    async with httpx.AsyncClient(proxy=proxy_url, follow_redirects=False) as client:
        response = await client.post(
            endpoint,
            json=body,
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds),
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("third_party_non_json") from exc
    if not isinstance(data, dict):
        raise RuntimeError("third_party_invalid_json")
    # 403 is included for this endpoint because an upstream may reject the
    # current source IP while still accepting the same credential via a proxy.
    if response.status_code >= 500 or response.status_code in {403, 408, 429}:
        raise RuntimeError(f"third_party_http_{response.status_code}")
    return response, data


async def _relay_third_party_order(
    payload: OrderRelayRequest,
    options: dict[str, Any],
) -> dict[str, Any]:
    endpoint = _third_party_endpoint(payload)
    body = dict(payload.json_body or {})
    if not body:
        return {
            "success": False,
            "error_code": "third_party_payload_empty",
            "retryable": False,
            "message": "第三方订单请求体为空",
        }
    headers = _safe_headers(payload.headers)
    started_at = time.monotonic()
    deadline = started_at + float(options["third_party_timeout_seconds"])
    direct_timeout = min(
        float(options["third_party_direct_timeout_seconds"]),
        float(options["third_party_timeout_seconds"]),
    )
    try:
        response, data = await _request_third_party_direct(
            endpoint,
            body,
            headers,
            max(1.0, direct_timeout),
        )
        return {
            "success": True,
            "upstream_status_code": response.status_code,
            "data": data,
            "route": "direct",
            "proxy_attempt_count": 0,
        }
    except Exception as direct_error:
        errors = [f"direct:{direct_error.__class__.__name__}"]

    api_url = str(options.get("proxy_api_url") or "").strip()
    if not options.get("proxy_fallback_enabled") or not api_url:
        return {
            "success": False,
            "error_code": "third_party_direct_failed",
            "retryable": True,
            "message": "第三方订单接口直连失败，未配置代理 IP 兜底",
            "attempt_count": 0,
        }

    for _ in range(int(options["proxy_retry_count"]) + 1):
        remaining = deadline - time.monotonic()
        if remaining < 1.0:
            break
        proxy_url = await proxy_pool.acquire(
            use_pool=bool(options["use_pool"]),
            api_url=api_url,
        )
        if not proxy_url:
            errors.append("proxy_api_unavailable")
            continue
        try:
            response, data = await _request_third_party_direct(
                endpoint,
                body,
                headers,
                max(1.0, min(float(UPSTREAM_TIMEOUT_SECONDS), remaining)),
                proxy_url=proxy_url,
            )
            await proxy_pool.report_success(proxy_url)
            return {
                "success": True,
                "upstream_status_code": response.status_code,
                "data": data,
                "route": "proxy",
                "proxy_attempt_count": len(errors),
            }
        except Exception as exc:
            await proxy_pool.report_failure(proxy_url)
            errors.append(f"proxy:{exc.__class__.__name__}")

    return {
        "success": False,
        "error_code": "third_party_proxy_failed",
        "retryable": True,
        "message": "第三方订单接口直连和代理 IP 请求均失败",
        "attempt_count": len(errors),
    }


@app.post("/relay/meituan/order-query")
async def relay_order_query(
    payload: OrderRelayRequest,
    x_order_relay_secret: str | None = Header(default=None),
) -> dict[str, Any]:
    _assert_secret(x_order_relay_secret)
    operation = str(payload.operation or "").strip()
    target = OPERATIONS.get(operation)
    if target is None:
        raise HTTPException(status_code=400, detail="unsupported order relay operation")
    options = _relay_options(payload.relay_options)
    queue_started_at = time.monotonic()
    try:
        await asyncio.wait_for(request_limiter.acquire(), timeout=float(options["queue_wait_seconds"]))
    except TimeoutError:
        return {
            "success": False,
            "error_code": "relay_busy",
            "retryable": True,
            "message": "订单 Relay 当前繁忙，请稍后重试",
            "queue_wait_seconds": options["queue_wait_seconds"],
        }
    try:
        if operation == "third_party_order":
            result = await _relay_third_party_order(payload, options)
            result["queue_waited_ms"] = int((time.monotonic() - queue_started_at) * 1000)
            return result

        api_url = str(options.get("proxy_api_url") or "").strip()
        if not api_url:
            return {"success": False, "error_code": "proxy_api_unavailable", "retryable": True, "message": "订单 Relay 未配置代理 IP 接口"}

        method, endpoint = target
        headers = _safe_headers(payload.headers)
        errors: list[str] = []
        queue_waited_ms = int((time.monotonic() - queue_started_at) * 1000)
        acquired_proxy = False
        for _ in range(int(options["proxy_retry_count"]) + 1):
            proxy_url = await proxy_pool.acquire(
                use_pool=bool(options["use_pool"]),
                api_url=api_url,
            )
            if not proxy_url:
                errors.append("proxy_api_unavailable")
                continue
            acquired_proxy = True
            try:
                async with httpx.AsyncClient(proxy=proxy_url, follow_redirects=False) as client:
                    response = await client.request(
                        method,
                        endpoint,
                        params=dict(payload.params or {}) if method == "GET" else None,
                        data=dict(payload.form or {}) if method == "POST" else None,
                        headers=headers,
                        timeout=httpx.Timeout(UPSTREAM_TIMEOUT_SECONDS),
                    )
                try:
                    data = response.json()
                except ValueError as exc:
                    raise RuntimeError("upstream_non_json") from exc
                if not isinstance(data, (dict, list)):
                    raise RuntimeError("upstream_invalid_json")
                # 5xx/408/429 are transport-side transient failures. Retry
                # them with another proxy, but leave token/business 4xx data
                # untouched for the existing business layer to interpret.
                if response.status_code >= 500 or response.status_code in {408, 429}:
                    raise RuntimeError(f"upstream_http_{response.status_code}")
                await proxy_pool.report_success(proxy_url)
                return {
                    "success": True,
                    "upstream_status_code": response.status_code,
                    "data": data,
                    "proxy_mode": options["proxy_mode"],
                    "proxy_attempt_count": len(errors) + 1,
                    "queue_waited_ms": queue_waited_ms,
                }
            except Exception as exc:
                await proxy_pool.report_failure(proxy_url)
                errors.append(exc.__class__.__name__)
        return {
            "success": False,
            "error_code": "proxy_request_failed" if acquired_proxy else "proxy_api_unavailable",
            "retryable": True,
            "message": "订单 Relay 代理请求失败" if acquired_proxy else "订单 Relay 无法获取代理",
            "attempt_count": len(errors),
            "proxy_mode": options["proxy_mode"],
        }
    finally:
        request_limiter.release()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)
