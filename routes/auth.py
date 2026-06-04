"""
认证相关路由
"""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import time
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import config as config_package
import config.config as app_config
from config import get_config
from utils.auth_utils import (
    SESSION_COOKIE_NAME,
    create_session_token,
    verify_credentials,
    get_current_user,
    clear_session,
    clear_all_sessions,
)
from utils.go_local_api import GO_LOCAL_API_SOCKET_PATH
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path, resolve_runtime_data_path
from utils.proxy_utils import get_proxy_runtime_state
from utils.runtime_identity import build_runtime_identity
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_allowance_schedule_config,
    save_system_settings_store,
)
from utils.wechat_utils import access_token_cache
from utils.inflight_request_store import get_inflight_request_store
from wechat_account_store import load_wechat_account_store

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["认证"])


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


LOGIN_FAILURE_WINDOW_SECONDS = _get_env_int("WX_LOGIN_FAILURE_WINDOW_SECONDS", 900)
LOGIN_FAILURE_MAX_ATTEMPTS = _get_env_int("WX_LOGIN_FAILURE_MAX_ATTEMPTS", 8)
LOGIN_LOCK_SECONDS = _get_env_int("WX_LOGIN_LOCK_SECONDS", 900)
_login_failures: dict[str, list[float]] = {}
_login_blocked_until: dict[str, float] = {}
_ADMIN_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
_ADMIN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TOML_SECTION_RE = re.compile(r"^\s*\[([^\]]+)]\s*$")
_ADMIN_LINE_RE = re.compile(r"^(\s*)([^\s=#][^=]*?)(\s*=\s*)([\"'])([0-9a-fA-F]{64})([\"'])(.*)$")


def _client_ip_from_request(request: Request) -> str:
    client_host = request.client.host if request.client else ""
    x_forwarded_for = str(request.headers.get("x-forwarded-for", "") or "").split(",", 1)[0].strip()
    x_real_ip = str(request.headers.get("x-real-ip", "") or "").strip()
    return x_forwarded_for or x_real_ip or client_host or "unknown"


def _login_rate_keys(username: str, client_ip: str) -> list[str]:
    normalized_username = str(username or "").strip().lower() or "unknown"
    normalized_ip = str(client_ip or "").strip() or "unknown"
    return [f"user:{normalized_username}", f"ip:{normalized_ip}"]


def _prune_login_rate_state(now: float) -> None:
    cutoff = now - LOGIN_FAILURE_WINDOW_SECONDS
    for key in list(_login_failures.keys()):
        values = [ts for ts in _login_failures.get(key, []) if ts >= cutoff]
        if values:
            _login_failures[key] = values
        else:
            _login_failures.pop(key, None)
    for key, blocked_until in list(_login_blocked_until.items()):
        if blocked_until <= now:
            _login_blocked_until.pop(key, None)


def _is_login_rate_limited(username: str, client_ip: str) -> bool:
    now = time.time()
    _prune_login_rate_state(now)
    return any(_login_blocked_until.get(key, 0) > now for key in _login_rate_keys(username, client_ip))


def _record_login_failure(username: str, client_ip: str) -> None:
    now = time.time()
    _prune_login_rate_state(now)
    for key in _login_rate_keys(username, client_ip):
        attempts = _login_failures.setdefault(key, [])
        attempts.append(now)
        if len(attempts) >= LOGIN_FAILURE_MAX_ATTEMPTS:
            _login_blocked_until[key] = now + LOGIN_LOCK_SECONDS


def _clear_login_failures(username: str, client_ip: str) -> None:
    for key in _login_rate_keys(username, client_ip):
        _login_failures.pop(key, None)
        _login_blocked_until.pop(key, None)


def _auth_cookie_secure(request: Request) -> bool:
    configured = str(os.getenv("WX_AUTH_COOKIE_SECURE", "") or "").strip().lower()
    if configured in {"1", "true", "yes", "on"}:
        return True
    if configured in {"0", "false", "no", "off"}:
        return False
    return request.url.scheme == "https"


def _require_admin_user(username: str) -> None:
    user_map = load_users()
    user = user_map.get(str(username or "").strip())
    if not user or not bool(user.get("is_admin")):
        raise PermissionError("需要管理员权限")


def _admin_setup_required() -> bool:
    admin_users = getattr(app_config, "ADMIN_USERS", {}) or {}
    return not isinstance(admin_users, dict) or not bool(admin_users)


def _admin_config_path() -> Path:
    raw_path = str(os.getenv("WX_SERVICE_CONFIG_FILE") or os.getenv("CONFIG_FILE") or "").strip()
    if raw_path:
        return Path(raw_path).expanduser()

    runtime_data_dir = str(os.getenv("WX_SERVICE_DATA_DIR") or "").strip()
    if runtime_data_dir:
        return Path(runtime_data_dir).expanduser() / "config.toml"

    return resolve_runtime_data_path("config.toml")


