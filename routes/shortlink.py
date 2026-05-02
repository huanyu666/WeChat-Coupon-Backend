from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from utils.redis_async import redis_get

router = APIRouter(tags=["短链"])

_SHORT_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
_SHORTLINK_KEY_PREFIX = "wx:shortlink:key:"


async def _resolve_shortlink_target(short_key: str) -> str:
    normalized_short_key = str(short_key or "").strip()
    if not _SHORT_KEY_RE.fullmatch(normalized_short_key):
        raise HTTPException(status_code=404, detail="Not Found")

    raw_payload = await redis_get(f"{_SHORTLINK_KEY_PREFIX}{normalized_short_key}")
    if not raw_payload:
        raise HTTPException(status_code=404, detail="Not Found")

    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Not Found") from exc

    target_url = str(payload.get("url") or "").strip()
    if not target_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=404, detail="Not Found")
    return target_url


@router.get("/key/{short_key}")
async def shortlink_redirect_with_key(short_key: str):
    return RedirectResponse(url=await _resolve_shortlink_target(short_key), status_code=302)


@router.get("/{short_key}")
async def shortlink_redirect(short_key: str):
    return RedirectResponse(url=await _resolve_shortlink_target(short_key), status_code=302)
