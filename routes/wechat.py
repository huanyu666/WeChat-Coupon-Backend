"""
微信消息处理相关路由
"""
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional, Any, Dict, List
from config import DEFAULT_WECHAT_CONFIG
from utils import *
from utils.wechat_utils import get_account_config
from utils.inflight_request_store import get_inflight_request_store
from utils.auth_utils import get_current_user
from utils.logger import setup_logger
from utils.request_cache import get_request_cache
from utils.msg_id_dedup import get_msg_id_dedup
from utils.response import TextRspMsg
from handlers import *
from miniprogram import *
from text_processors import *
from pydantic import BaseModel
from utils import http_client as requests
from utils.xml_parser import extract_xml_fields
import logging
import urllib.parse
import time
import json
import re
import os
import resource
from utils.path_utils import resolve_project_path

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["微信接口"])
WECHAT_SYNC_WAIT_SECONDS = 15.0
WECHAT_RESOURCE_EXHAUSTED_WAIT_SECONDS = 1.0
WECHAT_ROUTE_SLOW_STEP_WARN_SECONDS = 4.0
WECHAT_TEXT_HANDLER_WARN_SECONDS = 1.5
ANCHOR_OPEN_TAG_RE = re.compile(r"<a\b[^>]*>")


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
    rsp.content = content
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
    from config import ACCOUNT_SPECIFIC_CONFIGS, WECHAT_ACCOUNTS

    global _account_text_processor_cache, _processor_class_name_map

    _processor_class_name_map = {
        processor.__class__.__name__: processor
        for _, processor in _all_text_processors
    }

    cache: Dict[str, List[Any]] = {}
    all_known_accounts = set(WECHAT_ACCOUNTS.keys()) | set(ACCOUNT_SPECIFIC_CONFIGS.keys())
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


