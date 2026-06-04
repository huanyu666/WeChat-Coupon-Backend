from __future__ import annotations

import os
from typing import Any, Dict

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


DEFAULT_ENDPOINT = "https://adapi.waimai.meituan.com/adhub/lite/landingPage/getAds"
LISTEN_HOST = os.getenv("MEITUAN_ALLOWANCE_RELAY_HOST", "0.0.0.0").strip() or "0.0.0.0"
LISTEN_PORT = int(os.getenv("MEITUAN_ALLOWANCE_RELAY_PORT", "43183") or "43183")
RELAY_SECRET = str(os.getenv("MEITUAN_ALLOWANCE_RELAY_SECRET") or "").strip()
REQUEST_TIMEOUT_SECONDS = float(os.getenv("MEITUAN_ALLOWANCE_RELAY_TIMEOUT_SECONDS", "15") or "15")

app = FastAPI(title="meituan-allowance-relay", version="1.0.0")


class RelayRequest(BaseModel):
    endpoint: str = DEFAULT_ENDPOINT
    params: Dict[str, Any]
    headers: Dict[str, str]


@app.get("/healthz")
async def healthz() -> Dict[str, Any]:
    return {
        "ok": True,
        "endpoint": DEFAULT_ENDPOINT,
        "secret_enabled": bool(RELAY_SECRET),
    }


@app.post("/relay/meituan/allowance")
async def relay_meituan_allowance(
    payload: RelayRequest,
    x_allowance_relay_secret: str | None = Header(default=None),
) -> Dict[str, Any]:
    if RELAY_SECRET and x_allowance_relay_secret != RELAY_SECRET:
        raise HTTPException(status_code=401, detail="relay secret invalid")

    endpoint = str(payload.endpoint or "").strip() or DEFAULT_ENDPOINT
    if endpoint != DEFAULT_ENDPOINT:
        raise HTTPException(status_code=400, detail="unsupported endpoint")

    headers = dict(payload.headers or {})
    params = dict(payload.params or {})
    headers.pop("Host", None)
    headers.pop("Content-Length", None)

    try:
        async with httpx.AsyncClient(follow_redirects=False) as client:
            response = await client.post(
                endpoint,
                data=params,
                headers=headers,
                timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
            )
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"relay timeout: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text[:500]) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"relay request failed: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"relay upstream non-json: {response.text[:500]}") from exc

    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="relay upstream invalid json structure")
    return data


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=LISTEN_HOST, port=LISTEN_PORT)
