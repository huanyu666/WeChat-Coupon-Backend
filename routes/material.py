"""
素材管理相关路由
"""
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from utils import http_client as requests
import json
from pydantic import BaseModel, Field
from config import get_config, get_wechat_accounts
from utils.auth_utils import get_current_user
from utils.wechat_utils import get_access_token, access_token_cache
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path
from wechat_account_store import (
    delete_wechat_account,
    load_wechat_account_store,
    set_default_wechat_account,
    upsert_wechat_account,
    upsert_wechat_account_specific_config,
    upsert_wechat_account_keyword_responses,
)

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["素材管理"])
URL_MODE_OPTIONS = [
    {"value": "all", "label": "all（美团 + 大众点评）"},
    {"value": "meituan", "label": "meituan（仅美团）"},
    {"value": "dianping", "label": "dianping（仅大众点评）"},
]


class WechatAccountPayload(BaseModel):
    account_id: str
    name: str = ""
    appid: str = ""
    app_secret: str = ""
    token: str = ""
    encoding_aes_key: str = ""
    zmkey: str = ""
    set_as_default: bool = False
    welcome_message: str = ""
    default_reply: str = ""
    enabled_text_processors: list[str] = Field(default_factory=list)
    enabled_miniprogram_appids: list[str] = Field(default_factory=list)
    meituan_base_url: str = ""
    meituan_official_cashback_url: str = ""
    url_mode: str = "all"
    authorized_users: list[str] = Field(default_factory=list)
    default_code_duration: str = ""
    keyword_responses: list[dict[str, str]] = Field(default_factory=list)


def _reload_wechat_runtime_configs() -> None:
    from routes.wechat import reload_wechat_runtime_configs

    reload_wechat_runtime_configs()


def _get_text_processor_options() -> list[dict[str, str]]:
    from routes.wechat import _PROCESSOR_LABELS, get_available_text_processor_options

    base_options = get_available_text_processor_options()
    seen = {item.get("value", "") for item in base_options}
    extra_options: list[dict[str, str]] = []
    effective_configs = get_config().get("account_specific_configs", {})
    if isinstance(effective_configs, dict):
        for account_config in effective_configs.values():
            if not isinstance(account_config, dict):
                continue
            for value in account_config.get("enabled_text_processors", []):
                normalized_value = str(value or "").strip()
                if not normalized_value or normalized_value in seen:
                    continue
                seen.add(normalized_value)
                extra_options.append({
                    "value": normalized_value,
                    "label": _PROCESSOR_LABELS.get(normalized_value, normalized_value),
                })
    return base_options + sorted(extra_options, key=lambda item: item["value"])


def _get_miniprogram_options() -> list[dict[str, str]]:
    miniprogram_appids = get_config().get("miniprogram_appids", {})
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    if isinstance(miniprogram_appids, dict):
        for appid, label in miniprogram_appids.items():
            normalized_appid = str(appid or "").strip()
            if not normalized_appid:
                continue
            seen.add(normalized_appid)
            normalized_label = str(label or "").strip()
            options.append({
                "value": normalized_appid,
                "label": f"{normalized_appid} ({normalized_label})" if normalized_label else normalized_appid,
            })

    effective_configs = get_config().get("account_specific_configs", {})
    if isinstance(effective_configs, dict):
        for account_config in effective_configs.values():
            if not isinstance(account_config, dict):
                continue
            for value in account_config.get("enabled_miniprogram_appids", []):
                normalized_value = str(value or "").strip()
                if not normalized_value or normalized_value in seen:
                    continue
                seen.add(normalized_value)
                options.append({"value": normalized_value, "label": normalized_value})

    return options


def _get_effective_keyword_responses() -> dict[str, dict[str, object]]:
    from config.config import KEYWORD_RESPONSES_FILE, load_toml_file, merge_keyword_response_config

    store_data = load_wechat_account_store()
    return merge_keyword_response_config(
        load_toml_file(KEYWORD_RESPONSES_FILE, {}),
        store_data.get("keyword_responses", {}),
    )


