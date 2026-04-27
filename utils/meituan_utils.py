"""
美团相关工具函数
"""
import base64
import json
import os
import re
import urllib.parse
import logging
import sys
from typing import Optional, Tuple, Dict, Any
from utils.logger import setup_logger

logger = setup_logger("utils.meituan_utils")
CASHBACK_SHORTLINK_TTL_SECONDS = 7 * 24 * 60 * 60
CASHBACK_MINIPROGRAM_APPID = "wxfdba1f3193621ebf"
MEITUAN_COLLECTION_PAGE_V8 = "collection_waimai_v8"
MEITUAN_COLLECTION_PAGE_V5 = "collection_waimai_v6"
_SHORTLINK_SOCKET_MISSING_WARNED = False


def _decode_until_stable(value: str, max_rounds: int = 3) -> str:
    """
    对URL编码字符串做有限次解码，直到结果稳定。
    """
    current = str(value or "")
    for _ in range(max_rounds):
        decoded = urllib.parse.unquote(current)
        if decoded == current:
            break
        current = decoded
    return current


def parse_meituan_shop_link(shop_link: str, logger_instance=None) -> Dict[str, Any]:
    """
    解析美团订单列表中的shopLink，提取商家页相关参数。

    支持：
    - imeituan://www.meituan.com/web?url=...
    - imeituan://www.meituan.com/takeout/foods?...
    - 普通 http/https 商家页链接

    Returns:
        {
            "shop_link": 原始链接,
            "resolved_url": 解析后的内层/最终URL,
            "poi_id_str": 商家poi_id_str,
            "poi_id": poi_id,
            "g_source": g_source,
        }
    """
    log = logger_instance if logger_instance else logger
    raw_link = str(shop_link or "").strip()
    result = {
        "shop_link": raw_link,
        "resolved_url": "",
        "poi_id_str": "",
        "poi_id": "",
        "g_source": "",
    }
    if not raw_link:
        return result

    def _extract_from_url(url_value: str) -> Dict[str, str]:
        parsed = urllib.parse.urlparse(url_value)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        return {
            "resolved_url": url_value,
            "poi_id_str": str(query.get("poi_id_str", [""])[0] or "").strip(),
            "poi_id": str(query.get("poi_id", [""])[0] or "").strip(),
            "g_source": str(query.get("g_source", [""])[0] or "").strip(),
        }

    try:
        parsed_outer = urllib.parse.urlparse(raw_link)
        outer_query = urllib.parse.parse_qs(parsed_outer.query, keep_blank_values=True)
        inner_url = str(outer_query.get("url", [""])[0] or "").strip()
        if inner_url:
            decoded_inner_url = _decode_until_stable(inner_url)
            result.update(_extract_from_url(decoded_inner_url))
            if result["poi_id_str"]:
                return result

        result.update(_extract_from_url(_decode_until_stable(raw_link)))
        return result
    except Exception as e:
        log.warning(f"解析shopLink失败: {e}, shop_link={raw_link}")
        return result


def build_meituan_coupon_variant_url(
    url: str,
    *,
    variant: str = "v8",
    logger_instance=None,
) -> str:
    log = logger_instance if logger_instance else logger
    normalized_url = str(url or "").strip()
    normalized_variant = str(variant or "v8").strip().lower()
    if not normalized_url:
        return ""
    if normalized_variant in {"v8", MEITUAN_COLLECTION_PAGE_V8}:
        return normalized_url
    if normalized_variant in {"v5", MEITUAN_COLLECTION_PAGE_V5}:
        if MEITUAN_COLLECTION_PAGE_V8 not in normalized_url:
            log.warning(f"无法生成商家券2链接，原链接不包含 {MEITUAN_COLLECTION_PAGE_V8}: {normalized_url}")
            return ""
        return normalized_url.replace(MEITUAN_COLLECTION_PAGE_V8, MEITUAN_COLLECTION_PAGE_V5, 1)
    log.warning(f"未知商家券链接版本: {variant}")
    return ""


