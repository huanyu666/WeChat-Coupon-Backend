from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from utils.auth_utils import get_current_user
from utils.shortlink_service import (
    create_manual_shortlink_async,
    delete_shortlink_async,
    list_manual_shortlinks_async,
    resolve_shortlink_target_async,
)

router = APIRouter(tags=["短链"])


class ManualShortlinkCreatePayload(BaseModel):
    url: str = Field(min_length=1)


async def _resolve_shortlink_target(short_key: str) -> str:
    try:
        return await resolve_shortlink_target_async(short_key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Not Found") from exc


@router.get("/key/{short_key}")
async def shortlink_redirect_with_key(short_key: str):
    return RedirectResponse(url=await _resolve_shortlink_target(short_key), status_code=302)


@router.post("/api/shortlink/manual")
async def create_manual_shortlink(
    payload: ManualShortlinkCreatePayload,
    current_user: str = Depends(get_current_user),
):
    try:
        result = await create_manual_shortlink_async(url=payload.url)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"success": False, "error": "创建手动短链失败"}, status_code=500)
    return JSONResponse({"success": True, **result})


@router.get("/api/shortlink/manual")
async def list_manual_shortlinks(current_user: str = Depends(get_current_user)):
    try:
        links = await list_manual_shortlinks_async()
    except Exception as exc:
        return JSONResponse({"success": False, "error": "获取手动短链列表失败"}, status_code=500)
    return JSONResponse({"success": True, "links": links})


@router.delete("/api/shortlink/manual/{short_key}")
async def delete_manual_shortlink(short_key: str, current_user: str = Depends(get_current_user)):
    try:
        deleted = await delete_shortlink_async(short_key)
        if not deleted:
            return JSONResponse({"success": False, "error": "短链不存在"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"success": False, "error": "删除短链失败"}, status_code=500)
    return JSONResponse({"success": True, "message": "短链已删除"})