def _serialize_keyword_response_items(keyword_responses: object) -> list[dict[str, str]]:
    if not isinstance(keyword_responses, dict):
        return []

    items: list[dict[str, str]] = []
    for keyword, response in keyword_responses.items():
        normalized_keyword = str(keyword or "").strip()
        if not normalized_keyword:
            continue

        if isinstance(response, str) and not response.startswith("__LAMBDA__:"):
            items.append({
                "keyword": normalized_keyword,
                "response_type": "text",
                "response_content": response,
            })
            continue

        if isinstance(response, str):
            response_content = response
        else:
            response_content = json.dumps(response, ensure_ascii=False, indent=2)

        items.append({
            "keyword": normalized_keyword,
            "response_type": "advanced",
            "response_content": response_content,
        })

    return items


def _parse_keyword_response_items(items: list[dict[str, str]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    seen: set[str] = set()

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue

        keyword = str(raw_item.get("keyword", "") or "").strip()
        if not keyword:
            continue
        if keyword in seen:
            raise ValueError(f"关键词重复: {keyword}")

        response_type = str(raw_item.get("response_type", "text") or "text").strip() or "text"
        response_content = str(raw_item.get("response_content", "") or "")

        if response_type == "advanced":
            stripped_content = response_content.strip()
            if not stripped_content:
                continue
            if stripped_content.startswith("{") or stripped_content.startswith("["):
                try:
                    parsed[keyword] = json.loads(stripped_content)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"关键词 {keyword} 的高级回复内容不是合法 JSON: {exc}") from exc
            else:
                parsed[keyword] = stripped_content
        else:
            normalized_content = response_content.strip()
            if not normalized_content:
                continue
            parsed[keyword] = normalized_content

        seen.add(keyword)

    return parsed


def _serialize_account_store() -> dict:
    store_data = load_wechat_account_store()
    effective_configs = get_config().get("account_specific_configs", {})
    effective_keyword_responses = _get_effective_keyword_responses()
    accounts = []
    default_account_id = store_data.get("default_account_id", "")
    for account_id, config in store_data.get("accounts", {}).items():
        specific_config = effective_configs.get(account_id, {}) if isinstance(effective_configs, dict) else {}
        if not isinstance(specific_config, dict):
            specific_config = {}
        accounts.append({
            "id": account_id,
            "name": config.get("name", account_id),
            "appid": config.get("appid", ""),
            "app_secret": config.get("app_secret", ""),
            "token": config.get("token", ""),
            "encoding_aes_key": config.get("encoding_aes_key", ""),
            "zmkey": config.get("zmkey", ""),
            "welcome_message": specific_config.get("welcome_message", ""),
            "default_reply": specific_config.get("default_reply", ""),
            "enabled_text_processors": list(specific_config.get("enabled_text_processors", [])),
            "enabled_miniprogram_appids": list(specific_config.get("enabled_miniprogram_appids", [])),
            "meituan_base_url": specific_config.get("meituan_base_url", ""),
            "meituan_official_cashback_url": specific_config.get("meituan_official_cashback_url", ""),
            "url_mode": specific_config.get("url_mode", "all"),
            "authorized_users": list(specific_config.get("authorized_users", [])),
            "default_code_duration": specific_config.get("default_code_duration", ""),
            "keyword_responses": _serialize_keyword_response_items(effective_keyword_responses.get(account_id, {})),
            "is_default": account_id == default_account_id,
        })
    accounts.sort(key=lambda item: (not item["is_default"], item["name"], item["id"]))
    return {
        "default_account_id": default_account_id,
        "accounts": accounts,
        "processor_options": _get_text_processor_options(),
        "miniprogram_options": _get_miniprogram_options(),
        "url_mode_options": URL_MODE_OPTIONS,
    }


@router.get("/material", response_class=HTMLResponse)
async def material_page(request: Request):
    """
    返回素材上传管理页面
    注意：页面本身不验证 token，而是在前端 JavaScript 中验证
    如果未登录，前端会自动跳转到 /login
    """
    return templates.TemplateResponse(request, "material_upload.html", {"request": request})


@router.get("/wechat-account-settings", response_class=HTMLResponse)
async def wechat_account_settings_page(request: Request):
    return templates.TemplateResponse(request, "wechat_account_settings.html", {"request": request})


@router.get("/api/wechat/accounts")
async def get_accounts_list(current_user: str = Depends(get_current_user)):
    """
    获取所有可用的公众号列表（需要登录）
    """
    accounts = []
    for account_id, config in get_wechat_accounts().items():
        accounts.append({
            "id": account_id,
            "name": config.get("name", account_id),
            "appid": config.get("appid", "")
        })

    return JSONResponse({
        "success": True,
        "accounts": accounts
    })


@router.get("/api/wechat/account-settings")
async def get_wechat_account_settings(current_user: str = Depends(get_current_user)):
    return JSONResponse({
        "success": True,
        **_serialize_account_store(),
    })


@router.post("/api/wechat/account-settings")
async def save_wechat_account_settings(
    payload: WechatAccountPayload,
    current_user: str = Depends(get_current_user),
):
    account_id = str(payload.account_id or "").strip()
    if not account_id:
        return JSONResponse({
            "success": False,
            "error": "公众号原始ID不能为空"
        }, status_code=400)

    if not str(payload.appid or "").strip():
        return JSONResponse({
            "success": False,
            "error": "AppID 不能为空"
        }, status_code=400)

    try:
        parsed_keyword_responses = _parse_keyword_response_items(payload.keyword_responses)
    except ValueError as exc:
        return JSONResponse({
            "success": False,
            "error": str(exc)
        }, status_code=400)

    store_data = upsert_wechat_account(
        account_id,
        {
            "name": payload.name,
            "appid": payload.appid,
            "app_secret": payload.app_secret,
            "token": payload.token,
            "encoding_aes_key": payload.encoding_aes_key,
            "zmkey": payload.zmkey,
        },
        set_default=payload.set_as_default,
    )
    upsert_wechat_account_specific_config(
        account_id,
        {
            "welcome_message": payload.welcome_message,
            "default_reply": payload.default_reply,
            "enabled_text_processors": payload.enabled_text_processors,
            "enabled_miniprogram_appids": payload.enabled_miniprogram_appids,
            "meituan_base_url": payload.meituan_base_url,
            "meituan_official_cashback_url": payload.meituan_official_cashback_url,
            "url_mode": payload.url_mode,
            "authorized_users": payload.authorized_users,
            "default_code_duration": payload.default_code_duration,
        },
    )
    upsert_wechat_account_keyword_responses(account_id, parsed_keyword_responses)
    access_token_cache.pop(account_id, None)
    _reload_wechat_runtime_configs()
    logger.info("公众号配置已保存: account_id=%s operator=%s", account_id, current_user)
    return JSONResponse({
        "success": True,
        "message": "公众号配置已保存",
        "default_account_id": store_data.get("default_account_id", ""),
        "operator": current_user,
        **_serialize_account_store(),
    })


@router.post("/api/wechat/account-settings/default/{account_id}")
async def set_default_wechat_account_settings(
    account_id: str,
    current_user: str = Depends(get_current_user),
):
    store_data = set_default_wechat_account(account_id)
    if store_data.get("default_account_id") != str(account_id or "").strip():
        return JSONResponse({
            "success": False,
            "error": "设置默认公众号失败，账号不存在"
        }, status_code=404)

    _reload_wechat_runtime_configs()
    logger.info("默认公众号已更新: account_id=%s operator=%s", account_id, current_user)
    return JSONResponse({
        "success": True,
        "message": "默认公众号已更新",
        **_serialize_account_store(),
    })


@router.delete("/api/wechat/account-settings/{account_id}")
async def delete_wechat_account_settings(
    account_id: str,
    current_user: str = Depends(get_current_user),
):
    store_data_before = load_wechat_account_store()
    if str(account_id or "").strip() not in store_data_before.get("accounts", {}):
        return JSONResponse({
            "success": False,
            "error": "公众号不存在"
        }, status_code=404)

    delete_wechat_account(account_id)
    access_token_cache.pop(str(account_id or "").strip(), None)
    _reload_wechat_runtime_configs()
    logger.info("公众号配置已删除: account_id=%s operator=%s", account_id, current_user)
    return JSONResponse({
        "success": True,
        "message": "公众号配置已删除",
        **_serialize_account_store(),
    })


@router.post("/api/wechat/upload_temp_material")
async def upload_temp_material(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    type: str = Form(...),
    current_user: str = Depends(get_current_user)
):
    """
    上传临时素材（需要登录）
    
    Args:
        file: 上传的文件
        account_id: 公众号ID
        type: 素材类型 (image/voice/video/thumb)
    """
    try:
                         
        access_token = await get_access_token(account_id)
        if not access_token:
            return JSONResponse({
                "success": False,
                "error": "获取 access_token 失败，请检查配置"
            }, status_code=500)
        
                
        file_content = await file.read()
        
                  
        url = f"https://api.weixin.qq.com/cgi-bin/media/upload?access_token={access_token}&type={type}"
        
        files = {
            'media': (file.filename, file_content, file.content_type)
        }
        
        logger.info(f"上传临时素材: {file.filename}, 类型: {type}, 大小: {len(file_content)} bytes, 操作人: {current_user}")
        
        response = await requests.post(url, files=files, timeout=30)
        result = response.json()
        
        if "media_id" in result:
            logger.info(f"上传成功: media_id={result['media_id']}, 操作人: {current_user}")
            return JSONResponse({
                "success": True,
                "data": result
            })
        else:
            logger.error(f"上传失败: {result}")
            error_msg = result.get("errmsg", "上传失败")
            return JSONResponse({
                "success": False,
                "error": f"微信API错误: {error_msg} (错误码: {result.get('errcode', 'unknown')})"
            }, status_code=400)
            
    except Exception as e:
        logger.error(f"上传临时素材异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse({
            "success": False,
            "error": f"服务器错误: {str(e)}"
        }, status_code=500)