def build_meituan_coupon_url(
    to_user_name: str,
    poi_id_str: str,
    logger_instance=None,
    *,
    variant: str = "v8",
) -> str:
    """
    按美团小程序处理器的口径拼接商家券主链接。

    Args:
        to_user_name: 公众号ID
        poi_id_str: 商家poi_id_str值，不包含参数名
        logger_instance: 日志记录器实例

    Returns:
        完整商家券链接；缺少配置或参数时返回空字符串
    """
    log = logger_instance if logger_instance else logger
    normalized_to_user_name = str(to_user_name or "").strip()
    normalized_poi_id_str = str(poi_id_str or "").strip()
    if not normalized_to_user_name or not normalized_poi_id_str:
        return ""

    try:
        from config.config import ACCOUNT_SPECIFIC_CONFIGS

        account_config = ACCOUNT_SPECIFIC_CONFIGS.get(normalized_to_user_name, {})
        meituan_base_url = str(account_config.get("meituan_base_url", "") or "").strip()
        if not meituan_base_url:
            log.info(f"未配置meituan_base_url: {normalized_to_user_name}")
            return ""

        meituan_base_url = build_meituan_coupon_variant_url(
            meituan_base_url,
            variant=variant,
            logger_instance=log,
        )
        if not meituan_base_url:
            return ""

        full_url = f"{meituan_base_url}&poi_id=-100&poi_id_str={normalized_poi_id_str}"
        log.info(f"拼接美团商家券主链接成功: {full_url}")
        return full_url
    except Exception as e:
        log.warning(f"拼接美团商家券主链接失败: {e}")
        return ""


def build_meituan_official_cashback_url(to_user_name: str, poi_id_str: str, logger_instance=None) -> str:
    """
    按公众号配置拼接官方返现活动报名基础路径。

    Args:
        to_user_name: 公众号ID
        poi_id_str: 商家poi_id_str值，不包含参数名
        logger_instance: 日志记录器实例

    Returns:
        完整官方返现活动路径；缺少配置或参数时返回空字符串
    """
    log = logger_instance if logger_instance else logger
    normalized_to_user_name = str(to_user_name or "").strip()
    normalized_poi_id_str = str(poi_id_str or "").strip()
    if not normalized_to_user_name or not normalized_poi_id_str:
        return ""

    try:
        from config.config import ACCOUNT_SPECIFIC_CONFIGS

        account_config = ACCOUNT_SPECIFIC_CONFIGS.get(normalized_to_user_name, {})
        cashback_base_url = str(account_config.get("meituan_official_cashback_url", "") or "").strip()
        if not cashback_base_url:
            log.info(f"未配置meituan_official_cashback_url: {normalized_to_user_name}")
            return ""

        if cashback_base_url.endswith("poi_id_str="):
            full_url = f"{cashback_base_url}{normalized_poi_id_str}"
        elif "poi_id_str=" in cashback_base_url:
            full_url = re.sub(
                r"(poi_id_str=)[^&]*",
                lambda match: f"{match.group(1)}{normalized_poi_id_str}",
                cashback_base_url,
                count=1,
            )
        elif "?" in cashback_base_url:
            full_url = f"{cashback_base_url}&poi_id_str={normalized_poi_id_str}"
        else:
            full_url = f"{cashback_base_url}?poi_id_str={normalized_poi_id_str}"
        log.info(f"拼接官方返现活动路径成功: {full_url}")
        return full_url
    except Exception as e:
        log.warning(f"拼接官方返现活动路径失败: {e}")
        return ""


async def abuild_go_shortlink_html(
    url: str,
    link_text: str,
    logger_instance=None,
    *,
    ttl_seconds: int = CASHBACK_SHORTLINK_TTL_SECONDS,
    fallback_to_long_link: bool = True,
) -> str:
    """
    通过 Go 本地短链服务生成可点击短链接 HTML。

    失败时可回退为原始长链接，避免入口完全不可用。
    """
    log = logger_instance if logger_instance else logger
    full_url = str(url or "").strip()
    normalized_text = str(link_text or "").strip()
    if not full_url or not normalized_text:
        return ""

    try:
        from utils.go_local_api import GO_LOCAL_API_SOCKET_PATH, build_public_shortlink_url, create_shortlink_async

        global _SHORTLINK_SOCKET_MISSING_WARNED
        if GO_LOCAL_API_SOCKET_PATH and not os.path.exists(GO_LOCAL_API_SOCKET_PATH):
            if not _SHORTLINK_SOCKET_MISSING_WARNED:
                log.warning(f"短链接服务 socket 不存在，已回退长链接: {GO_LOCAL_API_SOCKET_PATH}")
                _SHORTLINK_SOCKET_MISSING_WARNED = True
            if fallback_to_long_link:
                return render_clickable_link_html(full_url, normalized_text)
            return ""

        payload = await create_shortlink_async(
            full_url,
            ttl_seconds=int(ttl_seconds),
            timeout=1.5,
        )
        path = str(payload.get("path") or "").strip()
        if not path.startswith("/key/"):
            raise Exception("短链接服务返回 path 无效")
        shortlink_url = build_public_shortlink_url(path)
        log.info(f"创建短链接成功: {shortlink_url}")
        return render_clickable_link_html(shortlink_url, normalized_text)
    except Exception as e:
        log.warning(f"创建短链接失败，url={full_url}, error={e}")
        if fallback_to_long_link:
            return render_clickable_link_html(full_url, normalized_text)
        return ""