def _quote_toml_key(username: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", username):
        return username
    return json.dumps(username, ensure_ascii=False)


def _toml_line_key(line: str) -> str | None:
    match = _ADMIN_LINE_RE.match(line)
    if not match:
        return None
    raw_key = match.group(2).strip()
    if (raw_key.startswith('"') and raw_key.endswith('"')) or (raw_key.startswith("'") and raw_key.endswith("'")):
        return raw_key[1:-1]
    return raw_key


def _find_admin_section(lines: list[str]) -> tuple[int | None, int | None]:
    start: int | None = None
    end: int | None = None
    for index, line in enumerate(lines):
        match = _TOML_SECTION_RE.match(line)
        if not match:
            continue
        if start is None:
            if match.group(1).strip() == "admin_users":
                start = index
            continue
        end = index
        break
    return start, end


def _upsert_admin_user(lines: list[str], username: str, password_hash: str) -> tuple[list[str], str]:
    start, end = _find_admin_section(lines)
    new_line = f'{_quote_toml_key(username)} = "{password_hash}"'
    if start is None:
        output = list(lines)
        if output and output[-1].strip():
            output.append("")
        output.extend(["[admin_users]", new_line])
        return output, "created_section"

    section_end = end if end is not None else len(lines)
    output = list(lines)
    for index in range(start + 1, section_end):
        if _toml_line_key(output[index]) == username:
            output[index] = new_line
            return output, "updated_user"

    insert_at = section_end
    while insert_at > start + 1 and not output[insert_at - 1].strip():
        insert_at -= 1
    output.insert(insert_at, new_line)
    return output, "created_user"


def _replace_admin_users(lines: list[str], username: str, password_hash: str) -> list[str]:
    start, end = _find_admin_section(lines)
    new_line = f'{_quote_toml_key(username)} = "{password_hash}"'
    if start is None:
        output = list(lines)
        if output and output[-1].strip():
            output.append("")
        output.extend(["[admin_users]", new_line])
        return output

    section_end = end if end is not None else len(lines)
    return list(lines[: start + 1]) + [new_line] + list(lines[section_end:])


def get_admin_usernames() -> list[str]:
    admin_users = getattr(app_config, "ADMIN_USERS", {}) or {}
    if not isinstance(admin_users, dict):
        return []
    return sorted(str(username) for username in admin_users.keys())


def set_admin_password(username: str, password_hash: str) -> str:
    if not _ADMIN_USERNAME_RE.fullmatch(username):
        raise ValueError("管理员用户名只能包含字母、数字、下划线、点、@ 和短横线，长度 1-64")
    if not _ADMIN_HASH_RE.fullmatch(password_hash):
        raise ValueError("密码摘要格式不正确")

    config_path = _admin_config_path().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.exists() else []
    output_lines, action = _upsert_admin_user(lines, username, password_hash)

    if config_path.exists():
        backup_path = config_path.with_name(f"{config_path.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(f".{config_path.name}.tmp")
    temp_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    os.replace(temp_path, config_path)
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass

    app_config.reload_config()
    config_package.ADMIN_USERS = app_config.ADMIN_USERS
    if _admin_setup_required():
        raise RuntimeError("管理员账号写入后未能加载，请检查配置路径")
    return action


def set_single_admin_credentials(username: str, password_hash: str) -> str:
    if not _ADMIN_USERNAME_RE.fullmatch(username):
        raise ValueError("管理员用户名只能包含字母、数字、下划线、点、@ 和短横线，长度 1-64")
    if not _ADMIN_HASH_RE.fullmatch(password_hash):
        raise ValueError("密码摘要格式不正确")

    config_path = _admin_config_path().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.exists() else []
    output_lines = _replace_admin_users(lines, username, password_hash)

    if config_path.exists():
        backup_path = config_path.with_name(f"{config_path.name}.bak.{datetime.now().strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(config_path, backup_path)

    temp_path = config_path.with_name(f".{config_path.name}.tmp")
    temp_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    os.replace(temp_path, config_path)
    try:
        os.chmod(config_path, 0o600)
    except OSError:
        pass

    app_config.reload_config()
    config_package.ADMIN_USERS = app_config.ADMIN_USERS
    if _admin_setup_required():
        raise RuntimeError("管理员账号写入后未能加载，请检查配置路径")
    clear_all_sessions()
    return "replaced_single_admin"


def _create_initial_admin(username: str, password_hash: str) -> None:
    if not _admin_setup_required():
        raise PermissionError("管理员账号已存在，首次设置入口已关闭")
    set_admin_password(username, password_hash)


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
        "environment": build_runtime_identity(),
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
    return templates.TemplateResponse(
        request,
        "login.html",
        {"request": request, "setup_required": _admin_setup_required()},
    )


@router.get("/index", response_class=HTMLResponse)
async def index_page(request: Request):
    """
    返回系统首页
    注意：页面本身不验证会话，而是在前端 JavaScript 中验证 Cookie 会话
    如果未登录，前端会自动跳转到 /login
    """
    return templates.TemplateResponse(request, "dashboard.html", {"request": request})


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {"request": request})


@router.get("/web/admin/api/allowance-settings")
async def get_allowance_settings(current_user: str = Depends(get_current_user)):
    try:
        _require_admin_user(current_user)
    except PermissionError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=403)

    store = load_system_settings_store()
    config = normalize_allowance_schedule_config(store.get("allowance_schedule_config", {}))
    from utils.meituan_allowance_scheduler import get_allowance_schedule_status

    return JSONResponse({
        "success": True,
        "config": config,
        "runtime": get_allowance_schedule_status(),
    })


@router.post("/web/admin/api/allowance-settings")
async def save_allowance_settings(request: Request, current_user: str = Depends(get_current_user)):
    try:
        _require_admin_user(current_user)
    except PermissionError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=403)

    try:
        payload = await request.json()
        config = normalize_allowance_schedule_config(payload)
        store = load_system_settings_store()
        store["allowance_schedule_config"] = config
        save_system_settings_store(store)
        from utils.meituan_allowance_scheduler import get_allowance_schedule_status

        return JSONResponse({
            "success": True,
            "message": "津贴定时设置已保存",
            "config": config,
            "runtime": get_allowance_schedule_status(),
        })
    except Exception as exc:
        logger.warning("保存津贴定时设置失败: %s", exc, exc_info=True)
        return JSONResponse({"success": False, "error": "保存津贴定时设置失败"}, status_code=500)


