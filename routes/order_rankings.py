"""
接单时间排行榜页面
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.logger import setup_logger
from utils.order_leaderboard_service import (
    get_effective_rule_times,
    get_active_shared_leaderboard_rules,
    get_local_order_leaderboard_store,
    get_primary_shared_leaderboard_rule,
    get_rule_by_id,
)
from utils.order_rankings_link_crypto import decrypt_rank_payload
from utils.path_utils import resolve_project_path
from utils.timezone_utils import get_timezone

logger = setup_logger(__name__)
BASE_DIR = resolve_project_path()
TEMPLATE_DIR = BASE_DIR / "html"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(prefix="", tags=["接单时间排行榜"])

DEFAULT_TIMEZONE = "Asia/Shanghai"


def _build_initial_payload(
    rule_id: str | None = None,
    keyword: str | None = None,
    date: str | None = None,
    slot_time: str | None = None,
):
    rules = get_active_shared_leaderboard_rules()
    selected_rule = None
    normalized_rule_id = str(rule_id or "").strip()
    if normalized_rule_id:
        selected_rule = next((item for item in rules if str(item.get("id") or "") == normalized_rule_id), None)
    if selected_rule is None:
        selected_rule = get_primary_shared_leaderboard_rule()

    keywords = list(selected_rule.get("keywords", [])) if isinstance(selected_rule, dict) else []
    timezone_name = str((selected_rule or {}).get("timezone", DEFAULT_TIMEZONE)).strip() or DEFAULT_TIMEZONE
    selected_date = date or datetime.now(get_timezone(timezone_name)).date().isoformat()
    times = get_effective_rule_times(selected_rule, selected_date) if isinstance(selected_rule, dict) else []

    selected_keyword = keyword if keyword in keywords else (keywords[0] if keywords else "")
    selected_slot_time = slot_time if slot_time in times else (times[0] if times else "")

    return {
        "rules": rules,
        "selected_rule": dict(selected_rule or {}),
        "selected_rule_id": str((selected_rule or {}).get("id") or ""),
        "selected_rule_name": str((selected_rule or {}).get("name") or ""),
        "keywords": keywords,
        "times": times,
        "selected_keyword": selected_keyword,
        "selected_slot_time": selected_slot_time,
        "selected_date": selected_date,
        "has_config": bool(keywords and times),
        "timezone_name": timezone_name,
    }


def _normalize_accept_time(accept_time: int | None):
    if accept_time is None:
        return None
    try:
        normalized = int(accept_time)
        if normalized > 1_000_000_000_000:
            normalized = int(normalized / 1000)
        return normalized
    except (TypeError, ValueError):
        return None


def _extract_rank_payload(rank_token: str | None) -> dict[str, Any]:
    normalized = str(rank_token or "").strip()
    if not normalized:
        return {}
    try:
        payload = decrypt_rank_payload(normalized)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _apply_rank_payload(
    *,
    rule_id: str | None,
    keyword: str | None,
    date: str | None,
    slot_time: str | None,
    accept_time: int | None,
    rank_token: str | None,
) -> dict[str, Any]:
    rank_payload = _extract_rank_payload(rank_token)
    resolved_rule_id = str(rule_id or rank_payload.get("rule_id") or "").strip() or None
    resolved_keyword = str(keyword or rank_payload.get("keyword") or "").strip() or None
    resolved_date = str(date or rank_payload.get("date") or "").strip() or None
    resolved_slot_time = str(slot_time or rank_payload.get("slot_time") or "").strip() or None
    resolved_accept_time = _normalize_accept_time(accept_time)
    if resolved_accept_time is None:
        resolved_accept_time = _normalize_accept_time(rank_payload.get("accept_time"))
    return {
        "rule_id": resolved_rule_id,
        "keyword": resolved_keyword,
        "date": resolved_date,
        "slot_time": resolved_slot_time,
        "accept_time": resolved_accept_time,
        "service_order_id": str(rank_payload.get("service_order_id") or "").strip(),
        "order_id": str(rank_payload.get("order_id") or "").strip(),
        "rank_payload": rank_payload,
    }


@router.get("/order-rankings", response_class=HTMLResponse)
async def order_rankings_page(
    request: Request,
    rule_id: str | None = None,
    keyword: str | None = None,
    date: str | None = None,
    slot_time: str | None = None,
    accept_time: int | None = None,
    rank_token: str | None = None,
):
    resolved = _apply_rank_payload(
        rule_id=rule_id,
        keyword=keyword,
        date=date,
        slot_time=slot_time,
        accept_time=accept_time,
        rank_token=rank_token,
    )
    initial_payload = _build_initial_payload(
        rule_id=resolved["rule_id"],
        keyword=resolved["keyword"],
        date=resolved["date"],
        slot_time=resolved["slot_time"],
    )
    return templates.TemplateResponse(request, "order-rankings.html", {
        "request": request,
        "rules": initial_payload["rules"],
        "selected_rule": initial_payload["selected_rule"],
        "selected_rule_id": initial_payload["selected_rule_id"],
        "selected_rule_name": initial_payload["selected_rule_name"],
        "keywords": initial_payload["keywords"],
        "times": initial_payload["times"],
        "selected_keyword": initial_payload["selected_keyword"],
        "selected_slot_time": initial_payload["selected_slot_time"],
        "selected_date": initial_payload["selected_date"],
        "selected_accept_time": resolved["accept_time"],
        "user_rank": None,
        "rankings": [],
        "has_config": initial_payload["has_config"],
        "rank_token": rank_token or "",
    })


@router.get("/api/order-rankings/query")
async def order_rankings_query_api(
    rule_id: str | None = None,
    keyword: str | None = None,
    date: str | None = None,
    slot_time: str | None = None,
    accept_time: int | None = None,
    rank_token: str | None = None,
    cursor: str | None = None,
    page_size: int | None = 50,
):
    try:
        resolved = _apply_rank_payload(
            rule_id=rule_id,
            keyword=keyword,
            date=date,
            slot_time=slot_time,
            accept_time=accept_time,
            rank_token=rank_token,
        )
        initial_payload = _build_initial_payload(
            rule_id=resolved["rule_id"],
            keyword=resolved["keyword"],
            date=resolved["date"],
            slot_time=resolved["slot_time"],
        )
        selected_accept_time = resolved["accept_time"]

        if not initial_payload["has_config"] or not initial_payload["selected_keyword"] or not initial_payload["selected_slot_time"]:
            return JSONResponse({
                "ok": True,
                "rules": initial_payload["rules"],
                "selected_rule": initial_payload["selected_rule"],
                "selected_rule_id": initial_payload["selected_rule_id"],
                "selected_rule_name": initial_payload["selected_rule_name"],
                "keywords": initial_payload["keywords"],
                "times": initial_payload["times"],
                "selected_keyword": initial_payload["selected_keyword"],
                "selected_slot_time": initial_payload["selected_slot_time"],
                "selected_date": initial_payload["selected_date"],
                "selected_accept_time": selected_accept_time,
                "user_rank": None,
                "rankings": [],
                "has_config": initial_payload["has_config"],
                "has_more": False,
                "next_cursor": "",
                "total": 0,
            })

        store = get_local_order_leaderboard_store()
        local_payload = store.query_rankings(
            rule_id=initial_payload["selected_rule_id"],
            record_date=initial_payload["selected_date"],
            slot_time=initial_payload["selected_slot_time"],
            cursor=str(cursor or "").strip(),
            page_size=int(page_size or 50),
            accept_time=selected_accept_time,
            service_order_id=resolved["service_order_id"],
            order_id=resolved["order_id"],
        )
        return JSONResponse({
            "ok": True,
            "rules": initial_payload["rules"],
            "selected_rule": initial_payload["selected_rule"],
            "selected_rule_id": initial_payload["selected_rule_id"],
            "selected_rule_name": initial_payload["selected_rule_name"],
            "keywords": initial_payload["keywords"],
            "times": initial_payload["times"],
            "selected_keyword": initial_payload["selected_keyword"],
            "selected_slot_time": initial_payload["selected_slot_time"],
            "selected_date": initial_payload["selected_date"],
            "selected_accept_time": selected_accept_time,
            "user_rank": local_payload.get("user_rank"),
            "rankings": local_payload.get("rankings") or [],
            "has_config": initial_payload["has_config"],
            "has_more": bool(local_payload.get("has_more")),
            "next_cursor": str(local_payload.get("next_cursor") or ""),
            "total": int(local_payload.get("total") or 0),
        })
    except Exception as e:
        logger.error(f"异步查询接单时间排行榜失败: {e}", exc_info=True)
        return JSONResponse({
            "ok": False,
            "message": "查询排行榜失败",
            "rankings": [],
            "user_rank": None,
            "has_more": False,
            "next_cursor": "",
            "total": 0,
        }, status_code=500)