async def abuild_meituan_official_cashback_shortlink_url(
    to_user_name: str,
    poi_id_str: str,
    link_text: str = "点击报名该商家「官方返现」活动",
    logger_instance=None,
) -> str:
    """
    生成官方返现活动小程序链接 HTML。
    """
    log = logger_instance if logger_instance else logger
    use_cashback_miniprogram = True

    # 返现小程序逻辑
    if use_cashback_miniprogram:
        cashback_path = build_meituan_official_cashback_url(to_user_name, poi_id_str, log)
        if not cashback_path:
            return ""

        cashback_link_html = (
            f'<a data-miniprogram-appid="{CASHBACK_MINIPROGRAM_APPID}" '
            f'data-miniprogram-path="{cashback_path}" href="">{str(link_text or "").strip()}</a>'
        )
        log.info(f"生成返现小程序链接成功: {cashback_link_html}")
        return cashback_link_html

    #  H5 + Go 短链逻辑
    # full_url = build_meituan_official_cashback_url(to_user_name, poi_id_str, log)
    # if not full_url:
    #     return ""
    
    # return await abuild_go_shortlink_html(
    #     full_url,
    #     str(link_text or "").strip(),
    #     log,
    #     ttl_seconds=CASHBACK_SHORTLINK_TTL_SECONDS,
    #     fallback_to_long_link=False,
    # )


def generate_miniprogram_link(
    meituan_url: str,
    to_user_name: str = "",
    config_override: Optional[Dict[str, Any]] = None,
) -> str:
    """
    生成美团小程序链接
    
    Args:
        meituan_url: 美团优惠链接
        to_user_name: 公众号ID，用于从配置中读取文字
        
    Returns:
        小程序链接HTML
    """
                     
    miniprogram_open_prefix = "领取商家券 (小程序版)"
    if config_override is not None:
        miniprogram_open_prefix = str(
            config_override.get("miniprogram_open_prefix", miniprogram_open_prefix)
        )
    elif to_user_name:
        try:
            from config.config import MINIPROGRAM_CONFIG
            config = MINIPROGRAM_CONFIG.get(to_user_name, {})
            miniprogram_open_prefix = config.get("miniprogram_open_prefix", miniprogram_open_prefix)
        except Exception:
            pass
    
              
    appid = "wx2c348cf579062e56"
                                                                         
    webview_url = urllib.parse.quote(meituan_url, safe='')
    miniprogram_path = f"pages/web-view/web-view?type=DIRECT&webviewUrl={webview_url}"
    logger.info(f"[meituan_utils] 生成小程序路径，路径长度: {len(miniprogram_path)}")
    
                        
                                                                            
                                                                                      
                                                                                                         
    
             
    miniprogram_link = f'<a data-miniprogram-appid="{appid}" data-miniprogram-path="{miniprogram_path}" href="">{miniprogram_open_prefix}</a>\n'
                                                                                                                                                                                                                                                                                        
    logger.info(f"[meituan_utils] 成功生成小程序链接，链接: {miniprogram_link}")
    
    return f"{miniprogram_link}"


def render_clickable_link_html(
    href: str,
    text: str,
    suffix: str = "",
    *,
    prepend_space: bool = False,
) -> str:
    """
    渲染带可选后缀的链接 HTML。

    suffix 会直接拼接在 </a> 后面，不参与任何 join 换行逻辑。
    """
    normalized_href = str(href or "").strip()
    normalized_text = str(text or "")
    normalized_suffix = str(suffix or "")
    prefix = " " if prepend_space else ""
    return f'{prefix}<a href="{normalized_href}">{normalized_text}</a>{normalized_suffix}'


