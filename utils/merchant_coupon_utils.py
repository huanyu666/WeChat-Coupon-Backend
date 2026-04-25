"""商家券相关工具函数"""
from typing import Tuple
from utils.meituan_utils import extract_parameter_value
from utils.encrypted_payload_utils import adecrypt_payload, aencrypt_payload


def extract_page_params(page_path: str) -> Tuple[str, str]:
    """从page_path提取allowance_alliance_scenes和ad_activity_flag"""
    allowance = extract_parameter_value(page_path, 'allowance_alliance_scenes', None) or ""
    ad_activity_flag = extract_parameter_value(page_path, 'ad_activity_flag', None) or ""
    return allowance, ad_activity_flag


async def aencrypt_merchant_coupon_data(
    poi_value: str,
    allowance: str,
    ad_activity_flag: str,
    title: str,
    logger=None,
) -> str:
    """异步加密商家券数据。"""
    try:
        payload = {
            "type": "merchant_coupon",
            "poi": poi_value,
            "all": allowance,
            "aaf": ad_activity_flag,
            "tit": title,
        }
        return await aencrypt_payload(payload, logger)
    except Exception as e:
        raise Exception(f"加密商家券数据失败: {e}")


async def adecrypt_merchant_coupon_data(encrypted_str: str, logger=None) -> dict:
    """异步解密商家券数据。"""
    try:
        data = await adecrypt_payload(encrypted_str, logger, expired_message="商家券信息过期请重新生成")
        if data.get("type") != "merchant_coupon":
            raise Exception("保存失败")
        result = {
            "poi_value": data.get("poi", ""),
            "allowance": data.get("all", ""),
            "ad_activity_flag": data.get("aaf", ""),
            "title": data.get("tit", "未知商家"),
        }
        return result
    except Exception as e:
        if str(e) == "商家券信息过期请重新生成":
            raise
        if logger:
            logger.error(f"解密商家券数据失败: {e}", exc_info=True)
        raise Exception("保存失败")
