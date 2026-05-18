from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.auth_utils import get_current_user
from utils.log_panel_service import get_log_panel_data
from utils.path_utils import resolve_project_path


templates = Jinja2Templates(directory=str(resolve_project_path("html")))
router = APIRouter(prefix="", tags=["运行日志"])


@router.get("/log-panel", response_class=HTMLResponse)
async def log_panel_page(request: Request):
    return templates.TemplateResponse(request, "log_panel.html", {"request": request})


@router.get("/api/log-panel")
async def log_panel_data(
    current_user: str = Depends(get_current_user),
    minutes: int = Query(default=30, ge=1, le=1440),
    level: str = Query(default=""),
    keyword: str = Query(default="", max_length=120),
    category: str = Query(default=""),
    limit: int = Query(default=300, ge=20, le=1000),
):
    del current_user
    data = get_log_panel_data(
        minutes=minutes,
        level=level,
        keyword=keyword,
        category=category,
        limit=limit,
    )
    return JSONResponse(data)