@router.post("/api/auth/login")
async def login(request: Request):
    """
    用户登录接口
    
    """
    try:
        client_ip = _client_ip_from_request(request)
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

        if _is_login_rate_limited(username, client_ip):
            logger.warning("登录请求被限流: username=%s client_ip=%s", username, client_ip)
            return JSONResponse({
                "success": False,
                "error": "登录失败次数过多，请稍后再试"
            }, status_code=429)
        
                            
        if not verify_credentials(username, password_hash, timestamp, nonce):
            _record_login_failure(username, client_ip)
            return JSONResponse({
                "success": False,
                "error": "用户名或密码错误"
            }, status_code=401)
        
                   
        token = create_session_token(username, remember_me)
        _clear_login_failures(username, client_ip)

        response = JSONResponse({
            "success": True,
            "message": "登录成功"
        })
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=7 * 24 * 60 * 60 if remember_me else 24 * 60 * 60,
            httponly=True,
            samesite="lax",
            secure=_auth_cookie_secure(request),
            path="/",
        )
        return response
        
    except Exception as e:
        logger.error(f"登录异常: {e}")
        return JSONResponse({
            "success": False,
            "error": "登录失败，请稍后重试"
        }, status_code=500)


@router.get("/api/auth/setup/status")
async def setup_status():
    return JSONResponse({
        "success": True,
        "setup_required": _admin_setup_required()
    })


@router.post("/api/auth/setup")
async def setup_admin(request: Request):
    """
    首次部署时通过浏览器创建第一个管理员。
    只有当前没有任何管理员账号时允许调用；创建后立即关闭入口。
    """
    try:
        if not _admin_setup_required():
            return JSONResponse({
                "success": False,
                "error": "管理员账号已存在，请直接登录"
            }, status_code=409)

        client_ip = _client_ip_from_request(request)
        data = await request.json()
        username = str(data.get("username", "") or "").strip()
        password_hash = str(data.get("password_hash", "") or "").strip().lower()
        if not username or not password_hash:
            return JSONResponse({
                "success": False,
                "error": "用户名和密码不能为空"
            }, status_code=400)
        if _is_login_rate_limited(username, client_ip):
            return JSONResponse({
                "success": False,
                "error": "请求次数过多，请稍后再试"
            }, status_code=429)

        try:
            _create_initial_admin(username, password_hash)
        except ValueError as exc:
            _record_login_failure(username, client_ip)
            return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
        except PermissionError as exc:
            return JSONResponse({"success": False, "error": str(exc)}, status_code=409)

        token = create_session_token(username, remember_me=False)
        _clear_login_failures(username, client_ip)
        response = JSONResponse({
            "success": True,
            "message": "管理员账号已创建"
        })
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=24 * 60 * 60,
            httponly=True,
            samesite="lax",
            secure=_auth_cookie_secure(request),
            path="/",
        )
        return response
    except Exception as exc:
        logger.error("首次管理员设置失败: %s", exc)
        return JSONResponse({
            "success": False,
            "error": "初始化失败，请稍后重试"
        }, status_code=500)


@router.get("/api/auth/verify")
async def verify_token(current_user: str = Depends(get_current_user)):
    """
    验证当前 Cookie 会话是否有效
    """
    return JSONResponse({
        "success": True,
        "username": current_user
    })


@router.get("/api/dashboard/overview")
async def dashboard_overview(current_user: str = Depends(get_current_user)):
    return JSONResponse(_build_dashboard_overview(current_user))


@router.post("/api/auth/logout")
async def logout(request: Request):
    """
    用户登出
    """
    cookie_token = str(request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if cookie_token:
        clear_session(cookie_token)
    
    response = JSONResponse({
        "success": True,
        "message": "登出成功"
    })
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return response