def append_link_suffix(link_html: str, suffix: str = "") -> str:
    """
    给已经生成好的链接 HTML 追加后缀。

    基础链接自身不保留尾部换行；用户配置的 suffix 按原样保留。
    """
    normalized_link_html = str(link_html or "").rstrip("\n")
    normalized_suffix = str(suffix or "")
    if not normalized_suffix:
        return normalized_link_html
    return normalized_link_html + normalized_suffix


def extract_parameter_value(page_path: str, param_name: str, logger_instance=None) -> Optional[str]:
    """
    从page_path中提取指定参数的值
    
    Args:
        page_path: 原始page_path
        param_name: 参数名
        logger_instance: 日志记录器实例，如果为None则使用模块级logger
        
    Returns:
        参数值或None
    """
    log = logger_instance if logger_instance else logger
    
            
    param_pos = page_path.find(f'{param_name}=')
    if param_pos == -1:
        return None
    
              
    value_start = param_pos + len(param_name) + 1
    
                          
    value_end = page_path.find('&', value_start)
    if value_end == -1:
        value_end = len(page_path)
    
         
    value = page_path[value_start:value_end]
    
             
    value = value.replace('\r', '').replace('\n', '')
    
    log.info(f"提取参数 {param_name}: {value}")
    return value


def build_extra_params_url(poi_value: str, page_path: str, logger_instance=None, to_user_name: str = "", mode: str = None) -> Tuple[Optional[str], bool]:
    """
    构建额外的参数URL
    
    即使token和allowance都为null也会构建URL
    
    Args:
        poi_value: poi_id_str的值（不包含参数名）
        page_path: 原始page_path
        logger_instance: 日志记录器实例，如果为None则使用模块级logger
        to_user_name: 公众号ID，用于从配置中读取mode参数
        mode: 模式参数（'a'/'b'/'c'），如果提供则直接使用，否则从配置中读取
        
    Returns:
        (构建的完整URL或None, token是否为null)
    """
    log = logger_instance if logger_instance else logger
    
    try:
        if not poi_value:
            log.warning("poi_value为空")
            return None, False
        
                                                      
        allowance = extract_parameter_value(page_path, 'allowance_alliance_scenes', log)
        if not allowance:
            log.info("未找到allowance_alliance_scenes参数，将设为null")
        
                                  
        token = None
        token_is_null = False
        ad_activity_flag_str = extract_parameter_value(page_path, 'ad_activity_flag', log)
        if ad_activity_flag_str:
                                            
            try:
                             
                ad_activity_flag = json.loads(ad_activity_flag_str)
            except json.JSONDecodeError:
                                      
                try:
                    decoded_flag = urllib.parse.unquote(ad_activity_flag_str)
                    ad_activity_flag = json.loads(decoded_flag)
                except (json.JSONDecodeError, Exception) as e:
                    log.warning(f"解析ad_activity_flag失败: {e}，token将设为null")
                    ad_activity_flag = None
            
                                       
            if ad_activity_flag:
                token = ad_activity_flag.get('token', None)
                if not token:
                    log.warning("ad_activity_flag中未找到token，token将设为null")
                    token_is_null = True
            else:
                token_is_null = True
        else:
            log.info("未找到ad_activity_flag参数，token将设为null")
            token_is_null = True
        
                                              
        i_param = {
            "token": token,
            "allowance": allowance,
            "poi": poi_value
        }
        
                           
        i_param_json = json.dumps(i_param, ensure_ascii=False)
        i_param_encoded = urllib.parse.quote(i_param_json)
        
                                       
        if mode is None:
            mode = "a"       
            if to_user_name:
                try:
                    from config.config import ACCOUNT_SPECIFIC_CONFIGS
                    account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
                    url_mode = account_config.get("url_mode", "all")
                                                           
                    mode_mapping = {
                        "all": "a",
                        "meituan": "b",
                        "dianping": "c"
                    }
                    if url_mode in mode_mapping:
                        mode = mode_mapping[url_mode]
                    else:
                        log.info(f"配置的url_mode值无效: {url_mode}，使用默认值a（all）")
                except Exception as e:
                    log.info(f"从配置读取url_mode失败: {e}，使用默认值a（all）")
        
                           
        base_url = "http://waimaiyouhui.top/dianping"
        full_url = f"{base_url}?i={i_param_encoded}&mode={mode}"
        
        log.info(f"构建额外参数URL成功: {full_url} (mode={mode})")
        return full_url, token_is_null
        
    except Exception as e:
        log.error(f"构建额外参数URL时发生错误: {e}")
        return None, False
