from __future__ import annotations

import sqlite3
import math
import re
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routes.auth import _get_current_web_admin_user
from utils.meituan_expand import (
    get_meituan_expand_config,
    get_meituan_expand_coordinate_presets,
    get_meituan_expand_service,
    normalize_credential,
    parse_mttouch_url,
    mask_value,
    token_fingerprint,
)
from utils.path_utils import resolve_runtime_data_path
from utils.logger import setup_logger
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_meituan_expand_config,
    save_system_settings_store,
)


router = APIRouter(tags=["美团神券膨胀"])
logger = setup_logger(__name__)


class ExpandSettingsRequest(BaseModel):
    enabled: bool = False
    user_enabled: bool = False
    purchase_url: str = ""
    clear_purchase_url: bool = False
    default_latitude: float = Field(default=40.60353704, ge=-90, le=90)
    default_longitude: float = Field(default=120.75256222, ge=-180, le=180)
    global_concurrency_limit: int = Field(default=4, ge=1, le=16)
    direct_retry_count: int = Field(default=3, ge=1, le=3)
    task_timeout_seconds: int = Field(default=90, ge=30, le=300)
    proxy_api_url: str = ""
    clear_proxy_api_url: bool = False
    proxy_max_switches: int = Field(default=3, ge=0, le=10)
    proxy_timeout_seconds: int = Field(default=15, ge=3, le=60)


class ExpandPrecheckRequest(BaseModel):
    token_id: int | None = Field(default=None, gt=0)
    target_user_id: int | None = Field(default=None, gt=0)
    mttouch_url: str = ""
    latitude: float | None = None
    longitude: float | None = None


class ExpandBatchPrecheckRequest(BaseModel):
    token_ids: list[int] = Field(default_factory=list)
    coordinate_preset_ids: list[str] = Field(default_factory=list)


class ExpandExecuteRequest(BaseModel):
    confirm: bool = False
    target_index: int = Field(default=0, ge=0)


class UserExpandPrecheckRequest(BaseModel):
    token_id: int = Field(gt=0)


def _error(message: str, status_code: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"success": False, "error": message, **extra}, status_code=status_code)


async def _require_admin(request: Request) -> dict[str, Any] | JSONResponse:
    try:
        user = await _get_current_web_admin_user(request)
        if not isinstance(user, dict) or not bool(user.get("is_admin")):
            return _error("需要管理员权限", 403)
        return user
    except PermissionError as exc:
        status_code = 401 if "登录" in str(exc) else 403
        return _error(str(exc), status_code, login_expired=status_code == 401)
    except Exception as exc:
        logger.warning("读取神券膨胀管理员登录态失败: %s", exc, exc_info=True)
        return _error("读取管理员登录状态失败", 503)


