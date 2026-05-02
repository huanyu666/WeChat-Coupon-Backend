from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from utils.auth_utils import SESSION_COOKIE_NAME, get_current_user
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_system_settings_store,
    save_system_settings_store,
)

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))
router = APIRouter(prefix="", tags=["系统设置"])


def _audit_system_settings(action: str, operator: str, success: bool, **fields) -> None:
    safe_fields = {key: value for key, value in fields.items() if key in {"error"}}
    log = logger.info if success else logger.warning
    log("system_settings_audit action=%s operator=%s success=%s fields=%s", action, operator, success, safe_fields)


class SystemSettingsPayload(BaseModel):
    prompts_config: dict[str, dict[str, Any]] = Field(default_factory=dict)
    link_config: dict[str, dict[str, Any]] = Field(default_factory=dict)
    order_leaderboard_config: dict[str, dict[str, Any]] = Field(default_factory=dict)


class AdminPasswordPayload(BaseModel):
    username: str | None = Field(default=None, max_length=64)
    password_hash: str = Field(min_length=64, max_length=64)


def _reload_runtime_configs() -> None:
    from routes.wechat import reload_wechat_runtime_configs

    reload_wechat_runtime_configs()


def _serialize_system_settings() -> dict[str, Any]:
    from config.config import LINK_CONFIG, ORDER_LEADERBOARD_CONFIG, PROMPTS_CONFIG

    return {
        "prompts_config": deepcopy(PROMPTS_CONFIG),
        "link_config": deepcopy(LINK_CONFIG),
        "order_leaderboard_config": deepcopy(ORDER_LEADERBOARD_CONFIG),
        "runtime_store": load_system_settings_store(),
    }


@router.get("/system-settings", response_class=HTMLResponse)
async def system_settings_page(request: Request):
    return templates.TemplateResponse(request, "system_settings.html", {"request": request})


@router.get("/api/system-settings")
async def get_system_settings(current_user: str = Depends(get_current_user)):
    return JSONResponse({"success": True, **_serialize_system_settings()})


@router.post("/api/system-settings")
async def save_system_settings(
    payload: SystemSettingsPayload,
    current_user: str = Depends(get_current_user),
):
    try:
        normalized = normalize_system_settings_store(payload.model_dump())
        save_system_settings_store(normalized)
        _reload_runtime_configs()
    except Exception as exc:
        _audit_system_settings("save", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": "保存系统设置失败"}, status_code=500)

    _audit_system_settings("save", current_user, True)
    return JSONResponse({"success": True, "message": "系统设置已保存", **_serialize_system_settings()})


@router.get("/api/system-settings/admin-users")
async def get_admin_users(current_user: str = Depends(get_current_user)):
    from routes.auth import get_admin_usernames

    users = get_admin_usernames()
    return JSONResponse({
        "success": True,
        "current_user": current_user,
        "users": users,
        "single_admin_mode": True,
    })


@router.post("/api/system-settings/admin-users")
async def reset_admin_password(
    payload: AdminPasswordPayload,
    current_user: str = Depends(get_current_user),
):
    from routes.auth import get_admin_usernames, set_single_admin_credentials

    try:
        requested_username = str(payload.username or "").strip()
        if not requested_username:
            raise ValueError("管理员用户名不能为空")
        if current_user not in get_admin_usernames():
            raise ValueError("当前管理员账号不存在，请重新登录")
        set_single_admin_credentials(requested_username, payload.password_hash.strip().lower())
    except ValueError as exc:
        _audit_system_settings("admin_password", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        _audit_system_settings("admin_password", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": "保存管理员账号失败"}, status_code=500)

    _audit_system_settings("admin_password", current_user, True)
    response = JSONResponse({
        "success": True,
        "message": "管理员账号已修改，请使用新账号重新登录",
        "current_user": requested_username,
        "users": get_admin_usernames(),
        "single_admin_mode": True,
    })
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
