from __future__ import annotations

import json
import sqlite3
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routes.auth import _get_current_web_admin_user
from utils.meituan_coupon_claim import (
    get_meituan_coupon_claim_config,
    get_meituan_coupon_claim_service,
)
from utils.path_utils import resolve_runtime_data_path
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_meituan_coupon_claim_config,
    save_system_settings_store,
)


router = APIRouter(tags=["美团40-20神券领取"])


class ClaimCreateRequest(BaseModel):
    token_id: int = Field(gt=0)
    confirm: bool = False


class ClaimSettingsRequest(BaseModel):
    enabled: bool = False
    global_concurrency_limit: int = Field(default=4, ge=1, le=16)
    channel_timeout_seconds: int = Field(default=35, ge=5, le=60)
    task_timeout_seconds: int = Field(default=90, ge=30, le=180)
    direct_retry_count: int = Field(default=3, ge=0, le=3)


def _error(message: str, status_code: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"success": False, "error": message, **extra}, status_code=status_code)


async def _current_user_or_error(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        from routes.auth import _get_current_web_query_user, _resolve_web_query_user_id

        user = await _get_current_web_query_user(request)
        user_id = _resolve_web_query_user_id(user)
        if user_id <= 0:
            return None, _error("无法识别当前用户", 401, login_expired=True)
        return {**user, "id": user_id}, None
    except PermissionError as exc:
        return None, _error(str(exc), 401, login_expired=True)
    except Exception:
        return None, _error("读取登录状态失败", 503)


async def _admin_or_error(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        user = await _get_current_web_admin_user(request)
        if not isinstance(user, dict) or not bool(user.get("is_admin")):
            return None, _error("需要管理员权限", 403)
        return user, None
    except PermissionError as exc:
        return None, _error(str(exc), 401, login_expired=True)
    except Exception:
        return None, _error("读取管理员登录状态失败", 503)


def _query_db() -> sqlite3.Connection:
    connection = sqlite3.connect(str(resolve_runtime_data_path("meituan_query.db")), timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def _load_token(user_id: int, token_id: int) -> dict[str, Any] | None:
    with _query_db() as connection:
        row = connection.execute(
            """
            SELECT id, user_id, name, token, meituan_user_id, is_active
            FROM tokens WHERE id=? AND user_id=? LIMIT 1
            """,
            (int(token_id), int(user_id)),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": int(row["id"] or 0),
        "user_id": int(row["user_id"] or 0),
        "name": str(row["name"] or ""),
        "token": str(row["token"] or ""),
        "meituan_user_id": str(row["meituan_user_id"] or ""),
        "is_active": bool(row["is_active"]),
    }


def _public_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    channels = job.get("channels") or {}
    safe_channels = {}
    for name in ("workbuddy", "tabbit"):
        item = channels.get(name) if isinstance(channels, dict) else {}
        item = item if isinstance(item, dict) else {}
        safe_channels[name] = {
            "status": str(item.get("status") or ""),
            "message": str(item.get("message") or "")[:160],
            "business_code": item.get("business_code"),
            "http_status": int(item.get("http_status") or 0),
            "coupon_count": int(item.get("coupon_count") or 0),
            "coupons": item.get("coupons") or [],
            "duration_ms": int(item.get("duration_ms") or 0),
        }
    return {
        "id": str(job.get("id") or ""),
        "web_user_id": int(job.get("web_user_id") or 0),
        "token_id": int(job.get("token_id") or 0),
        "token_name": str(job.get("token_name") or "")[:100],
        "meituan_user_id_masked": str(job.get("meituan_user_id_masked") or ""),
        "business_date": str(job.get("business_date") or ""),
        "source": str(job.get("source") or ""),
        "status": str(job.get("status") or ""),
        "channels": safe_channels,
        "result": job.get("result") or {},
        "error_code": str(job.get("error_code") or ""),
        "error_message": str(job.get("error_message") or "")[:200],
        "retry_count": int(job.get("retry_count") or 0),
        "duration_ms": int(job.get("duration_ms") or 0),
        "cancel_requested": bool(job.get("cancel_requested")),
        "created_at": str(job.get("created_at") or ""),
        "started_at": str(job.get("started_at") or ""),
        "finished_at": str(job.get("finished_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
    }


@router.get("/web/api/meituan-coupon-claim/settings")
async def get_user_claim_settings(request: Request, token_id: int = 0):
    user, error = await _current_user_or_error(request)
    if error is not None:
        return error
    service = get_meituan_coupon_claim_service()
    latest = service.storage.latest_for_user(int(user["id"]), int(token_id or 0))
    return JSONResponse({
        "success": True,
        "enabled": bool(get_meituan_coupon_claim_config()["enabled"]),
        "latest_job": _public_job(latest),
        "runtime": service.runtime(),
    })


@router.post("/web/api/meituan-coupon-claim/jobs")
async def create_user_claim_job(request: Request, payload: ClaimCreateRequest):
    user, error = await _current_user_or_error(request)
    if error is not None:
        return error
    if not payload.confirm:
        return _error("请确认后再领取优惠券", 400)
    config = get_meituan_coupon_claim_config()
    if not config["enabled"]:
        return _error("40-20领券功能暂未开启", 403)
    token = _load_token(int(user["id"]), int(payload.token_id))
    if not token or not token["is_active"] or not token["token"] or not token["meituan_user_id"]:
        return _error("只能使用自己已保存的有效 Token", 400)
    try:
        job = get_meituan_coupon_claim_service().submit(
            {
                "web_user_id": int(user["id"]),
                "token_id": int(token["id"]),
                "token_name": token["name"],
                "source": "web",
            },
            token["token"],
            token["meituan_user_id"],
        )
    except ValueError as exc:
        return _error(str(exc), 409)
    return JSONResponse({"success": True, "job": _public_job(job), "message": "领券任务已加入队列"}, status_code=202)


@router.get("/web/api/meituan-coupon-claim/jobs/{job_id}")
async def get_user_claim_job(request: Request, job_id: str):
    user, error = await _current_user_or_error(request)
    if error is not None:
        return error
    job = get_meituan_coupon_claim_service().storage.get_job(job_id)
    if not job or int(job.get("web_user_id") or 0) != int(user["id"]):
        return _error("任务不存在或无权访问", 404)
    return JSONResponse({"success": True, "job": _public_job(job)})


@router.post("/web/api/meituan-coupon-claim/jobs/{job_id}/cancel")
async def cancel_user_claim_job(request: Request, job_id: str):
    user, error = await _current_user_or_error(request)
    if error is not None:
        return error
    service = get_meituan_coupon_claim_service()
    job = service.storage.get_job(job_id)
    if not job or int(job.get("web_user_id") or 0) != int(user["id"]):
        return _error("任务不存在或无权访问", 404)
    return JSONResponse({"success": True, "job": _public_job(service.cancel(job_id))})


@router.get("/web/admin/api/meituan-coupon-claim/settings")
async def get_admin_claim_settings(request: Request):
    admin, error = await _admin_or_error(request)
    if error is not None:
        return error
    service = get_meituan_coupon_claim_service()
    return JSONResponse({
        "success": True,
        "settings": get_meituan_coupon_claim_config(),
        "runtime": service.runtime(),
        "stats": service.storage.stats(),
    })


@router.put("/web/admin/api/meituan-coupon-claim/settings")
async def update_admin_claim_settings(request: Request, payload: ClaimSettingsRequest):
    admin, error = await _admin_or_error(request)
    if error is not None:
        return error
    normalized = normalize_meituan_coupon_claim_config(payload.model_dump() if hasattr(payload, "model_dump") else payload.dict())
    store = load_system_settings_store()
    store["meituan_coupon_claim_config"] = normalized
    save_system_settings_store(store)
    service = get_meituan_coupon_claim_service()
    return JSONResponse({
        "success": True, "settings": normalized, "runtime": service.runtime(),
        "stats": service.storage.stats(),
    })


@router.get("/web/admin/api/meituan-coupon-claim/jobs")
async def list_admin_claim_jobs(request: Request, limit: int = 100, status: str = ""):
    admin, error = await _admin_or_error(request)
    if error is not None:
        return error
    jobs = get_meituan_coupon_claim_service().storage.list_jobs(limit=limit, status=status)
    return JSONResponse({"success": True, "jobs": [_public_job(job) for job in jobs]})


@router.get("/web/admin/api/meituan-coupon-claim/tokens")
async def list_admin_claim_tokens(request: Request):
    admin, error = await _admin_or_error(request)
    if error is not None:
        return error
    with _query_db() as connection:
        rows = connection.execute(
            """
            SELECT id, name, meituan_user_id
            FROM tokens
            WHERE user_id=? AND is_active=1
              AND TRIM(COALESCE(token, ''))!=''
              AND TRIM(COALESCE(meituan_user_id, ''))!=''
            ORDER BY id ASC
            """,
            (int(admin["id"]),),
        ).fetchall()
    return JSONResponse({
        "success": True,
        "tokens": [
            {
                "id": int(row["id"]),
                "name": str(row["name"] or "未命名"),
                "meituan_user_id_masked": (str(row["meituan_user_id"] or "")[:3] + "***" + str(row["meituan_user_id"] or "")[-2:]),
            }
            for row in rows
        ],
    })


@router.get("/web/admin/api/meituan-coupon-claim/jobs/{job_id}")
async def get_admin_claim_job(request: Request, job_id: str):
    admin, error = await _admin_or_error(request)
    if error is not None:
        return error
    job = get_meituan_coupon_claim_service().storage.get_job(job_id)
    if not job:
        return _error("任务不存在", 404)
    return JSONResponse({"success": True, "job": _public_job(job)})


@router.get("/web/admin/api/meituan-coupon-claim/stats")
async def get_admin_claim_stats(request: Request):
    admin, error = await _admin_or_error(request)
    if error is not None:
        return error
    service = get_meituan_coupon_claim_service()
    return JSONResponse({"success": True, "stats": service.storage.stats(), "runtime": service.runtime()})


@router.post("/web/admin/api/meituan-coupon-claim/test")
async def admin_test_claim(request: Request, payload: ClaimCreateRequest):
    admin, error = await _admin_or_error(request)
    if error is not None:
        return error
    if not payload.confirm:
        return _error("真实测试会调用美团领券接口，请明确确认", 400)
    token = _load_token(int(admin["id"]), int(payload.token_id))
    if not token or not token["is_active"] or not token["token"] or not token["meituan_user_id"]:
        return _error("只能使用管理员本人已保存的有效 Token", 400)
    try:
        job = get_meituan_coupon_claim_service().submit(
            {"web_user_id": int(admin["id"]), "token_id": int(token["id"]), "token_name": token["name"], "source": "admin_test"},
            token["token"], token["meituan_user_id"],
        )
    except ValueError as exc:
        return _error(str(exc), 409)
    return JSONResponse({"success": True, "job": _public_job(job), "message": "真实测试任务已加入队列"}, status_code=202)