def _db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(str(resolve_runtime_data_path("meituan_query.db")), timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection


def _public_settings(config: dict[str, Any]) -> dict[str, Any]:
    result = dict(config)
    proxy_url = str(result.pop("proxy_api_url", "") or "").strip()
    result["proxy_api_configured"] = bool(proxy_url)
    result["proxy_api_url_masked"] = (proxy_url[:12] + "***") if proxy_url else ""
    return result


def _public_user_job(job: dict[str, Any]) -> dict[str, Any]:
    """Remove coordinates and other admin-only fields from a user result."""
    pre = job.get("pre_result") or {}
    targets = []
    for target in pre.get("targets") or []:
        targets.append({
            key: target.get(key)
            for key in (
                "target_coupon_config_id", "target_coupon_amount_yuan", "target_coupon_threshold_yuan",
                "target_coupon_amount", "target_coupon_threshold", "target_asset_type", "channel",
                "coupon_name", "coupon_amount_yuan", "coupon_threshold_yuan", "coupon_count",
                "status", "failure_reason", "is_large_amount", "request_duration_ms",
            )
            if key in target
        })
    return {
        "id": str(job.get("id") or ""),
        "batch_id": str(job.get("batch_id") or ""),
        "status": str(job.get("status") or ""),
        "mode": str(job.get("mode") or ""),
        "pre_result": {
            "coupon": {
                key: (pre.get("coupon") or {}).get(key)
                for key in ("coupon_name", "coupon_amount_yuan", "coupon_threshold_yuan", "coupon_count")
                if key in (pre.get("coupon") or {})
            },
            "targets": targets,
            "has_large_target": bool(pre.get("has_large_target")),
            "max_target_amount_yuan": pre.get("max_target_amount_yuan", 0),
            "default_target_index": pre.get("default_target_index", 0),
        },
        "result": job.get("result") or {},
        "error_code": str(job.get("error_code") or ""),
        "error_message": str(job.get("error_message") or "")[:160],
        "retry_count": int(job.get("retry_count") or 0),
        "duration_ms": int(job.get("duration_ms") or 0),
        "created_at": str(job.get("created_at") or ""),
        "started_at": str(job.get("started_at") or ""),
        "finished_at": str(job.get("finished_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
    }


def _public_user_batch(snapshot: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in snapshot.items() if key != "jobs"}
    jobs = [_public_user_job(job) for job in snapshot.get("jobs") or []]
    jobs.sort(key=lambda job: (
        not bool((job.get("pre_result") or {}).get("has_large_target")),
        -float((job.get("pre_result") or {}).get("max_target_amount_yuan") or 0),
        str(job.get("created_at") or ""),
    ))
    result["jobs"] = jobs
    return result


async def _current_user_or_error(request: Request) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        from routes.auth import _get_current_web_query_user, _resolve_web_query_user_id
        user = await _get_current_web_query_user(request)
        user_id = _resolve_web_query_user_id(user)
        if user_id <= 0:
            return None, _error("无法识别当前用户", 401)
        return {**user, "id": user_id}, None
    except PermissionError as exc:
        return None, _error(str(exc), 401, login_expired=True)
    except Exception:
        return None, _error("读取登录状态失败", 503)


def _public_user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"] or 0),
        "username": str(row["username"] or ""),
        "status": str(row["status"] or ""),
        "is_admin": bool(row["is_admin"]),
        "valid_token_count": int(row["valid_token_count"] or 0) if "valid_token_count" in row.keys() else 0,
    }


def _coordinate_label(latitude: float, longitude: float, fallback: str = "自定义坐标") -> str:
    for preset in get_meituan_expand_coordinate_presets():
        if (
            abs(float(preset["latitude"]) - float(latitude)) < 0.0000001
            and abs(float(preset["longitude"]) - float(longitude)) < 0.0000001
        ):
            return str(preset["label"])
    return fallback


@router.get("/web/admin/api/meituan-expand/settings")
async def get_settings(request: Request):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    service = get_meituan_expand_service()
    return JSONResponse({
        "success": True,
        "settings": _public_settings(get_meituan_expand_config()),
        "coordinate_presets": get_meituan_expand_coordinate_presets(),
        "runtime": service.runtime(),
        "stats": service.storage.stats(),
    })


@router.put("/web/admin/api/meituan-expand/settings")
async def update_settings(request: Request, payload: ExpandSettingsRequest):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    raw = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    existing = get_meituan_expand_config()
    proxy_api_url = str(raw.get("proxy_api_url") or "").strip()
    purchase_url = str(raw.get("purchase_url") or "").strip()
    if purchase_url:
        parsed_purchase = urlparse(purchase_url)
        if parsed_purchase.scheme != "https" or not parsed_purchase.hostname or len(purchase_url) > 2048:
            return _error("神券购买链接必须是合法的 HTTPS 地址", 400)
    if raw.pop("clear_purchase_url", False):
        raw["purchase_url"] = ""
    elif not purchase_url:
        raw["purchase_url"] = existing.get("purchase_url", "")
    if proxy_api_url:
        parsed_proxy = urlparse(proxy_api_url)
        if parsed_proxy.scheme not in {"http", "https"} or not parsed_proxy.hostname or len(proxy_api_url) > 2048:
            return _error("代理接口地址必须是合法的 HTTP 或 HTTPS 地址", 400)
    if raw.pop("clear_proxy_api_url", False):
        raw["proxy_api_url"] = ""
    elif not str(raw.get("proxy_api_url") or "").strip():
        raw["proxy_api_url"] = existing.get("proxy_api_url", "")
    normalized = normalize_meituan_expand_config(raw)
    if normalized["default_latitude"] == 0 and normalized["default_longitude"] == 0:
        return _error("默认经纬度不能同时为 0", 400)
    runtime = get_meituan_expand_service().runtime()
    if runtime["active_tasks"] and normalized["global_concurrency_limit"] != existing["global_concurrency_limit"]:
        return _error("存在运行中任务，暂时不能修改全局并发上限", 409)
    store = load_system_settings_store()
    store["meituan_expand_config"] = normalized
    save_system_settings_store(store)
    return JSONResponse({"success": True, "settings": _public_settings(normalized), "runtime": get_meituan_expand_service().runtime()})


@router.get("/web/api/meituan-expand/settings")
async def get_user_expand_settings(request: Request):
    user, error = await _current_user_or_error(request)
    if error is not None:
        return error
    config = get_meituan_expand_config()
    latest_batch_id = get_meituan_expand_service().storage.latest_batch_id_for_actor(int(user["id"]))
    return JSONResponse({
        "success": True,
        "enabled": bool(config.get("enabled") and config.get("user_enabled")),
        "purchase_url": str(config.get("purchase_url") or ""),
        "coordinate_count": len(get_meituan_expand_coordinate_presets()),
        "user_id": int(user["id"]),
        "latest_batch_id": latest_batch_id,
    })


@router.post("/web/api/meituan-expand/precheck")
async def user_precheck(request: Request, payload: UserExpandPrecheckRequest):
    user, error = await _current_user_or_error(request)
    if error is not None:
        return error
    config = get_meituan_expand_config()
    if not config.get("enabled") or not config.get("user_enabled"):
        return _error("用户膨胀功能暂未开启", 403)
    from routes.auth import _load_web_token_record_for_user
    token_record = _load_web_token_record_for_user(user_id=int(user["id"]), token_id=int(payload.token_id))
    if not token_record or not token_record.get("is_active"):
        return _error("只能使用自己已保存的有效 Token", 400)
    if get_meituan_expand_service().storage.has_active_jobs_for_token(int(token_record["id"])):
        return _error("该 Token 已有膨胀查询正在进行，请等待完成后再试", 409)
    try:
        credential = normalize_credential(token_record.get("token"), token_record.get("meituan_user_id"))
    except ValueError:
        return _error("Token 信息不完整，请重新添加", 400)
    coordinates = get_meituan_expand_coordinate_presets()
    actor = {
        "actor_id": int(user["id"]),
        "actor_username": str(user.get("username") or "用户"),
        "target_user_id": int(user["id"]),
        "target_username": str(user.get("username") or ""),
        "token_source": "saved_token",
        "token_id": int(token_record["id"]),
    }
    batch = get_meituan_expand_service().submit_batch_precheck(
        actor, [(credential, {"target_user_id": int(user["id"]), "target_username": str(user.get("username") or ""), "token_id": int(token_record["id"]), "token_name": str(token_record.get("name") or "")})], coordinates
    )
    public_batch = _public_user_batch(batch)
    return JSONResponse({
        "success": True,
        "batch": public_batch,
        "jobs": public_batch.get("jobs") or [],
        "message": f"已开始检查 {len(coordinates)} 个位置",
    }, status_code=202)


def _user_batch_owned(snapshot: dict[str, Any], user_id: int) -> bool:
    if int(snapshot.get("actor_id") or 0) == int(user_id):
        return True
    return any(int(job.get("target_user_id") or 0) == int(user_id) for job in snapshot.get("jobs") or [])


@router.get("/web/api/meituan-expand/batches/{batch_id}")
async def get_user_expand_batch(request: Request, batch_id: str):
    user, error = await _current_user_or_error(request)
    if error is not None:
        return error
    snapshot = get_meituan_expand_service().storage.batch_snapshot(batch_id)
    if not snapshot or not _user_batch_owned(snapshot, int(user["id"])):
        return _error("批量任务不存在或无权访问", 404)
    public = _public_user_batch(snapshot)
    return JSONResponse({"success": True, "batch": public, "jobs": public.get("jobs") or []})


@router.post("/web/api/meituan-expand/jobs/{job_id}/execute")
async def user_execute_job(request: Request, job_id: str, payload: ExpandExecuteRequest):
    user, error = await _current_user_or_error(request)
    if error is not None:
        return error
    if not payload.confirm:
        return _error("实际膨胀必须明确确认", 400)
    job = get_meituan_expand_service().storage.get_job(job_id, include_secrets=False)
    if not job or int(job.get("target_user_id") or 0) != int(user["id"]):
        return _error("任务不存在或无权访问", 404)
    try:
        result = get_meituan_expand_service().submit_execute(
            job_id, int(payload.target_index), {"actor_id": int(user["id"]), "actor_username": str(user.get("username") or "用户")}
        )
    except KeyError:
        return _error("任务不存在", 404)
    except ValueError as exc:
        return _error(str(exc), 409)
    return JSONResponse({"success": True, "job": _public_user_job(result), "message": "实际膨胀任务已创建"}, status_code=202)


@router.get("/web/admin/api/meituan-expand/users")
async def list_users(request: Request):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    with _db_connection() as connection:
        rows = connection.execute(
            """
            SELECT u.id, u.username, u.status, u.is_admin, COUNT(t.id) AS valid_token_count
            FROM users u
            JOIN tokens t ON t.user_id=u.id
            WHERE u.status='approved' AND t.is_active=1
              AND t.token IS NOT NULL AND t.token!=''
              AND t.meituan_user_id IS NOT NULL AND t.meituan_user_id!=''
            GROUP BY u.id, u.username, u.status, u.is_admin
            ORDER BY u.is_admin DESC, u.username COLLATE NOCASE
            """
        ).fetchall()
    return JSONResponse({"success": True, "users": [_public_user(row) for row in rows]})


@router.get("/web/admin/api/meituan-expand/tokens")
async def list_tokens(request: Request, user_id: int = 0):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    clauses = [
        "t.is_active=1", "t.token IS NOT NULL", "t.token != ''",
        "t.meituan_user_id IS NOT NULL", "t.meituan_user_id != ''",
        "u.status='approved'",
    ]
    params: list[Any] = []
    if int(user_id or 0) > 0:
        clauses.append("t.user_id=?")
        params.append(int(user_id))
    with _db_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT t.id, t.user_id, t.name, t.token, t.meituan_user_id, t.updated_at,
                   u.username, u.status AS user_status
            FROM tokens t JOIN users u ON u.id=t.user_id
            WHERE {' AND '.join(clauses)}
            ORDER BY u.username COLLATE NOCASE, t.updated_at DESC, t.id DESC
            """,
            params,
        ).fetchall()
    return JSONResponse({
        "success": True,
        "tokens": [
            {
                "id": int(row["id"]), "user_id": int(row["user_id"]),
                "username": str(row["username"] or ""), "name": str(row["name"] or ""),
                "meituan_user_id": mask_value(row["meituan_user_id"]),
                "token_fingerprint": token_fingerprint(str(row["token"] or ""))[:10],
                "token_configured": True, "updated_at": str(row["updated_at"] or ""),
            }
            for row in rows
        ],
    })


def _load_admin_token(token_id: int) -> tuple[dict[str, Any], dict[str, Any]] | None:
    with _db_connection() as connection:
        row = connection.execute(
            """
            SELECT t.id, t.user_id, t.name, t.token, t.meituan_user_id,
                   u.username, u.status
            FROM tokens t JOIN users u ON u.id=t.user_id
            WHERE t.id=? AND t.is_active=1 AND t.token!=''
              AND t.meituan_user_id!='' AND u.status='approved'
            LIMIT 1
            """,
            (int(token_id),),
        ).fetchone()
    if not row:
        return None
    try:
        credential = normalize_credential(row["token"], row["meituan_user_id"])
    except ValueError:
        return None
    owner = {
        "target_user_id": int(row["user_id"]), "target_username": str(row["username"] or ""),
        "token_id": int(row["id"]), "token_name": str(row["name"] or ""),
    }
    return credential, owner


@router.post("/web/admin/api/meituan-expand/precheck")
async def precheck(request: Request, payload: ExpandPrecheckRequest):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    config = get_meituan_expand_config()
    if not config["enabled"]:
        return _error("神券膨胀功能尚未启用", 409)
    if payload.token_id and payload.mttouch_url.strip():
        return _error("token_id 和 mttouch_url 只能选择一个", 400)
    owner: dict[str, Any] = {}
    if payload.token_id:
        loaded = _load_admin_token(int(payload.token_id))
        if loaded is None:
            return _error("Token 不存在、已失效或不具备 userId", 404)
        credential, owner = loaded
        if payload.target_user_id and int(payload.target_user_id) != int(owner["target_user_id"]):
            return _error("所选 Token 不属于目标用户", 400)
        token_source = "saved_token"
    elif payload.mttouch_url.strip():
        try:
            credential = parse_mttouch_url(payload.mttouch_url)
        except ValueError as exc:
            return _error(str(exc), 400)
        token_source = "pasted_link"
        if payload.target_user_id:
            with _db_connection() as connection:
                row = connection.execute(
                    "SELECT id, username, status FROM users WHERE id=? AND status='approved'",
                    (int(payload.target_user_id),),
                ).fetchone()
            if not row:
                return _error("目标用户不存在", 404)
            owner = {"target_user_id": int(row["id"]), "target_username": str(row["username"] or "")}
    else:
        return _error("请选择有效 Token 或粘贴 mttouch 链接", 400)

    latitude = config["default_latitude"] if payload.latitude is None else float(payload.latitude)
    longitude = config["default_longitude"] if payload.longitude is None else float(payload.longitude)
    if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or (latitude == 0 and longitude == 0):
        return _error("经纬度不合法", 400)
    actor_payload = {
        "actor_id": int(admin.get("id") or 0), "actor_username": str(admin.get("username") or ""),
        **owner, "token_source": token_source, "token_id": owner.get("token_id"),
        "latitude": latitude, "longitude": longitude,
        "coordinate_label": _coordinate_label(latitude, longitude),
    }
    job = get_meituan_expand_service().submit_precheck(actor_payload, credential)
    return JSONResponse({"success": True, "job": job, "message": "预查询任务已创建"}, status_code=202)


@router.post("/web/admin/api/meituan-expand/precheck-batch")
async def precheck_batch(request: Request, payload: ExpandBatchPrecheckRequest):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    config = get_meituan_expand_config()
    if not config["enabled"]:
        return _error("神券膨胀功能尚未启用", 409)

    token_ids = list(dict.fromkeys(int(value) for value in payload.token_ids if int(value) > 0))
    preset_ids = list(dict.fromkeys(str(value or "").strip() for value in payload.coordinate_preset_ids if str(value or "").strip()))
    if not token_ids:
        return _error("请至少选择一个有效 Token", 400)
    if not preset_ids:
        return _error("请至少选择一个预置地址或系统默认坐标", 400)
    preset_map = {str(item["id"]): item for item in get_meituan_expand_coordinate_presets()}
    coordinates: list[dict[str, Any]] = []
    seen_coordinates: set[tuple[float, float]] = set()
    for preset_id in preset_ids:
        if preset_id == "default":
            coordinate = {
                "id": "default",
                "label": "系统默认坐标",
                "latitude": float(config["default_latitude"]),
                "longitude": float(config["default_longitude"]),
            }
        else:
            preset = preset_map.get(preset_id)
            if not preset:
                return _error(f"未知预置地址：{preset_id[:40]}", 400)
            coordinate = dict(preset)
        key = (round(float(coordinate["latitude"]), 7), round(float(coordinate["longitude"]), 7))
        if key in seen_coordinates:
            continue
        seen_coordinates.add(key)
        coordinates.append(coordinate)

    accounts: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen_accounts: set[str] = set()
    for token_id in token_ids:
        loaded = _load_admin_token(token_id)
        if loaded is None:
            return _error(f"Token {token_id} 不存在、已失效或不具备 userId", 404)
        credential, owner = loaded
        account_key = str(credential["meituan_user_id"])
        if account_key in seen_accounts:
            continue
        seen_accounts.add(account_key)
        accounts.append((credential, owner))

    combination_count = len(accounts) * len(coordinates)
    if combination_count <= 0:
        return _error("没有可创建的批量预查询组合", 400)

    service = get_meituan_expand_service()
    batch = service.submit_batch_precheck(
        {"actor_id": int(admin.get("id") or 0), "actor_username": str(admin.get("username") or "")},
        accounts,
        coordinates,
    )
    return JSONResponse(
        {
            "success": True,
            "batch": batch,
            "jobs": batch.get("jobs") or [],
            "account_count": len(accounts),
            "coordinate_count": len(coordinates),
            "combination_count": combination_count,
            "message": f"已创建批量预查询，计划检查 {combination_count} 个组合",
        },
        status_code=202,
    )


@router.get("/web/admin/api/meituan-expand/batches/{batch_id}")
async def get_batch(request: Request, batch_id: str):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    snapshot = get_meituan_expand_service().storage.batch_snapshot(batch_id)
    if not snapshot:
        return _error("批量任务不存在", 404)
    return JSONResponse({"success": True, "batch": snapshot, "jobs": snapshot.get("jobs") or []})


@router.get("/web/admin/api/meituan-expand/jobs/{job_id}")
async def get_job(request: Request, job_id: str):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    job = get_meituan_expand_service().storage.get_job(job_id, include_secrets=False)
    if not job:
        return _error("任务不存在", 404)
    return JSONResponse({"success": True, "job": job})


@router.post("/web/admin/api/meituan-expand/jobs/{job_id}/execute")
async def execute_job(request: Request, job_id: str, payload: ExpandExecuteRequest):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    if not payload.confirm:
        return _error("实际膨胀必须明确确认", 400)
    actor = {"actor_id": int(admin.get("id") or 0), "actor_username": str(admin.get("username") or "")}
    try:
        job = get_meituan_expand_service().submit_execute(job_id, int(payload.target_index), actor)
    except KeyError:
        return _error("任务不存在", 404)
    except ValueError as exc:
        return _error(str(exc), 409)
    return JSONResponse({"success": True, "job": job, "message": "实际膨胀任务已创建"}, status_code=202)


@router.post("/web/admin/api/meituan-expand/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    actor = {"actor_id": int(admin.get("id") or 0), "actor_username": str(admin.get("username") or "")}
    try:
        job = get_meituan_expand_service().cancel(job_id, actor)
    except KeyError:
        return _error("任务不存在", 404)
    except ValueError as exc:
        return _error(str(exc), 409)
    return JSONResponse({"success": True, "job": job})


@router.get("/web/admin/api/meituan-expand/jobs")
async def list_jobs(
    request: Request,
    status: str = "",
    user_id: int = 0,
    actor_id: int = 0,
    created_from: str = "",
    created_to: str = "",
    limit: int = 100,
):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    service = get_meituan_expand_service()
    for value in (created_from, created_to):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}:\d{2})?", value):
            return _error("时间筛选格式不正确", 400)
    normalized_from = created_from + " 00:00:00" if len(created_from) == 10 else created_from
    normalized_to = created_to + " 23:59:59" if len(created_to) == 10 else created_to
    return JSONResponse({
        "success": True,
        "jobs": service.storage.list_jobs(
            status=status[:40],
            user_id=int(user_id or 0),
            actor_id=int(actor_id or 0),
            created_from=normalized_from,
            created_to=normalized_to,
            limit=limit,
        ),
    })


@router.get("/web/admin/api/meituan-expand/stats")
async def stats(request: Request):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    service = get_meituan_expand_service()
    return JSONResponse({"success": True, "stats": service.storage.stats(), "runtime": service.runtime()})


@router.get("/web/admin/api/meituan-expand/today-overview")
async def today_overview(request: Request):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    return JSONResponse({
        "success": True,
        "overview": get_meituan_expand_service().storage.today_execute_overview(),
    })


@router.get("/web/admin/api/meituan-expand/jobs/{job_id}/audit")
async def audit(request: Request, job_id: str):
    admin = await _require_admin(request)
    if isinstance(admin, JSONResponse):
        return admin
    job = get_meituan_expand_service().storage.get_job(job_id, include_secrets=False)
    if not job:
        return _error("任务不存在", 404)
    return JSONResponse({"success": True, "audit": get_meituan_expand_service().storage.audits(job_id)})