@router.post("/api/wechat/upload_material")
async def upload_permanent_material(
    file: UploadFile = File(...),
    account_id: str = Form(...),
    type: str = Form(...),
    title: Optional[str] = Form(None),
    introduction: Optional[str] = Form(None),
    current_user: str = Depends(get_current_user)
):
    """
    上传永久素材（需要登录）
    
    Args:
        file: 上传的文件
        account_id: 公众号ID
        type: 素材类型 (image/voice/video/thumb)
        title: 视频标题（仅视频素材需要）
        introduction: 视频简介（仅视频素材需要）
    """
    try:
                         
        access_token = await get_access_token(account_id)
        if not access_token:
            return JSONResponse({
                "success": False,
                "error": "获取 access_token 失败，请检查配置"
            }, status_code=500)
        
                
        file_content = await file.read()
        
                  
        url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={access_token}&type={type}"
        
        files = {
            'media': (file.filename, file_content, file.content_type)
        }
        
                          
        data = {}
        if type == 'video' and (title or introduction):
            description = {}
            if title:
                description['title'] = title
            if introduction:
                description['introduction'] = introduction
            data['description'] = json.dumps(description)
        
        logger.info(f"上传永久素材: {file.filename}, 类型: {type}, 大小: {len(file_content)} bytes, 操作人: {current_user}")
        
        response = await requests.post(url, files=files, data=data, timeout=30)
        result = response.json()
        
        if "media_id" in result:
            logger.info(f"上传成功: media_id={result['media_id']}, 操作人: {current_user}")
            return JSONResponse({
                "success": True,
                "data": result
            })
        else:
            logger.error(f"上传失败: {result}")
            error_msg = result.get("errmsg", "上传失败")
            return JSONResponse({
                "success": False,
                "error": f"微信API错误: {error_msg} (错误码: {result.get('errcode', 'unknown')})"
            }, status_code=400)
            
    except Exception as e:
        logger.error(f"上传永久素材异常: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse({
            "success": False,
            "error": f"服务器错误: {str(e)}"
        }, status_code=500)


@router.get("/api/wechat/access_token/{account_id}")
async def get_access_token_api(account_id: str, current_user: str = Depends(get_current_user)):
    """
    获取指定公众号的 access_token（用于测试，需要登录）
    """
    access_token = await get_access_token(account_id)
    if access_token:
        return JSONResponse({
            "success": True,
            "access_token": access_token,
            "account_id": account_id
        })
    else:
        return JSONResponse({
            "success": False,
            "error": "获取 access_token 失败"
        }, status_code=500)
