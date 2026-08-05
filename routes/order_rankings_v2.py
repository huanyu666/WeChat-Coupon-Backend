"""Public preview and administrator APIs for the aggregated ranking V2."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi import File, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from routes.auth import _get_current_web_admin_user
from utils.order_rankings_v2 import (
    TIMEZONE,
    get_order_rankings_v2_service,
    get_ranking_v2_config,
    save_ranking_v2_config,
    serialize_ranking_v2_config,
)
from utils.path_utils import resolve_project_path, resolve_runtime_data_path


router = APIRouter(tags=["聚合排行榜 V2"])
templates = Jinja2Templates(directory=str(resolve_project_path("html")))


class SettingsPayload(BaseModel):
    collection_enabled: bool = False
    public_enabled: bool = False
    rank_text_enabled: bool = False
    source1_enabled: bool = False
    source1_username: str = ""
    source1_password: str = ""
    clear_source1_password: bool = False
    clear_source1_credentials: bool = False
    source1_relay_url: str = ""
    source1_relay_secret: str = ""
    clear_source1_relay_url: bool = False
    clear_source1_relay_secret: bool = False
    announcement_enabled: bool = False
    announcement_title: str = ""
    announcement_body: str = ""
    announcement_image_url: str = ""
    announcement_link_url: str = ""


class TestQueryPayload(BaseModel):
    merchant_name: str
    record_date: str
    slot_time: str


def _error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


async def _require_admin(request: Request) -> JSONResponse | None:
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        return _error(str(exc), 401 if "登录" in str(exc) else 403)
    except Exception:
        return _error("读取管理员登录状态失败", 503)
    return None


def _catalog_payload(record_date: str) -> dict[str, Any]:
    storage = get_order_rankings_v2_service().storage
    activities = storage.list_activities(record_date)
    return {
        "date": record_date,
        "activities": [
            {
                "id": int(item["id"]),
                "merchant_name": item["merchant_name"],
                "slot_time": item["slot_time"],
                "quantity_per_slot": int(item.get("quantity_per_slot") or 0),
                "max_discount": str(item.get("max_discount") or ""),
            }
            for item in activities
        ],
    }


def _announcement_payload() -> dict[str, Any]:
    config = get_ranking_v2_config()
    return {
        "enabled": bool(config.get("announcement_enabled")),
        "title": str(config.get("announcement_title") or ""),
        "body": str(config.get("announcement_body") or ""),
        "image_url": str(config.get("announcement_image_url") or ""),
        "link_url": str(config.get("announcement_link_url") or ""),
    }


def _announcement_image_dir() -> Path:
    directory = resolve_runtime_data_path("order_rankings_v2")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _validate_announcement_url(value: str, field_name: str) -> None:
    candidate = str(value or "").strip()
    if not candidate:
        return
    if candidate.startswith("/") and not candidate.startswith("//"):
        return
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name}必须是 HTTP/HTTPS 地址或站内相对路径")


@router.get("/order-rankings-v2", response_class=HTMLResponse)
async def order_rankings_v2_preview(request: Request):
    today = datetime.now(TIMEZONE).date().isoformat()
    return templates.TemplateResponse(request, "order_rankings_v2.html", {"request": request, "record_date": today, "announcement": _announcement_payload()})


@router.get("/web/admin/order-rankings-v2/preview", response_class=HTMLResponse)
async def order_rankings_v2_admin_preview(request: Request):
    error = await _require_admin(request)
    if error:
        return error
    today = datetime.now(TIMEZONE).date().isoformat()
    return templates.TemplateResponse(request, "order_rankings_v2.html", {"request": request, "record_date": today, "admin_preview": True, "announcement": _announcement_payload()})


@router.get("/api/order-rankings/v2/announcement-image")
async def get_order_rankings_v2_announcement_image():
    directory = _announcement_image_dir()
    for suffix, media_type in ((".jpg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp"), (".gif", "image/gif")):
        path = directory / f"announcement{suffix}"
        if path.is_file():
            return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-cache"})
    return _error("排行榜公告图片不存在", 404)


@router.get("/api/order-rankings/v2/catalog")
async def get_order_rankings_v2_catalog(date: str | None = None):
    record_date = str(date or datetime.now(TIMEZONE).date().isoformat())
    return JSONResponse({"success": True, **_catalog_payload(record_date)})


@router.get("/api/order-rankings/v2/query")
async def get_order_rankings_v2_query(activity_id: int | None = None, merchant_name: str | None = None, date: str | None = None, slot_time: str | None = None):
    storage = get_order_rankings_v2_service().storage
    activity = storage.get_activity(int(activity_id)) if activity_id else storage.find_activity(str(merchant_name or ""), str(date or ""), str(slot_time or ""))
    if not activity:
        return _error("未找到对应排行榜活动", 404)
    payload = storage.get_public_payload(int(activity["id"]))
    if payload is None:
        return _error("未找到排行榜数据", 404)
    return JSONResponse({"success": True, **payload})


@router.get("/web/admin/api/order-rankings-v2/settings")
async def get_order_rankings_v2_settings(request: Request):
    error = await _require_admin(request)
    if error:
        return error
    service = get_order_rankings_v2_service()
    return JSONResponse({"success": True, "config": serialize_ranking_v2_config(), "runtime": service.storage.status()})


@router.put("/web/admin/api/order-rankings-v2/settings")
async def put_order_rankings_v2_settings(request: Request, payload: SettingsPayload):
    error = await _require_admin(request)
    if error:
        return error
    try:
        _validate_announcement_url(payload.announcement_image_url, "公告图片地址")
        _validate_announcement_url(payload.announcement_link_url, "公告跳转链接")
    except ValueError as exc:
        return _error(str(exc), 400)
    before = get_ranking_v2_config()
    config = save_ranking_v2_config(payload.model_dump())
    credential_changed = (
        before.get("source1_username") != config.get("source1_username")
        or bool(payload.source1_password)
        or bool(payload.clear_source1_password)
        or bool(payload.clear_source1_credentials)
        or bool(payload.source1_relay_url)
        or bool(payload.source1_relay_secret)
        or bool(payload.clear_source1_relay_url)
        or bool(payload.clear_source1_relay_secret)
    )
    service = get_order_rankings_v2_service()
    if credential_changed:
        await service.reset_source1_session()
    return JSONResponse({"success": True, "message": "排行榜 V2 设置已保存", "config": serialize_ranking_v2_config(config), "runtime": service.storage.status()})


@router.post("/web/admin/api/order-rankings-v2/announcement/image")
async def upload_order_rankings_v2_announcement_image(request: Request, file: UploadFile = File(...)):
    error = await _require_admin(request)
    if error:
        return error
    allowed = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    suffix = allowed.get(str(file.content_type or "").lower())
    if not suffix:
        return _error("只支持 JPG、PNG、WebP 或 GIF 图片", 400)
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        return _error("公告图片不能超过 5MB", 400)
    directory = _announcement_image_dir()
    for old_suffix in allowed.values():
        old_path = directory / f"announcement{old_suffix}"
        if old_path.exists():
            old_path.unlink()
    target = directory / f"announcement{suffix}"
    target.write_bytes(content)
    config = save_ranking_v2_config({"announcement_image_url": "/api/order-rankings/v2/announcement-image"})
    return JSONResponse({
        "success": True,
        "message": "公告图片上传成功",
        "image_url": config["announcement_image_url"],
        "config": serialize_ranking_v2_config(config),
    })


@router.get("/web/admin/api/order-rankings-v2/status")
async def get_order_rankings_v2_status(request: Request):
    error = await _require_admin(request)
    if error:
        return error
    service = get_order_rankings_v2_service()
    today = datetime.now(TIMEZONE).date().isoformat()
    return JSONResponse({"success": True, "config": serialize_ranking_v2_config(), "runtime": service.storage.status(), "catalog": _catalog_payload(today)})


@router.post("/web/admin/api/order-rankings-v2/source1/test-login")
async def test_order_rankings_v2_source1_login(request: Request):
    error = await _require_admin(request)
    if error:
        return error
    try:
        session = await get_order_rankings_v2_service().test_source1_login()
        return JSONResponse({"success": True, "message": "来源 1 登录和 Cookie 验证成功", "session": {"is_valid": bool(session.get("is_valid")), "last_login_at": int(session.get("last_login_at") or 0), "last_verified_at": int(session.get("last_verified_at") or 0)}})
    except Exception as exc:
        return _error(str(exc) or "来源 1 登录失败", 502)


@router.post("/web/admin/api/order-rankings-v2/source1/activities/refresh")
async def refresh_order_rankings_v2_source1_activities(request: Request):
    error = await _require_admin(request)
    if error:
        return error
    try:
        activities = await get_order_rankings_v2_service().refresh_source1_activities_for_admin()
        return JSONResponse({
            "success": True,
            "message": f"已从来源 1 获取 {len(activities)} 个今日活动场次",
            "date": datetime.now(TIMEZONE).date().isoformat(),
            "activities": [
                {
                    "id": int(item["id"]),
                    "merchant_name": str(item["merchant_name"]),
                    "record_date": str(item["record_date"]),
                    "slot_time": str(item["slot_time"]),
                    "quantity_per_slot": int(item.get("quantity_per_slot") or 0),
                    "max_discount": str(item.get("max_discount") or ""),
                }
                for item in activities
            ],
        })
    except Exception as exc:
        return _error(str(exc) or "来源 1 活动获取失败", 502)


@router.post("/web/admin/api/order-rankings-v2/source1/test-query")
async def test_order_rankings_v2_source1_query(request: Request, payload: TestQueryPayload):
    error = await _require_admin(request)
    if error:
        return error
    try:
        result = await get_order_rankings_v2_service().test_source1_query(payload.merchant_name, payload.record_date, payload.slot_time)
        return JSONResponse({"success": True, "result": result})
    except Exception as exc:
        return _error(str(exc) or "来源 1 榜单解析失败", 502)


@router.post("/web/admin/api/order-rankings-v2/source2/test-query")
async def test_order_rankings_v2_source2_query(request: Request, payload: TestQueryPayload):
    error = await _require_admin(request)
    if error:
        return error
    try:
        result = await get_order_rankings_v2_service().test_source2_query(payload.record_date, payload.slot_time)
        return JSONResponse({"success": True, "result": result})
    except Exception as exc:
        return _error(str(exc) or "来源 2 页面解析失败", 502)


@router.post("/web/admin/api/order-rankings-v2/runs")
async def start_order_rankings_v2_run(request: Request, payload: TestQueryPayload):
    error = await _require_admin(request)
    if error:
        return error
    try:
        run = get_order_rankings_v2_service().start_manual(payload.merchant_name, payload.record_date, payload.slot_time)
        return JSONResponse({"success": True, "message": "已创建 10 分钟灰度采集任务", "run": run})
    except Exception as exc:
        return _error(str(exc) or "创建灰度任务失败", 400)
