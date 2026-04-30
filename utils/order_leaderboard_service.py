"""
接单时间排行榜共享服务
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional
import time

from utils.go_local_api import ingest_leaderboard_hit_async
from utils.timezone_utils import get_timezone

GLOBAL_CONFIG_KEY = "global"
LEGACY_ACCOUNT_ID = "gh_81203cdf19a5"
DEFAULT_TIMEZONE = "Asia/Shanghai"
ORDER_LEADERBOARD_CONFIG_SOURCE = "runtime-data/system_settings.runtime.json"


def get_shared_order_leaderboard_config() -> dict:
    from config.config import ORDER_LEADERBOARD_CONFIG

    config = ORDER_LEADERBOARD_CONFIG.get(GLOBAL_CONFIG_KEY)
    if isinstance(config, dict):
        return dict(config)
    legacy_config = ORDER_LEADERBOARD_CONFIG.get(LEGACY_ACCOUNT_ID)
    if isinstance(legacy_config, dict):
        return dict(legacy_config)
    return {}


async def arecord_leaderboard_hits(
    logger,
    results: Iterable[Dict[str, Any]],
    to_user_name: str,
    user_id: str,
    account_config: Optional[dict] = None,
    ingest_timeout_seconds: float = 0.8,
    deadline_at: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    result_list = list(results)
    config = account_config if isinstance(account_config, dict) else get_shared_order_leaderboard_config()
    times = [str(item).strip() for item in list(config.get("times", [])) if str(item).strip()]
    keywords = [str(item).strip() for item in list(config.get("keywords", [])) if str(item).strip()]
    timezone = str(config.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE

    if not times or not keywords:
        logger.info("排行榜写入跳过: 原因=empty_config times=%s keywords=%s", times, keywords)
        return None

    for index, item in enumerate(result_list, start=1):
        if not isinstance(item, dict):
            logger.warning("排行榜写入跳过: index=%d 原因=invalid_result_item item_type=%s", index, type(item).__name__)
            continue

        if item.get("error"):
            logger.info(
                "排行榜写入跳过: index=%d 原因=item_error error=%s",
                index,
                _format_exception_message(item.get("error")),
            )
            continue

        poi_name = str(item.get("poi_name") or "").strip()
        poi_id_str = str(item.get("poiIdStr", item.get("poi_id_str")) or "").strip()
        raw_accept_time = item.get("acceptTime", item.get("accept_time"))
        try:
            accept_timestamp = normalize_timestamp_seconds(raw_accept_time)
        except Exception as exc:
            logger.warning(
                "排行榜写入跳过: index=%d 原因=invalid_accept_time raw_accept_time=%r error=%s",
                index,
                raw_accept_time,
                _format_exception_message(exc),
            )
            continue

        if not poi_name:
            logger.info(
                "排行榜写入跳过: index=%d 原因=missing_poi_name service_order_id=%s order_id=%s",
                index,
                str(item.get("serviceOrderId", item.get("service_order_id")) or "").strip(),
                str(item.get("orderId", item.get("order_id")) or "").strip(),
            )
            continue
        if accept_timestamp is None:
            logger.info(
                "排行榜写入跳过: index=%d 原因=missing_accept_time poi_name=%s service_order_id=%s order_id=%s",
                index,
                poi_name,
                str(item.get("serviceOrderId", item.get("service_order_id")) or "").strip(),
                str(item.get("orderId", item.get("order_id")) or "").strip(),
            )
            continue

        predicted_match = _predict_leaderboard_match(
            poi_name=poi_name,
            accept_timestamp=int(accept_timestamp),
            keywords=keywords,
            times=times,
            timezone=timezone,
        )
        if not predicted_match["matched_keywords"]:
            logger.info("排行榜本地预判跳过: index=%d 原因=keyword_not_matched poi_name=%s", index, poi_name)
            continue
        if not predicted_match["matched_slots"]:
            logger.info(
                "排行榜本地预判跳过: index=%d 原因=slot_not_matched poi_name=%s accept_time=%s",
                index,
                poi_name,
                accept_timestamp,
            )
            continue

        effective_timeout = _resolve_ingest_timeout(ingest_timeout_seconds, deadline_at)
        if effective_timeout is None:
            logger.info(
                "排行榜写入跳过: index=%d 原因=budget_exhausted poi_name=%s remaining=%.2fs",
                index,
                poi_name,
                _remaining_seconds(deadline_at),
            )
            return None

        service_order_id = str(item.get("serviceOrderId", item.get("service_order_id")) or "").strip()
        order_id = str(item.get("orderId", item.get("order_id")) or "").strip()
        payload = {
            "source": "wx_service",
            "to_user_name": str(to_user_name or "").strip(),
            "user_id": str(user_id or "").strip(),
            "service_order_id": service_order_id,
            "order_id": order_id,
            "poi_id_str": poi_id_str,
            "poi_name": poi_name,
            "accept_time": int(accept_timestamp),
        }
        logger.info(
            "排行榜写入请求: index=%d timeout=%.2fs payload=%s",
            index,
            effective_timeout,
            _summarize_payload(payload),
        )
        try:
            response = await ingest_leaderboard_hit_async(payload, timeout=effective_timeout)
            logger.info(
                "排行榜写入响应: index=%d success=%s recorded=%s reason=%s match=%s",
                index,
                response.get("success"),
                response.get("recorded"),
                response.get("reason"),
                response.get("match"),
            )
            if response.get("recorded") and isinstance(response.get("match"), dict):
                match = dict(response["match"])
                logger.info("排行榜写入命中首个 match: index=%d match=%s", index, match)
                return match
            if not response.get("recorded"):
                logger.warning(
                    "排行榜未写入: index=%d reason=%s poi_name=%s service_order_id=%s order_id=%s accept_time=%s config_source=%s times=%s keywords=%s",
                    index,
                    str(response.get("reason") or "unknown"),
                    poi_name,
                    service_order_id,
                    order_id,
                    accept_timestamp,
                    ORDER_LEADERBOARD_CONFIG_SOURCE,
                    times,
                    keywords,
                )
        except Exception as exc:
            logger.error(
                "写入排行榜失败: index=%d service_order_id=%s order_id=%s poi_name=%s accept_time=%s error=%s",
                index,
                service_order_id,
                order_id,
                poi_name,
                accept_timestamp,
                _format_exception_message(exc),
            )
            return None
        return None
    return None


def _predict_leaderboard_match(
    poi_name: str,
    accept_timestamp: int,
    keywords: Iterable[str],
    times: Iterable[str],
    timezone: str,
) -> Dict[str, Any]:
    accept_dt = datetime.fromtimestamp(int(accept_timestamp), _load_timezone(timezone))
    matched_keywords = []
    for keyword in keywords:
        trimmed_keyword = str(keyword or "").strip()
        if trimmed_keyword and trimmed_keyword in poi_name:
            matched_keywords.append(trimmed_keyword)

    matched_slots = []
    for slot_time in times:
        trimmed_slot_time = str(slot_time or "").strip()
        if trimmed_slot_time and _matches_slot_time(accept_dt, trimmed_slot_time):
            matched_slots.append(trimmed_slot_time)

    return {
        "matched_keywords": matched_keywords,
        "matched_slots": matched_slots,
    }


def _load_timezone(timezone: str):
    normalized = str(timezone or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    return get_timezone(normalized)


def _matches_slot_time(accept_dt: datetime, slot_time: str) -> bool:
    parts = str(slot_time or "").strip().split(":", 1)
    if len(parts) != 2:
        return False
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return False
    if hour < 0 or minute < 0:
        return False

    slot_same_day = accept_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    candidates = [slot_same_day]
    if accept_dt < slot_same_day:
        candidates.append(slot_same_day - timedelta(days=1))

    for candidate in candidates:
        slot_end = candidate + timedelta(minutes=30)
        if candidate <= accept_dt <= slot_end:
            return True
    return False

def normalize_timestamp_seconds(timestamp: Any) -> Optional[int]:
    if timestamp is None:
        return None
    value = int(timestamp)
    if value > 1_000_000_000_000:
        value = int(value / 1000)
    return value


def _format_exception_message(exc: Any) -> str:
    if exc is None:
        return ""
    if isinstance(exc, BaseException):
        message = str(exc).strip()
        if message:
            return f"{exc.__class__.__name__}: {message}"
        return exc.__class__.__name__
    return str(exc).strip()


def _summarize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "to_user_name": str(payload.get("to_user_name") or "").strip(),
        "user_id": str(payload.get("user_id") or "").strip(),
        "service_order_id": str(payload.get("service_order_id") or "").strip(),
        "order_id": str(payload.get("order_id") or "").strip(),
        "poi_id_str": str(payload.get("poi_id_str") or "").strip(),
        "poi_name": str(payload.get("poi_name") or "").strip(),
        "accept_time": payload.get("accept_time"),
    }


def _remaining_seconds(deadline_at: Optional[float]) -> float:
    if deadline_at is None:
        return float("inf")
    return max(0.0, float(deadline_at) - time.time())


def _resolve_ingest_timeout(default_timeout: float, deadline_at: Optional[float]) -> Optional[float]:
    if deadline_at is None:
        return default_timeout
    remaining = _remaining_seconds(deadline_at)
    allowed = remaining - 1.0
    if allowed < 0.25:
        return None
    return min(default_timeout, allowed)
