"""Domestic relay for the authenticated source-1 order-ranking website."""
from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


SOURCE1_BASE = "https://naiba666.com/md"
SOURCE2_URL = "https://mt.liliabc.fun/page/wm/md"
CZ88_BASE_URL = "https://www.cz88.net/api/cz88/ip/base"
LISTEN_HOST = os.getenv("ORDER_RANKINGS_RELAY_HOST", "0.0.0.0").strip() or "0.0.0.0"
LISTEN_PORT = int(os.getenv("ORDER_RANKINGS_RELAY_PORT", "18081") or "18081")
RELAY_SECRET = str(os.getenv("ORDER_RANKINGS_RELAY_SECRET") or "").strip()
TIMEOUT_SECONDS = max(3.0, float(os.getenv("ORDER_RANKINGS_RELAY_TIMEOUT_SECONDS", "8") or "8"))
MAX_CONCURRENCY = min(16, max(1, int(os.getenv("ORDER_RANKINGS_RELAY_MAX_CONCURRENCY", "8") or "8")))
STATE_FILE = Path(os.getenv("ORDER_RANKINGS_RELAY_STATE_FILE", "/root/meituan-rankings-relay/source1-session.json"))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
PROXY_ENDPOINT_RE = re.compile(r"(?<![\w.-])((?:\d{1,3}\.){3}\d{1,3}:\d{2,5})(?![\w.-])")


class RelayRequest(BaseModel):
    operation: Literal["check_login", "login", "activities", "ranking", "source2_ranking"]
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    merchant_name: str = Field(default="", max_length=128)
    record_date: str = Field(default="", pattern=r"^\d{4}-\d{2}-\d{2}$")
    slot_time: str = Field(default="", pattern=r"^\d{2}:\d{2}$")
    proxy_options: dict[str, Any] = Field(default_factory=dict)


class ProxyTransportError(RuntimeError):
    """A transport or upstream structure error eligible for proxy fallback."""


def _proxy_options(raw: dict[str, Any] | None) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    try:
        cache_seconds = int(value.get("validation_cache_seconds", 60))
    except (TypeError, ValueError):
        cache_seconds = 60
    try:
        retry_count = int(value.get("retry_count", 2))
    except (TypeError, ValueError):
        retry_count = 2
    api_url = str(value.get("api_url") or "").strip()
    parsed = urlparse(api_url)
    return {
        "enabled": bool(value.get("enabled")) and parsed.scheme in {"http", "https"} and bool(parsed.netloc),
        "api_url": api_url,
        "validation_cache_seconds": min(600, max(10, cache_seconds)),
        "retry_count": min(5, max(0, retry_count)),
        "force": bool(value.get("force")),
    }


def _proxy_endpoint(value: str) -> tuple[str, str]:
    match = PROXY_ENDPOINT_RE.search(str(value or "").strip())
    if not match:
        raise ProxyTransportError("代理接口未返回 IP:端口")
    host, port = match.group(1).rsplit(":", 1)
    try:
        ipaddress.ip_address(host)
        port_number = int(port)
    except ValueError as exc:
        raise ProxyTransportError("代理接口返回的 IP:端口格式异常") from exc
    if not 1 <= port_number <= 65535:
        raise ProxyTransportError("代理接口返回的端口无效")
    return host, port


