"""
素材管理相关路由
"""
from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Optional
from utils import http_client as requests
import json
from config import WECHAT_ACCOUNTS
from utils.auth_utils import get_current_user
from utils.wechat_utils import get_access_token
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path

logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))

router = APIRouter(prefix="", tags=["素材管理"])


@router.get("/material", response_class=HTMLResponse)
async def material_page(request: Request):
    """
    返回素材上传管理页面
    注意：页面本身不验证 token，而是在前端 JavaScript 中验证
    如果未登录，前端会自动跳转到 /login
    """
    return templates.TemplateResponse(request, "material_upload.html", {"request": request})


@router.get("/api/wechat/accounts")
async def get_accounts_list(current_user: str = Depends(get_current_user)):
    """
    获取所有可用的公众号列表（需要登录）
    """
    accounts = []
    for account_id, config in WECHAT_ACCOUNTS.items():
        accounts.append({
            "id": account_id,
            "name": config.get("name", account_id),
            "appid": config.get("appid", "")
        })
    
    return JSONResponse({
        "success": True,
        "accounts": accounts
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
