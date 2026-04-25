"""
微信 API 相关工具函数
"""
from typing import Optional
import time
from . import http_client as requests
from config import WECHAT_ACCOUNTS, DEFAULT_WECHAT_CONFIG
from utils.logger import setup_logger

logger = setup_logger(__name__)

                 
                                                                    
access_token_cache = {}


async def get_access_token(account_id: str) -> Optional[str]:
    """
    获取公众号的 Access Token（带缓存）
    
    Args:
        account_id: 公众号ID
        
    Returns:
        access_token 字符串，失败返回 None
    """
    global access_token_cache
    
              
    if account_id in access_token_cache:
        cached = access_token_cache[account_id]
                 
        if cached["expires_at"] > time.time() + 300:
            logger.info(f"使用缓存的 access_token: {account_id}")
            return cached["access_token"]
    
            
    account_config = WECHAT_ACCOUNTS.get(account_id)
    if not account_config:
        account_config = DEFAULT_WECHAT_CONFIG
        logger.warning(f"未找到账号 {account_id}，使用默认配置")
    
    appid = account_config.get("appid")
    app_secret = account_config.get("app_secret")
    
    if not appid or not app_secret:
        logger.error(f"账号 {account_id} 缺少 appid 或 app_secret")
        return None
    
    try:
                               
        url = "https://api.weixin.qq.com/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": appid,
            "secret": app_secret
        }
        
        logger.info(f"正在获取 access_token: {account_id}")
        response = await requests.get(url, params=params, timeout=10)
        result = response.json()
        
        if "access_token" in result:
            access_token = result["access_token"]
            expires_in = result.get("expires_in", 315360000)
            
                      
            access_token_cache[account_id] = {
                "access_token": access_token,
                "expires_at": time.time() + expires_in
            }
            
            logger.info(f"成功获取 access_token: {account_id}, 有效期: {expires_in}秒")
            return access_token
        else:
            logger.error(f"获取 access_token 失败: {result}")
            return None
            
    except Exception as e:
        logger.error(f"获取 access_token 异常: {e}")
        return None

async def send_customer_service_message(account_id: str, openid: str, msg_type: str, content: dict) -> bool:
    """
    发送客服消息（主动消息）
    
    Args:
        account_id: 公众号ID
        openid: 用户OpenID
        msg_type: 消息类型（text, image, voice, video, music, news）
        content: 消息内容字典
        
    Returns:
        是否发送成功
    """
    access_token = await get_access_token(account_id)
    if not access_token:
        logger.error(f"无法获取 access_token，无法发送客服消息: {account_id}")
        return False
    
    try:
        url = f"https://api.weixin.qq.com/cgi-bin/message/custom/send?access_token={access_token}"
        
               
        message = {
            "touser": openid,
            "msgtype": msg_type
        }
        
                    
        if msg_type == "text":
            message["text"] = {"content": content.get("content", "")}
        elif msg_type == "image":
            message["image"] = {"media_id": content.get("media_id", "")}
        elif msg_type == "voice":
            message["voice"] = {"media_id": content.get("media_id", "")}
        elif msg_type == "video":
            message["video"] = {
                "media_id": content.get("media_id", ""),
                "thumb_media_id": content.get("thumb_media_id", ""),
                "title": content.get("title", ""),
                "description": content.get("description", "")
            }
        elif msg_type == "music":
            message["music"] = {
                "title": content.get("title", ""),
                "description": content.get("description", ""),
                "musicurl": content.get("musicurl", ""),
                "hqmusicurl": content.get("hqmusicurl", ""),
                "thumb_media_id": content.get("thumb_media_id", "")
            }
        elif msg_type == "news":
            message["news"] = {"articles": content.get("articles", [])}
        else:
            logger.error(f"不支持的消息类型: {msg_type}")
            return False
        
        response = await requests.post(url, json=message, timeout=10)
        result = response.json()
        
        if result.get("errcode") == 0:
            logger.info(f"客服消息发送成功: {account_id}, openid: {openid}, type: {msg_type}")
            return True
        else:
            error_msg = result.get("errmsg", "未知错误")
            logger.error(f"客服消息发送失败: {account_id}, openid: {openid}, error: {error_msg}")
                                    
            if result.get("errcode") == 45015:
                logger.warning(f"用户48小时内未互动，无法发送客服消息: {openid}")
            return False
            
    except Exception as e:
        logger.error(f"发送客服消息异常: {e}")
        return False



def get_account_config(to_user_name: str) -> Optional[dict]:
    """
    根据 ToUserName 获取对应的公众号配置
    
    Args:
        to_user_name: 公众号原始ID（从消息中的ToUserName字段获取）
        
    Returns:
        公众号配置字典，如果找不到则返回None
    """
    return WECHAT_ACCOUNTS.get(to_user_name)
