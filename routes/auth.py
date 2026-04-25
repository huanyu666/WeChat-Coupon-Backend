"""
认证相关路由
"""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPAuthorizationCredentials
from utils.auth_utils import (
    create_session_token,
    verify_credentials,
    get_current_user,
    clear_session,
    security
)
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["认证"])


@router.get("/", response_class=HTMLResponse)
async def root():
    """
    根路径重定向到登录页面
    """
    return RedirectResponse(url="/login")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """
    返回登录页面
    """
    return templates.TemplateResponse(request, "login.html", {"request": request})


@router.get("/index", response_class=HTMLResponse)
async def index_page(request: Request):
    """
    返回系统首页
    注意：页面本身不验证 token，而是在前端 JavaScript 中验证
    如果未登录，前端会自动跳转到 /login
    """
    return templates.TemplateResponse(request, "index.html", {"request": request})


@router.post("/api/auth/login")
async def login(request: Request):
    """
    用户登录接口
    
    """
    try:
        data = await request.json()
        username = data.get("username", "").strip()
        password_hash = data.get("password_hash", "")
        timestamp = data.get("timestamp")
        nonce = data.get("nonce")
        remember_me = data.get("remember_me", False)
        
        if not username or not password_hash:
            return JSONResponse({
                "success": False,
                "error": "用户名和密码不能为空"
            }, status_code=400)
        
        if timestamp is None or nonce is None:
            return JSONResponse({
                "success": False,
                "error": "What are you doing?"
            }, status_code=404)
        
                            
        if not verify_credentials(username, password_hash, timestamp, nonce):
            return JSONResponse({
                "success": False,
                "error": "用户名或密码错误"
            }, status_code=401)
        
                   
        token = create_session_token(username, remember_me)
        
        return JSONResponse({
            "success": True,
            "token": token,
            "message": "登录成功"
        })
        
    except Exception as e:
        logger.error(f"登录异常: {e}")
        return JSONResponse({
            "success": False,
            "error": f"芜湖"
        }, status_code=404)


@router.get("/api/auth/verify")
async def verify_token(current_user: str = Depends(get_current_user)):
    """
    验证token是否有效
    """
    return JSONResponse({
        "success": True,
        "username": current_user
    })


@router.post("/api/auth/logout")
async def logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """
    用户登出
    """
    if credentials:
        token = credentials.credentials
        clear_session(token)
    
    return JSONResponse({
        "success": True,
        "message": "登出成功"
    })
