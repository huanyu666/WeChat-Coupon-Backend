"""Domestic relay for the authenticated source-1 order-ranking website."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


SOURCE1_BASE = "https://naiba666.com/md"
LISTEN_HOST = os.getenv("ORDER_RANKINGS_RELAY_HOST", "0.0.0.0").strip() or "0.0.0.0"
LISTEN_PORT = int(os.getenv("ORDER_RANKINGS_RELAY_PORT", "18081") or "18081")
RELAY_SECRET = str(os.getenv("ORDER_RANKINGS_RELAY_SECRET") or "").strip()
TIMEOUT_SECONDS = max(3.0, float(os.getenv("ORDER_RANKINGS_RELAY_TIMEOUT_SECONDS", "8") or "8"))
MAX_CONCURRENCY = min(16, max(1, int(os.getenv("ORDER_RANKINGS_RELAY_MAX_CONCURRENCY", "8") or "8")))
STATE_FILE = Path(os.getenv("ORDER_RANKINGS_RELAY_STATE_FILE", "/root/meituan-rankings-relay/source1-session.json"))
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"


class RelayRequest(BaseModel):
    operation: Literal["check_login", "login", "activities", "ranking"]
    username: str = Field(default="", max_length=128)
    password: str = Field(default="", max_length=256)
    merchant_name: str = Field(default="", max_length=128)
    record_date: str = Field(default="", pattern=r"^\d{4}-\d{2}-\d{2}$")
    slot_time: str = Field(default="", pattern=r"^\d{2}:\d{2}$")


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

    async def check_login(self) -> bool:
        client = await self.client()
        response = await client.get(f"{SOURCE1_BASE}/api.php?action=check_login", timeout=TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        return isinstance(payload, dict) and str(payload.get("code")) in {"1", "200"}

    async def login(self, username: str, password: str) -> bool:
        if not username or not password:
            raise HTTPException(status_code=400, detail="source1 credentials required")
        async with self._lock:
            client = await self.client()
            response = await client.post(
                f"{SOURCE1_BASE}/api.php?action=login",
                data={"username": username, "password": password},
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or str(payload.get("code")) not in {"1", "200"}:
                raise HTTPException(status_code=401, detail="source1 login rejected")
            valid = await self.check_login()
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
app = FastAPI(title="order-rankings-source1-relay", version="1.0.0")


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
    except (httpx.HTTPError, ValueError) as exc:
        session._last_error = exc.__class__.__name__
        raise HTTPException(status_code=502, detail="source1 upstream request failed") from exc


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "order-rankings-source1-relay",
        "source1_relay_secret_enabled": bool(RELAY_SECRET),
        "max_concurrency": MAX_CONCURRENCY,
        "session_file_exists": STATE_FILE.is_file(),
        "last_login_at": session._last_login_at,
        "last_error": session._last_error,
    }


@app.post("/relay/order-rankings/source1")
async def relay_source1(payload: RelayRequest, x_order_rankings_relay_secret: str | None = Header(default=None)) -> dict[str, Any]:
    _assert_secret(x_order_rankings_relay_secret)

    if payload.operation == "check_login":
        valid = await _run(session.check_login)
        return {"success": True, "session_valid": valid}

    if payload.operation == "login":
        await _run(lambda: session.login(payload.username, payload.password))
        return {"success": True, "session_valid": True}

    if payload.operation == "activities":
        async def activities():
            client = await session.client()
            response = await client.get(f"{SOURCE1_BASE}/api.php?action=get_activities", timeout=TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("invalid activities payload")
            return data
        return {"success": True, "data": await _run(activities)}

    async def ranking():
        if not payload.merchant_name or not payload.record_date or not payload.slot_time:
            raise HTTPException(status_code=400, detail="merchant_name, record_date and slot_time required")
        client = await session.client()
        response = await client.get(
            f"{SOURCE1_BASE}/ranking.php",
            params={"brand": payload.merchant_name, "time": f"{payload.record_date} {payload.slot_time}:00", "_": str(int(time.time() * 1000))},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.text
    return {"success": True, "html": await _run(ranking)}


@app.on_event("shutdown")
async def shutdown() -> None:
    await session.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)
