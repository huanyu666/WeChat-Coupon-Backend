"""Third-party Meituan order-time query client.

This endpoint is intentionally isolated from the Meituan Relay/proxy stack: it
does not send cookies, does not acquire a proxy, and only receives the token
value and optional order id needed by the provider.
"""
from __future__ import annotations

import asyncio
import time
import urllib.parse
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from utils import http_client
from utils.system_settings_store import load_system_settings_store, normalize_order_relay_pool_config


class ThirdPartyOrderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status_code: int = 502):
        super().__init__(message)
        self.retryable = bool(retryable)
        self.status_code = int(status_code)


_SEMAPHORES: dict[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = {}
_RUNTIME = {
    "active": 0,
    "success_count": 0,
    "failure_count": 0,
    "last_error": "",
    "last_success_at": 0.0,
    "last_failure_at": 0.0,
}


def get_third_party_order_config() -> dict[str, Any]:
    return normalize_order_relay_pool_config(
        load_system_settings_store().get("order_relay_pool_config", {})
    )


def get_third_party_order_runtime() -> dict[str, Any]:
    return dict(_RUNTIME)


def _get_semaphore(limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    saved = _SEMAPHORES.get(loop)
    if saved is None or saved[0] != limit:
        semaphore = asyncio.Semaphore(limit)
        _SEMAPHORES[loop] = (limit, semaphore)
        return semaphore
    return saved[1]


def _parse_time(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        raw_value = int(value)
        return raw_value // 1000 if raw_value > 1_000_000_000_000 else raw_value
    text = str(value).strip()
    for format_string in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(text, format_string).replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
        except ValueError:
            continue
    return None


def _normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    order_id = str(item.get("id") or item.get("orderId") or "").strip()
    create_time = _parse_time(item.get("createTime"))
    if create_time is None:
        create_time = _parse_time(item.get("creatTimeLong"))
    return {
        "serviceOrderId": order_id,
        "orderId": order_id,
        "poi_name": str(item.get("shopName") or item.get("poi_name") or "未知商家").strip() or "未知商家",
        "createTime": create_time,
        "payTime": _parse_time(item.get("payTime")),
        "acceptTime": _parse_time(item.get("acceptTime")),
        "shopLink": "",
        "poiIdStr": "",
        "merchantCouponUrl": "",
        "merchantCouponAvailable": False,
    }


def _build_provider_token(token: str, meituan_user_id: str = "") -> str:
    normalized_token = str(token or "").strip()
    if normalized_token.startswith(("http://", "https://")):
        return normalized_token
    normalized_user_id = str(meituan_user_id or "").strip()
    if not normalized_user_id:
        return normalized_token
    return "https://i.meituan.com/mttouch/page/account?" + urllib.parse.urlencode({
        "userId": normalized_user_id,
        "token": normalized_token,
    })


def _business_error_message(payload: dict[str, Any]) -> str:
    return str(payload.get("msg") or payload.get("message") or "第三方订单查询失败").strip()


async def query_third_party_orders(
    token: str,
    order_id: str = "",
    meituan_user_id: str = "",
) -> list[dict[str, Any]]:
    config = get_third_party_order_config()
    normalized_token = str(token or "").strip()
    normalized_order_id = str(order_id or "").strip()
    if not normalized_token:
        raise ThirdPartyOrderError("Token不能为空", retryable=False, status_code=400)

    semaphore = _get_semaphore(int(config["third_party_concurrency_limit"]))
    async with semaphore:
        _RUNTIME["active"] += 1
        try:
            response = await http_client.post(
                str(config["third_party_url"]),
                json={
                    "token": _build_provider_token(normalized_token, meituan_user_id),
                    "orderId": normalized_order_id,
                    "key": "0",
                    "counts": "1",
                },
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=int(config["third_party_timeout_seconds"]),
                stateless_cookies=True,
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code >= 500:
                raise ThirdPartyOrderError("第三方订单接口暂时不可用", retryable=True, status_code=status_code)
            if status_code >= 400:
                raise ThirdPartyOrderError("第三方订单接口请求失败", retryable=False, status_code=status_code)
            try:
                payload = response.json()
            except Exception as exc:
                raise ThirdPartyOrderError("第三方订单接口响应非JSON", retryable=True) from exc
            if not isinstance(payload, dict):
                raise ThirdPartyOrderError("第三方订单接口响应结构异常", retryable=True)
            if int(payload.get("code") or 0) != 200:
                raise ThirdPartyOrderError(_business_error_message(payload), retryable=False, status_code=400)
            raw_data = payload.get("data")
            if not isinstance(raw_data, list):
                raise ThirdPartyOrderError("第三方订单接口响应结构异常", retryable=True)
            results = []
            for raw_item in raw_data:
                if not isinstance(raw_item, dict):
                    continue
                if not any(
                    raw_item.get(key) not in (None, "")
                    for key in ("id", "orderId", "order_id", "shopName", "poi_name", "createTime", "creatTimeLong", "payTime", "acceptTime")
                ):
                    continue
                item = _normalize_item(raw_item)
                results.append(item)
            _RUNTIME["success_count"] += 1
            _RUNTIME["last_success_at"] = time.time()
            return results
        except ThirdPartyOrderError:
            _RUNTIME["failure_count"] += 1
            _RUNTIME["last_failure_at"] = time.time()
            _RUNTIME["last_error"] = "third_party_request_failed"
            raise
        except (http_client.Timeout, http_client.ConnectionError) as exc:
            _RUNTIME["failure_count"] += 1
            _RUNTIME["last_failure_at"] = time.time()
            _RUNTIME["last_error"] = exc.__class__.__name__
            raise ThirdPartyOrderError("第三方订单接口连接超时", retryable=True) from exc
        except Exception as exc:
            _RUNTIME["failure_count"] += 1
            _RUNTIME["last_failure_at"] = time.time()
            _RUNTIME["last_error"] = exc.__class__.__name__
            raise ThirdPartyOrderError("第三方订单接口请求异常", retryable=True) from exc
        finally:
            _RUNTIME["active"] = max(0, int(_RUNTIME["active"]) - 1)
