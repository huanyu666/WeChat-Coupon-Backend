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
    leaderboard_rules: list[dict[str, Any]] = Field(default_factory=list)
    shortlink_config: dict[str, Any] = Field(default_factory=dict)
    proxy_config: dict[str, Any] = Field(default_factory=dict)
    global_leaderboard_config: dict[str, Any] = Field(default_factory=dict)


class AdminPasswordPayload(BaseModel):
    username: str | None = Field(default=None, max_length=64)
    password_hash: str = Field(min_length=64, max_length=64)


class ShortlinkTestPayload(BaseModel):
    text: str = ""
    include_bare_urls: bool = False
    shortlink_config: dict[str, Any] = Field(default_factory=dict)


def _reload_runtime_configs() -> None:
    from routes.wechat import reload_wechat_runtime_configs

    reload_wechat_runtime_configs()


def _serialize_system_settings() -> dict[str, Any]:
    from config.config import LINK_CONFIG, ORDER_LEADERBOARD_CONFIG, PROMPTS_CONFIG
    from utils.order_leaderboard_service import (
        get_shared_leaderboard_rules,
        resolve_auto_leaderboard_base_url,
        resolve_auto_leaderboard_url,
    )
    from utils.proxy_utils import get_effective_proxy_api_url

    return {
        "prompts_config": deepcopy(PROMPTS_CONFIG),
        "link_config": deepcopy(LINK_CONFIG),
        "order_leaderboard_config": deepcopy(ORDER_LEADERBOARD_CONFIG),
        "leaderboard_rules": get_shared_leaderboard_rules(),
        "leaderboard_runtime": {
            "auto_base_url": resolve_auto_leaderboard_base_url(),
            "auto_leaderboard_url": resolve_auto_leaderboard_url(),
        },
        "runtime_store": load_system_settings_store(),
        "proxy_runtime": {
            "effective_api_url": get_effective_proxy_api_url(),
        },
    }


def _model_to_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _model_to_partial_dict(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(exclude_unset=True)
    return model.dict(exclude_unset=True)


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
        from utils.order_leaderboard_service import derive_legacy_order_leaderboard_config, normalize_leaderboard_rules_for_runtime

        existing_store = load_system_settings_store()
        merged_payload = {
            **existing_store,
            **_model_to_partial_dict(payload),
        }
        normalized = normalize_system_settings_store(merged_payload)
        normalized_rules = normalize_leaderboard_rules_for_runtime(normalized.get("leaderboard_rules"))
        normalized["leaderboard_rules"] = normalized_rules
        normalized["order_leaderboard_config"] = derive_legacy_order_leaderboard_config(normalized_rules)
        save_system_settings_store(normalized)
        _reload_runtime_configs()
    except ValueError as exc:
        _audit_system_settings("save", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        _audit_system_settings("save", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": "保存系统设置失败"}, status_code=500)

    _audit_system_settings("save", current_user, True)
    return JSONResponse({"success": True, "message": "系统设置已保存", **_serialize_system_settings()})


@router.post("/api/system-settings/proxy/test")
async def test_proxy_settings(current_user: str = Depends(get_current_user)):
    from utils.proxy_utils import test_proxy_api_async

    try:
        result = await test_proxy_api_async(number=1)
    except ValueError as exc:
        _audit_system_settings("proxy_test", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        _audit_system_settings("proxy_test", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": "代理测试失败"}, status_code=500)

    _audit_system_settings("proxy_test", current_user, True)
    return JSONResponse({"success": True, **result})


@router.post("/api/system-settings/shortlink/test")
async def test_shortlink_settings(
    payload: ShortlinkTestPayload,
    current_user: str = Depends(get_current_user),
):
    from utils.shortlink_service import normalize_shortlink_config, transform_shortlinks_in_text_async

    try:
        config = normalize_shortlink_config(payload.shortlink_config)
        if not str(config.get("public_base_url") or "").strip():
            raise ValueError("短链公开地址未配置")
        result = await transform_shortlinks_in_text_async(
            payload.text,
            ttl_seconds=int(config.get("default_ttl_seconds") or 604800),
            include_bare_urls=bool(payload.include_bare_urls),
            max_success_count=100,
            public_base_url=str(config.get("public_base_url") or "").strip(),
            excluded_domains=list(config.get("excluded_domains") or []),
            excluded_prefixes=list(config.get("excluded_prefixes") or []),
        )
    except ValueError as exc:
        _audit_system_settings("shortlink_test", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    except Exception as exc:
        _audit_system_settings("shortlink_test", current_user, False, error=str(exc))
        return JSONResponse({"success": False, "error": "短链测试失败"}, status_code=500)

    _audit_system_settings("shortlink_test", current_user, True)
    return JSONResponse({"success": True, "result": result})


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
