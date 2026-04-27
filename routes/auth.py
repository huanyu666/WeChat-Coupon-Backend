"""
认证相关路由
"""
from datetime import datetime
import os
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPAuthorizationCredentials
from config import get_config
from utils.auth_utils import (
    create_session_token,
    verify_credentials,
    get_current_user,
    clear_session,
    security
)
from utils.go_local_api import GO_LOCAL_API_SOCKET_PATH
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path
from utils.proxy_utils import get_proxy_runtime_state
from utils.wechat_utils import access_token_cache
from utils.inflight_request_store import get_inflight_request_store
from wechat_account_store import load_wechat_account_store

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["认证"])


def _build_dashboard_overview(username: str) -> dict:
    store_data = load_wechat_account_store()
    account_specific_configs = store_data.get("account_specific_configs", {})
    keyword_responses = store_data.get("keyword_responses", {})
    default_account_id = str(store_data.get("default_account_id", "") or "")
    accounts = store_data.get("accounts", {})
    if not isinstance(account_specific_configs, dict):
        account_specific_configs = {}
    if not isinstance(keyword_responses, dict):
        keyword_responses = {}
    if not isinstance(accounts, dict):
        accounts = {}

    url_mode_labels = {
        "all": "美团 + 大众点评",
        "meituan": "仅美团",
        "dianping": "仅大众点评",
    }

    activity_items = []
    keyword_rule_count = 0
    processor_binding_count = 0
    authorized_user_count = 0

    for account_id, account_config in accounts.items():
        base_config = account_config if isinstance(account_config, dict) else {}
        specific_config = account_specific_configs.get(account_id, {})
        if not isinstance(specific_config, dict):
            specific_config = {}
        account_keywords = keyword_responses.get(account_id, {})
        if not isinstance(account_keywords, dict):
            account_keywords = {}

        processors = [str(item).strip() for item in specific_config.get("enabled_text_processors", []) if str(item).strip()]
        authorized_users = [str(item).strip() for item in specific_config.get("authorized_users", []) if str(item).strip()]
        keyword_count = len(account_keywords)
        processor_count = len(processors)
        keyword_rule_count += keyword_count
        processor_binding_count += processor_count
        authorized_user_count += len(authorized_users)

        missing_fields = [
            field_name
            for field_name in ("appid", "app_secret", "token")
            if not str(base_config.get(field_name, "") or "").strip()
        ]
        if missing_fields:
            status = "warning"
            status_text = "配置待补齐"
        elif processor_count or keyword_count or str(specific_config.get("default_reply", "") or "").strip() or str(specific_config.get("welcome_message", "") or "").strip():
            status = "online"
            status_text = "运行就绪"
        else:
            status = "processing"
            status_text = "基础配置"

        primary_module = ", ".join(processors[:2]) if processors else "默认文本链路"
        if processor_count > 2:
            primary_module = f"{primary_module} 等 {processor_count} 项"

        mode_label = url_mode_labels.get(str(specific_config.get("url_mode", "all") or "all"), "自定义模式")
        detail_parts = [f"模式：{mode_label}"]
        if authorized_users:
            detail_parts.append(f"授权用户 {len(authorized_users)} 人")
        if keyword_count:
            detail_parts.append(f"关键词 {keyword_count} 条")
        elif str(specific_config.get("default_reply", "") or "").strip():
            detail_parts.append("已配置默认回复")
        if account_id == default_account_id:
            detail_parts.insert(0, "默认账号")

        activity_items.append({
            "account_id": str(account_id),
            "name": str(base_config.get("name", account_id) or account_id),
            "module": primary_module,
            "rule_scale": f"关键词 {keyword_count} · 处理器 {processor_count}",
            "status": status,
            "status_text": status_text,
            "detail": " · ".join(detail_parts),
            "is_default": account_id == default_account_id,
        })

    activity_items.sort(key=lambda item: (not item["is_default"], item["name"], item["account_id"]))

    inflight_store = get_inflight_request_store()
    inflight_entries = inflight_store.get_entries_snapshot()
    if not isinstance(inflight_entries, dict):
        inflight_entries = {}
    inflight_values = list(inflight_entries.values())
    completed_entries = [entry for entry in inflight_values if entry.get("completed_at")]
    successful_entries = [entry for entry in completed_entries if entry.get("response") is not None and not entry.get("error")]
    pending_count = sum(1 for entry in inflight_values if not entry.get("completed_at"))
    avg_latency_seconds = None
    if completed_entries:
        durations = [float(entry.get("duration") or 0.0) for entry in completed_entries]
        avg_latency_seconds = round(sum(durations) / len(durations), 2)
    success_rate = round(len(successful_entries) * 100 / len(completed_entries), 1) if completed_entries else None

    proxy_state = get_proxy_runtime_state()
    proxy_pool_size = int(proxy_state.get("pool_size") or 0) if isinstance(proxy_state, dict) else 0
    proxy_pool_valid_size = int(proxy_state.get("pool_valid_size") or 0) if isinstance(proxy_state, dict) else 0
    client_pool_size = 0
    watcher_count = 0
    try:
        from utils import http_client
        client_pool_size = int(http_client.get_client_stats().get("client_pool_size") or 0)
    except Exception:
        client_pool_size = 0
    try:
        from utils.p_value_storage import get_p_value_storage
        from utils.verification_code import (
            get_link_verification_manager,
            get_mt_order_verification_manager,
            get_verification_manager,
        )
        watcher_count += get_verification_manager().get_watcher_count()
        watcher_count += get_link_verification_manager().get_watcher_count()
        watcher_count += get_mt_order_verification_manager().get_watcher_count()
        watcher_count += get_p_value_storage().get_watcher_count()
    except Exception:
        watcher_count = watcher_count or 0

    active_token_count = 0
    try:
        active_token_count = len(access_token_cache)
    except Exception:
        active_token_count = 0

    go_socket_exists = bool(GO_LOCAL_API_SOCKET_PATH and os.path.exists(GO_LOCAL_API_SOCKET_PATH))
    nodes = [
        {
            "name": "Nginx / OpenResty",
            "meta": "入口路由在线",
            "status": "online",
            "status_text": "正常",
        },
        {
            "name": "Python Backend API",
            "meta": f"账号 {len(activity_items)} 个 · Watcher {watcher_count} 个",
            "status": "online",
            "status_text": "正常",
        },
        {
            "name": "代理池 / HTTP 客户端",
            "meta": f"有效代理 {proxy_pool_valid_size}/{proxy_pool_size} · 连接池 {client_pool_size}",
            "status": "online" if proxy_pool_valid_size > 0 or proxy_pool_size == 0 else "warning",
            "status_text": "可用" if proxy_pool_valid_size > 0 or proxy_pool_size == 0 else "待关注",
        },
        {
            "name": "Go Shortlink Server",
            "meta": GO_LOCAL_API_SOCKET_PATH or "未配置 socket",
            "status": "online" if go_socket_exists else "warning",
            "status_text": "在线" if go_socket_exists else "缺失 / 降级",
        },
    ]

    config_payload = get_config()
    miniprogram_count = 0
    if isinstance(config_payload, dict):
        miniprogram_appids = config_payload.get("miniprogram_appids", {})
        if isinstance(miniprogram_appids, dict):
            miniprogram_count = len(miniprogram_appids)

    return {
        "success": True,
        "username": username,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": {
            "account_count": len(activity_items),
            "default_account_id": default_account_id,
            "keyword_rule_count": keyword_rule_count,
            "processor_binding_count": processor_binding_count,
            "authorized_user_count": authorized_user_count,
            "miniprogram_count": miniprogram_count,
            "recent_request_count": len(inflight_values),
            "pending_request_count": pending_count,
            "avg_latency_seconds": avg_latency_seconds,
            "success_rate": success_rate,
            "watcher_count": watcher_count,
            "client_pool_size": client_pool_size,
            "proxy_pool_size": proxy_pool_size,
            "proxy_pool_valid_size": proxy_pool_valid_size,
            "active_token_count": active_token_count,
            "passive_reply_timeout_budget": 3.5,
        },
        "activity": activity_items,
        "nodes": nodes,
    }


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
    return templates.TemplateResponse(request, "dashboard.html", {"request": request})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"request": request})


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
                "error": "请求参数不完整"
            }, status_code=400)
        
                            
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
            "error": "登录失败，请稍后重试"
        }, status_code=500)


@router.get("/api/auth/verify")
async def verify_token(current_user: str = Depends(get_current_user)):
    """
    验证token是否有效
    """
    return JSONResponse({
        "success": True,
        "username": current_user
    })


@router.get("/api/dashboard/overview")
async def dashboard_overview(current_user: str = Depends(get_current_user)):
    return JSONResponse(_build_dashboard_overview(current_user))


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
