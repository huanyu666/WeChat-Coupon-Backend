"""
圣诞帽生成器相关路由
"""
import gzip
from fastapi import APIRouter, Request, Header
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["圣诞帽生成器"])

BASE_DIR = resolve_project_path()
HTML_DIR = BASE_DIR / "html"
STATIC_DIR = HTML_DIR / "static"

def compress_content(content: bytes) -> bytes:
    """
    压缩内容
    """
    return gzip.compress(content, compresslevel=6)


async def get_compressed_response(
    file_path: Path,
    media_type: str,
    accept_encoding: Optional[str] = None,
    cache_control: str = "public, max-age=3600"
) -> Response:
    if not file_path.exists() or not file_path.is_file():
        return HTMLResponse(content="File not found", status_code=404)
    content = file_path.read_bytes()
    if accept_encoding and 'gzip' in accept_encoding.lower():
        compressed_content = compress_content(content)
        headers = {
            "Content-Encoding": "gzip",
            "Cache-Control": cache_control,
            "Content-Type": media_type,
        }
        return Response(
            content=compressed_content,
            media_type=media_type,
            headers=headers
        )
    return FileResponse(
        path=file_path,
        media_type=media_type,
        headers={"Cache-Control": cache_control}
    )


@router.get("/christmas-hat", response_class=HTMLResponse)
async def christmas_hat_page(request: Request):
    """
    返回圣诞帽生成器页面
    """
    return templates.TemplateResponse(request, "christmas-hat.html", {"request": request})


@router.get("/christmas-hat.css")
async def christmas_hat_css(accept_encoding: Optional[str] = Header(None)):
    """
    返回圣诞帽生成器CSS文件
    """
    css_path = HTML_DIR / "christmas-hat.css"
    return await get_compressed_response(
        file_path=css_path,
        media_type="text/css",
        accept_encoding=accept_encoding,
        cache_control="public, max-age=3600"
    )


@router.get("/christmas-hat.js")
async def christmas_hat_js(accept_encoding: Optional[str] = Header(None)):
    """
    返回圣诞帽生成器JavaScript文件
    """
    js_path = HTML_DIR / "christmas-hat.js"
    return await get_compressed_response(
        file_path=js_path,
        media_type="application/javascript",
        accept_encoding=accept_encoding,
        cache_control="public, max-age=3600"
    )


@router.get("/static/{filename:path}")
async def static_files(
    filename: str,
    accept_encoding: Optional[str] = Header(None)
):
    file_path = STATIC_DIR / filename
    
                         
    try:
        file_path.resolve().relative_to(STATIC_DIR.resolve())
    except ValueError:
        logger.warning(f"尝试访问static目录外的文件: {filename}")
        return HTMLResponse(content="Forbidden", status_code=403)
    
    if not file_path.exists() or not file_path.is_file():
        logger.warning(f"静态文件不存在: {filename}")
        return HTMLResponse(content="File not found", status_code=404)
    
                   
    ext = file_path.suffix.lower()
    media_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.gif': 'image/gif',
        '.svg': 'image/svg+xml',
        '.webp': 'image/webp',
    }
    media_type = media_types.get(ext, 'application/octet-stream')
    
    return await get_compressed_response(
        file_path=file_path,
        media_type=media_type,
        accept_encoding=accept_encoding,
        cache_control="public, max-age=86400"            
    )
