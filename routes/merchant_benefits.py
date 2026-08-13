from __future__ import annotations

from typing import Any

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
from utils.meituan_allowance_task_storage import get_meituan_allowance_task_storage
from utils.merchant_benefits import (
    get_benefits_link_status,
    get_merchant_benefits_config,
    get_merchant_benefits_service,
)
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_merchant_benefits_config,
    save_system_settings_store,
)

router = APIRouter(tags=["商家权益查询"])
logger = setup_logger(__name__)


class WebMerchantBenefitsRequest(BaseModel):
    token_id: int = Field(gt=0)
    address_id: str
    allowance_type: str = "large"
    poi_id_str: str
    merchant_name: str


class AdminMerchantBenefitsSettingsRequest(BaseModel):
    enabled: bool = False
    latitude: float = 29.688253
    longitude: float = 106.600316
    positive_cache_seconds: int = Field(default=300, ge=30, le=3600)
    negative_cache_seconds: int = Field(default=120, ge=30, le=1800)


class AdminMerchantBenefitsTestRequest(BaseModel):
    poi_id_str: str
    merchant_name: str
    account_id: str = ""


class PublicTaskMerchantBenefitsRequest(BaseModel):
    task_id: str
    poi_id_str: str
    merchant_name: str


def _error(message: str, status_code: int = 400, **extra: Any) -> JSONResponse:
    return JSONResponse({"success": False, "error": message, **extra}, status_code=status_code)


def _normalize_allowance_type(value: Any) -> str:
    normalized = str(value or "large").strip()
    if normalized not in {"large", "small_free_order"}:
        raise ValueError("津贴类型不合法")
    return normalized


def _merchant_matches(merchants: Any, *, poi_id_str: str, merchant_name: str) -> bool:
    normalized_poi = str(poi_id_str or "").strip()
    normalized_name = str(merchant_name or "").strip()
    if not isinstance(merchants, list) or not normalized_poi or not normalized_name:
        return False
    for item in merchants:
        if not isinstance(item, dict):
            continue
        item_poi = str(item.get("poi_id_str") or item.get("poi_id") or item.get("wm_poi_id_str") or "").strip()
        item_name = str(item.get("poi_name") or item.get("name") or "").strip()
        if item_poi == normalized_poi and item_name == normalized_name:
            return True
    return False


async def _require_admin(request: Request) -> JSONResponse | None:
    try:
        await _get_current_web_admin_user(request)
    except PermissionError as exc:
        status = 401 if "登录" in str(exc) else 403
        return _error(str(exc), status, login_expired=status == 401)
    except Exception:
        return _error("读取管理员登录状态失败", 503)
    return None


@router.post("/web/api/meituan/allowance/merchant-benefits")
async def query_web_allowance_merchant_benefits(request: Request, payload: WebMerchantBenefitsRequest):
    try:
        user = await _get_current_web_query_user(request)
    except PermissionError as exc:
        return _error(str(exc), 401, login_expired=True)
    except Exception:
        return _error("读取登录状态失败", 503)

    user_id = _resolve_web_query_user_id(user or {})
    token = _load_web_token_record_for_user(user_id=user_id, token_id=int(payload.token_id))
    if token is None:
        return _error("Token 不存在或无权访问", 404)
    meituan_user_id = str(token.get("meituan_user_id") or "").strip()
    address_id = str(payload.address_id or "").strip()
    if not meituan_user_id or not address_id:
        return _error("Token 或地址信息不完整", 400)
    try:
        allowance_type = _normalize_allowance_type(payload.allowance_type)
    except ValueError as exc:
        return _error(str(exc), 400)

    aggregate = get_meituan_allowance_task_storage().get_daily_aggregate(
        meituan_user_id=meituan_user_id,
        address_id=address_id,
        allowance_type=allowance_type,
    )
    if not aggregate or not _merchant_matches(
        aggregate.get("merchants"),
        poi_id_str=payload.poi_id_str,
        merchant_name=payload.merchant_name,
    ):
        return _error("该商家不在当前账号今日津贴结果中", 404)

    try:
        result = await get_merchant_benefits_service().query(
            poi_id_str=payload.poi_id_str,
            merchant_name=payload.merchant_name,
            source="web",
        )
        return JSONResponse({"success": True, **result})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:
        logger.warning("Web 商家权益查询失败: user_id=%s error=%s", user_id, exc)
        return _error("权益信息暂时无法获取", 502, status="unknown")


@router.post("/api/meituan/allowance/merchant-benefits")
async def query_public_task_merchant_benefits(payload: PublicTaskMerchantBenefitsRequest):
    task_id = str(payload.task_id or "").strip()
    storage = get_meituan_allowance_task_storage()
    task = storage.get_task(task_id)
    if not task:
        return _error("任务不存在", 404)
    merchants: Any = task.get("merchants")
    if task.get("meituan_user_id") and task.get("address_id"):
        aggregate = storage.get_daily_aggregate(
            meituan_user_id=str(task.get("meituan_user_id") or ""),
            address_id=str(task.get("address_id") or ""),
            allowance_type=_normalize_allowance_type(task.get("allowance_type")),
        )
        if aggregate:
            merchants = aggregate.get("merchants")
    if not _merchant_matches(merchants, poi_id_str=payload.poi_id_str, merchant_name=payload.merchant_name):
        return _error("该商家不在当前津贴结果中", 404)
    try:
        result = await get_merchant_benefits_service().query(
            poi_id_str=payload.poi_id_str,
            merchant_name=payload.merchant_name,
            source="public_result",
        )
        return JSONResponse({"success": True, **result})
    except Exception as exc:
        logger.warning("独立津贴结果页商家权益查询失败: task=%s error=%s", task_id[:12], exc)
        return _error("权益信息暂时无法获取", 502, status="unknown")


@router.get("/web/admin/api/merchant-benefits/settings")
async def get_admin_merchant_benefits_settings(request: Request):
    error = await _require_admin(request)
    if error is not None:
        return error
    config = get_merchant_benefits_config()
    health = await get_merchant_benefits_service().health()
    return JSONResponse(
        {
            "success": True,
            "settings": config,
            "link_status": get_benefits_link_status(),
            "health": health,
            "stats": get_merchant_benefits_service().storage.stats(),
        }
    )


@router.put("/web/admin/api/merchant-benefits/settings")
async def update_admin_merchant_benefits_settings(request: Request, payload: AdminMerchantBenefitsSettingsRequest):
    error = await _require_admin(request)
    if error is not None:
        return error
    raw = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    normalized = normalize_merchant_benefits_config(raw)
    if normalized["latitude"] == 0 and normalized["longitude"] == 0:
        return _error("默认经纬度不能同时为 0", 400)
    store = load_system_settings_store()
    store["merchant_benefits_config"] = normalized
    save_system_settings_store(store)
    return JSONResponse({"success": True, "settings": normalized})


@router.post("/web/admin/api/merchant-benefits/test")
async def test_admin_merchant_benefits(request: Request, payload: AdminMerchantBenefitsTestRequest):
    error = await _require_admin(request)
    if error is not None:
        return error
    try:
        result = await get_merchant_benefits_service().query(
            poi_id_str=payload.poi_id_str,
            merchant_name=payload.merchant_name,
            account_id=payload.account_id,
            source="admin_test",
            force_refresh=True,
        )
        return JSONResponse({"success": True, **result})
    except ValueError as exc:
        return _error(str(exc), 400)
    except Exception as exc:
        logger.warning("后台商家权益链路测试失败: %s", exc)
        return _error(f"链路测试失败：{str(exc)[:120]}", 502, status="unknown")
