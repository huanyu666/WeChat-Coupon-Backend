from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from utils.shortlink_service import resolve_shortlink_target_async

router = APIRouter(tags=["短链"])


async def _resolve_shortlink_target(short_key: str) -> str:
    try:
        return await resolve_shortlink_target_async(short_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Not Found") from exc


@router.get("/key/{short_key}")
async def shortlink_redirect_with_key(short_key: str):
    return RedirectResponse(url=await _resolve_shortlink_target(short_key), status_code=302)
