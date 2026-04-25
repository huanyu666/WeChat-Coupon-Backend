"""返现活动文本处理器。"""
from typing import Any, Dict, Optional

from config.config import ACCOUNT_SPECIFIC_CONFIGS
from utils.meituan_utils import build_meituan_official_cashback_url
from utils.response import TextRspMsg
from utils.cashback_activity_utils import decrypt_cashback_activity_data

from .base_processor import BaseTextProcessor


class CashbackActivityProcessor(BaseTextProcessor):
    """处理返现活动加密口令。"""

    def __init__(self, logger):
        super().__init__(logger)

    def can_handle(self, text: str) -> bool:
        normalized = str(text or "").strip()
        return normalized.startswith("返现活动 ") or normalized.startswith("返现活动 - ")

    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        normalized = str(text or "").strip()
        rsp = TextRspMsg(msg)
        to_user_name = msg.get("ToUserName", "")
        account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
        cashback_base_url = str(account_config.get("meituan_official_cashback_url", "") or "").strip()
        if not cashback_base_url:
            rsp.content = "该公众号暂未配置返现活动"
            return rsp

        token = normalized[len("返现活动"):].strip()
        if token.startswith("-"):
            token = token[1:].strip()
        if not token:
            rsp.content = "返现活动参数无效"
            return rsp

        try:
            data = decrypt_cashback_activity_data(token, self.logger)
            poi_value = data.get("poi_value", "")
            full_url = build_meituan_official_cashback_url(to_user_name, poi_value, self.logger)
            if not full_url:
                rsp.content = "该公众号暂未配置返现活动"
                return rsp
            rsp.content = f'<a href="{full_url}">点击报名该商家「官方返现」活动</a>'
            return rsp
        except Exception as e:
            rsp.content = str(e) if str(e) == "返现活动参数无效" else "返现活动参数无效"
            return rsp
