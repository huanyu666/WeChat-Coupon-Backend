"""
微信消息处理相关路由
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional, Any, Dict, List, Callable, Awaitable
from config import get_default_wechat_config, get_wechat_accounts
from utils import *
from utils.wechat_utils import get_account_config
from utils.inflight_request_store import get_inflight_request_store
from utils.auth_utils import get_current_user
from utils.logger import setup_logger
from utils.request_cache import get_request_cache
from utils.msg_id_dedup import get_msg_id_dedup
from utils.shortlink_service import get_shortlink_config, transform_shortlinks_in_text_async
from utils.response import TextRspMsg
from utils.account_config import merge_account_runtime_config, has_zmkey
from handlers import *
from miniprogram import *
from text_processors import *
from pydantic import BaseModel
from utils import http_client as requests
from utils.xml_parser import extract_xml_fields
import logging
import asyncio
import urllib.parse
import time
import json
import re
import os
import resource
import traceback
import hashlib
import uuid
from utils.path_utils import resolve_project_path
from utils.meituan_allowance_task_storage import get_meituan_allowance_task_storage
from utils.meituan_utils import build_meituan_coupon_url
from wechat_account_store import load_wechat_account_store

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["微信接口"])


def _get_env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)) or default)
    except (TypeError, ValueError):
        return default


WECHAT_SYNC_WAIT_SECONDS = 3.5
WECHAT_RESOURCE_EXHAUSTED_WAIT_SECONDS = 1.0
WECHAT_ROUTE_SLOW_STEP_WARN_SECONDS = 4.0
WECHAT_TEXT_HANDLER_WARN_SECONDS = 1.5
WECHAT_MAX_BODY_BYTES = _get_env_int("WX_WECHAT_MAX_BODY_BYTES", 262144)
WECHAT_PASSIVE_TEXT_MAX_LENGTH = 1580
WECHAT_PASSIVE_TEXT_SAFE_CONTENT_BYTES = _get_env_int("WX_WECHAT_PASSIVE_TEXT_SAFE_CONTENT_BYTES", 1900)
WECHAT_PASSIVE_TEXT_SAFE_XML_BYTES = _get_env_int("WX_WECHAT_PASSIVE_TEXT_SAFE_XML_BYTES", 3000)
WECHAT_PASSIVE_TEXT_TRUNCATE_WARNING = "\n\n⚠️ 内容过长，请分段尝试"
ANCHOR_OPEN_TAG_RE = re.compile(r"<a\b[^>]*>")
PASSIVE_TEXT_ANCHOR_RE = re.compile(r"<a\b(?P<attrs>[^>]*)>(?P<label>.*?)</a>", re.IGNORECASE | re.DOTALL)
PASSIVE_TEXT_HREF_RE = re.compile(r'\bhref=(["\'])(?P<href>.*?)\1', re.IGNORECASE | re.DOTALL)
PASSIVE_TEXT_MP_APPID_RE = re.compile(r'\bdata-miniprogram-appid=(["\'])(?P<appid>.*?)\1', re.IGNORECASE | re.DOTALL)
PASSIVE_TEXT_MP_PATH_RE = re.compile(r'\bdata-miniprogram-path=(["\'])(?P<path>.*?)\1', re.IGNORECASE | re.DOTALL)
PASSIVE_TEXT_WEBVIEW_URL_RE = re.compile(r"(?:^|[?&])webviewUrl=(?P<url>[^&]+)", re.IGNORECASE)
MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS = _get_env_int("WX_MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS", 3)
MEITUAN_PROXY_RETRY_DELAY_SECONDS = 0.15
MEITUAN_ALLOWANCE_PAGE_SIZE = 10
MEITUAN_ALLOWANCE_REQUEST_DELAY_SECONDS = 0.3
MEITUAN_ALLOWANCE_PAGE_TIMEOUT_MAX_ATTEMPTS = _get_env_int("WX_MEITUAN_ALLOWANCE_PAGE_TIMEOUT_MAX_ATTEMPTS", 2)
MEITUAN_ALLOWANCE_PAGE_TIMEOUT_RETRY_DELAY_SECONDS = _get_env_int("WX_MEITUAN_ALLOWANCE_PAGE_TIMEOUT_RETRY_DELAY_SECONDS", 3)
MEITUAN_ALLOWANCE_EMPTY_STOP_THRESHOLD = 3
MEITUAN_ALLOWANCE_MAX_PAGES = 100
MEITUAN_ALLOWANCE_ENDPOINT = "https://adapi.waimai.meituan.com/adhub/lite/landingPage/getAds"
MEITUAN_ALLOWANCE_RELAY_URL = str(os.getenv("WX_MEITUAN_ALLOWANCE_RELAY_URL") or "").strip()
MEITUAN_ALLOWANCE_RELAY_SECRET = str(os.getenv("WX_MEITUAN_ALLOWANCE_RELAY_SECRET") or "").strip()


def _format_error_message(error: Any, default: str = "查询失败，请稍后重试") -> str:
    message = str(error or "").strip()
    return message or default


def _is_local_resource_exhausted_message(error: Any) -> bool:
    message = _format_error_message(error, "").lower()
    return "too many open files" in message or "emfile" in message or "local_resource_exhausted" in message


def _is_local_resource_pressure_high() -> bool:
    try:
        fd_limit = int(resource.getrlimit(resource.RLIMIT_NOFILE)[0])
        if fd_limit <= 0:
            return False
        fd_open_count = len(os.listdir("/proc/self/fd"))
        return fd_open_count >= int(fd_limit * 0.9)
    except Exception:
        return False


def _is_info_enabled() -> bool:
    return logger.isEnabledFor(logging.INFO)


def _is_debug_enabled() -> bool:
    return logger.isEnabledFor(logging.DEBUG)


def _should_retry_proxy_request(error: Exception) -> bool:
    return isinstance(error, (requests.Timeout, requests.ConnectionError, requests.HTTPError))


def _truncate_wechat_passive_text(content: str, *, log_if_truncated: bool = True) -> str:
    max_length = WECHAT_PASSIVE_TEXT_MAX_LENGTH
    warning_msg = WECHAT_PASSIVE_TEXT_TRUNCATE_WARNING
    warning_length = len(warning_msg)
    target_visible = max_length - warning_length
    if len(content) <= max_length:
        return content
    if "<a" in content:
        visible_length = len(ANCHOR_OPEN_TAG_RE.sub("", content))
    else:
        visible_length = len(content)
    if visible_length <= target_visible:
        return content

    truncated_length = max_length - warning_length
    max_search_distance = 200
    search_start = max(0, truncated_length - max_search_distance)
    last_newline_pos = content.rfind("\n", search_start, truncated_length)

    if last_newline_pos != -1 and (truncated_length - last_newline_pos) <= max_search_distance:
        truncated_content = content[:last_newline_pos]
        if log_if_truncated and _is_info_enabled():
            logger.info(
                "文本消息内容过长（%s字符，可见%s字符），从换行处截断至%s字符",
                len(content),
                visible_length,
                last_newline_pos,
            )
    else:
        truncated_content = content[:truncated_length]
        if log_if_truncated and _is_info_enabled():
            logger.info(
                "文本消息内容过长（%s字符，可见%s字符），已截断至%s字符",
                len(content),
                visible_length,
                truncated_length,
            )
    return truncated_content + warning_msg


def _build_wechat_text_diagnostics(msg: Dict[str, Any], content: Any) -> Dict[str, int]:
    text = str(content or "")
    rsp = TextRspMsg(msg)
    rsp.content = text
    response_xml = rsp.dump_xml()
    xml_len = len(response_xml) if isinstance(response_xml, (str, bytes)) else len(str(response_xml))
    xml_bytes = len(response_xml.encode("utf-8")) if isinstance(response_xml, str) else xml_len
    return {
        "content_len": len(text),
        "content_bytes": len(text.encode("utf-8")),
        "xml_len": xml_len,
        "xml_bytes": xml_bytes,
    }


def _wechat_passive_text_budget_status(
    content: str,
    diagnostics: Dict[str, int],
) -> Dict[str, Any]:
    char_within_budget = _truncate_wechat_passive_text(content, log_if_truncated=False) == content
    content_bytes = int(diagnostics.get("content_bytes") or 0)
    xml_bytes = int(diagnostics.get("xml_bytes") or 0)
    content_bytes_within_budget = content_bytes <= WECHAT_PASSIVE_TEXT_SAFE_CONTENT_BYTES
    xml_bytes_within_budget = xml_bytes <= WECHAT_PASSIVE_TEXT_SAFE_XML_BYTES
    return {
        "within_budget": bool(
            char_within_budget
            and content_bytes_within_budget
            and xml_bytes_within_budget
        ),
        "char_within_budget": char_within_budget,
        "content_bytes_within_budget": content_bytes_within_budget,
        "xml_bytes_within_budget": xml_bytes_within_budget,
        "content_bytes_limit": WECHAT_PASSIVE_TEXT_SAFE_CONTENT_BYTES,
        "xml_bytes_limit": WECHAT_PASSIVE_TEXT_SAFE_XML_BYTES,
    }


def _sanitize_wechat_passive_text(content: Any) -> str:
    text = str(content or "")
    if "<a" not in text:
        return text

    def _has_miniprogram_target(attrs: str) -> bool:
        appid_match = PASSIVE_TEXT_MP_APPID_RE.search(attrs)
        path_match = PASSIVE_TEXT_MP_PATH_RE.search(attrs)
        appid = str(appid_match.group("appid") or "").strip() if appid_match else ""
        path = str(path_match.group("path") or "").strip() if path_match else ""
        return bool(appid and path)

    def _replace_anchor(match: re.Match) -> str:
        attrs = match.group("attrs") or ""
        label = match.group("label") or ""
        href_match = PASSIVE_TEXT_HREF_RE.search(attrs)
        href = str(href_match.group("href") or "").strip() if href_match else ""
        if "data-miniprogram-" in attrs.lower():
            path_match = PASSIVE_TEXT_MP_PATH_RE.search(attrs)
            if path_match:
                webview_match = PASSIVE_TEXT_WEBVIEW_URL_RE.search(path_match.group("path") or "")
                if webview_match:
                    href = urllib.parse.unquote(webview_match.group("url") or "").strip()
            if not href and _has_miniprogram_target(attrs):
                return label
            if not href:
                return label
        if not href or href.lower() in {"http://", "https://"}:
            return label
        href = href.replace('"', "%22")
        return f'<a href="{href}">{label}</a>'

    sanitized = PASSIVE_TEXT_ANCHOR_RE.sub(_replace_anchor, text)
    if sanitized.count("<a") > sanitized.count("</a>"):
        last_open = sanitized.rfind("<a")
        last_close = sanitized.rfind("</a>")
        if last_open > last_close:
            sanitized = sanitized[:last_open].rstrip()
    return sanitized


def _log_wechat_route_stage_if_slow(
    stage: str,
    started_at: float,
    processing_key: str = "",
    account_name: str = "",
    msg_type: str = "",
    extra: str = "",
    stage_timings: Optional[List[Dict[str, Any]]] = None,
) -> None:
    elapsed = max(0.0, time.time() - started_at)
    if stage_timings is not None:
        stage_timings.append(
            {
                "stage": stage,
                "elapsed": round(elapsed, 3),
            }
        )
    if elapsed < WECHAT_ROUTE_SLOW_STEP_WARN_SECONDS:
        return
    details = [
        f"stage={stage}",
        f"elapsed={elapsed:.2f}s",
    ]
    if processing_key:
        details.append(f"key={processing_key}")
    if account_name:
        details.append(f"account={account_name}")
    if msg_type:
        details.append(f"msg_type={msg_type}")
    if extra:
        details.append(extra)
    logger.warning("微信消息慢步骤: %s", ", ".join(details))


def _log_wechat_route_summary_if_slow(
    started_at: float,
    stage_timings: List[Dict[str, Any]],
    processing_key: str = "",
    account_name: str = "",
    msg_type: str = "",
) -> None:
    if not stage_timings:
        return
    slow_items = [
        item for item in stage_timings
        if float(item.get("elapsed", 0.0)) >= WECHAT_ROUTE_SLOW_STEP_WARN_SECONDS
    ]
    if not slow_items:
        return

    total_elapsed = max(0.0, time.time() - started_at)
    top_items = sorted(
        stage_timings,
        key=lambda item: float(item.get("elapsed", 0.0)),
        reverse=True,
    )[:5]
    top_summary = ", ".join(
        f"{item.get('stage')}={float(item.get('elapsed', 0.0)):.2f}s"
        for item in top_items
    )
    details = [f"total={total_elapsed:.2f}s", f"top={top_summary}"]
    if processing_key:
        details.append(f"key={processing_key}")
    if account_name:
        details.append(f"account={account_name}")
    if msg_type:
        details.append(f"msg_type={msg_type}")
    logger.warning("微信消息整请求慢步骤摘要: %s", ", ".join(details))


def _emit_text_handler_timing_logs(
    msg: Dict[str, Any],
    processing_key: str = "",
    account_name: str = "",
    msg_type: str = "",
    stage_timings: Optional[List[Dict[str, Any]]] = None,
) -> None:
    text_timings = msg.get("_text_handler_timings", [])
    if not isinstance(text_timings, list):
        return
    for item in text_timings:
        if not isinstance(item, dict):
            continue
        stage = str(item.get("stage") or "").strip()
        elapsed = float(item.get("elapsed") or 0.0)
        if not stage:
            continue
        extra_parts = []
        processor = str(item.get("processor") or "").strip()
        if processor:
            extra_parts.append(f"processor={processor}")
        if "matched" in item:
            extra_parts.append(f"matched={bool(item.get('matched'))}")
        if "responded" in item:
            extra_parts.append(f"responded={bool(item.get('responded'))}")
        if "processor_count" in item:
            extra_parts.append(f"processor_count={int(item.get('processor_count') or 0)}")
        if stage_timings is not None:
            stage_timings.append(
                {
                    "stage": stage,
                    "elapsed": round(elapsed, 3),
                }
            )
        if elapsed >= WECHAT_TEXT_HANDLER_WARN_SECONDS:
            details = [
                f"stage={stage}",
                f"elapsed={elapsed:.2f}s",
            ]
            if processing_key:
                details.append(f"key={processing_key}")
            if account_name:
                details.append(f"account={account_name}")
            if msg_type:
                details.append(f"msg_type={msg_type}")
            if extra_parts:
                details.append(", ".join(extra_parts))
            logger.warning("微信消息慢步骤: %s", ", ".join(details))


async def _build_wechat_text_response(
    msg: Dict[str, Any],
    content: str,
    encrypt_type: Optional[str],
    msg_signature: Optional[str],
    nonce: Optional[str],
    timestamp: Optional[str],
    account_config: dict,
):
    import asyncio

    rsp = TextRspMsg(msg)
    shortened = await _shorten_wechat_passive_text(_sanitize_wechat_passive_text(content))
    rsp.content = await asyncio.to_thread(_truncate_wechat_passive_text, shortened)
    response_xml = rsp.dump_xml()
    if encrypt_type == "aes" and msg_signature:
        wxcpt = create_wxcpt_instance(account_config)
        response_str = response_xml.decode("utf-8") if isinstance(response_xml, bytes) else response_xml
        encrypted = await asyncio.get_event_loop().run_in_executor(
            None, wxcpt.encrypt_msg, response_str, nonce, timestamp
        )
        content_bytes = encrypted.encode("utf-8") if isinstance(encrypted, str) else encrypted
        return Response(content=content_bytes, media_type="application/xml; charset=utf-8")

    if isinstance(response_xml, bytes):
        return Response(content=response_xml, media_type="text/plain; charset=utf-8")
    return PlainTextResponse(response_xml)


async def _shorten_wechat_passive_text(content: Any) -> str:
    text = str(content or "")
    if "http://" not in text and "https://" not in text:
        return text
    config = get_shortlink_config()
    if not config.public_base_url:
        logger.warning("短链公开地址未配置，跳过被动回复短链转换")
        return text
    try:
        result = await transform_shortlinks_in_text_async(
            text,
            ttl_seconds=config.default_ttl_seconds,
            include_bare_urls=False,
            max_success_count=100,
        )
    except Exception as exc:
        logger.warning("被动回复短链转换失败: %s", exc, exc_info=True)
        return text
    matched_count = int(result.get("matched_count") or 0)
    success_count = int(result.get("success_count") or 0)
    failed_count = int(result.get("failed_count") or 0)
    if matched_count > 0:
        logger.warning(
            "被动回复短链转换完成: matched=%s success=%s failed=%s",
            matched_count,
            success_count,
            failed_count,
        )
    return str(result.get("text") or text)


                                                         

                           
_all_text_processors = [
    ("get_link", GetLinkProcessor(logger)),                            
    ("generate_link", GenerateLinkProcessor(logger)),                         
    ("get_tuangou", GetTuangouProcessor(logger)),                     
    ("get_meituan", GetMeituanProcessor(logger)),                     
    ("get_eleme", GetElemeProcessor(logger)),                         
    ("get_jd", GetJdProcessor(logger)),                                 
    ("leaderboard_config", LeaderboardConfigProcessor(logger)),         
    ("verification_code", ActivationCodeProcessor(logger)),              
    ("p_value", PValueProcessor(logger)),                          
    ("scene", SceneProcessor(logger)),                              
    ("merchant_coupon", MerchantCouponProcessor(logger)),          
    ("shortlink_generator", ShortlinkGeneratorProcessor(logger)),
    ("meituan_magical_coupon", MeituanMagicalCouponProcessor(logger)),
    ("meituan_order_query", MeituanOrderQueryProcessor(logger)),           
    ("meituan_shop_query", MeituanShopQueryProcessor(logger)),             
    ("meituan_miniprogram_link", MeituanMiniprogramLinkProcessor(logger)),              
    ("meituan_link", MeituanLinkProcessor(logger, pattern=("美团", ["http://dpurl.cn", "https://dpurl.cn"]))),
    ("cache_test", CacheTestProcessor(logger)),                              
    ("echo_user_id", EchoUserIdProcessor(logger)),          
    ("keyword_reply", KeywordReplyProcessor(logger, multi_match=True)),
]
                
_text_processor_map = {name: processor for name, processor in _all_text_processors}
text_processors = []
_account_text_processor_cache: Dict[str, List[Any]] = {}
_processor_class_name_map: Dict[str, Any] = {}

                
miniprogram_processors = {
    "wx2c348cf579062e56": MeituanProcessor(logger),
    "wxde8ac0a21135c07d": MeituanProcessor(logger)
}

       
message_handlers = {
    "text": TextHandler(logger, text_processors, processor_map=_text_processor_map),
    "image": ImageHandler(logger),
    "voice": VoiceHandler(logger),
    "video": VideoHandler(logger),
    "shortvideo": VideoHandler(logger),
    "location": LocationHandler(logger),
    "link": LinkHandler(logger),
    "event": EventHandler(logger),
    "miniprogrampage": MiniprogramHandler(logger, miniprogram_processors)
}


def _rebuild_text_processor_runtime_caches() -> None:
    from config.config import ACCOUNT_SPECIFIC_CONFIGS

    global _account_text_processor_cache, _processor_class_name_map

    _processor_class_name_map = {
        processor.__class__.__name__: processor
        for _, processor in _all_text_processors
    }

    cache: Dict[str, List[Any]] = {}
    wechat_accounts = get_wechat_accounts()
    all_known_accounts = set(wechat_accounts.keys()) | set(ACCOUNT_SPECIFIC_CONFIGS.keys())
    for to_user_name in all_known_accounts:
        account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
        enabled_processors = account_config.get("enabled_text_processors", [])
        if not enabled_processors:
            cache[to_user_name] = []
            continue
        enabled_set = set(enabled_processors)
        cache[to_user_name] = [
            processor
            for name, processor in _all_text_processors
            if name in enabled_set
        ]

    _account_text_processor_cache = cache
    text_handler = message_handlers.get("text")
    if isinstance(text_handler, TextHandler):
        text_handler.set_runtime_caches(_account_text_processor_cache, _processor_class_name_map)


_rebuild_text_processor_runtime_caches()


_PROCESSOR_LABELS: Dict[str, str] = {
    "get_link": "链接解析（提取链接并转换）",
    "generate_link": "链接生成（生成推广链接）",
    "get_tuangou": "团购优惠（获取团购信息）",
    "get_meituan": "美团红包（获取美团外卖红包）",
    "get_eleme": "饿了么红包（获取饿了么红包）",
    "get_jd": "京东优惠（获取京东优惠信息）",
    "leaderboard_config": "排行榜（订单排行榜功能）",
    "verification_code": "激活码（生成/管理激活码）",
    "p_value": "P值管理（设置推广 P 值）",
    "scene": "场景值管理（设置场景参数）",
    "merchant_coupon": "商家券（查询商家代金券）",
    "shortlink_generator": "短链生成（生成短链接）",
    "meituan_magical_coupon": "美团神券（美团神券推送）",
    "meituan_order_query": "订单查询（美团订单返佣查询）",
    "meituan_shop_query": "店铺查询（美团店铺优惠查询）",
    "meituan_miniprogram_link": "美团小程序链接（解析小程序链接）",
    "meituan_link": "美团链接识别（识别美团/点评链接）",
    "cache_test": "缓存测试（开发调试用）",
    "echo_user_id": "回显用户ID（开发调试用）",
    "keyword_reply": "关键词回复（自定义关键词自动回复）",
    "cashback_activity": "返现活动（返现活动推送）",
}


def get_available_text_processor_options() -> List[Dict[str, str]]:
    return [
        {
            "value": name,
            "label": _PROCESSOR_LABELS.get(name, f"{name} ({processor.__class__.__name__})"),
        }
        for name, processor in _all_text_processors
    ]


def reload_wechat_runtime_configs() -> None:
    from config.config import reload_config

    reload_config()
    for _, processor in _all_text_processors:
        if hasattr(processor, '_reload_configs'):
            try:
                processor._reload_configs()
            except Exception as e:
                logger.error("重新加载处理器 %s 配置失败: %s", processor.__class__.__name__, e)
    _rebuild_text_processor_runtime_caches()


def create_wxcpt_instance(account_config: dict) -> WXBizMsgCrypt:
    """
    根据账号配置创建加解密实例
    
    Args:
        account_config: 公众号配置字典
        
    Returns:
        WXBizMsgCrypt实例
    """
    return WXBizMsgCrypt(
        account_config.get("token", ""),
        account_config.get("encoding_aes_key", ""),
        account_config.get("appid", "")
    )


def _message_processing_key(msg_id: str = "", from_user: str = "", create_time: str = "") -> str:
    if msg_id:
        return f"msg:{msg_id}"
    return f"evt:{from_user}_{create_time}"


async def _build_wechat_response_from_raw(
    raw_response: bytes | str,
    encrypt_type: Optional[str],
    msg_signature: Optional[str],
    nonce: Optional[str],
    timestamp: Optional[str],
    account_config: dict,
):
    import asyncio

    if encrypt_type == "aes" and msg_signature:
        wxcpt = create_wxcpt_instance(account_config)
        response_str = raw_response.decode("utf-8") if isinstance(raw_response, bytes) else raw_response
        encrypted = await asyncio.get_event_loop().run_in_executor(
            None, wxcpt.encrypt_msg, response_str, nonce, timestamp
        )
        content = encrypted.encode("utf-8") if isinstance(encrypted, str) else encrypted
        return Response(content=content, media_type="application/xml; charset=utf-8")

    if isinstance(raw_response, bytes):
        return Response(content=raw_response, media_type="text/plain; charset=utf-8")
    return PlainTextResponse(raw_response)


def verify_request_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    """
    验证请求签名（明文模式）
    
    Args:
        token: 公众号的token
        signature: 请求中的签名
        timestamp: 时间戳
        nonce: 随机数
        
    Returns:
        验证是否通过
    """
    return verify_signature(token, signature, timestamp, nonce)


@router.get("/wechat")
@router.get("/wx_mp_svr")
async def verify_server(
    signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
    encrypt_type: Optional[str] = None,
    msg_signature: Optional[str] = None
):
    """
    服务器验证接口（GET）- 统一多公众号入口
    
    微信服务器在配置URL时会发送GET请求进行验证。
    由于GET请求没有消息体，无法获取ToUserName，这里使用默认配置进行验证。
    
    如果需要为不同公众号使用不同的token，可以：
    1. 为每个公众号配置不同的URL路径（如 /wx?account=1）
    2. 或者确保所有公众号使用相同的token
    """
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        wechat_accounts = get_wechat_accounts()
        default_wechat_config = get_default_wechat_config()
        
                    
        if not encrypt_type or encrypt_type != "aes":
                                         
            for account_id, account_config in wechat_accounts.items():
                verify_result = await loop.run_in_executor(
                    None,
                    verify_request_signature,
                    account_config["token"],
                    signature,
                    timestamp,
                    nonce
                )
                if verify_result:
                    logger.info("验证成功，匹配账号: %s", account_config.get('name', account_id))
                    return PlainTextResponse(echostr)
            
                             
            verify_result = await loop.run_in_executor(
                None,
                verify_request_signature,
                default_wechat_config.get("token", ""),
                signature,
                timestamp,
                nonce
            )
            if verify_result:
                logger.info("验证成功，使用默认配置")
                return PlainTextResponse(echostr)
            
            raise HTTPException(status_code=400, detail="签名验证失败")
        
                      
        else:
                                     
            for account_id, account_config in wechat_accounts.items():
                try:
                    wxcpt = create_wxcpt_instance(account_config)
                    echo_str = await loop.run_in_executor(
                        None,
                        wxcpt.verify_url,
                        msg_signature,
                        timestamp,
                        nonce,
                        echostr
                    )
                    logger.info("安全模式验证成功，匹配账号: %s", account_config.get('name', account_id))
                    return PlainTextResponse(echo_str)
                except Exception:
                    continue
            
                    
            try:
                wxcpt = create_wxcpt_instance(default_wechat_config)
                echo_str = await loop.run_in_executor(
                    None,
                    wxcpt.verify_url,
                    msg_signature,
                    timestamp,
                    nonce,
                    echostr
                )
                logger.info("安全模式验证成功，使用默认配置")
                return PlainTextResponse(echo_str)
            except Exception as e:
                logger.error("安全模式验证失败: %s", e)
                raise HTTPException(status_code=400, detail="签名验证失败")
                
    except HTTPException:
        raise
    except Exception as e:
        logger.error("服务器验证失败: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/wechat")
@router.post("/wx_mp_svr")
async def handle_wechat_message(
    request: Request,
    signature: Optional[str] = None,
    timestamp: Optional[str] = None,
    nonce: Optional[str] = None,
    openid: Optional[str] = None,
    encrypt_type: Optional[str] = None,
    msg_signature: Optional[str] = None
):
    """
    处理微信消息（POST）- 统一多公众号入口
    
    根据消息中的 ToUserName 字段自动识别公众号，并使用对应的配置处理。
    支持明文模式和安全模式（加密）。
    
    """
              
    request_cache = get_request_cache()
    inflight_store = get_inflight_request_store()
    route_stage_timings: List[Dict[str, Any]] = []
    route_account_name = ""
    route_msg_type = ""
    route_processing_key = ""
    
           
    stage_started_at = time.time()
    body = await request.body()
    if len(body) > WECHAT_MAX_BODY_BYTES:
        logger.warning("微信消息体过大: bytes=%d limit=%d", len(body), WECHAT_MAX_BODY_BYTES)
        raise HTTPException(status_code=413, detail="消息体过大")
    _log_wechat_route_stage_if_slow("request_body_read", stage_started_at, stage_timings=route_stage_timings)
    
            
    start_time = time.time()
    
    try:
        import asyncio
        import sys
        
        stage_started_at = time.time()
        xml_data = body.decode('utf-8')
        _log_wechat_route_stage_if_slow("body_decode", stage_started_at, stage_timings=route_stage_timings)
        
        if _is_debug_enabled():
            logger.debug("收到微信消息体: bytes=%d chars=%d", len(body), len(xml_data))
        
                                                    
                                      
        loop = asyncio.get_event_loop()
        stage_started_at = time.time()
        outer_msg = await loop.run_in_executor(None, extract_xml_fields, xml_data, ("ToUserName", "Encrypt"))
        _log_wechat_route_stage_if_slow("initial_xml_parse", stage_started_at, stage_timings=route_stage_timings)
        to_user_name = outer_msg.get("ToUserName", "")
        
        logger.info("目标公众号ToUserName: %s", to_user_name)
        
                                 
        account_config = get_account_config(to_user_name)
        default_wechat_config = get_default_wechat_config()
        
        if not account_config:
            logger.warning("未找到 ToUserName=%s 对应的配置，使用默认配置", to_user_name)
            account_config = default_wechat_config
        else:
            logger.info("使用账号配置: %s", account_config.get('name', to_user_name))
        route_account_name = account_config.get("name", to_user_name)
        
                                    
        if not encrypt_type or encrypt_type != "aes":
            if signature:
                stage_started_at = time.time()
                verify_result = await loop.run_in_executor(
                    None, 
                    verify_request_signature,
                    account_config.get("token", ""),
                    signature,
                    timestamp,
                    nonce
                )
                _log_wechat_route_stage_if_slow(
                    "signature_verify_plain",
                    stage_started_at,
                    account_name=account_config.get("name", to_user_name),
                    stage_timings=route_stage_timings,
                )
                if not verify_result:
                    logger.warning("签名验证失败")
                    return PlainTextResponse("success")
        
                                          
        if encrypt_type == "aes" and msg_signature:
            try:
                wxcpt = create_wxcpt_instance(account_config)
                stage_started_at = time.time()
                             
                xml_data = await loop.run_in_executor(
                    None,
                    wxcpt.decrypt_msg,
                    xml_data,
                    msg_signature,
                    timestamp,
                    nonce
                )
                _log_wechat_route_stage_if_slow(
                    "decrypt_xml",
                    stage_started_at,
                    account_name=account_config.get("name", to_user_name),
                    stage_timings=route_stage_timings,
                )
                if _is_debug_enabled():
                    logger.debug("微信消息解密完成: chars=%d", len(xml_data))
            except Exception as e:
                logger.error("消息解密失败: %s", e)
                return PlainTextResponse("success")
        
                   
        stage_started_at = time.time()
        msg = await loop.run_in_executor(None, parse_xml_message, xml_data)
        _log_wechat_route_stage_if_slow(
            "decrypted_xml_parse",
            stage_started_at,
            account_name=account_config.get("name", to_user_name),
            stage_timings=route_stage_timings,
        )
        msg_type = msg.get("MsgType", "")
        route_msg_type = msg_type
        msg_id = msg.get("MsgId", "")
        from_user = msg.get("FromUserName", "")
        create_time = msg.get("CreateTime", "")
        processing_key = _message_processing_key(msg_id, from_user, create_time)
        route_processing_key = processing_key
        
                                                   
                                                            
        stage_started_at = time.time()
        cached = await request_cache.get_by_msg_id_async(msg_id, from_user, create_time)
        _log_wechat_route_stage_if_slow(
            "request_cache_lookup",
            stage_started_at,
            processing_key=processing_key,
            account_name=account_config.get("name", to_user_name),
            msg_type=msg_type,
            stage_timings=route_stage_timings,
        )
        if cached:
            cached_raw, cached_duration = cached
            logger.info("命中 msg_id 缓存 (耗时: %.2f秒)", cached_duration)
            if msg_id:
                get_msg_id_dedup().mark_processed(msg_id)
            return await _build_wechat_response_from_raw(
                cached_raw, encrypt_type, msg_signature, nonce, timestamp, account_config
            )

        msg_id_dedup = get_msg_id_dedup()
        if msg_id and msg_id_dedup.is_duplicate(msg_id):
            stage_started_at = time.time()
            completed_entry = await inflight_store.get_completed(processing_key)
            _log_wechat_route_stage_if_slow(
                "completed_result_lookup",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            if completed_entry:
                if completed_entry.get("response") is not None:
                    logger.info("重复消息命中已完成结果 MsgId: %s", msg_id)
                    return await _build_wechat_response_from_raw(
                        completed_entry["response"], encrypt_type, msg_signature, nonce, timestamp, account_config
                    )
                completed_error = _format_error_message(completed_entry.get("error"))
                logger.warning("重复消息命中已完成失败结果: %s, error=%s", processing_key, completed_error)
                return await _build_wechat_text_response(
                    msg,
                    f"❌ {completed_error}",
                    encrypt_type,
                    msg_signature,
                    nonce,
                    timestamp,
                    account_config,
                )

        inflight_entry, is_owner = await inflight_store.get_or_create(processing_key)
        if not is_owner:
            logger.info("消息正在处理中，等待首次结果: %s", processing_key)
            stage_started_at = time.time()
            wait_timeout = (
                WECHAT_RESOURCE_EXHAUSTED_WAIT_SECONDS
                if _is_local_resource_pressure_high()
                else WECHAT_SYNC_WAIT_SECONDS
            )
            waited_entry = await inflight_store.wait_result(processing_key, wait_timeout)
            _log_wechat_route_stage_if_slow(
                "inflight_wait_result",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            if waited_entry and waited_entry.get("response") is not None:
                if msg_id:
                    msg_id_dedup.mark_processed(msg_id)
                return await _build_wechat_response_from_raw(
                    waited_entry["response"], encrypt_type, msg_signature, nonce, timestamp, account_config
                )
            if waited_entry and waited_entry.get("error"):
                waited_error = _format_error_message(waited_entry.get("error"))
                logger.warning("处理中已有失败结果但无响应文本: %s, error=%s", processing_key, waited_error)
                return await _build_wechat_text_response(
                    msg,
                    f"❌ {waited_error}",
                    encrypt_type,
                    msg_signature,
                    nonce,
                    timestamp,
                    account_config,
                )
            if _is_local_resource_pressure_high():
                logger.warning("检测到本地资源压力较高，提前结束重复请求等待: %s", processing_key)
            logger.warning("等待处理中结果超时且无结果: %s", processing_key)
            if encrypt_type == "aes" and msg_signature:
                wxcpt = create_wxcpt_instance(account_config)
                encrypted = await loop.run_in_executor(
                    None, wxcpt.encrypt_msg, "success", nonce, timestamp
                )
                content = encrypted.encode('utf-8') if isinstance(encrypted, str) else encrypted
                return Response(content=content, media_type="application/xml; charset=utf-8")
            return PlainTextResponse("success")
        
              
                      
        from config.config import ACCOUNT_SPECIFIC_CONFIGS

        message_account_config = merge_account_runtime_config(
            to_user_name,
            account_config,
            ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {}),
        )
        msg["_account_config"] = message_account_config
        msg["_account_name"] = message_account_config.get("name") or account_config.get("name", to_user_name)
        if msg_type == "text":
            enabled_processors = message_account_config.get("enabled_text_processors", [])
            logger.warning(
                "微信文本配置诊断: account=%s has_zmkey=%s meituan_miniprogram_link_enabled=%s processor_count=%d",
                msg["_account_name"],
                has_zmkey(msg, message_account_config),
                "meituan_miniprogram_link" in enabled_processors if isinstance(enabled_processors, list) else False,
                len(enabled_processors) if isinstance(enabled_processors, list) else 0,
            )
        
                    
        handler = message_handlers.get(msg_type)
        
                 
        rsp_msg = EmptyRspMsg()
        
        if handler:
            stage_started_at = time.time()
            response = await handler.ahandle(msg)
            handler_elapsed = max(0.0, time.time() - stage_started_at)
            route_stage_timings.append(
                {
                    "stage": "handler_ahandle",
                    "elapsed": round(handler_elapsed, 3),
                }
            )
            _emit_text_handler_timing_logs(
                msg,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            _log_wechat_route_stage_if_slow(
                "handler_ahandle",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
            )
            
                                    
            if isinstance(response, WxRspMsg):
                rsp_msg = response
                             
            elif isinstance(response, str):
                text_rsp = TextRspMsg(msg)
                text_rsp.content = response
                rsp_msg = text_rsp
                     
        else:
                    
            logger.warning("收到未知类型消息: %s", msg_type)
                                
        if isinstance(rsp_msg, TextRspMsg) and rsp_msg.content:
            sanitized = _sanitize_wechat_passive_text(rsp_msg.content)
            if sanitized != rsp_msg.content:
                logger.warning(
                    "DIAG 被动回复内容已清洗: key=%s account=%s before_len=%d after_len=%d",
                    processing_key,
                    account_config.get("name", to_user_name),
                    len(rsp_msg.content),
                    len(sanitized),
                )
            fallback_content = str(rsp_msg.shortlink_fallback_content or "").strip()
            fallback_reason = str(rsp_msg.shortlink_fallback_reason or "").strip()
            stage_started_at = time.time()
            shortened = await _shorten_wechat_passive_text(sanitized)
            shortened_diag = _build_wechat_text_diagnostics(msg, shortened)
            shortened_budget = _wechat_passive_text_budget_status(shortened, shortened_diag)
            shortened_within_budget = bool(shortened_budget["within_budget"])
            logger.warning(
                "被动回复预算诊断: key=%s account=%s content_len=%d content_bytes=%d content_bytes_limit=%d xml_len=%d xml_bytes=%d xml_bytes_limit=%d within_budget=%s char_within=%s content_bytes_within=%s xml_bytes_within=%s fallback_available=%s fallback_reason=%s",
                processing_key,
                account_config.get("name", to_user_name),
                shortened_diag["content_len"],
                shortened_diag["content_bytes"],
                shortened_budget["content_bytes_limit"],
                shortened_diag["xml_len"],
                shortened_diag["xml_bytes"],
                shortened_budget["xml_bytes_limit"],
                shortened_within_budget,
                shortened_budget["char_within_budget"],
                shortened_budget["content_bytes_within_budget"],
                shortened_budget["xml_bytes_within_budget"],
                bool(fallback_content),
                fallback_reason or "-",
            )
            selected_content = shortened
            selected_diag = shortened_diag
            if not shortened_within_budget and fallback_content:
                fallback_sanitized = _sanitize_wechat_passive_text(fallback_content)
                fallback_shortened = await _shorten_wechat_passive_text(fallback_sanitized)
                fallback_diag = _build_wechat_text_diagnostics(msg, fallback_shortened)
                fallback_budget = _wechat_passive_text_budget_status(fallback_shortened, fallback_diag)
                fallback_within_budget = bool(fallback_budget["within_budget"])
                logger.warning(
                    "被动回复已降级为精简版: key=%s account=%s reason=%s full_content_bytes=%d full_xml_bytes=%d fallback_content_bytes=%d fallback_xml_bytes=%d fallback_within_budget=%s fallback_char_within=%s fallback_content_bytes_within=%s fallback_xml_bytes_within=%s",
                    processing_key,
                    account_config.get("name", to_user_name),
                    fallback_reason or "text_reply_budget",
                    shortened_diag["content_bytes"],
                    shortened_diag["xml_bytes"],
                    fallback_diag["content_bytes"],
                    fallback_diag["xml_bytes"],
                    fallback_within_budget,
                    fallback_budget["char_within_budget"],
                    fallback_budget["content_bytes_within_budget"],
                    fallback_budget["xml_bytes_within_budget"],
                )
                selected_content = fallback_shortened
                selected_diag = fallback_diag
            elif shortened_within_budget:
                logger.warning(
                    "被动回复短链后未降级: key=%s account=%s content_bytes=%d xml_bytes=%d",
                    processing_key,
                    account_config.get("name", to_user_name),
                    shortened_diag["content_bytes"],
                    shortened_diag["xml_bytes"],
                )
            _log_wechat_route_stage_if_slow(
                "text_shortlink",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            stage_started_at = time.time()
            rsp_msg.content = await asyncio.to_thread(_truncate_wechat_passive_text, selected_content)
            if rsp_msg.content != selected_content:
                final_diag = _build_wechat_text_diagnostics(msg, rsp_msg.content)
                logger.warning(
                    "被动回复最终触发截断: key=%s account=%s before_content_bytes=%d before_xml_bytes=%d after_content_bytes=%d after_xml_bytes=%d",
                    processing_key,
                    account_config.get("name", to_user_name),
                    selected_diag["content_bytes"],
                    selected_diag["xml_bytes"],
                    final_diag["content_bytes"],
                    final_diag["xml_bytes"],
                )
            _log_wechat_route_stage_if_slow(
                "text_truncate",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
        
                 
        response_xml = rsp_msg.dump_xml()
        
        duration = time.time() - start_time

        # Diagnostic: log what we're about to return to WeChat
        _rsp_type = type(rsp_msg).__name__
        _rsp_content_len = len(rsp_msg.content) if hasattr(rsp_msg, 'content') and rsp_msg.content else 0
        _rsp_content_bytes = len(rsp_msg.content.encode("utf-8")) if hasattr(rsp_msg, 'content') and rsp_msg.content else 0
        _rsp_xml_len = len(response_xml) if isinstance(response_xml, (str, bytes)) else len(str(response_xml))
        _rsp_xml_bytes = len(response_xml.encode("utf-8")) if isinstance(response_xml, str) else _rsp_xml_len
        logger.warning(
            "微信回复诊断: type=%s content_len=%d content_bytes=%d xml_len=%d xml_bytes=%d duration=%.2fs key=%s account=%s encrypt=%s",
            _rsp_type, _rsp_content_len, _rsp_content_bytes, _rsp_xml_len, _rsp_xml_bytes, duration, processing_key,
            account_config.get("name", to_user_name),
            "aes" if (encrypt_type == "aes" and msg_signature) else "plain",
        )
        
                        
        if encrypt_type == "aes" and msg_signature:
                                      
            if isinstance(response_xml, bytes):
                response_str = response_xml.decode('utf-8')
            else:
                response_str = response_xml
            
                  
            wxcpt = create_wxcpt_instance(account_config)
            stage_started_at = time.time()
            encrypted_response = await loop.run_in_executor(
                None,
                wxcpt.encrypt_msg,
                response_str,
                nonce,
                timestamp
            )
            _log_wechat_route_stage_if_slow(
                "response_encrypt",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            
                          
            if isinstance(encrypted_response, str):
                encrypted_response_bytes = encrypted_response.encode('utf-8')
            else:
                encrypted_response_bytes = encrypted_response
            
                                          
            raw_for_cache = response_str.encode('utf-8') if isinstance(response_str, str) else response_str
            stage_started_at = time.time()
            await inflight_store.set_result(processing_key, raw_for_cache, duration)
            _log_wechat_route_stage_if_slow(
                "inflight_set_result",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            if msg_id:
                msg_id_dedup.mark_processed(msg_id)
            stage_started_at = time.time()
            await request_cache.set_by_msg_id_async(msg_id, raw_for_cache, duration, from_user, create_time)
            _log_wechat_route_stage_if_slow(
                "request_cache_set",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            return Response(content=encrypted_response, media_type="application/xml; charset=utf-8")
        else:
                      
            if isinstance(response_xml, bytes):
                response_bytes = response_xml
            else:
                response_bytes = response_xml.encode('utf-8')
                                     
            stage_started_at = time.time()
            await inflight_store.set_result(processing_key, response_bytes, duration)
            _log_wechat_route_stage_if_slow(
                "inflight_set_result",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            if msg_id:
                msg_id_dedup.mark_processed(msg_id)
            stage_started_at = time.time()
            await request_cache.set_by_msg_id_async(msg_id, response_bytes, duration, from_user, create_time)
            _log_wechat_route_stage_if_slow(
                "request_cache_set",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            if isinstance(response_xml, bytes):
                return Response(content=response_xml, media_type="text/plain; charset=utf-8")
            else:
                return PlainTextResponse(response_xml)
            
    except Exception as e:
        error_message = _format_error_message(e)
        logger.error("处理消息时发生错误: %s", error_message)
        import traceback
        logger.error(traceback.format_exc())
        try:
            await inflight_store.set_error(processing_key, error_message)
        except Exception:
            pass
        try:
            if "msg" in locals() and isinstance(msg, dict) and msg.get("FromUserName") and msg.get("ToUserName"):
                return await _build_wechat_text_response(
                    msg,
                    f"❌ {error_message}",
                    encrypt_type,
                    msg_signature,
                    nonce,
                    timestamp,
                    account_config,
                )
        except Exception:
            pass
        return PlainTextResponse("success")
    finally:
        _log_wechat_route_summary_if_slow(
            start_time,
            route_stage_timings,
            processing_key=route_processing_key,
            account_name=route_account_name,
            msg_type=route_msg_type,
        )


@router.get("/dianping", response_class=HTMLResponse)
async def dianping_page(request: Request):
    """
    返回大众点评跳转页面。
    前端会自动从 URL 查询参数中读取 ?i=... 的内容。
    """
    return templates.TemplateResponse(request, "dianping.html", {"request": request})


@router.get("/reload_config")
async def reload_config_endpoint(current_user: str = Depends(get_current_user)):
    """
    重新加载配置文件（需要登录）
    
    无需重启服务器即可加载新的配置
    包括：
    - config.toml（主配置）
    - wechat_accounts.runtime.json（账号级运行时配置）
    - system_settings.runtime.json（系统级运行时配置）
    - miniprogram/miniprogram_config.toml（默认小程序配置）
    - text_processors/keyword_responses.toml（默认关键词回复配置）
    - text_processors/click_event_responses.toml（默认菜单点击回复配置）
    - text_processors/meituan_link.toml（默认美团短链回复配置）
    - text_processors/meituan_miniprogram_link_processor.toml（默认小程序链接文本回复配置）
    - link_handlers/prompts.toml（默认提示语配置）
    - link_handlers/link_config.toml（默认链接识别配置）
    - text_processors/merchant_coupon_prompts.toml（默认商家券文案）
    - text_processors/order_leaderboard.toml（默认排行榜配置）
    """
    try:
        reload_wechat_runtime_configs()
        
        logger.info("所有配置重新加载成功，操作人: %s", current_user)
        return JSONResponse({
            "success": True,
            "message": "所有配置已重新加载（包括网页运行时配置和各 TOML 默认配置）",
            "operator": current_user,
            "reloaded": [
                "config.toml",
                "wechat_accounts.runtime.json",
                "system_settings.runtime.json",
                "miniprogram/miniprogram_config.toml",
                "text_processors/keyword_responses.toml",
                "text_processors/click_event_responses.toml",
                "text_processors/meituan_link.toml",
                "text_processors/meituan_miniprogram_link_processor.toml",
                "link_handlers/prompts.toml",
                "link_handlers/link_config.toml",
                "text_processors/merchant_coupon_prompts.toml",
                "text_processors/order_leaderboard.toml"
            ]
        })
    except Exception as e:
        logger.error("重新加载配置失败: %s", e)
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse({
            "success": False,
            "error": f"重新加载配置失败: {str(e)}"
        }, status_code=500)

@router.get("/check_meituan_order", response_class=HTMLResponse)
async def check_page(request: Request):
    return templates.TemplateResponse(request, "check.html", {"request": request})

                                                      
def _parse_nested_json_strings(obj: Any) -> Any:
    """
    递归解析嵌套的 JSON 字符串，将 string_data、ad_data 等字段中的 JSON 字符串解析成对象
    
    Args:
        obj: 要解析的对象（可以是 dict、list、str 等）
        
    Returns:
        解析后的对象
    """
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
                                                       
            if key in ["string_data", "ad_data"] and isinstance(value, str):
                try:
                                 
                    parsed = json.loads(value)
                                
                    result[key] = _parse_nested_json_strings(parsed)
                except (json.JSONDecodeError, TypeError):
                                 
                    result[key] = value
            else:
                          
                result[key] = _parse_nested_json_strings(value)
        return result
    elif isinstance(obj, list):
        return [_parse_nested_json_strings(item) for item in obj]
    elif isinstance(obj, str):
                                             
                            
        return obj
    else:
        return obj


class MeituanOrderQueryRequest(BaseModel):
    """美团订单查询请求"""
    token: str
    order_id: str


class MeituanLandingPageRequest(BaseModel):
    """美团落地页请求"""
    wm_latitude: str
    wm_longitude: str
    wm_actual_latitude: str
    wm_actual_longitude: str
    userId: str
    token: str
    keyword: Optional[str] = None             
    page_num: Optional[int] = 1              
    page_size: Optional[int] = 10                 
    sortType: Optional[int] = 0                
    filterInfo: Optional[str] = ""                   


class MeituanAllowanceQueryRequest(BaseModel):
    token: str
    user_id: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    resolved_address: Optional[Dict[str, Any]] = None


class MeituanAllowanceAddressRequest(BaseModel):
    token: str
    user_id: Optional[str] = None


class MeituanAllowanceExecutionError(Exception):
    def __init__(
        self,
        *,
        message: str,
        status_code: int,
        summary: Dict[str, Any],
        progress: List[Dict[str, Any]],
        merchants: List[Dict[str, Any]],
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.summary = summary
        self.progress = progress
        self.merchants = merchants


MEITUAN_ALLOWANCE_TERMINAL_STATUSES = {"succeeded", "failed", "interrupted"}
_meituan_allowance_background_tasks: Dict[str, asyncio.Task[Any]] = {}


def _extract_meituan_token(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""

    if "\n" in text or ";" in text or "=" in text:
        candidates = (
            r"(?:^|[\s;])wm_logintoken=([^;\s]+)",
            r"(?:^|[\s;])token=([^;\s]+)",
            r"(?:^|[\s;])oops=([^;\s]+)",
            r"(?:^|[\s;])wm_logintoken:\s*([^\s;]+)",
            r"(?:^|[\s;])token:\s*([^\s;]+)",
        )
        for pattern in candidates:
            matched = re.search(pattern, text, re.IGNORECASE)
            if matched and matched.group(1):
                return urllib.parse.unquote(matched.group(1).strip())
    return text


def _extract_meituan_user_id(raw_value: Any) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text

    try:
        parsed_url = urllib.parse.urlparse(text)
        if parsed_url.scheme and parsed_url.netloc:
            query = urllib.parse.parse_qs(parsed_url.query)
            for key in ("userId", "userid"):
                values = query.get(key) or []
                if values and str(values[0]).strip():
                    return str(values[0]).strip()
    except Exception:
        pass

    candidates = (
        r"(?:^|[\s;?&])userId=([^;&\s]+)",
        r"(?:^|[\s;?&])userid=([^;&\s]+)",
        r"(?:^|[\s;])userId:\s*([^\s;]+)",
        r"(?:^|[\s;])userid:\s*([^\s;]+)",
    )
    for pattern in candidates:
        matched = re.search(pattern, text, re.IGNORECASE)
        if matched and matched.group(1):
            return urllib.parse.unquote(matched.group(1).strip())
    return ""


def _normalize_meituan_coordinate(raw_value: Any, *, is_latitude: bool) -> str:
    text = str(raw_value or "").strip()
    if not text:
        raise ValueError("经纬度不能为空")

    max_abs_degrees = 90.0 if is_latitude else 180.0
    max_abs_scaled = int(max_abs_degrees * 1_000_000)

    try:
        if "." in text:
            numeric = float(text)
            if abs(numeric) > max_abs_degrees:
                raise ValueError("经纬度超出范围")
            scaled = int(round(numeric * 1_000_000))
        else:
            numeric_int = int(text)
            if abs(numeric_int) <= max_abs_degrees:
                scaled = int(round(float(numeric_int) * 1_000_000))
            else:
                scaled = numeric_int
    except ValueError as exc:
        raise ValueError("经纬度格式不正确") from exc

    if abs(scaled) > max_abs_scaled:
        raise ValueError("经纬度超出范围")
    return str(scaled)


def _parse_json_if_needed(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except (TypeError, json.JSONDecodeError):
            return value
    return value


def _to_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_amount_text(value: Any) -> str:
    numeric = _to_number(value)
    if numeric is None:
        return ""
    if abs(numeric - round(numeric)) < 1e-9:
        return str(int(round(numeric)))
    return f"{numeric:.2f}".rstrip("0").rstrip(".")


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _build_meituan_allowance_uuid(token: str) -> str:
    return "0" * 64


def _build_meituan_history_uuid(token: str, user_id: str) -> str:
    digest = hashlib.md5(f"{token}|{user_id}".encode("utf-8")).hexdigest()
    return f"{digest[:13]}-{digest[13:29]}-0-0-{digest[:13]}"


async def _request_meituan_history_addresses(
    *,
    token: str,
    user_id: str,
) -> Dict[str, Any]:
    request_uuid = _build_meituan_history_uuid(token, user_id)
    endpoint = "https://apimobile.meituan.com/citylist/history"
    params = {
        "utm_medium": "android",
        "utm_term": "undefined",
        "version_name": "undefined",
        "uuid": request_uuid,
        "sourceBuId": "mtpt",
        "sourcePageId": "mtminiapp_home",
        "sourcePageName": "美小平台地址首页",
        "userid": user_id,
        "token": token,
        "yodaReady": "wx",
        "csecappid": "wxde8ac0a21135c07d",
        "csecplatform": "3",
        "csecversionname": "10.21.4",
        "csecversion": "3.0.1",
    }
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "M-APPKEY": "wxmp_mt-weapp",
        "clientversion": "3.16.1",
        "uuid": request_uuid,
        "csecuuid": request_uuid,
        "xweb_xhr": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
            "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
            "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
            "UnifiedPCWindowsWechat(0xf2541022) XWEB/16467"
        ),
        "Referer": "https://servicewechat.com/wxde8ac0a21135c07d/1547/page-frame.html",
        "token": token,
    }
    if user_id:
        headers["csecuserid"] = user_id

    url = f"{endpoint}?{urllib.parse.urlencode(params)}"
    response = await requests.post(url, json={}, headers=headers, timeout=8)
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise RuntimeError("历史地址响应结构异常")
    return result


def _extract_meituan_history_addresses(result: Dict[str, Any]) -> list[Dict[str, Any]]:
    data = result.get("data", {})
    if not isinstance(data, dict):
        raise ValueError("历史地址返回为空")

    candidates: list[Dict[str, Any]] = []
    for source_key, card, fallback_name in (
        ("common", data.get("commonAddressesCard"), "常用地点"),
        ("history", data.get("historyAddressesCard"), "历史地点"),
    ):
        if not isinstance(card, dict):
            continue
        card_name = _safe_text(card.get("addressesCardName")) or fallback_name
        items = card.get("addressesCardData")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            longitude = item.get("longitude")
            latitude = item.get("latitude")
            if longitude in (None, "") or latitude in (None, ""):
                continue
            try:
                timestamp = int(item.get("timestamp") or 0)
            except (TypeError, ValueError):
                timestamp = 0
            candidates.append(
                {
                    "source": source_key,
                    "card_name": card_name,
                    "timestamp": timestamp,
                    "longitude": str(longitude).strip(),
                    "latitude": str(latitude).strip(),
                    "address": _safe_text(item.get("address")),
                    "house_number": _safe_text(item.get("houseNumber")),
                    "receiver": _safe_text(item.get("receiver")) or _safe_text(item.get("name")),
                    "address_id": _safe_text(item.get("id")),
                }
            )

    candidates.sort(key=lambda item: item.get("timestamp") or 0, reverse=True)
    return candidates


def _build_meituan_history_address_option(selected: Dict[str, Any]) -> Dict[str, Any]:
    latitude_raw = str(selected.get("latitude") or "").strip()
    longitude_raw = str(selected.get("longitude") or "").strip()
    latitude = _normalize_meituan_coordinate(latitude_raw, is_latitude=True)
    longitude = _normalize_meituan_coordinate(longitude_raw, is_latitude=False)
    resolved_parts = [
        str(selected.get("address") or "").strip(),
        str(selected.get("house_number") or "").strip(),
    ]
    return {
        "input_latitude": latitude_raw,
        "input_longitude": longitude_raw,
        "normalized_latitude": latitude,
        "normalized_longitude": longitude,
        "resolved_address": {
            "source": str(selected.get("source") or ""),
            "card_name": str(selected.get("card_name") or ""),
            "receiver": str(selected.get("receiver") or ""),
            "address": str(selected.get("address") or ""),
            "house_number": str(selected.get("house_number") or ""),
            "display_text": " ".join(part for part in resolved_parts if part).strip(),
            "timestamp": int(selected.get("timestamp") or 0),
            "latitude": latitude_raw,
            "longitude": longitude_raw,
            "address_id": str(selected.get("address_id") or ""),
        },
    }


def _pick_meituan_history_address(result: Dict[str, Any]) -> Dict[str, Any]:
    candidates = _extract_meituan_history_addresses(result)
    if not candidates:
        raise ValueError("未获取到可用的历史地址")
    return _build_meituan_history_address_option(candidates[0])


def _normalize_meituan_resolved_address_payload(raw_value: Any) -> Dict[str, Any]:
    if not isinstance(raw_value, dict):
        raise ValueError("地址数据格式不正确")

    latitude_raw = str(raw_value.get("latitude") or "").strip()
    longitude_raw = str(raw_value.get("longitude") or "").strip()
    if not latitude_raw or not longitude_raw:
        raise ValueError("所选地址缺少经纬度")

    latitude = _normalize_meituan_coordinate(latitude_raw, is_latitude=True)
    longitude = _normalize_meituan_coordinate(longitude_raw, is_latitude=False)

    resolved_address = {
        "source": _safe_text(raw_value.get("source")),
        "card_name": _safe_text(raw_value.get("card_name")),
        "receiver": _safe_text(raw_value.get("receiver")),
        "address": _safe_text(raw_value.get("address")),
        "house_number": _safe_text(raw_value.get("house_number")),
        "display_text": _safe_text(raw_value.get("display_text")),
        "timestamp": int(raw_value.get("timestamp") or 0),
        "latitude": latitude_raw,
        "longitude": longitude_raw,
        "address_id": _safe_text(raw_value.get("address_id")),
    }
    return {
        "input_latitude": latitude_raw,
        "input_longitude": longitude_raw,
        "normalized_latitude": latitude,
        "normalized_longitude": longitude,
        "resolved_address": resolved_address,
    }


async def _load_meituan_history_address_options(
    *,
    token: str,
    user_id: str,
) -> list[Dict[str, Any]]:
    history_result = await _request_meituan_history_addresses(token=token, user_id=user_id)
    history_code = int(history_result.get("code") or 0)
    if history_code != 0:
        history_message = _safe_text(history_result.get("msg")) or "获取历史地址失败"
        raise ValueError(history_message)
    candidates = _extract_meituan_history_addresses(history_result)
    if not candidates:
        raise ValueError("未获取到可用的历史地址")
    return [_build_meituan_history_address_option(item) for item in candidates]


def _build_meituan_allowance_request_payload(
    *,
    token: str,
    longitude: str,
    latitude: str,
    page_num: int,
    page_size: int,
    wm_context: str = "",
) -> tuple[Dict[str, str], Dict[str, str]]:
    device_uuid = _build_meituan_allowance_uuid(token)
    params = {
        "wm_ctype": "mtiphone",
        "wm_dversion": "16.5.1",
        "content_personalized_switch": "0",
        "wm_dtype": "iPhone 14",
        "mt_back_rci": "120100",
        "wmUserIdDeregistration": "-1",
        "future": "2",
        "ad_allowance_entry_channel": "2",
        "entry": "tuansousuo",
        "personalized": "1",
        "partner": "4",
        "modelcode": "jintie",
        "app_model": "0",
        "platform": "5",
        "notitlebar": "1",
        "ad_personalized_switch": "0",
        "wm_appversion": "12.58.401",
        "utm_campaign": "AgroupBgroupG",
        "app": "0",
        "wmUuidDeregistration": "-1",
        "uuid": device_uuid,
        "utm_term": "12.58.401",
        "utm_source": "AppStore",
        "utm_content": device_uuid,
        "version_name": "12.58.401",
        "utm_medium": "iphone",
        "language": "zh-CN",
        "regionid": "",
        "f": "iphone",
        "ci": "40",
        "msid": "",
        "wm_longitude": longitude,
        "wm_latitude": latitude,
        "wm_actual_longitude": longitude,
        "wm_actual_latitude": latitude,
        "mt_selected_longitude": longitude,
        "mt_selected_latitude": latitude,
        "entry_channel": "2",
        "page_num": str(page_num),
        "page_size": str(page_size),
        "filterInfo": "",
        "sortType": "0",
        "clicked_poi_str": "",
        "clicked_poi_channel": "",
        "wm_context": wm_context,
        "ad_page_type": "0",
        "biz": "newScene",
        "slotId": "91196",
    }

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://adfec.meituan.com",
        "Referer": "https://adfec.meituan.com/",
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5_1 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
            "TitansX/20.0.1.old KNB/1.0 iOS/16.5.1 "
            "meituangroup/com.meituan.imeituan/12.58.401 "
            "meituangroup/12.58.401 App/10110/12.58.401 iPhone/iPhone14 WKWebView"
        ),
        "wm_logintoken": token,
        "token": token,
        "userToken": "",
    }
    return params, headers


def _extract_allowance_amount_from_activity(activity: Dict[str, Any]) -> str:
    for key in ("amount", "reduceFree", "reduceFee", "shippingFeeReduce"):
        amount_text = _format_amount_text(activity.get(key))
        if amount_text:
            return amount_text
    return ""


def _extract_allowance_sku_items(ad_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    sku_items: list[Dict[str, Any]] = []
    seen_keys: set[str] = set()
    for sku in ad_data.get("sku_list", []) or []:
        if not isinstance(sku, dict):
            continue
        benefit_display_info = _parse_json_if_needed(sku.get("benefit_display_info"))
        if not isinstance(benefit_display_info, dict):
            continue
        benefits = benefit_display_info.get("activityBenefits") or []
        if not isinstance(benefits, list):
            continue

        allowance_amount = ""
        for benefit in benefits:
            if not isinstance(benefit, dict):
                continue
            if int(benefit.get("actId") or 0) != 364:
                continue
            allowance_amount = _extract_allowance_amount_from_activity(benefit)
            if allowance_amount:
                break

        if not allowance_amount:
            continue

        sku_id = _safe_text(sku.get("sku_id") or sku.get("skuId"))
        sku_name = _safe_text(sku.get("name"))
        dedupe_key = f"{sku_id}|{sku_name}|{allowance_amount}"
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        sku_items.append({
            "sku_id": sku_id,
            "name": sku_name or "未命名商品",
            "allowance_amount": allowance_amount,
            "price_text": _format_amount_text(sku.get("price")),
            "origin_price_text": _format_amount_text(sku.get("origin_price")),
            "month_sales_text": _safe_text(sku.get("month_sales_tip")),
        })
    return sku_items


def _extract_allowance_activities(ad_data: Dict[str, Any]) -> list[Dict[str, Any]]:
    results: list[Dict[str, Any]] = []
    for activity in ad_data.get("discountActivities", []) or []:
        if not isinstance(activity, dict):
            continue
        activity_type = int(activity.get("type") or 0)
        if activity_type != 364:
            continue
        results.append({
            "type": activity_type,
            "name": _safe_text(activity.get("name")) or "津贴优惠",
            "amount": _extract_allowance_amount_from_activity(activity),
        })
    return results


def _build_allowance_merchant_id(ad_data: Dict[str, Any]) -> str:
    primary = _safe_text(
        ad_data.get("wm_poi_id_str")
        or ad_data.get("poi_id_str")
        or ad_data.get("poiIdStr")
    )
    if primary:
        return primary
    fallback_parts = [
        _safe_text(ad_data.get("poi_name")),
        _safe_text(ad_data.get("distance")),
        _safe_text(ad_data.get("scheme")),
    ]
    return "|".join(part for part in fallback_parts if part) or "unknown_merchant"


def _get_default_allowance_coupon_account_id() -> str:
    try:
        store_data = load_wechat_account_store()
        default_account_id = _safe_text(store_data.get("default_account_id"))
        if default_account_id:
            return default_account_id
    except Exception:
        logger.warning("读取默认公众号配置失败", exc_info=True)

    try:
        accounts = get_wechat_accounts()
        if isinstance(accounts, dict):
            for account_id in accounts.keys():
                normalized = _safe_text(account_id)
                if normalized:
                    return normalized
    except Exception:
        logger.warning("读取公众号列表失败", exc_info=True)
    return ""


def _extract_allowance_merchants_from_result(parsed_result: Dict[str, Any]) -> list[Dict[str, Any]]:
    data = parsed_result.get("data", {})
    module_list = data.get("module_list", [])
    if not isinstance(module_list, list):
        return []

    default_coupon_account_id = _get_default_allowance_coupon_account_id()
    merchants: list[Dict[str, Any]] = []
    for module in module_list:
        if not isinstance(module, dict):
            continue
        template_id = _safe_text(module.get("template_id"))
        if template_id not in {"module_allowance_list", "module_poi_list", "module_list"}:
            continue

        string_data = _parse_json_if_needed(module.get("string_data"))
        if not isinstance(string_data, dict):
            continue
        ad_data = _parse_json_if_needed(string_data.get("ad_data"))
        if not isinstance(ad_data, dict):
            continue

        activities = _extract_allowance_activities(ad_data)
        if not activities:
            continue

        sku_allowance_items = _extract_allowance_sku_items(ad_data)
        allowance_amount = ""
        for item in activities:
            if item.get("amount"):
                allowance_amount = str(item["amount"])
                break

        poi_id_str = _safe_text(
            ad_data.get("wm_poi_id_str")
            or ad_data.get("poi_id_str")
            or ad_data.get("poiIdStr")
        )
        merchant_coupon_url_v8 = ""
        merchant_coupon_url_v6 = ""
        if default_coupon_account_id and poi_id_str:
            merchant_coupon_url_v8 = build_meituan_coupon_url(
                default_coupon_account_id,
                poi_id_str,
                logger,
                variant="v8",
            )
            merchant_coupon_url_v6 = build_meituan_coupon_url(
                default_coupon_account_id,
                poi_id_str,
                logger,
                variant="v5",
            )

        merchants.append({
            "poi_id": _build_allowance_merchant_id(ad_data),
            "poi_id_str": poi_id_str,
            "poi_name": _safe_text(ad_data.get("poi_name")) or "未知商家",
            "distance_text": _safe_text(ad_data.get("distance")),
            "delivery_time_text": _safe_text(ad_data.get("delivery_time_tip")),
            "min_price_text": _safe_text(ad_data.get("min_price_tip")),
            "shipping_fee_text": _safe_text(ad_data.get("shipping_fee_tip")),
            "score": ad_data.get("wm_poi_score"),
            "month_sales_text": _safe_text(ad_data.get("month_sales_tip")),
            "allowance_amount": allowance_amount,
            "activities": activities,
            "sku_allowance_items": sku_allowance_items,
            "sku_allowance_note": "" if sku_allowance_items else "未返回商品级津贴明细",
            "meituan_app_url": _safe_text(ad_data.get("scheme")),
            "merchant_coupon_url_v8": merchant_coupon_url_v8,
            "merchant_coupon_url_v6": merchant_coupon_url_v6,
            "coupon_account_id": default_coupon_account_id,
        })
    return merchants


async def _request_meituan_allowance_page(
    *,
    token: str,
    longitude: str,
    latitude: str,
    page_num: int,
    page_size: int,
    wm_context: str = "",
) -> Dict[str, Any]:
    params, headers = _build_meituan_allowance_request_payload(
        token=token,
        longitude=longitude,
        latitude=latitude,
        page_num=page_num,
        page_size=page_size,
        wm_context=wm_context,
    )

    if MEITUAN_ALLOWANCE_RELAY_URL:
        relay_headers = {"Content-Type": "application/json"}
        if MEITUAN_ALLOWANCE_RELAY_SECRET:
            relay_headers["X-Allowance-Relay-Secret"] = MEITUAN_ALLOWANCE_RELAY_SECRET
        relay_response = await requests.post(
            MEITUAN_ALLOWANCE_RELAY_URL,
            json={
                "endpoint": MEITUAN_ALLOWANCE_ENDPOINT,
                "params": params,
                "headers": headers,
            },
            headers=relay_headers,
            timeout=15,
        )
        relay_response.raise_for_status()
        result = relay_response.json()
        if not isinstance(result, dict):
            raise RuntimeError("津贴中转响应结构异常")
        return result

    from utils.proxy_utils import report_proxy_failure_async, report_proxy_success_async

    result = None
    last_retryable_error: Exception | None = None
    for attempt in range(1, max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS) + 1):
        try:
            proxies = await require_proxy_config_async()
        except ProxyUnavailableError:
            raise RuntimeError("网络繁忙，请稍后重试")

        proxy_url = str(proxies.get("http") or proxies.get("https") or "").strip()
        try:
            response = await requests.post(
                MEITUAN_ALLOWANCE_ENDPOINT,
                data=urllib.parse.urlencode(params),
                headers=headers,
                proxies=proxies,
                timeout=8,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise RuntimeError("美团津贴响应结构异常")
            if proxy_url:
                await report_proxy_success_async(proxy_url)
            break
        except Exception as exc:
            if proxy_url:
                await report_proxy_failure_async(proxy_url, exc)
            if not _should_retry_proxy_request(exc) or attempt >= max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS):
                raise
            last_retryable_error = exc
            logger.warning(
                "美团津贴请求失败，准备重试: page_num=%s attempt=%d/%d error=%s",
                page_num,
                attempt,
                max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS),
                _format_error_message(exc),
            )
            await asyncio.sleep(MEITUAN_PROXY_RETRY_DELAY_SECONDS)

    if not isinstance(result, dict):
        if last_retryable_error is not None:
            raise last_retryable_error
        raise RuntimeError("美团津贴响应结构异常")
    return result


@router.post("/api/meituan/query_order")
async def query_meituan_order(request_data: MeituanOrderQueryRequest):
    """
    美团订单查询代理接口（解决跨域问题）
    
    请求体:
        {
            "token": "从cookie中获取的oops值",
            "order_id": "订单ID"
        }
    
    返回:
        {
            "success": true/false,
            "data": {
                "orderId": "...",
                "accepted": true/false,
                "acceptTime": 时间戳或null,
                "poi_name": "商家名称"
            },
            "error": "错误信息（如果失败）"
        }
    """
    try:
        token = request_data.token.strip()
        order_id = request_data.order_id.strip()
        
        if not token:
            return JSONResponse({
                "success": False,
                "error": "Token不能为空"
            }, status_code=400)
        
        if not order_id or not order_id.isdigit() or len(order_id) < 15 or len(order_id) > 22:
            return JSONResponse({
                "success": False,
                "error": "订单ID格式不正确，应为15-22位数字"
            }, status_code=400)
        
                
        params = {
            "order_view_id": order_id,
            "wm_logintoken": token,
            "wm_ctype": "wxapp",
            "req_time": str(int(time.time() * 1000)),
            "wm_latitude": "34832076",
            "wm_longitude": "113516251"
        }
        
        from utils.proxy_utils import report_proxy_failure_async, report_proxy_success_async

        url = "https://wx.waimai.meituan.com/weapp/v2/order/historystatus"
        result = None
        last_retryable_error: Exception | None = None
        for attempt in range(1, max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS) + 1):
            try:
                proxies = await require_proxy_config_async()
            except ProxyUnavailableError:
                return JSONResponse({
                    "success": False,
                    "error": "网络繁忙，请稍后重试"
                }, status_code=503)

            proxy_url = str(proxies.get("http") or proxies.get("https") or "").strip()
            try:
                response = await requests.post(
                    url,
                    data=urllib.parse.urlencode(params),
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded"
                    },
                    proxies=proxies,
                    timeout=5,
                )
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise RuntimeError("美团订单查询响应结构异常")
                if proxy_url:
                    await report_proxy_success_async(proxy_url)
                break
            except Exception as exc:
                if proxy_url:
                    await report_proxy_failure_async(proxy_url, exc)
                if not _should_retry_proxy_request(exc) or attempt >= max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS):
                    raise
                last_retryable_error = exc
                logger.warning(
                    "美团订单查询请求失败，准备重试: order_id=%s attempt=%d/%d error=%s",
                    order_id,
                    attempt,
                    max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS),
                    _format_error_message(exc),
                )
                await asyncio.sleep(MEITUAN_PROXY_RETRY_DELAY_SECONDS)

        if not isinstance(result, dict):
            if last_retryable_error is not None:
                raise last_retryable_error
            raise RuntimeError("美团订单查询响应结构异常")
        
                   
        if _is_info_enabled():
            logger.info("美团订单查询响应 - 订单ID: %s, 响应内容: %s", order_id, result)
        
              
        if result.get("code") == 50001:
            return JSONResponse({
                "success": False,
                "error": "认证失败，请检查Token是否正确"
            }, status_code=401)
        
        if result.get("code") != 0:
            return JSONResponse({
                "success": False,
                "error": result.get("msg", "查询失败")
            }, status_code=400)
        
                               
        status_list = result.get("data", {}).get("status_list", [])
        accepted_status = next((s for s in status_list if s.get("status") == 3), None)
        
        return JSONResponse({
            "success": True,
            "data": {
                "orderId": order_id,
                "accepted": accepted_status is not None,
                "acceptTime": accepted_status.get("status_time") if accepted_status else None,
                "poi_name": "未知商家"                
            }
        })
        
    except requests.Timeout:
        return JSONResponse({
            "success": False,
            "error": "请求超时，请稍后重试"
        }, status_code=504)
    except requests.RequestException as e:
        logger.error("美团订单查询请求失败: %s", e)
        return JSONResponse({
            "success": False,
            "error": f"网络错误: {str(e)}"
        }, status_code=500)
    except Exception as e:
        logger.error("美团订单查询异常: %s", e)
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse({
            "success": False,
            "error": f"服务器错误: {str(e)}"
        }, status_code=500)


@router.post("/api/meituan/landing_page")
async def meituan_landing_page(request_data: MeituanLandingPageRequest):
    """
    美团落地页API代理接口
    
    请求体:
        {
            "wm_latitude": "纬度",
            "wm_longitude": "经度",
            "wm_actual_latitude": "实际纬度",
            "wm_actual_longitude": "实际经度",
            "userId": "用户ID",
            "token": "认证token",
            "keyword": "搜索关键词（可选）",
            "page_num": 1,
            "page_size": 10,
            "sortType": 0,
            "filterInfo": ""
        }
    
    返回:
        美团API的原始响应
    """
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        
              
        if not request_data.token.strip():
            return JSONResponse({
                "success": False,
                "error": "Token不能为空"
            }, status_code=400)
        
        if not request_data.userId.strip():
            return JSONResponse({
                "success": False,
                "error": "UserId不能为空"
            }, status_code=400)
        
        token = request_data.token.strip()
        userId = request_data.userId.strip()
        
                   
        keyword = request_data.keyword.strip() if request_data.keyword else ""
        page_num = request_data.page_num if request_data.page_num is not None else 1
        page_size = request_data.page_size if request_data.page_size is not None else 10
        sortType = request_data.sortType if request_data.sortType is not None else 0
        filterInfo = request_data.filterInfo if request_data.filterInfo is not None else ""
        
                  
        cookie = f"mt_c_token={token}; userId={userId}; oops={token}"
        
                       
        params = {
            "page_num": str(page_num),
            "notitleba": "1",
            "app_model": "0",
            "platform": "5",
            "address": "",            
            "partner": "4",
            "version": "12.45.402",
            "wm_dversion": "26.0.1",
            "content_personalized_switch": "0",
            "mt_back_rci": "",
            "wm_visitid": "",
            "app": "0",
            "wmUserIdDeregistration": "0",
            "region_version": "1763486325573",
            "future": "2",
            "region_id": "1000100000",
            "wm_appversion": "12.45.402",
            "wm_ctype": "mtiphone",
            "scene_id": "344",
            "wm_logintoken": token,
            "wm_dtype": "iPhone4S",
            "wm_did": "",
            "poilist_wm_cityid": "",
            "wmUuidDeregistration": "0",
            "poilist_mt_cityid": "",
            "wm_uuid": "",
            "entry": "tuansousuo",
            "personalized": "1",
            "ad_personalized_switch": "0",
            "utm_campaign": "AgroupBgroupG",
            "userid": userId,
            "uuid": "",
            "utm_term": "12.45.402",
            "utm_source": "AppStore",
            "utm_content": "",
            "version_name": "12.45.402",
            "utm_medium": "iphone",
            "token": token,
            "language": "zh-CN",
            "regionid": "",
            "f": "iphone",
            "ci": "",
            "msid": "",
            "wm_longitude": request_data.wm_longitude,
            "wm_latitude": request_data.wm_latitude,
            "wm_actual_longitude": request_data.wm_actual_longitude,
            "wm_actual_latitude": request_data.wm_actual_latitude,
            "entry_channel": "2",
            "page_size": str(page_size),
            "filterInfo": filterInfo,
            "sortType": str(sortType),
            "clicked_poi_str": "",
            "clicked_poi_channel": "",
            "wm_context": "",        
            "ad_page_type": "0"
        }
        
        if keyword:
            params["keyword"] = keyword
        
        url = "https://adapi.waimai.meituan.com/api/ad/landingPage"
        
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": cookie
        }
        headers.update({
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 TitansX/20.0.1.old KNB/1.0 iOS/26.1 meituangroup/com.meituan.imeituan/12.46.402 meituangroup/12.46.402 App/10110/12.46.402 iPhone/iPhone17Pro WKWebView",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Origin": "https://h5.waimai.meituan.com",
            "Referer": "https://h5.waimai.meituan.com/"
        })
        
        from utils.proxy_utils import report_proxy_failure_async, report_proxy_success_async
        result = None
        last_retryable_error: Exception | None = None
        for attempt in range(1, max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS) + 1):
            try:
                proxies = await require_proxy_config_async()
            except ProxyUnavailableError:
                return JSONResponse({
                    "success": False,
                    "error": "网络繁忙，请稍后重试"
                }, status_code=503)

            proxy_url = str(proxies.get("http") or proxies.get("https") or "").strip()
            try:
                response = await requests.post(
                    url,
                    data=urllib.parse.urlencode(params),
                    headers=headers,
                    proxies=proxies,
                    timeout=5,
                )
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise RuntimeError("美团落地页响应结构异常")
                if proxy_url:
                    await report_proxy_success_async(proxy_url)
                break
            except Exception as exc:
                if proxy_url:
                    await report_proxy_failure_async(proxy_url, exc)
                if not _should_retry_proxy_request(exc) or attempt >= max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS):
                    raise
                last_retryable_error = exc
                logger.warning(
                    "美团落地页请求失败，准备重试: user_id=%s attempt=%d/%d error=%s",
                    userId,
                    attempt,
                    max(1, MEITUAN_PROXY_REQUEST_MAX_ATTEMPTS),
                    _format_error_message(exc),
                )
                await asyncio.sleep(MEITUAN_PROXY_RETRY_DELAY_SECONDS)

        if not isinstance(result, dict):
            if last_retryable_error is not None:
                raise last_retryable_error
            raise RuntimeError("美团落地页响应结构异常")
        if _is_info_enabled():
            logger.info("美团落地页API响应 - UserId: %s, 响应内容: %s", userId, result)
        
        try:
            parsed_result = _parse_nested_json_strings(result)
        except Exception as exc:
            raise
        
        return JSONResponse({
            "success": True,
            "data": parsed_result
        })
        
    except requests.Timeout:
        return JSONResponse({
            "success": False,
            "error": "请求超时，请稍后重试"
        }, status_code=504)
    except requests.RequestException as e:
        logger.error("美团落地页API请求失败: %s", e)
        return JSONResponse({
            "success": False,
            "error": f"网络错误: {str(e)}"
        }, status_code=500)
    except Exception as e:
        logger.error("美团落地页API异常: %s", e)
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse({
            "success": False,
            "error": f"服务器错误: {str(e)}"
        }, status_code=500)


def _mask_meituan_token(token: str) -> str:
    token = str(token or "").strip()
    if not token:
        return ""
    if len(token) <= 8:
        return token[:2] + "***" + token[-2:]
    return token[:4] + "*" * min(8, max(4, len(token) - 8)) + token[-4:]


def _fingerprint_meituan_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _build_meituan_allowance_result_url(task_id: str) -> str:
    return f"/meituan-allowance/result/{urllib.parse.quote(str(task_id or '').strip())}"


def _build_meituan_allowance_summary(
    *,
    started_at: float,
    input_latitude: str,
    input_longitude: str,
    normalized_latitude: str,
    normalized_longitude: str,
    resolved_address: Optional[Dict[str, Any]] = None,
    pages_requested: int,
    merchant_count: int,
    stop_reason: str,
    consecutive_empty_pages: int,
    finished_at: float | None = None,
) -> Dict[str, Any]:
    ended_at = finished_at if finished_at is not None else time.time()
    summary = {
        "pages_requested": int(pages_requested),
        "merchant_count": int(merchant_count),
        "stop_reason": str(stop_reason or ""),
        "consecutive_empty_pages": int(consecutive_empty_pages),
        "duration_seconds": round(max(0.0, ended_at - started_at), 3),
        "page_size": MEITUAN_ALLOWANCE_PAGE_SIZE,
        "request_delay_ms": int(MEITUAN_ALLOWANCE_REQUEST_DELAY_SECONDS * 1000),
        "empty_page_stop_threshold": MEITUAN_ALLOWANCE_EMPTY_STOP_THRESHOLD,
        "max_pages": MEITUAN_ALLOWANCE_MAX_PAGES,
        "started_at": int(started_at),
        "finished_at": int(ended_at) if finished_at is not None else None,
        "input_coordinates": {
            "latitude": input_latitude,
            "longitude": input_longitude,
        },
        "normalized_coordinates": {
            "latitude": normalized_latitude,
            "longitude": normalized_longitude,
        },
    }
    if isinstance(resolved_address, dict) and resolved_address:
        summary["resolved_address"] = resolved_address
    return summary


async def _prepare_meituan_allowance_inputs(
    request_data: MeituanAllowanceQueryRequest,
) -> tuple[str, str, str, str, str, Dict[str, Any]]:
    raw_token_value = str(request_data.token or "").strip()
    token = _extract_meituan_token(raw_token_value)
    user_id = _extract_meituan_user_id(request_data.user_id) or _extract_meituan_user_id(raw_token_value)
    latitude_raw = str(request_data.latitude or "").strip()
    longitude_raw = str(request_data.longitude or "").strip()
    resolved_address_payload = request_data.resolved_address

    if not token:
        raise ValueError("Token不能为空")
    if isinstance(resolved_address_payload, dict) and resolved_address_payload:
        selected_address = _normalize_meituan_resolved_address_payload(resolved_address_payload)
        return (
            token,
            selected_address["input_latitude"],
            selected_address["input_longitude"],
            selected_address["normalized_latitude"],
            selected_address["normalized_longitude"],
            dict(selected_address.get("resolved_address") or {}),
        )
    if not user_id:
        if latitude_raw and longitude_raw:
            latitude = _normalize_meituan_coordinate(latitude_raw, is_latitude=True)
            longitude = _normalize_meituan_coordinate(longitude_raw, is_latitude=False)
            return token, latitude_raw, longitude_raw, latitude, longitude, {}
        raise ValueError("缺少 userId，请使用带 userId 的完整美团链接，或从统一工具页选择已保存账号")
    try:
        history_result = await _request_meituan_history_addresses(token=token, user_id=user_id)
        history_code = int(history_result.get("code") or 0)
        if history_code != 0:
            history_message = _safe_text(history_result.get("msg")) or "获取历史地址失败"
            raise ValueError(history_message)
        selected_address = _pick_meituan_history_address(history_result)
    except requests.Timeout as exc:
        if latitude_raw and longitude_raw:
            latitude = _normalize_meituan_coordinate(latitude_raw, is_latitude=True)
            longitude = _normalize_meituan_coordinate(longitude_raw, is_latitude=False)
            return token, latitude_raw, longitude_raw, latitude, longitude, {}
        raise ValueError("获取历史地址超时，请稍后重试") from exc
    except requests.RequestException as exc:
        if latitude_raw and longitude_raw:
            latitude = _normalize_meituan_coordinate(latitude_raw, is_latitude=True)
            longitude = _normalize_meituan_coordinate(longitude_raw, is_latitude=False)
            return token, latitude_raw, longitude_raw, latitude, longitude, {}
        raise ValueError(f"获取历史地址失败: {str(exc)}") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"解析历史地址失败: {_format_error_message(exc)}") from exc

    return (
        token,
        selected_address["input_latitude"],
        selected_address["input_longitude"],
        selected_address["normalized_latitude"],
        selected_address["normalized_longitude"],
        dict(selected_address.get("resolved_address") or {}),
    )


async def _load_meituan_allowance_address_list_payload(
    request_data: MeituanAllowanceAddressRequest,
) -> Dict[str, Any]:
    raw_token_value = str(request_data.token or "").strip()
    token = _extract_meituan_token(raw_token_value)
    user_id = _extract_meituan_user_id(request_data.user_id) or _extract_meituan_user_id(raw_token_value)
    if not token:
        raise ValueError("Token不能为空")
    if not user_id:
        raise ValueError("缺少 userId，请使用带 userId 的完整美团链接，或从统一工具页选择已保存账号")

    try:
        addresses = await _load_meituan_history_address_options(token=token, user_id=user_id)
    except requests.Timeout as exc:
        raise ValueError("获取历史地址超时，请稍后重试") from exc
    except requests.RequestException as exc:
        raise ValueError(f"获取历史地址失败: {str(exc)}") from exc
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"解析历史地址失败: {_format_error_message(exc)}") from exc

    return {
        "token": token,
        "user_id": user_id,
        "addresses": addresses,
        "default_index": 0,
    }


@router.post("/api/meituan/allowance/addresses")
async def get_meituan_allowance_addresses(request_data: MeituanAllowanceAddressRequest):
    try:
        payload = await _load_meituan_allowance_address_list_payload(request_data)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"success": True, **payload})


async def _emit_meituan_allowance_progress(
    callback: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]],
    payload: Dict[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(payload)
    if asyncio.iscoroutine(result):
        await result


async def _execute_meituan_allowance_query(
    *,
    token: str,
    input_latitude: str,
    input_longitude: str,
    normalized_latitude: str,
    normalized_longitude: str,
    resolved_address: Optional[Dict[str, Any]] = None,
    on_progress: Optional[Callable[[Dict[str, Any]], Awaitable[None] | None]] = None,
) -> Dict[str, Any]:
    started_at = time.time()
    all_merchants: list[Dict[str, Any]] = []
    seen_merchant_ids: set[str] = set()
    progress: list[Dict[str, Any]] = []
    wm_context = ""
    stop_reason = "completed"
    empty_pages = 0
    total_pages_requested = 0

    def build_summary(current_stop_reason: str, *, finished: bool = False) -> Dict[str, Any]:
        return _build_meituan_allowance_summary(
            started_at=started_at,
            input_latitude=input_latitude,
            input_longitude=input_longitude,
            normalized_latitude=normalized_latitude,
            normalized_longitude=normalized_longitude,
            resolved_address=resolved_address,
            pages_requested=total_pages_requested,
            merchant_count=len(all_merchants),
            stop_reason=current_stop_reason,
            consecutive_empty_pages=empty_pages,
            finished_at=time.time() if finished else None,
        )

    for page_num in range(MEITUAN_ALLOWANCE_MAX_PAGES):
        page_started_at = time.time()
        total_pages_requested = page_num + 1

        try:
            timeout_attempts = max(1, MEITUAN_ALLOWANCE_PAGE_TIMEOUT_MAX_ATTEMPTS)
            raw_result: Dict[str, Any] | None = None
            parsed_result: Dict[str, Any] | None = None
            for timeout_attempt in range(1, timeout_attempts + 1):
                try:
                    raw_result = await _request_meituan_allowance_page(
                        token=token,
                        longitude=normalized_longitude,
                        latitude=normalized_latitude,
                        page_num=page_num,
                        page_size=MEITUAN_ALLOWANCE_PAGE_SIZE,
                        wm_context=wm_context,
                    )
                    parsed_result = _parse_nested_json_strings(raw_result)
                    break
                except requests.Timeout:
                    if timeout_attempt >= timeout_attempts:
                        raise
                    logger.warning(
                        "美团津贴页请求超时，等待重试: page_num=%s attempt=%d/%d delay_seconds=%s",
                        page_num,
                        timeout_attempt,
                        timeout_attempts,
                        MEITUAN_ALLOWANCE_PAGE_TIMEOUT_RETRY_DELAY_SECONDS,
                    )
                    await asyncio.sleep(MEITUAN_ALLOWANCE_PAGE_TIMEOUT_RETRY_DELAY_SECONDS)
            if not isinstance(parsed_result, dict):
                raise RuntimeError("美团津贴响应结构异常")
        except requests.Timeout as exc:
            stop_reason = f"page_failed:{page_num}:timeout"
            raise MeituanAllowanceExecutionError(
                message="请求超时，请稍后重试",
                status_code=504,
                summary=build_summary(stop_reason, finished=True),
                progress=progress,
                merchants=all_merchants,
            ) from exc
        except requests.RequestException as exc:
            stop_reason = f"page_failed:{page_num}:{exc.__class__.__name__}"
            raise MeituanAllowanceExecutionError(
                message=f"网络错误: {str(exc)}",
                status_code=500,
                summary=build_summary(stop_reason, finished=True),
                progress=progress,
                merchants=all_merchants,
            ) from exc
        except Exception as exc:
            stop_reason = f"page_failed:{page_num}:{exc.__class__.__name__}"
            raise MeituanAllowanceExecutionError(
                message=f"服务器错误: {_format_error_message(exc)}",
                status_code=500,
                summary=build_summary(stop_reason, finished=True),
                progress=progress,
                merchants=all_merchants,
            ) from exc

        code = int(parsed_result.get("code") or 0)
        if code != 0:
            stop_reason = f"api_error:{code}"
            error_message = _safe_text(parsed_result.get("msg")) or "美团接口返回失败"
            raise MeituanAllowanceExecutionError(
                message=error_message,
                status_code=400,
                summary=build_summary(stop_reason, finished=True),
                progress=progress,
                merchants=all_merchants,
            )

        merchants = _extract_allowance_merchants_from_result(parsed_result)
        new_merchants = 0
        duplicate_merchants = 0
        for merchant in merchants:
            merchant_id = str(merchant.get("poi_id") or "").strip()
            if not merchant_id:
                duplicate_merchants += 1
                continue
            if merchant_id in seen_merchant_ids:
                duplicate_merchants += 1
                continue
            seen_merchant_ids.add(merchant_id)
            all_merchants.append(merchant)
            new_merchants += 1

        empty_pages = empty_pages + 1 if new_merchants == 0 else 0

        data = parsed_result.get("data", {})
        json_data = data.get("json_data", {})
        page_info = json_data.get("page", {}) if isinstance(json_data, dict) else {}
        has_next_page = bool(page_info.get("hasNextPage"))
        next_wm_context = _safe_text(json_data.get("wm_context")) if isinstance(json_data, dict) else ""
        if next_wm_context:
            wm_context = next_wm_context

        page_progress = {
            "page_num": page_num,
            "merchant_candidates": len(merchants),
            "new_merchants": new_merchants,
            "duplicate_merchants": duplicate_merchants,
            "consecutive_empty_pages": empty_pages,
            "has_next_page": has_next_page,
            "duration_seconds": round(max(0.0, time.time() - page_started_at), 3),
        }
        progress.append(page_progress)

        await _emit_meituan_allowance_progress(
            on_progress,
            {
                "summary": build_summary("running"),
                "progress": list(progress),
                "merchants": list(all_merchants),
                "latest_progress": page_progress,
            },
        )

        if empty_pages >= MEITUAN_ALLOWANCE_EMPTY_STOP_THRESHOLD:
            stop_reason = "consecutive_empty_pages"
            break
        if not has_next_page:
            stop_reason = "has_next_page_false"
            break

        await asyncio.sleep(MEITUAN_ALLOWANCE_REQUEST_DELAY_SECONDS)
    else:
        stop_reason = "max_pages_reached"

    summary = build_summary(stop_reason, finished=True)
    return {
        "summary": summary,
        "progress": progress,
        "merchants": all_merchants,
    }


def _build_meituan_allowance_task_payload(task: Dict[str, Any]) -> Dict[str, Any]:
    progress = task.get("progress") or []
    latest_progress = progress[-1] if progress else None
    merchants = _hydrate_allowance_merchants_with_coupon_urls(task.get("merchants") or [])
    payload = {
        "success": True,
        "task_id": task.get("task_id"),
        "status": task.get("status"),
        "meituan_user_id": task.get("meituan_user_id") or "",
        "address_id": task.get("address_id") or "",
        "token_masked": task.get("token_masked"),
        "summary": task.get("summary") or {},
        "pages_requested": task.get("pages_requested") or 0,
        "merchant_count": task.get("merchant_count") or 0,
        "stop_reason": task.get("stop_reason") or "",
        "consecutive_empty_pages": task.get("consecutive_empty_pages") or 0,
        "error_message": task.get("error_message") or "",
        "latest_progress": latest_progress,
        "progress": progress,
        "merchants": merchants,
    }
    if str(task.get("status") or "") in MEITUAN_ALLOWANCE_TERMINAL_STATUSES:
        payload["result_url"] = _build_meituan_allowance_result_url(str(task.get("task_id") or ""))
    return payload


def _build_meituan_allowance_daily_payload(aggregate: Dict[str, Any]) -> Dict[str, Any]:
    merchants = _hydrate_allowance_merchants_with_coupon_urls(aggregate.get("merchants") or [])
    summary = dict(aggregate.get("summary") or {})
    latest_task_summary = summary.get("latest_task_summary") if isinstance(summary.get("latest_task_summary"), dict) else {}
    payload = {
        "success": True,
        "task_id": aggregate.get("last_task_id") or "",
        "status": aggregate.get("last_task_status") or "",
        "meituan_user_id": aggregate.get("meituan_user_id") or "",
        "address_id": aggregate.get("address_id") or "",
        "token_masked": "",
        "summary": {
            **latest_task_summary,
            "date_key": aggregate.get("date_key") or "",
            "merchant_count": int(aggregate.get("merchant_count") or 0),
            "task_count": len(aggregate.get("task_ids") or []),
            "resolved_address": aggregate.get("resolved_address") or {},
        },
        "pages_requested": int((latest_task_summary or {}).get("pages_requested") or 0),
        "merchant_count": int(aggregate.get("merchant_count") or 0),
        "stop_reason": str((latest_task_summary or {}).get("stop_reason") or ""),
        "consecutive_empty_pages": int((latest_task_summary or {}).get("consecutive_empty_pages") or 0),
        "error_message": "",
        "latest_progress": None,
        "progress": [],
        "merchants": merchants,
        "result_url": _build_meituan_allowance_result_url(str(aggregate.get("last_task_id") or "")),
        "aggregate_mode": True,
        "aggregate_task_ids": aggregate.get("task_ids") or [],
    }
    return payload


def _hydrate_allowance_merchants_with_coupon_urls(merchants: Any) -> list[Dict[str, Any]]:
    if not isinstance(merchants, list):
        return []

    default_coupon_account_id = _get_default_allowance_coupon_account_id()
    hydrated: list[Dict[str, Any]] = []
    for raw_item in merchants:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        poi_id_str = _safe_text(item.get("poi_id_str") or item.get("poi_id"))
        item["poi_id_str"] = poi_id_str

        if default_coupon_account_id:
            item["coupon_account_id"] = _safe_text(item.get("coupon_account_id")) or default_coupon_account_id
        else:
            item["coupon_account_id"] = _safe_text(item.get("coupon_account_id"))

        if poi_id_str:
            if not _safe_text(item.get("merchant_coupon_url_v8")) and item["coupon_account_id"]:
                item["merchant_coupon_url_v8"] = build_meituan_coupon_url(
                    item["coupon_account_id"],
                    poi_id_str,
                    logger,
                    variant="v8",
                )
            if not _safe_text(item.get("merchant_coupon_url_v6")) and item["coupon_account_id"]:
                item["merchant_coupon_url_v6"] = build_meituan_coupon_url(
                    item["coupon_account_id"],
                    poi_id_str,
                    logger,
                    variant="v5",
                )
        hydrated.append(item)
    return hydrated


async def _run_meituan_allowance_task(
    *,
    task_id: str,
    meituan_user_id: str = "",
    address_id: str = "",
    token: str,
    input_latitude: str,
    input_longitude: str,
    normalized_latitude: str,
    normalized_longitude: str,
    resolved_address: Optional[Dict[str, Any]] = None,
) -> None:
    storage = get_meituan_allowance_task_storage()
    started_at = int(time.time())
    try:
        storage.update_task(task_id, status="running", started_at=started_at)

        async def persist_progress(snapshot: Dict[str, Any]) -> None:
            summary = dict(snapshot.get("summary") or {})
            summary["started_at"] = started_at
            storage.update_task(
                task_id,
                status="running",
                started_at=started_at,
                pages_requested=int(summary.get("pages_requested") or 0),
                merchant_count=int(summary.get("merchant_count") or 0),
                stop_reason=str(summary.get("stop_reason") or ""),
                consecutive_empty_pages=int(summary.get("consecutive_empty_pages") or 0),
                summary=summary,
                progress=list(snapshot.get("progress") or []),
                merchants=list(snapshot.get("merchants") or []),
            )

        result = await _execute_meituan_allowance_query(
            token=token,
            input_latitude=input_latitude,
            input_longitude=input_longitude,
            normalized_latitude=normalized_latitude,
            normalized_longitude=normalized_longitude,
            resolved_address=resolved_address,
            on_progress=persist_progress,
        )

        finished_at = int(time.time())
        summary = dict(result.get("summary") or {})
        summary["started_at"] = started_at
        summary["finished_at"] = finished_at
        storage.update_task(
            task_id,
            status="succeeded",
            started_at=started_at,
            finished_at=finished_at,
            pages_requested=int(summary.get("pages_requested") or 0),
            merchant_count=int(summary.get("merchant_count") or 0),
            stop_reason=str(summary.get("stop_reason") or ""),
            consecutive_empty_pages=int(summary.get("consecutive_empty_pages") or 0),
            error_message="",
            summary=summary,
            progress=list(result.get("progress") or []),
            merchants=list(result.get("merchants") or []),
        )
        storage.update_daily_aggregate(
            meituan_user_id=meituan_user_id,
            address_id=address_id,
            resolved_address=resolved_address,
            task_id=task_id,
            task_status="succeeded",
            merchants=list(result.get("merchants") or []),
            summary=summary,
            date_key=None,
        )
    except MeituanAllowanceExecutionError as exc:
        finished_at = int(time.time())
        summary = dict(exc.summary or {})
        summary["started_at"] = started_at
        summary["finished_at"] = finished_at
        storage.update_task(
            task_id,
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
            pages_requested=int(summary.get("pages_requested") or 0),
            merchant_count=int(summary.get("merchant_count") or 0),
            stop_reason=str(summary.get("stop_reason") or ""),
            consecutive_empty_pages=int(summary.get("consecutive_empty_pages") or 0),
            error_message=exc.message,
            summary=summary,
            progress=list(exc.progress or []),
            merchants=list(exc.merchants or []),
        )
        if exc.merchants:
            storage.update_daily_aggregate(
                meituan_user_id=meituan_user_id,
                address_id=address_id,
                resolved_address=resolved_address,
                task_id=task_id,
                task_status="failed",
                merchants=list(exc.merchants or []),
                summary=summary,
                date_key=None,
            )
        logger.warning("美团津贴任务失败: task_id=%s error=%s", task_id, exc.message)
    except Exception as exc:
        finished_at = int(time.time())
        error_message = f"服务器错误: {_format_error_message(exc)}"
        fallback_summary = _build_meituan_allowance_summary(
            started_at=float(started_at),
            input_latitude=input_latitude,
            input_longitude=input_longitude,
            normalized_latitude=normalized_latitude,
            normalized_longitude=normalized_longitude,
            resolved_address=resolved_address,
            pages_requested=0,
            merchant_count=0,
            stop_reason="task_exception",
            consecutive_empty_pages=0,
            finished_at=float(finished_at),
        )
        storage.update_task(
            task_id,
            status="failed",
            started_at=started_at,
            finished_at=finished_at,
            pages_requested=0,
            merchant_count=0,
            stop_reason="task_exception",
            consecutive_empty_pages=0,
            error_message=error_message,
            summary=fallback_summary,
        )
        logger.error("美团津贴任务异常: task_id=%s error=%s", task_id, exc)
        logger.error(traceback.format_exc())
    finally:
        _meituan_allowance_background_tasks.pop(task_id, None)


async def _create_meituan_allowance_task_internal(
    *,
    token: str,
    meituan_user_id: str = "",
    resolved_address: Optional[Dict[str, Any]] = None,
    latitude_raw: str | None = None,
    longitude_raw: str | None = None,
) -> Dict[str, Any]:
    selected_address = _normalize_meituan_resolved_address_payload(resolved_address or {})
    input_latitude = str(latitude_raw or selected_address["input_latitude"] or "").strip()
    input_longitude = str(longitude_raw or selected_address["input_longitude"] or "").strip()
    normalized_latitude = str(selected_address["normalized_latitude"] or "").strip()
    normalized_longitude = str(selected_address["normalized_longitude"] or "").strip()
    resolved_payload = dict(selected_address.get("resolved_address") or {})
    normalized_user_id = _extract_meituan_user_id(meituan_user_id)
    address_id = _safe_text((resolved_payload or {}).get("address_id"))

    storage = get_meituan_allowance_task_storage()
    task_id = uuid.uuid4().hex
    storage.create_task(
        task_id=task_id,
        status="queued",
        meituan_user_id=normalized_user_id,
        address_id=address_id,
        token_masked=_mask_meituan_token(token),
        token_fingerprint=_fingerprint_meituan_token(token),
        input_latitude=input_latitude,
        input_longitude=input_longitude,
        normalized_latitude=normalized_latitude,
        normalized_longitude=normalized_longitude,
    )
    if normalized_user_id and address_id and resolved_payload:
        storage.upsert_refresh_target(
            meituan_user_id=normalized_user_id,
            address_id=address_id,
            resolved_address=resolved_payload,
            last_task_id=task_id,
        )

    background_task = asyncio.create_task(
        _run_meituan_allowance_task(
            task_id=task_id,
            meituan_user_id=normalized_user_id,
            address_id=address_id,
            token=token,
            input_latitude=input_latitude,
            input_longitude=input_longitude,
            normalized_latitude=normalized_latitude,
            normalized_longitude=normalized_longitude,
            resolved_address=resolved_payload,
        )
    )
    _meituan_allowance_background_tasks[task_id] = background_task

    return {
        "task_id": task_id,
        "status": "queued",
        "meituan_user_id": normalized_user_id,
        "address_id": address_id,
        "result_url": _build_meituan_allowance_result_url(task_id),
    }


@router.post("/api/meituan/allowance/tasks")
async def create_meituan_allowance_task(request: Request, request_data: MeituanAllowanceQueryRequest):
    try:
        token, latitude_raw, longitude_raw, latitude, longitude, resolved_address = await _prepare_meituan_allowance_inputs(request_data)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    meituan_user_id = _extract_meituan_user_id(request_data.user_id) or _extract_meituan_user_id(request_data.token)
    try:
        payload = await _create_meituan_allowance_task_internal(
            token=token,
            meituan_user_id=meituan_user_id,
            resolved_address=resolved_address,
            latitude_raw=latitude_raw,
            longitude_raw=longitude_raw,
        )
    except Exception as exc:
        logger.error("创建美团津贴任务失败: %s", exc)
        logger.error(traceback.format_exc())
        return JSONResponse({"success": False, "error": "任务创建失败，请稍后重试"}, status_code=500)
    return JSONResponse({"success": True, **payload})


@router.get("/api/meituan/allowance/latest")
async def get_latest_meituan_allowance_task(meituan_user_id: str = "", address_id: str = ""):
    normalized_user_id = _extract_meituan_user_id(meituan_user_id)
    normalized_address_id = _safe_text(address_id)
    if not normalized_user_id:
        return JSONResponse({"success": False, "error": "缺少有效的 meituan_user_id"}, status_code=400)
    if not normalized_address_id:
        return JSONResponse({"success": False, "error": "缺少有效的 address_id"}, status_code=400)
    storage = get_meituan_allowance_task_storage()
    aggregate = storage.get_daily_aggregate(
        meituan_user_id=normalized_user_id,
        address_id=normalized_address_id,
    )
    if aggregate is not None and (aggregate.get("merchants") or aggregate.get("task_ids")):
        return JSONResponse(_build_meituan_allowance_daily_payload(aggregate))
    task = storage.get_latest_task_by_meituan_user_id_and_address_id(
        normalized_user_id,
        normalized_address_id,
    )
    if task is None:
        return JSONResponse({"success": False, "error": "当前账号在该地址下暂无津贴结果"}, status_code=404)
    return JSONResponse(_build_meituan_allowance_task_payload(task))


@router.get("/api/meituan/allowance/tasks/{task_id}")
async def get_meituan_allowance_task_status(task_id: str):
    task = get_meituan_allowance_task_storage().get_task(task_id)
    if task is None:
        return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
    return JSONResponse(_build_meituan_allowance_task_payload(task))


@router.get("/api/meituan/allowance/results/{task_id}")
async def get_meituan_allowance_result(task_id: str):
    task = get_meituan_allowance_task_storage().get_task(task_id)
    if task is None:
        return JSONResponse({"success": False, "error": "任务不存在"}, status_code=404)
    if task.get("meituan_user_id") and task.get("address_id"):
        aggregate = get_meituan_allowance_task_storage().get_daily_aggregate(
            meituan_user_id=str(task.get("meituan_user_id") or ""),
            address_id=str(task.get("address_id") or ""),
        )
        if aggregate is not None and (aggregate.get("last_task_id") == task_id or aggregate.get("task_ids")):
            payload = _build_meituan_allowance_daily_payload(aggregate)
            payload["task_id"] = task.get("task_id")
            payload["status"] = task.get("status")
            payload["error_message"] = task.get("error_message") or ""
            return JSONResponse(payload)
    merchants = _hydrate_allowance_merchants_with_coupon_urls(task.get("merchants") or [])
    return JSONResponse(
        {
            "success": True,
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "meituan_user_id": task.get("meituan_user_id") or "",
            "address_id": task.get("address_id") or "",
            "summary": task.get("summary") or {},
            "progress": task.get("progress") or [],
            "merchants": merchants,
            "error_message": task.get("error_message") or "",
            "result_url": _build_meituan_allowance_result_url(task_id),
        }
    )


@router.post("/api/meituan/allowance/query")
async def query_meituan_allowance(request_data: MeituanAllowanceQueryRequest):
    try:
        token, latitude_raw, longitude_raw, latitude, longitude, resolved_address = await _prepare_meituan_allowance_inputs(request_data)
    except ValueError as exc:
        return JSONResponse({"success": False, "error": str(exc)}, status_code=400)

    try:
        result = await _execute_meituan_allowance_query(
            token=token,
            input_latitude=latitude_raw,
            input_longitude=longitude_raw,
            normalized_latitude=latitude,
            normalized_longitude=longitude,
            resolved_address=resolved_address,
        )
        return JSONResponse({"success": True, "data": result})
    except MeituanAllowanceExecutionError as exc:
        logger.warning("美团津贴同步查询失败: %s", exc.message)
        return JSONResponse(
            {
                "success": False,
                "error": exc.message,
                "data": {
                    "summary": exc.summary,
                    "progress": exc.progress,
                    "merchants": exc.merchants,
                },
            },
            status_code=exc.status_code,
        )
    except Exception as exc:
        logger.error("美团津贴查询异常: %s", exc)
        logger.error(traceback.format_exc())
        return JSONResponse(
            {"success": False, "error": f"服务器错误: {_format_error_message(exc)}"},
            status_code=500,
        )


                                   
                            
         
                
    
                                
    
         
           
                                    
                                          
                                            
                                                   
                                      
           
         
          
                                                        
        
                              
                                      
                                       
        
                                        
                         
                                                  
                                       
                                     
                                      
           
        
                    
                              
                                             
                                                                              
                                
                                    
                                       
                           
           
                           
                                              
                                             
                               
                                                    
                                             
              
                                         
                      
                                                                 
                                                     
                                                                                                               
                                                                                                                    
                               
                                   
                              
                                
                                                  
                                                 
                                       
                  
                                             
                          
                                                                                       
                                                         
                                                                                                                   
                                                                                                                     
                                                                                   
                                   
                                            
                                                                           
                                  
                
                                                 
                                                     
                                                      
                                    
                                                  
                                          
                                                  
               
                                                    
        
                                     
        
                            
                                        
                          
                                              
                               
                               
                                         
                             

@router.get("/8e72d260464302e3d2d5c9b4a27a0917.txt")
async def wechat_verification_file():
    content = "ddf427c9e9a4b23a9bc985544afab1edc5941141"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "public, max-age=3600"
        }
    )

@router.get("/852353fecb23d2d20f11a34dfd7d1991.txt")
async def wechat_verification_file2():
    content = "597c789fc70303d86d420afb1d774566f2727d3b"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "public, max-age=3600"
        }
    )

@router.get("/MP_verify_BxTmY8B5HtNgBQ0c.txt")
async def wechat_verification_file2():
    content = "BxTmY8B5HtNgBQ0c"
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "public, max-age=3600"
        }
    )
