"""
认证相关工具函数
"""
from typing import Optional
from fastapi import HTTPException, Request
import secrets
import time
from utils.logger import setup_logger

logger = setup_logger(__name__)
SESSION_COOKIE_NAME = "wx_coupon_session"

        
                                                           
session_cache = {}


def create_session_token(username: str, remember_me: bool = False) -> str:
    """
    创建会话令牌
    
    Args:
        username: 用户名
        remember_me: 是否记住登录状态（7天）
    
    Returns:
        会话令牌字符串
    """
    global session_cache
    
               
    token = secrets.token_urlsafe(32)
    
            
    if remember_me:
        expires_in = 7 * 24 * 60 * 60      
    else:
        expires_in = 24 * 60 * 60        
    
           
    session_cache[token] = {
        "username": username,
        "expires_at": time.time() + expires_in
    }
    
    logger.info(f"创建会话: {username}, 有效期: {expires_in}秒")
    return token


def verify_session_token(token: str) -> Optional[str]:
    """
    验证会话令牌
    
    Args:
        token: 会话令牌
    
    Returns:
        用户名，如果会话无效返回None
    """
    global session_cache
    
    if token not in session_cache:
        return None
    
    session = session_cache[token]
    
            
    if session["expires_at"] < time.time():
        del session_cache[token]
        return None
    
    return session["username"]


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
    """
    获取当前登录用户（用于路由依赖注入）
    
    Args:
        credentials: HTTP认证凭据
    
    Returns:
        用户名
    
    Raises:
        HTTPException: 如果未登录或会话无效
    """
    cookie_token = str(request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if cookie_token:
        username = verify_session_token(cookie_token)
        if username:
            return username

    raise HTTPException(status_code=401, detail="登录已过期，请重新登录")


def clear_session(token: str) -> bool:
    """
    清除指定的会话
    
    Args:
        token: 会话令牌
    
    Returns:
        是否成功清除
    """
    global session_cache
    
    if token in session_cache:
        username = session_cache[token].get("username", "unknown")
        del session_cache[token]
        logger.info(f"用户 {username} 登出")
        return True
    
    return False