def create_wxcpt_instance(account_config: dict) -> WXBizMsgCrypt:
    """
    根据账号配置创建加解密实例
    
    Args:
        account_config: 公众号配置字典
        
    Returns:
        WXBizMsgCrypt实例
    """
    return WXBizMsgCrypt(
        account_config["token"],
        account_config["encoding_aes_key"],
        account_config["appid"]
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
        from config import WECHAT_ACCOUNTS
        
        import asyncio
        loop = asyncio.get_event_loop()
        
                    
        if not encrypt_type or encrypt_type != "aes":
                                         
            for account_id, account_config in WECHAT_ACCOUNTS.items():
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
                DEFAULT_WECHAT_CONFIG["token"],
                signature,
                timestamp,
                nonce
            )
            if verify_result:
                logger.info("验证成功，使用默认配置")
                return PlainTextResponse(echostr)
            
            raise HTTPException(status_code=400, detail="签名验证失败")
        
                      
        else:
                                     
            for account_id, account_config in WECHAT_ACCOUNTS.items():
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
                wxcpt = create_wxcpt_instance(DEFAULT_WECHAT_CONFIG)
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
    _log_wechat_route_stage_if_slow("request_body_read", stage_started_at, stage_timings=route_stage_timings)
    
            
    start_time = time.time()
    
    try:
        import asyncio
        import sys
        
        stage_started_at = time.time()
        xml_data = body.decode('utf-8')
        _log_wechat_route_stage_if_slow("body_decode", stage_started_at, stage_timings=route_stage_timings)
        
        if _is_info_enabled():
            logger.info("收到原始消息: %s", xml_data)
        
                                                    
                                      
        loop = asyncio.get_event_loop()
        stage_started_at = time.time()
        outer_msg = await loop.run_in_executor(None, extract_xml_fields, xml_data, ("ToUserName", "Encrypt"))
        _log_wechat_route_stage_if_slow("initial_xml_parse", stage_started_at, stage_timings=route_stage_timings)
        to_user_name = outer_msg.get("ToUserName", "")
        
        logger.info("目标公众号ToUserName: %s", to_user_name)
        
                                 
        account_config = get_account_config(to_user_name)
        
        if not account_config:
            logger.warning("未找到 ToUserName=%s 对应的配置，使用默认配置", to_user_name)
            account_config = DEFAULT_WECHAT_CONFIG
        else:
            logger.info("使用账号配置: %s", account_config.get('name', to_user_name))
        route_account_name = account_config.get("name", to_user_name)
        
                                    
        if not encrypt_type or encrypt_type != "aes":
            if signature:
                stage_started_at = time.time()
                verify_result = await loop.run_in_executor(
                    None, 
                    verify_request_signature,
                    account_config["token"],
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
                if _is_info_enabled():
                    logger.info("解密后消息: %s", xml_data)
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
        
              
                      
        msg["_account_config"] = account_config
        msg["_account_name"] = account_config.get("name", to_user_name)
        
                    
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
            def _truncate_async(content: str) -> str:
                max_length = 1580
                warning_msg = "\n\n⚠️ 内容过长，请分段尝试"
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
                last_newline_pos = content.rfind('\n', search_start, truncated_length)
                
                if last_newline_pos != -1 and (truncated_length - last_newline_pos) <= max_search_distance:
                                   
                    truncated_content = content[:last_newline_pos]
                    if _is_info_enabled():
                        logger.info(
                            "文本消息内容过长（%s字符，可见%s字符），从换行处截断至%s字符",
                            len(content),
                            visible_length,
                            last_newline_pos,
                        )
                else:
                               
                    truncated_content = content[:truncated_length]
                    if _is_info_enabled():
                        logger.info(
                            "文本消息内容过长（%s字符，可见%s字符），已截断至%s字符",
                            len(content),
                            visible_length,
                            truncated_length,
                        )
                return truncated_content + warning_msg

                              
            stage_started_at = time.time()
            truncated = await asyncio.to_thread(_truncate_async, rsp_msg.content)
            _log_wechat_route_stage_if_slow(
                "text_truncate",
                stage_started_at,
                processing_key=processing_key,
                account_name=account_config.get("name", to_user_name),
                msg_type=msg_type,
                stage_timings=route_stage_timings,
            )
            rsp_msg.content = truncated
        
                 
        response_xml = rsp_msg.dump_xml()
        
                
        duration = time.time() - start_time
        
                        
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
    - miniprogram/miniprogram_config.toml（小程序配置）
    - text_processors/keyword_responses.toml（关键词回复配置）
    - link_handlers/prompts.toml（提示语配置）
    - link_handlers/link_config.toml（链接识别配置）
    - text_processors/merchant_coupon_prompts.toml（商家券列表自定义文案）
    - text_processors/order_leaderboard.toml（排行榜配置）
    """
    try:
                 
        from config.config import reload_config
        reload_config()
        
                      
        for _, processor in _all_text_processors:
            if hasattr(processor, '_reload_configs'):
                try:
                    processor._reload_configs()
                    logger.info("重新加载处理器配置: %s", processor.__class__.__name__)
                except Exception as e:
                    logger.error("重新加载处理器 %s 配置失败: %s", processor.__class__.__name__, e)

        _rebuild_text_processor_runtime_caches()
        
        logger.info("所有配置重新加载成功，操作人: %s", current_user)
        return JSONResponse({
            "success": True,
            "message": "所有配置已重新加载（包括 config.toml、miniprogram_config.toml、keyword_responses.toml、prompts.toml、link_config.toml、merchant_coupon_prompts.toml、order_leaderboard.toml）",
            "operator": current_user,
            "reloaded": [
                "config.toml",
                "miniprogram/miniprogram_config.toml",
                "text_processors/keyword_responses.toml",
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
        import asyncio
        loop = asyncio.get_event_loop()
        
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
        try:
            proxies = await require_proxy_config_async()
        except ProxyUnavailableError:
            return JSONResponse({
                "success": False,
                "error": "网络繁忙，请稍后重试"
            }, status_code=503)
        proxy_url = str(proxies.get("http") or proxies.get("https") or "").strip()
        
                 
        url = "https://wx.waimai.meituan.com/weapp/v2/order/historystatus"
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
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            if proxy_url:
                await report_proxy_failure_async(proxy_url, exc)
            raise
        try:
            result = response.json()
        except Exception as exc:
            if proxy_url:
                await report_proxy_failure_async(proxy_url, exc)
            raise
        if not isinstance(result, dict):
            if proxy_url:
                await report_proxy_failure_async(proxy_url, RuntimeError("美团订单查询响应结构异常"))
            raise RuntimeError("美团订单查询响应结构异常")
        
                   
        if _is_info_enabled():
            logger.info("美团订单查询响应 - 订单ID: %s, 响应内容: %s", order_id, result)
        
              
        if result.get("code") == 50001:
            if proxy_url:
                await report_proxy_failure_async(proxy_url, RuntimeError("美团订单查询业务返回认证失败"))
            return JSONResponse({
                "success": False,
                "error": "认证失败，请检查Token是否正确"
            }, status_code=401)
        
        if result.get("code") != 0:
            if proxy_url:
                await report_proxy_failure_async(proxy_url, RuntimeError(f"美团订单查询业务返回失败 code={result.get('code')}"))
            return JSONResponse({
                "success": False,
                "error": result.get("msg", "查询失败")
            }, status_code=400)
        if proxy_url:
            await report_proxy_success_async(proxy_url)
        
                               
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
        
        from utils.proxy_utils import report_proxy_failure_async, report_proxy_success_async
        try:
            proxies = await require_proxy_config_async()
        except ProxyUnavailableError:
            return JSONResponse({
                "success": False,
                "error": "网络繁忙，请稍后重试"
            }, status_code=503)
        proxy_url = str(proxies.get("http") or proxies.get("https") or "").strip()
        
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
        
        try:
            response = await requests.post(
                url,
                data=urllib.parse.urlencode(params),
                headers=headers,
                proxies=proxies,
                timeout=5,
            )
            response.raise_for_status()
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            if proxy_url:
                await report_proxy_failure_async(proxy_url, exc)
            raise
        try:
            result = response.json()
        except Exception as exc:
            if proxy_url:
                await report_proxy_failure_async(proxy_url, exc)
            raise
        if not isinstance(result, dict):
            if proxy_url:
                await report_proxy_failure_async(proxy_url, RuntimeError("美团落地页响应结构异常"))
            raise RuntimeError("美团落地页响应结构异常")
        if _is_info_enabled():
            logger.info("美团落地页API响应 - UserId: %s, 响应内容: %s", userId, result)
        
        try:
            parsed_result = _parse_nested_json_strings(result)
        except Exception as exc:
            if proxy_url:
                await report_proxy_failure_async(proxy_url, exc)
            raise
        if proxy_url:
            await report_proxy_success_async(proxy_url)
        
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
