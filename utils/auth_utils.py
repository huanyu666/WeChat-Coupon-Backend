"""
认证相关工具函数
"""
from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

from utils.logger import setup_logger
from utils.path_utils import resolve_runtime_data_path

logger = setup_logger(__name__)
SESSION_COOKIE_NAME = "wx_coupon_session"
SESSION_STORE_FILE = "admin_sessions.runtime.json"


def _get_session_store_path() -> Path:
    return resolve_runtime_data_path(SESSION_STORE_FILE)


def _load_session_cache() -> dict[str, dict]:
    path = _get_session_store_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, dict] = {}
    now = time.time()
    for token, session in payload.items():
        if not isinstance(session, dict):
            continue
        username = str(session.get("username") or "").strip()
        expires_at = float(session.get("expires_at") or 0)
        if not token or not username or expires_at <= now:
            continue
        normalized[str(token)] = {
            "username": username,
            "expires_at": expires_at,
        }
    return normalized


def _save_session_cache(cache: dict[str, dict]) -> None:
    path = _get_session_store_path()
    payload = json.dumps(cache, ensure_ascii=False, indent=2)
    path.write_text(payload, encoding="utf-8")


session_cache = _load_session_cache()


def _prune_expired_sessions() -> None:
    global session_cache
    now = time.time()
    expired_tokens = [token for token, session in session_cache.items() if float(session.get("expires_at") or 0) <= now]
    if not expired_tokens:
        return
    for token in expired_tokens:
        session_cache.pop(token, None)
    _save_session_cache(session_cache)


def create_session_token(username: str, remember_me: bool = False) -> str:
    global session_cache

    token = secrets.token_urlsafe(32)
    expires_in = 7 * 24 * 60 * 60 if remember_me else 24 * 60 * 60
    _prune_expired_sessions()
    session_cache[token] = {
        "username": username,
        "expires_at": time.time() + expires_in
    }
    _save_session_cache(session_cache)

    logger.info(f"创建会话: {username}, 有效期: {expires_in}秒")
    return token


def verify_session_token(token: str) -> Optional[str]:
    global session_cache

    _prune_expired_sessions()
    if token not in session_cache:
        return None

    session = session_cache[token]
    if float(session.get("expires_at") or 0) < time.time():
        del session_cache[token]
        _save_session_cache(session_cache)
        return None

    return str(session.get("username") or "") or None


def verify_credentials(username: str, password_hash: str, timestamp: int, nonce: str) -> bool:
    from config.config import ADMIN_USERS
    import hashlib
    if username not in ADMIN_USERS:
        logger.warning(f"登录失败: 用户 {username} 不存在")
        return False
    if timestamp is None or nonce is None:
        logger.warning(f"登录失败: 用户 {username} （不安全的登录尝试）")
        return False
    stored_hash = ADMIN_USERS[username]

    current_time = int(time.time() * 1000)
    time_diff = abs(current_time - timestamp)
    if time_diff > 3 * 60 * 1000:
        logger.warning(f"登录失败: 用户 {username} 时间戳过期 (差值: {time_diff}ms, 约{time_diff//1000}秒)")
        return False

    combined = f"{stored_hash}{timestamp}{nonce}"
    expected_hash = hashlib.sha256(combined.encode()).hexdigest()
    if password_hash == expected_hash:
        logger.info(f"用户 {username} 登录成功（安全验证通过）")
        return True
    else:
        logger.warning(f"登录失败: 用户 {username} 密码错误")
        return False


async def get_current_user(
    request: Request,
) -> str:
    cookie_token = str(request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if cookie_token:
        username = verify_session_token(cookie_token)
        if username:
            return username

    raise HTTPException(status_code=401, detail="登录已过期，请重新登录")


def clear_session(token: str) -> bool:
    global session_cache

    if token in session_cache:
        username = session_cache[token].get("username", "unknown")
        del session_cache[token]
        _save_session_cache(session_cache)
        logger.info(f"用户 {username} 登出")
        return True

    return False


def clear_all_sessions() -> None:
    global session_cache
    session_cache.clear()
    _save_session_cache(session_cache)
    logger.info("已清除所有后台登录会话")