class ProxyLease:
    """One in-memory proxy lease shared by requests in the current activity slot."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._slot_key = ""
        self._api_url = ""
        self._proxy_url = ""
        self._proxy_ip = ""
        self._validated_at = 0.0
        self._last_validated_at = 0.0
        self._last_error = ""
        self._success_count = 0
        self._failure_count = 0

    async def _validate_ip(self, host: str, timeout: float) -> None:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            check = await client.get(CZ88_BASE_URL, params={"ip": host}, timeout=timeout)
            check.raise_for_status()
            payload = check.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        checked_ip = str(data.get("ip") or "").strip() if isinstance(data, dict) else ""
        if not isinstance(payload, dict) or payload.get("success") is not True or str(payload.get("code")) != "200":
            raise ProxyTransportError("cz88 IP 校验失败")
        if checked_ip != host:
            raise ProxyTransportError("cz88 返回 IP 与代理 IP 不一致")

    async def _fetch_and_validate(self, api_url: str, timeout: float) -> str:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            response = await client.get(
                api_url,
                timeout=timeout,
                headers={
                    "Accept": "text/plain,application/json,*/*",
                    "Accept-Encoding": "identity",
                    "User-Agent": "order-rankings-relay/1.2",
                },
            )
            response.raise_for_status()
            host, port = _proxy_endpoint(response.text)
        await self._validate_ip(host, timeout)
        return f"http://{host}:{port}"

    async def acquire(self, options: dict[str, Any], slot_key: str, excluded: set[str]) -> str:
        api_url = str(options.get("api_url") or "")
        if not options.get("enabled") or not api_url:
            return ""
        async with self._lock:
            if slot_key == "source1_control" and self._slot_key and self._slot_key != slot_key:
                slot_key = self._slot_key
            if slot_key != self._slot_key or api_url != self._api_url:
                self._slot_key = slot_key
                self._api_url = api_url
                self._proxy_url = ""
                self._proxy_ip = ""
                self._validated_at = 0.0
                self._last_validated_at = 0.0
            now = time.monotonic()
            cache_seconds = float(options.get("validation_cache_seconds") or 60)
            if self._proxy_url and self._proxy_url not in excluded:
                if now - self._validated_at < cache_seconds:
                    return self._proxy_url
                try:
                    await self._validate_ip(self._proxy_ip, timeout=min(TIMEOUT_SECONDS, 8.0))
                    self._validated_at = time.monotonic()
                    self._last_validated_at = time.time()
                    self._last_error = ""
                    return self._proxy_url
                except Exception as exc:
                    self._last_error = exc.__class__.__name__
                    self._proxy_url = ""
                    self._proxy_ip = ""
                    self._validated_at = 0.0
                    self._last_validated_at = 0.0
            if self._proxy_url in excluded:
                self._proxy_url = ""
                self._proxy_ip = ""
                self._validated_at = 0.0
            try:
                proxy_url = await self._fetch_and_validate(api_url, timeout=min(TIMEOUT_SECONDS, 8.0))
                if proxy_url in excluded:
                    raise ProxyTransportError("代理接口重复返回本次已失败 IP")
                self._proxy_url = proxy_url
                self._proxy_ip = proxy_url.rsplit("//", 1)[-1].rsplit(":", 1)[0]
                self._validated_at = time.monotonic()
                self._last_validated_at = time.time()
                self._last_error = ""
                return proxy_url
            except Exception as exc:
                self._last_error = exc.__class__.__name__
                raise ProxyTransportError("代理 IP 获取或校验失败") from exc

    async def invalidate(self, proxy_url: str) -> None:
        async with self._lock:
            self._failure_count += 1
            if proxy_url == self._proxy_url:
                self._proxy_url = ""
                self._proxy_ip = ""
                self._validated_at = 0.0
                self._last_validated_at = 0.0

    async def success(self) -> None:
        async with self._lock:
            self._success_count += 1

    async def runtime(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "active": bool(self._proxy_url),
                "slot_key": self._slot_key,
                "validated_at": int(self._last_validated_at or 0),
                "success_count": self._success_count,
                "failure_count": self._failure_count,
                "last_error": self._last_error,
            }


proxy_lease = ProxyLease()


class Source1Session:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._loaded = False
        self._lock = asyncio.Lock()
        self._last_login_at = 0
        self._last_error = ""

    async def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json, text/plain, */*",
                    "Referer": f"{SOURCE1_BASE}/index.html",
                },
            )
        if not self._loaded:
            self._loaded = True
            try:
                for item in json.loads(STATE_FILE.read_text(encoding="utf-8")):
                    self._client.cookies.set(
                        str(item["name"]), str(item["value"]),
                        domain=str(item.get("domain") or "naiba666.com"), path=str(item.get("path") or "/"),
                    )
            except (FileNotFoundError, ValueError, KeyError, TypeError):
                pass
        return self._client

    async def save(self) -> None:
        if self._client is None:
            return
        cookies = [
            {"name": item.name, "value": item.value, "domain": item.domain, "path": item.path, "expires": item.expires}
            for item in self._client.cookies.jar
            if "naiba666.com" in str(item.domain or "")
        ]
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = STATE_FILE.with_suffix(".tmp")
        temporary.write_text(json.dumps(cookies, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(STATE_FILE)

    async def request(self, method: str, url: str, *, proxy_url: str = "", **kwargs: Any) -> httpx.Response:
        base_client = await self.client()
        if not proxy_url:
            return await base_client.request(method, url, **kwargs)
        # A proxy switch cannot mutate an existing httpx client. Copy the
        # authenticated source-1 cookies into a short-lived proxy client and
        # merge any refreshed cookies back into the persistent session.
        async with httpx.AsyncClient(
            proxy=proxy_url,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{SOURCE1_BASE}/index.html",
            },
        ) as proxy_client:
            for item in base_client.cookies.jar:
                proxy_client.cookies.set(item.name, item.value, domain=item.domain, path=item.path)
            response = await proxy_client.request(method, url, **kwargs)
            for item in proxy_client.cookies.jar:
                if "naiba666.com" in str(item.domain or ""):
                    base_client.cookies.set(item.name, item.value, domain=item.domain, path=item.path)
            await self.save()
            return response

    async def check_login(self, *, proxy_url: str = "") -> bool:
        response = await self.request(
            "GET", f"{SOURCE1_BASE}/api.php?action=check_login", proxy_url=proxy_url, timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        return isinstance(payload, dict) and str(payload.get("code")) in {"1", "200"}

    async def login(self, username: str, password: str, *, proxy_url: str = "") -> bool:
        if not username or not password:
            raise HTTPException(status_code=400, detail="source1 credentials required")
        async with self._lock:
            response = await self.request(
                "POST",
                f"{SOURCE1_BASE}/api.php?action=login",
                proxy_url=proxy_url,
                data={"username": username, "password": password},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("code")) not in {"1", "200"}:
                raise HTTPException(status_code=401, detail="source1 login rejected")
            valid = await self.check_login(proxy_url=proxy_url)
            if not valid:
                raise HTTPException(status_code=502, detail="source1 session verification failed")
            self._last_login_at = int(time.time())
            self._last_error = ""
            await self.save()
            return True

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


session = Source1Session()
limiter = asyncio.Semaphore(MAX_CONCURRENCY)
app = FastAPI(title="order-rankings-relay", version="1.2.0")


def _assert_secret(value: str | None) -> None:
    if RELAY_SECRET and value != RELAY_SECRET:
        raise HTTPException(status_code=401, detail="relay secret invalid")


async def _run(operation):
    try:
        async with limiter:
            return await operation()
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        session._last_error = "upstream_timeout"
        raise HTTPException(status_code=504, detail="source1 upstream timeout") from exc
    except httpx.HTTPStatusError as exc:
        status = int(exc.response.status_code)
        session._last_error = f"upstream_http_{status}"
        raise HTTPException(status_code=502, detail=f"upstream HTTP {status}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        session._last_error = exc.__class__.__name__
        raise HTTPException(status_code=502, detail="upstream request failed") from exc
    except ProxyTransportError as exc:
        session._last_error = str(exc)
        raise HTTPException(status_code=502, detail="proxy fallback unavailable") from exc


async def _with_proxy_fallback(operation, slot_key: str, raw_options: dict[str, Any] | None):
    options = _proxy_options(raw_options)
    direct_error: Exception | None = None
    if not options["force"]:
        try:
            result = await operation("")
            return result, False, 0
        except (httpx.HTTPError, ValueError) as exc:
            direct_error = exc
    if not options["enabled"]:
        if direct_error is not None:
            raise direct_error
        raise ProxyTransportError("代理兜底未配置或无法获取代理")
    last_error: Exception = direct_error or ProxyTransportError("强制代理测试未执行")
    attempted: set[str] = set()
    for _ in range(int(options["retry_count"]) + 1):
        try:
            proxy_url = await proxy_lease.acquire(options, slot_key, attempted)
        except ProxyTransportError as exc:
            last_error = exc
            continue
        if not proxy_url:
            last_error = ProxyTransportError("代理兜底未配置或无法获取代理")
            continue
        attempted.add(proxy_url)
        try:
            result = await operation(proxy_url)
            await proxy_lease.success()
            return result, True, len(attempted)
        except (httpx.HTTPError, ValueError) as exc:
            last_error = exc
            await proxy_lease.invalidate(proxy_url)
    raise last_error


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "order-rankings-relay",
        "source2_supported": True,
        "source1_relay_secret_enabled": bool(RELAY_SECRET),
        "max_concurrency": MAX_CONCURRENCY,
        "session_file_exists": STATE_FILE.is_file(),
        "last_login_at": session._last_login_at,
        "last_error": session._last_error,
        "proxy": await proxy_lease.runtime(),
    }


@app.post("/relay/order-rankings/source1")
async def relay_source1(payload: RelayRequest, x_order_rankings_relay_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _assert_secret(x_order_rankings_relay_secret)

    if payload.operation == "check_login":
        valid, proxy_used, proxy_attempts = await _run(lambda: _with_proxy_fallback(
            lambda proxy: session.check_login(proxy_url=proxy), "source1_control", payload.proxy_options,
        ))
        return {"success": True, "session_valid": valid, "proxy_fallback_used": proxy_used, "proxy_attempts": proxy_attempts}

    if payload.operation == "login":
        result, proxy_used, proxy_attempts = await _run(lambda: _with_proxy_fallback(
            lambda proxy: session.login(payload.username, payload.password, proxy_url=proxy), "source1_control", payload.proxy_options,
        ))
        return {"success": True, "session_valid": bool(result), "proxy_fallback_used": proxy_used, "proxy_attempts": proxy_attempts}

    if payload.operation == "activities":
        async def activities(proxy_url: str = ""):
            response = await session.request(
                "GET", f"{SOURCE1_BASE}/api.php?action=get_activities", proxy_url=proxy_url, timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid activities payload")
            return data
        data, proxy_used, proxy_attempts = await _run(lambda: _with_proxy_fallback(
            activities, "source1_control", payload.proxy_options,
        ))
        return {"success": True, "data": data, "proxy_fallback_used": proxy_used, "proxy_attempts": proxy_attempts}

    if payload.operation == "source2_ranking":
        if not payload.record_date or not payload.slot_time:
            raise HTTPException(status_code=400, detail="record_date and slot_time required")

        async def source2_ranking(proxy_url: str = ""):
            try:
                slot = datetime.strptime(f"{payload.record_date} {payload.slot_time}", "%Y-%m-%d %H:%M")
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="invalid source2 slot") from exc
            # The source-2 CDN sets short-lived anti-bot cookies. Use a fresh
            # client per request so those cookies are never reused across polls.
            async with httpx.AsyncClient(
                proxy=proxy_url or None,
                follow_redirects=True,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            ) as client:
                response = await client.get(
                    SOURCE2_URL,
                    params={"key": f"mt-time-{slot.strftime('%m%d%H%M')}"},
                    timeout=TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                html = response.text
            if "shopData" not in html:
                raise ValueError("source2 ranking payload missing shopData")
            return html

        html, proxy_used, proxy_attempts = await _run(lambda: _with_proxy_fallback(
            source2_ranking, f"{payload.record_date} {payload.slot_time}", payload.proxy_options,
        ))
        return {"success": True, "html": html, "proxy_fallback_used": proxy_used, "proxy_attempts": proxy_attempts}

    async def ranking(proxy_url: str = ""):
        if not payload.merchant_name or not payload.record_date or not payload.slot_time:
            raise HTTPException(status_code=400, detail="merchant_name, record_date and slot_time required")
        response = await session.request(
            "GET",
            f"{SOURCE1_BASE}/ranking.php",
            proxy_url=proxy_url,
            params={"brand": payload.merchant_name, "time": f"{payload.record_date} {payload.slot_time}:00", "_": str(int(time.time() * 1000))},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.text
    html, proxy_used, proxy_attempts = await _run(lambda: _with_proxy_fallback(
        ranking, f"{payload.record_date} {payload.slot_time}", payload.proxy_options,
    ))
    return {"success": True, "html": html, "proxy_fallback_used": proxy_used, "proxy_attempts": proxy_attempts}


@app.on_event("shutdown")
async def shutdown() -> None:
    await session.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)
