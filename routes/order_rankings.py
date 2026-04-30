"""
接单时间排行榜页面
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.go_local_api import query_leaderboard_async
from utils.logger import setup_logger
from utils.order_leaderboard_service import get_shared_order_leaderboard_config
from utils.path_utils import resolve_project_path
from utils.timezone_utils import get_timezone

logger = setup_logger(__name__)
BASE_DIR = resolve_project_path()
TEMPLATE_DIR = BASE_DIR / "html"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

router = APIRouter(prefix="", tags=["接单时间排行榜"])

DEFAULT_TIMEZONE = "Asia/Shanghai"


def _get_shared_config():
    config = get_shared_order_leaderboard_config()
    return config if isinstance(config, dict) else {}


def _build_initial_payload(
    keyword: str | None = None,
    date: str | None = None,
    slot_time: str | None = None,
):
    account_config = _get_shared_config()
    keywords = list(account_config.get("keywords", []))
    times = list(account_config.get("times", []))
    timezone_name = str(account_config.get("timezone", DEFAULT_TIMEZONE)).strip() or DEFAULT_TIMEZONE

    selected_keyword = keyword if keyword in keywords else (keywords[0] if keywords else "")
    selected_slot_time = slot_time if slot_time in times else (times[0] if times else "")
    selected_date = date or datetime.now(get_timezone(timezone_name)).date().isoformat()

    return {
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


@router.get("/order-rankings", response_class=HTMLResponse)
async def order_rankings_page(
    request: Request,
    keyword: str | None = None,
    date: str | None = None,
    slot_time: str | None = None,
    accept_time: int | None = None,
    rank_token: str | None = None,
):
    initial_payload = _build_initial_payload(keyword=keyword, date=date, slot_time=slot_time)
    return templates.TemplateResponse(request, "order-rankings.html", {
        "request": request,
        "keywords": initial_payload["keywords"],
        "times": initial_payload["times"],
        "selected_keyword": initial_payload["selected_keyword"],
        "selected_slot_time": initial_payload["selected_slot_time"],
        "selected_date": initial_payload["selected_date"],
        "selected_accept_time": _normalize_accept_time(accept_time),
        "user_rank": None,
        "rankings": [],
        "has_config": initial_payload["has_config"],
        "rank_token": rank_token or "",
    })


@router.get("/api/order-rankings/query")
async def order_rankings_query_api(
    keyword: str | None = None,
    date: str | None = None,
    slot_time: str | None = None,
    accept_time: int | None = None,
    rank_token: str | None = None,
    cursor: str | None = None,
    page_size: int | None = 50,
):
    try:
        initial_payload = _build_initial_payload(keyword=keyword, date=date, slot_time=slot_time)
        selected_accept_time = _normalize_accept_time(accept_time)

        if not initial_payload["has_config"] or not initial_payload["selected_keyword"] or not initial_payload["selected_slot_time"]:
            return JSONResponse({
                "ok": True,
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

        go_params = {
            "keyword": initial_payload["selected_keyword"],
            "date": initial_payload["selected_date"],
            "slot_time": initial_payload["selected_slot_time"],
            "cursor": str(cursor or "").strip(),
            "page_size": int(page_size or 50),
        }
        if selected_accept_time is not None:
            go_params["accept_time"] = int(selected_accept_time)
        if rank_token:
            go_params["rank_token"] = str(rank_token).strip()

        go_payload = await query_leaderboard_async(go_params)
        return JSONResponse({
            "ok": True,
            "keywords": initial_payload["keywords"],
            "times": initial_payload["times"],
            "selected_keyword": initial_payload["selected_keyword"],
            "selected_slot_time": initial_payload["selected_slot_time"],
            "selected_date": initial_payload["selected_date"],
            "selected_accept_time": selected_accept_time,
            "user_rank": go_payload.get("user_rank"),
            "rankings": go_payload.get("rankings") or [],
            "has_config": initial_payload["has_config"],
            "has_more": bool(go_payload.get("has_more")),
            "next_cursor": str(go_payload.get("next_cursor") or ""),
            "total": int(go_payload.get("total") or 0),
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
