from __future__ import annotations

import hmac
import secrets
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routes.auth import (
    _get_current_web_admin_user,
    _get_current_web_query_user,
    _load_web_token_record_for_user,
    _resolve_web_query_user_id,
)
from utils.logger import setup_logger
from utils.pushplus_client import PushPlusError, get_pushplus_client
from utils.pushplus_service import (
    PUSHPLUS_QR_TTL_SECONDS,
    ensure_pushplus_config,
    hash_binding_nonce,
    normalize_keywords,
    reset_pushplus_runtime_after_config_change,
    serialize_pushplus_admin_settings,
    serialize_pushplus_user_settings,
)
from utils.pushplus_storage import get_pushplus_notification_storage
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_pushplus_config,
    save_system_settings_store,
)


logger = setup_logger(__name__)
router = APIRouter(tags=["PushPlus 推送"])


class PushPlusPreferencesRequest(BaseModel):
    token_invalid_enabled: bool = True
    merchant_match_enabled: bool = True


class PushPlusKeywordsRequest(BaseModel):
    keywords: list[str] = Field(default_factory=list)


class PushPlusAdminSettingsRequest(BaseModel):
    enabled: bool = False
    platform_token: str = ""
    secret_key: str = ""
    app_id: str = ""
    public_base_url: str = ""
    account_tier: str = "standard"
    clear_platform_token: bool = False
    clear_secret_key: bool = False


class WebAllowanceTaskRequest(BaseModel):
    token_id: str
    resolved_address: dict[str, Any]
    allowance_type: str = "large"


def _error(message: str, status_code: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"success": False, "error": message, **extra}, status_code=status_code)


