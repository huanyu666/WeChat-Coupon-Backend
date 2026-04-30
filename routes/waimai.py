"""
外卖活动落地页 + 静态资源路由

为 waimai.html 提供访问入口。

静态资源（如 wechat_qr.jpg/png）由 `wx_service/routes/christmas_hat.py` 中的 `/static/{filename:path}` 统一提供。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from utils.logger import setup_logger
from utils.path_utils import resolve_project_path

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["外卖落地页"])


@router.get("/waimai", response_class=HTMLResponse)
async def waimai_page(request: Request):
    return templates.TemplateResponse(request, "waimai.html", {"request": request})


@router.get("/boge", response_class=HTMLResponse)
async def boge_page(request: Request):
    return templates.TemplateResponse(request, "boge.html", {"request": request})


@router.get("/liuyue", response_class=HTMLResponse)
async def liuyue_page(request: Request):
    return templates.TemplateResponse(request, "liuyue.html", {"request": request})

@router.get("/youxi", response_class=HTMLResponse)
async def youxi_page(request: Request):
    if not resolve_project_path("html", "youxi.html").exists():
        raise HTTPException(status_code=404, detail="页面不存在")
    return templates.TemplateResponse(request, "youxi.html", {"request": request})


@router.get("/yuwen", response_class=HTMLResponse)
async def yuwen_page(request: Request):
    return templates.TemplateResponse(request, "zudui.html", {
        "request": request,
        "header_extra_lines": ["公众号：余温生活", "QQ群：538038685"]
    })

@router.get("/hxm", response_class=HTMLResponse)
async def hxm_page(request: Request):
    return templates.TemplateResponse(request, "zudui.html", {
        "request": request,
        "header_extra_lines": [
            "🔥 微信关注公众号【哈小弥】回复【进群】",
            "QQ群：776461626",
            "全网羊毛不错过！实时更新报水"
        ],
        "show_subtitle": False
    })

@router.get("/huage", response_class=HTMLResponse)
async def huage_page(request: Request):
    return templates.TemplateResponse(request, "zudui.html", {
        "request": request,
        "header_extra_lines": [
            "🔥 微信关注【公众号:羊毛省省宝】回复【进群】",
            "QQ群：1020123044",
            "全网羊毛不错过！实时更新报水"
        ],
        "show_subtitle": False
    })

@router.get("/bo", response_class=HTMLResponse)
async def bo_page(request: Request):
    return templates.TemplateResponse(request, "zudui.html", {
        "request": request,
        "header_extra_lines": [
            "公众号：羊毛博学长  发送进群",
            "QQ群：979994507"
        ]
    })


@router.get("/yangmao", response_class=HTMLResponse)
async def yangmao_page(request: Request):
    return templates.TemplateResponse(request, "xiaomagao.html", {
        "request": request,
        "header_extra_lines": [
            "🔥 微信关注公众号【羊毛up主】回复【进群】",
            "全网羊毛不错过！实时更新报水"
        ],
        "show_subtitle": False
    })


@router.get("/lanyu", response_class=HTMLResponse)
async def lanyu_page(request: Request):
    return templates.TemplateResponse(request, "zudui.html", {
        "request": request,
        "header_extra_lines": [
            "公众号：蓝雨小天使· ",
            "首发群号：1082568225"
        ]
    })