async def _require_web_user(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        user = await _get_current_web_query_user(request)
    except PermissionError as exc:
        return None, _error(str(exc), 401, login_expired=True)
    except Exception as exc:
        logger.warning("读取 PushPlus 用户登录态失败: %s", exc, exc_info=True)
        return None, _error("读取登录状态失败", 503)
    return user, None


async def _require_admin(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        user = await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status = 401 if "登录" in str(exc) else 403
        return None, _error(str(exc), status, login_expired=status == 401)
    except Exception as exc:
        logger.warning("读取 PushPlus 管理员登录态失败: %s", exc, exc_info=True)
        return None, _error("读取管理员状态失败", 503)
    return user, None


@router.get("/web/api/pushplus/settings")
async def get_user_pushplus_settings(request: Request):
    user, error_response = await _require_web_user(request)
    if error_response is not None:
        return error_response
    user_id = _resolve_web_query_user_id(user or {})
    return JSONResponse({"success": True, **serialize_pushplus_user_settings(user_id)})


@router.post("/web/api/pushplus/bind-sessions")
async def create_pushplus_bind_session(request: Request):
    user, error_response = await _require_web_user(request)
    if error_response is not None:
        return error_response
    user_id = _resolve_web_query_user_id(user or {})
    storage = get_pushplus_notification_storage()
    if storage.get_binding(user_id):
        return _error("当前账号已经绑定微信推送，请先解绑后再重新绑定", 409)

    raw_nonce = secrets.token_urlsafe(32)
    binding_id = uuid.uuid4().hex
    expires_at = int(time.time()) + PUSHPLUS_QR_TTL_SECONDS
    try:
        qr_image_url = await get_pushplus_client().get_personal_qr_code(
            content=raw_nonce,
            seconds=PUSHPLUS_QR_TTL_SECONDS,
            scan_count=1,
        )
    except PushPlusError as exc:
        return _error(str(exc), 502, pushplus_code=exc.code)
    storage.create_bind_session(
        binding_id=binding_id,
        user_id=user_id,
        nonce_hash=hash_binding_nonce(raw_nonce),
        qr_image_url=qr_image_url,
        expires_at=expires_at,
    )
    return JSONResponse(
        {
            "success": True,
            "binding_id": binding_id,
            "status": "pending",
            "qr_image_url": qr_image_url,
            "expires_at": expires_at,
            "expires_in": PUSHPLUS_QR_TTL_SECONDS,
        }
    )


@router.get("/web/api/pushplus/bind-sessions/{binding_id}")
async def get_pushplus_bind_session(request: Request, binding_id: str):
    user, error_response = await _require_web_user(request)
    if error_response is not None:
        return error_response
    user_id = _resolve_web_query_user_id(user or {})
    session = get_pushplus_notification_storage().get_bind_session(
        binding_id=str(binding_id or ""),
        user_id=user_id,
    )
    if session is None:
        return _error("绑定会话不存在", 404)
    return JSONResponse(
        {
            "success": True,
            "binding_id": session["binding_id"],
            "status": session["status"],
            "error": session["error_message"],
            "expires_at": int(session["expires_at"] or 0),
            "bound": session["status"] == "bound",
        }
    )


@router.put("/web/api/pushplus/preferences")
async def update_pushplus_preferences(request: Request, payload: PushPlusPreferencesRequest):
    user, error_response = await _require_web_user(request)
    if error_response is not None:
        return error_response
    user_id = _resolve_web_query_user_id(user or {})
    updated = get_pushplus_notification_storage().update_preferences(
        user_id=user_id,
        token_invalid_enabled=payload.token_invalid_enabled,
        merchant_match_enabled=payload.merchant_match_enabled,
    )
    if not updated:
        return _error("请先绑定微信推送", 409)
    return JSONResponse({"success": True, **serialize_pushplus_user_settings(user_id)})


@router.put("/web/api/pushplus/keywords")
async def update_pushplus_keywords(request: Request, payload: PushPlusKeywordsRequest):
    user, error_response = await _require_web_user(request)
    if error_response is not None:
        return error_response
    user_id = _resolve_web_query_user_id(user or {})
    if not get_pushplus_notification_storage().get_binding(user_id):
        return _error("请先绑定微信推送", 409)
    try:
        keywords = normalize_keywords(payload.keywords)
    except ValueError as exc:
        return _error(str(exc), 400)
    get_pushplus_notification_storage().replace_keywords(user_id=user_id, keywords=keywords)
    return JSONResponse({"success": True, **serialize_pushplus_user_settings(user_id)})


@router.post("/web/api/pushplus/test")
async def enqueue_pushplus_user_test(request: Request):
    user, error_response = await _require_web_user(request)
    if error_response is not None:
        return error_response
    user_id = _resolve_web_query_user_id(user or {})
    storage = get_pushplus_notification_storage()
    if not storage.get_binding(user_id):
        return _error("请先绑定微信推送", 409)
    config = normalize_pushplus_config(load_system_settings_store().get("pushplus_config", {}))
    if not config.get("enabled"):
        return _error("管理员当前已暂停 PushPlus 推送", 409)
    event_ref = uuid.uuid4().hex[:12]
    job_id = storage.enqueue_job(
        event_key=f"user_test:{user_id}:{event_ref}",
        user_id=user_id,
        event_type="test",
        title="平台微信推送测试",
        content=f"PushPlus 微信推送已经绑定成功。\n\n事件编号：`{event_ref}`",
        expires_at=int(time.time()) + 3600,
    )
    return JSONResponse({"success": True, "job_id": job_id, "message": "测试通知已加入发送队列"})


@router.delete("/web/api/pushplus/binding")
async def delete_pushplus_binding(request: Request):
    user, error_response = await _require_web_user(request)
    if error_response is not None:
        return error_response
    user_id = _resolve_web_query_user_id(user or {})
    storage = get_pushplus_notification_storage()
    binding = storage.get_binding(user_id)
    if not binding:
        return JSONResponse({"success": True, "message": "当前账号未绑定微信推送"})
    try:
        await get_pushplus_client().delete_friend(str(binding.get("friend_id") or ""))
    except PushPlusError as exc:
        return _error(f"PushPlus 远程解绑失败，本地绑定已保留：{exc}", 502, pushplus_code=exc.code)
    storage.delete_binding(user_id)
    return JSONResponse({"success": True, "message": "微信推送已解绑"})


@router.post("/api/pushplus/callback/{callback_secret}")
async def pushplus_callback(request: Request, callback_secret: str):
    config = ensure_pushplus_config()
    expected_secret = str(config.get("callback_secret") or "")
    if not expected_secret or not hmac.compare_digest(str(callback_secret or ""), expected_secret):
        return JSONResponse({"code": 403, "msg": "forbidden"}, status_code=403)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    event = str(payload.get("event") or "").strip()
    storage = get_pushplus_notification_storage()
    if event == "add_friend":
        friend_info = payload.get("friendInfo") if isinstance(payload.get("friendInfo"), dict) else {}
        qr_code = str(payload.get("qrCode") or "").strip()
        friend_token = str(friend_info.get("token") or "").strip()
        friend_id = str(friend_info.get("friendId") or "").strip()
        if qr_code and friend_token and friend_id:
            result = storage.complete_bind_session(
                nonce_hash=hash_binding_nonce(qr_code),
                friend_id=friend_id,
                friend_token=friend_token,
                nickname=str(friend_info.get("nickName") or "").strip(),
                head_img_url=str(friend_info.get("headImgUrl") or "").strip(),
            )
            if not result.get("success"):
                logger.warning("PushPlus 扫码绑定未完成: status=%s", result.get("status"))
    elif event == "message_complate":
        message_info = payload.get("messageInfo") if isinstance(payload.get("messageInfo"), dict) else {}
        short_code = str(message_info.get("shortCode") or "").strip()
        try:
            send_status = int(message_info.get("sendStatus") if message_info.get("sendStatus") is not None else -1)
        except (TypeError, ValueError):
            send_status = -1
        if short_code and send_status in {2, 3}:
            job_updated = storage.update_job_delivery_by_short_code(
                short_code=short_code,
                delivered=send_status == 2,
                error_message=str(message_info.get("message") or "PushPlus 投递失败"),
            )
            admin_test_updated = storage.update_send_attempt_delivery_by_short_code(
                short_code=short_code,
                delivery_status=send_status,
                error_message=str(message_info.get("message") or "PushPlus 投递失败"),
            )
            logger.info(
                "PushPlus 消息回调已处理: status=%s job_updated=%s admin_test_updated=%s",
                send_status,
                job_updated,
                admin_test_updated,
            )
    return JSONResponse({"code": 200, "msg": "success"})


@router.post("/web/api/meituan/allowance/tasks")
async def create_web_owned_allowance_task(request: Request, payload: WebAllowanceTaskRequest):
    user, error_response = await _require_web_user(request)
    if error_response is not None:
        return error_response
    web_user_id = _resolve_web_query_user_id(user or {})
    try:
        token_id = int(str(payload.token_id or "").strip())
    except (TypeError, ValueError):
        token_id = 0
    token_record = _load_web_token_record_for_user(user_id=web_user_id, token_id=token_id)
    if token_record is None:
        return _error("Token 不存在或无权访问", 404)
    if not token_record.get("is_active"):
        return _error("当前 Token 已失效，请重新添加", 409)
    if not token_record.get("token") or not token_record.get("meituan_user_id"):
        return _error("当前 Token 信息不完整", 400)

    try:
        from routes.wechat import _create_meituan_allowance_task_internal

        result = await _create_meituan_allowance_task_internal(
            token=str(token_record["token"]),
            allowance_type=str(payload.allowance_type or "large"),
            meituan_user_id=str(token_record["meituan_user_id"]),
            resolved_address=dict(payload.resolved_address or {}),
            web_user_id=web_user_id,
            web_token_id=token_id,
            task_source="manual",
        )
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:
        logger.error("创建 Web 津贴任务失败: user_id=%s token_id=%s error=%s", web_user_id, token_id, exc, exc_info=True)
        return _error("任务创建失败，请稍后重试", 500)
    return JSONResponse({"success": True, **result})


@router.get("/web/admin/api/pushplus/settings")
async def get_admin_pushplus_settings(request: Request):
    _, error_response = await _require_admin(request)
    if error_response is not None:
        return error_response
    config = ensure_pushplus_config()
    return JSONResponse({"success": True, "settings": serialize_pushplus_admin_settings(config)})


@router.put("/web/admin/api/pushplus/settings")
async def update_admin_pushplus_settings(request: Request, payload: PushPlusAdminSettingsRequest):
    _, error_response = await _require_admin(request)
    if error_response is not None:
        return error_response
    public_base_url = str(payload.public_base_url or "").strip().rstrip("/")
    if public_base_url:
        parsed = urlparse(public_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return _error("公开访问地址必须是完整的 http 或 https 地址", 400)
    account_tier = str(payload.account_tier or "standard").strip().lower()
    if account_tier not in {"standard", "member"}:
        return _error("PushPlus 套餐类型无效", 400)

    store = load_system_settings_store()
    current = normalize_pushplus_config(store.get("pushplus_config", {}))
    platform_token = "" if payload.clear_platform_token else (str(payload.platform_token or "").strip() or current["platform_token"])
    secret_key = "" if payload.clear_secret_key else (str(payload.secret_key or "").strip() or current["secret_key"])
    updated = normalize_pushplus_config(
        {
            **current,
            "enabled": payload.enabled,
            "platform_token": platform_token,
            "secret_key": secret_key,
            "app_id": str(payload.app_id or "").strip(),
            "public_base_url": public_base_url,
            "account_tier": account_tier,
        }
    )
    if updated["enabled"] and not (platform_token and secret_key and public_base_url):
        return _error("启用 PushPlus 前请完整配置平台 token、secretKey 和公开访问地址", 400)
    store["pushplus_config"] = updated
    save_system_settings_store(store)
    reset_pushplus_runtime_after_config_change()
    return JSONResponse({"success": True, "settings": serialize_pushplus_admin_settings(updated)})


@router.post("/web/admin/api/pushplus/test-access-key")
async def test_admin_pushplus_access_key(request: Request):
    _, error_response = await _require_admin(request)
    if error_response is not None:
        return error_response
    try:
        access_key = await get_pushplus_client().get_access_key(force_refresh=True)
    except PushPlusError as exc:
        return _error(str(exc), 502, pushplus_code=exc.code)
    return JSONResponse({"success": True, "message": "AccessKey 获取成功", "access_key_masked": access_key[:4] + "***" + access_key[-4:]})


@router.post("/web/admin/api/pushplus/test-send")
async def test_admin_pushplus_send(request: Request):
    _, error_response = await _require_admin(request)
    if error_response is not None:
        return error_response
    storage = get_pushplus_notification_storage()
    attempt_id = storage.record_send_attempt(job_id=0)
    try:
        result = await get_pushplus_client().send_message(
            title="平台 PushPlus 配置测试",
            content=f"管理员测试消息已被 PushPlus 接收。\n\n事件编号：`admin-{uuid.uuid4().hex[:12]}`",
        )
        storage.update_send_attempt(
            attempt_id,
            response_code=int(result.get("code") or 200),
            accepted=True,
            short_code=str(result.get("short_code") or ""),
            response_message=str(result.get("message") or ""),
        )
    except PushPlusError as exc:
        storage.update_send_attempt(attempt_id, response_code=exc.code, accepted=False)
        return _error(str(exc), 502, pushplus_code=exc.code)
    return JSONResponse({"success": True, "message": result.get("message"), "short_code": result.get("short_code")})


@router.get("/web/admin/api/pushplus/stats")
async def get_admin_pushplus_stats(request: Request):
    _, error_response = await _require_admin(request)
    if error_response is not None:
        return error_response
    config = ensure_pushplus_config()
    stats = get_pushplus_notification_storage().get_stats()
    access_key_status = get_pushplus_client().get_access_key_status()
    daily_limit = 2000 if config.get("account_tier") == "member" else 200
    return JSONResponse(
        {
            "success": True,
            "settings": serialize_pushplus_admin_settings(config),
            "stats": {**stats, "daily_limit": daily_limit, "access_key": access_key_status},
        }
    )
