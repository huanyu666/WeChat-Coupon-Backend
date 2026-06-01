"""
美团订单查询处理器

"""
import asyncio
import json
import re
import time
import urllib.parse
from typing import Dict, Any, Optional, Tuple, List
from .stateful_processor import StatefulTextProcessor
from utils import http_client as requests
from utils.response import TextRspMsg
from utils.order_leaderboard_service import (
    arecord_leaderboard_hits,
    get_active_shared_leaderboard_rules,
    get_global_leaderboard_url,
    normalize_timestamp_seconds,
    resolve_primary_leaderboard_url,
)
from utils.go_local_api import (
    fallback_random_millisecond,
    resolve_random_milliseconds_async,
)
from utils.order_rankings_link_crypto import encrypt_rank_payload
from utils.meituan_utils import parse_meituan_shop_link
from utils.order_query_capacity import (
    BUSY_MESSAGE as ORDER_QUERY_BUSY_MESSAGE,
    OrderQueryCapacityBusy,
    acquire_order_query_capacity,
    get_order_query_capacity_stats,
)
from utils.verification_code import (
    get_mt_order_verification_manager,
    get_verification_manager,
    migrate_code_between_pools,
)
from utils.proxy_utils import ProxyUnavailableError


ACTIVATION_CODE_SEPARATOR_RE = re.compile(r"[\s\-—–_·.,，。:：;；/\\|]+")
ACTIVATION_CODE_FORMAT_RE = re.compile(r"^[A-Z0-9]{8,64}$")


class OrderQueryQueueTimeoutError(RuntimeError):
    """订单查询并发名额排队超时。"""



class OrderCandidateLookupError(RuntimeError):
    """订单候选无法进入下一步订单号查询。"""

    def __init__(self, message: str, partial_result: Dict[str, Any], candidate_label: str = ""):
        super().__init__(message)
        self.partial_result = partial_result
        self.candidate_label = str(candidate_label or "").strip()


class MeituanOrderQueryProcessor(StatefulTextProcessor):
    """
    美团订单查询功能处理器

    """
    
    STATE_WAITING_URL = "waiting_url"
    STATE_WAITING_ACTIVATION_CODE = "waiting_activation_code"
    STATE_WAITING_RETRY = "waiting_retry"
    STATE_WAITING_ACCOUNT_CHOICE = "waiting_account_choice"
    INSURANCE_VISIT_SOURCE = "mutualaid_app-others-other1_990845"
    INSURANCE_ORIGIN = "https://insurancex.meituan.com"
    INSURANCE_REFERER = "https://insurancex.meituan.com/"
    REQUEST_TIMEOUT_SECONDS = 1.5
    INSURANCE_REQUEST_TIMEOUT_SECONDS = 1.2
    JCHUNUO_REQUEST_TIMEOUT_SECONDS = 1.2
    ORDER_CENTER_REQUEST_TIMEOUT_SECONDS = 1.2
    ORDER_CENTER_PREFETCH_REQUEST_TIMEOUT_SECONDS = 1.2
    ORDER_CENTER_PREFETCH_PAGES = 1
    ORDER_CENTER_LOOKUP_PAGES = 1
    ORDER_CENTER_PREFETCH_PAGE_LIMIT = 50
    REQUEST_RETRY_ATTEMPTS = 2
    ORDER_CENTER_PREFETCH_RETRY_ATTEMPTS = 1
    ORDER_CENTER_LOOKUP_RETRY_ATTEMPTS = 2
    ORDER_QUERY_CONCURRENCY_LIMIT = 16
    ORDER_QUERY_QUEUE_TIMEOUT_SECONDS = 2.0
    ORDER_QUERY_SLOW_STAGE_WARN_SECONDS = 4.0
    ORDER_CENTER_STAGE_WARN_SECONDS = 3.0
    ORDER_CENTER_PREFETCH_STAGE_WARN_SECONDS = 1.5
    INSURANCE_LIST_STAGE_WARN_SECONDS = 1.5
    EXTERNAL_ORDER_ID_STAGE_WARN_SECONDS = 1.5
    ORDER_QUERY_QUEUE_BUSY_MESSAGE = ORDER_QUERY_BUSY_MESSAGE
    ORDER_QUERY_TOTAL_BUDGET_SECONDS = 13.0
    ORDER_QUERY_RESPONSE_SAFETY_SECONDS = 1.5
    ORDER_QUERY_MIN_STAGE_TIMEOUT_SECONDS = 0.25
    ORDER_QUERY_SIDECAR_MIN_REMAINING_SECONDS = 1.2
    RANDOM_MILLISECOND_TIMEOUT_SECONDS = 0.5
    RANDOM_MILLISECOND_RETRY_ATTEMPTS = 2
    ORDER_QUERY_STAGE_TOTAL_TIMEOUT_SECONDS = 1.5
    ORDER_CENTER_PREFETCH_POST_WAIT_SECONDS = 0.1
    LEADERBOARD_SOFT_WAIT_SECONDS = 0.05
    ORDER_QUERY_PRESSURE_WARNING_OCCUPIED = 19
    ORDER_QUERY_PRESSURE_WARNING_QUEUE_LENGTH = 1
    INSURANCE_FANGXINCHI_BASE_DIFF_MILLISECONDS = 10 * 60 * 1000
    DEFAULT_LEADERBOARD_URL = ""
    GLOBAL_LEADERBOARD_CONFIG_KEY = "global"
    LEGACY_LEADERBOARD_CONFIG_KEY = "gh_81203cdf19a5"
    ACTIVATION_CODE_BYPASS_SENTINEL = "__activation_bypass__"
    INSURANCE_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
        "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
        "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
        "UnifiedPCWindowsWechat(0xf254162e) XWEB/18163 miniProgram/wxde8ac0a21135c07d"
    )
    FIXED_MEITUAN_COUPON_BASE_URL = (
        "https://offsiteact.meituan.com/web/hoae/collection_waimai_v8/index.html"
        "?recallBizId=cpsH5Coupon"
        "&bizId=dd7f2bd8f54b472ab349af75a9f62e63"
        "&mediumSrc1=dd7f2bd8f54b472ab349af75a9f62e63"
        "&scene=CPS_SELF_SRC"
        "&pageSrc1=CPS_SELF_OUT_SRC_H5_LINK"
        "&pageSrc2=dd7f2bd8f54b472ab349af75a9f62e63"
        "&pageSrc3=98e3993be4e1420e91e7fe093beef9d0"
        "&activityId=6"
        "&mediaPvId=dafkdsajffjafdfs"
        "&mediaUserId=10086"
        "&outActivityId=6"
        "&hoaePageV=8"
        "&p=1087755051743285248"
    )
    
    def __init__(self, logger):
        """
        初始化处理器
        
        Args:
            logger: 日志记录器
        """
        super().__init__(logger, state_timeout=600)
        self.activation_manager = get_mt_order_verification_manager()
        self.general_activation_manager = get_verification_manager()
        self.trigger_keywords = ["查询订单", "订单查询", "查订单", "美团接单时间"]
        self._order_query_semaphore = None
        self._order_query_semaphore_loop = None
        self._reload_configs()
        self.logger.info("MeituanOrderQueryProcessor 初始化完成")

    def _normalize_activation_code_input(self, text: str) -> str:
        normalized = str(text or "").strip().upper()
        if not normalized:
            return ""
        return ACTIVATION_CODE_SEPARATOR_RE.sub("", normalized)

    def _is_plausible_activation_code(self, text: str) -> bool:
        normalized = self._normalize_activation_code_input(text)
        return bool(ACTIVATION_CODE_FORMAT_RE.fullmatch(normalized))

    def _reload_configs(self):
        try:
            self.order_leaderboard_config = {
                "leaderboard_url": resolve_primary_leaderboard_url(),
            }
            self.logger.info("MeituanOrderQueryProcessor 排行榜配置重新加载成功")
        except Exception as e:
            self.order_leaderboard_config = {}
            self.logger.error(f"MeituanOrderQueryProcessor 排行榜配置重新加载失败: {e}")

    def _get_order_query_settings(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        account_config = msg.get("_account_config") if isinstance(msg.get("_account_config"), dict) else {}
        order_query_settings = account_config.get("order_query_settings", {}) if isinstance(account_config, dict) else {}
        return order_query_settings if isinstance(order_query_settings, dict) else {}

    def _get_runtime_trigger_keywords(self, msg: Dict[str, Any]) -> List[str]:
        settings = self._get_order_query_settings(msg)
        raw_keywords = settings.get("trigger_keywords", [])
        keywords: List[str] = []
        seen: set[str] = set()
        if isinstance(raw_keywords, list):
            for item in raw_keywords:
                keyword = str(item or "").strip()
                if not keyword or keyword in seen:
                    continue
                seen.add(keyword)
                keywords.append(keyword)
        return keywords or list(self.trigger_keywords)

    def is_trigger_for_message(self, text: str, msg: Dict[str, Any]) -> bool:
        text_lower = str(text or "").lower().strip()
        for keyword in self._get_runtime_trigger_keywords(msg):
            if keyword.lower() in text_lower:
                return True
        return False

    def _get_order_query_text(self, msg: Dict[str, Any], key: str, default: str = "") -> str:
        settings = self._get_order_query_settings(msg)
        value = str(settings.get(key) or "").strip()
        return value or default

    def _get_order_query_int_setting(
        self,
        msg: Dict[str, Any],
        key: str,
        default: int = 0,
        minimum: int = 0,
        maximum: int = 10,
    ) -> int:
        settings = self._get_order_query_settings(msg)
        raw_value = settings.get(key, default)
        try:
            value = int(str(raw_value).strip())
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _render_order_query_template(self, template: str, **kwargs: Any) -> str:
        content = str(template or "")
        for key, value in kwargs.items():
            content = content.replace(f"{{{key}}}", str(value))
        return content

    def _is_activation_code_bypassed_account(self, msg: Dict[str, Any]) -> bool:
        to_user_name = str(msg.get("ToUserName", "") or "").strip()
        if to_user_name == self.LEGACY_LEADERBOARD_CONFIG_KEY:
            return True
        account_config = msg.get("_account_config") if isinstance(msg.get("_account_config"), dict) else {}
        order_query_settings = (
            account_config.get("order_query_settings", {})
            if isinstance(account_config, dict)
            else {}
        )
        return bool(order_query_settings.get("bypass_activation_code"))
    
    def extract_auth_from_url(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """
        从URL中提取 token 和 userId
        
        Args:
            url: 美团链接
            
        Returns:
            (token, userId)
        """
        try:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            token = params.get('token', [None])[0]
            user_id = params.get('userId', [None])[0]
            return token, user_id
        except Exception as e:
            self.logger.error(f"提取认证信息失败: {e}")
            return None, None
    
    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        """
        异步处理触发关键词。
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        self.logger.info(f"[{account_name}] 用户 {user_id} 触发订单查询功能")

        if self._is_activation_code_bypassed_account(msg):
            self.logger.info(f"[{account_name}] 用户 {user_id} 命中免激活码账号，直接进入链接输入流程")
            return await self._ahandle_post_auth_ready(msg, self.ACTIVATION_CODE_BYPASS_SENTINEL)

        valid_code_info = self.activation_manager.has_valid_code(user_id)

        if not valid_code_info:
            self.logger.info(f"[{account_name}] 用户 {user_id} 无可用激活码，进入激活码校验流程")
            self.set_user_state(user_id, self.STATE_WAITING_ACTIVATION_CODE)

            rsp = TextRspMsg(msg)
            settings = self._get_order_query_settings(msg)
            rsp.content = str(settings.get("activation_prompt") or "请输入激活码").strip() or "请输入激活码"
            return rsp

        self.logger.info(f"[{account_name}] 用户 {user_id} 已有有效激活码")
        return await self._ahandle_post_auth_ready(msg, valid_code_info["code"])
    
    def _build_url_request_message(self, msg: Dict[str, Any]) -> TextRspMsg:
        """
        构建请求美团链接的提示消息
        """
        from config.config import KEYWORD_RESPONSES
        
        to_user_name = msg.get("ToUserName", "")
        keyword_responses = KEYWORD_RESPONSES.get(to_user_name, {})
        
        jiedan_time_config = keyword_responses.get("接单时间")
        has_image_config = False
        
        if jiedan_time_config:
            if isinstance(jiedan_time_config, dict):
                if jiedan_time_config.get("type") == "image":
                    has_image_config = True
            elif callable(jiedan_time_config):
                has_image_config = True
        
        rsp = TextRspMsg(msg)
        settings = self._get_order_query_settings(msg)
        content = str(settings.get("url_request_message") or "").strip() or """请输入美团链接：https://passport.meituan.com/useraccount/ilogin登录后右上角复制
         1⃣团团有20-10
        👉http://dpurl.cn/jDkjQsAz
        2⃣这里领商家券
        👉https://kzurl08.cn/7b6FJRR"""
        if has_image_config:
            content += "\n\n💡 提示：点击下方查看如何获取美团链接：\n"
            content += f'<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=接单时间">查看获取链接教程</a>'
        
        rsp.content = content
        return rsp

    def _build_account_choice_message(self, msg: Dict[str, Any], record: Dict[str, Any], activation_code: str) -> TextRspMsg:
        user_id = msg.get("FromUserName", "")
        updated_at = int(record.get("updated_at") or 0)
        updated_text = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated_at)) if updated_at else "未知"
        masked_meituan_user_id = str(record.get("meituan_user_id") or "").strip()
        if len(masked_meituan_user_id) > 4:
            masked_meituan_user_id = f"{masked_meituan_user_id[:2]}***{masked_meituan_user_id[-2:]}"
        self.set_user_state(
            user_id,
            self.STATE_WAITING_ACCOUNT_CHOICE,
            {
                "activation_code": activation_code,
                "saved_token": str(record.get("token") or "").strip(),
                "saved_meituan_user_id": str(record.get("meituan_user_id") or "").strip(),
                "saved_source_url": str(record.get("source_url") or "").strip(),
                "saved_updated_at": updated_at,
            },
        )
        rsp = TextRspMsg(msg)
        rsp.content = self._render_order_query_template(
            self._get_order_query_text(
                msg,
                "account_choice_message_template",
                "检测到你最近 7 天内查询过订单。\n"
                "最近记录时间：{updated_text}\n"
                "最近帐号：{masked_meituan_user_id}\n\n"
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=使用上次记录帐号">1.使用上次记录帐号</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=使用新帐号">2.使用新帐号</a>',
            ),
            updated_text=updated_text,
            masked_meituan_user_id=masked_meituan_user_id or "未知",
        )
        return rsp

    async def _ahandle_post_auth_ready(self, msg: Dict[str, Any], activation_code: str) -> Any:
        from utils.order_query_token_store import get_order_query_token_record

        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        try:
            record = await get_order_query_token_record(user_id)
        except Exception as e:
            self.logger.warning(f"[{account_name}] 查询 Redis 历史 token 失败，降级为手工输入链接: {e}")
            record = None

        if record and str(record.get("token") or "").strip() and str(record.get("meituan_user_id") or "").strip():
            self.logger.info(f"[{account_name}] 用户 {user_id} 命中历史 token 记录")
            return self._build_account_choice_message(msg, record, activation_code)

        self.set_user_state(user_id, self.STATE_WAITING_URL, {
            "activation_code": activation_code
        })
        return self._build_url_request_message(msg)
    
    async def ahandle_state(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        current_state = state.get("state")

        exit_keywords = ["取消", "退出", "返回", "quit", "cancel", "back"]
        if text.strip() in exit_keywords:
            self.clear_user_state(user_id, "用户取消操作")
            rsp = TextRspMsg(msg)
            settings = self._get_order_query_settings(msg)
            rsp.content = str(settings.get("cancel_message") or "已取消查询").strip() or "已取消查询"
            return rsp

        if text.strip() == "接单时间":
            return self._handle_jiedan_time_keyword(msg, state)

        from .stateful_processor import USER_STATES
        if user_id in USER_STATES:
            USER_STATES[user_id]["last_update_time"] = time.time()

        if current_state == self.STATE_WAITING_URL:
            return await self._ahandle_url(msg, text, state)
        if current_state == self.STATE_WAITING_ACCOUNT_CHOICE:
            return await self._ahandle_account_choice(msg, text, state)
        if current_state == self.STATE_WAITING_ACTIVATION_CODE:
            return await self._ahandle_activation_code(msg, text, state)
        if current_state == self.STATE_WAITING_RETRY:
            return await self._ahandle_retry(msg, text, state)

        self.logger.warning(f"[{account_name}] 未知状态: {current_state}")
        self.clear_user_state(user_id, "未知状态")
        return None
    
    async def _ahandle_activation_code(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        """异步处理激活码输入并绑定。"""
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        raw_activation_code = str(text or "").strip()
        activation_code = self._normalize_activation_code_input(raw_activation_code)

        if self._is_activation_code_bypassed_account(msg):
            self.logger.info(f"[{account_name}] 用户 {user_id} 属于免激活码账号，忽略激活码输入并进入链接输入流程")
            self.set_user_state(user_id, self.STATE_WAITING_URL, {
                "activation_code": self.ACTIVATION_CODE_BYPASS_SENTINEL
            })
            return self._build_url_request_message(msg)

        if not self._is_plausible_activation_code(raw_activation_code):
            self.logger.info(
                "[%s] 用户 %s 输入了非激活码格式文本，忽略迁移: raw=%s",
                account_name,
                user_id,
                raw_activation_code,
            )
            rsp = TextRspMsg(msg)
            rsp.content = self._get_order_query_text(
                msg,
                "activation_invalid_format_message",
                "❌ 激活码格式不正确，请重新输入激活码\n\n回复“取消”可退出当前流程",
            )
            return rsp
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 输入激活码: {activation_code}")
        
        try:
            ok, msg_text = migrate_code_between_pools(
                activation_code,
                user_id,
                self.general_activation_manager,
                self.activation_manager,
                logger=self.logger,
            )
        except Exception as e:
            ok = False
            msg_text = f"迁移异常: {e}"
        if not ok:
            try:
                src_codes = self.general_activation_manager.get_all_codes().get(user_id, [])
                dst_codes = self.activation_manager.get_all_codes().get(user_id, [])
                self.logger.warning(f"[{account_name}] migrate fail; src_codes={len(src_codes)}, dst_codes={len(dst_codes)}")
            except Exception as e:
                self.logger.warning(f"[{account_name}] migrate fail, dump codes error: {e}")
            rsp = TextRspMsg(msg)
            rsp.content = self._render_order_query_template(
                self._get_order_query_text(
                    msg,
                    "activation_verify_failed_message",
                    "❌ 激活码验证失败: {error}\n\n请重新输入激活码",
                ),
                error=msg_text,
            )
            return rsp
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 激活码验证成功并已绑定")
        return await self._ahandle_post_auth_ready(msg, activation_code)

    async def _ahandle_account_choice(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        command = text.strip()
        activation_code = str(state.get("activation_code") or "").strip()

        if command == "使用上次记录帐号":
            token = str(state.get("saved_token") or "").strip()
            meituan_user_id = str(state.get("saved_meituan_user_id") or "").strip()
            if not token or not meituan_user_id:
                self.logger.warning(f"[{account_name}] 用户 {user_id} 选择复用历史帐号，但状态参数缺失")
                self.set_user_state(user_id, self.STATE_WAITING_URL, {"activation_code": activation_code})
                return self._build_url_request_message(msg)
            self.logger.info(f"[{account_name}] 用户 {user_id} 复用历史订单查询帐号")
            return await self._aexecute_first_order_query(
                msg,
                token=token,
                meituan_user_id=meituan_user_id,
                activation_code=activation_code,
            )

        if command == "使用新帐号":
            self.logger.info(f"[{account_name}] 用户 {user_id} 选择输入新链接")
            self.set_user_state(user_id, self.STATE_WAITING_URL, {"activation_code": activation_code})
            return self._build_url_request_message(msg)

        return self._build_account_choice_message(
            msg,
            {
                "token": state.get("saved_token", ""),
                "meituan_user_id": state.get("saved_meituan_user_id", ""),
                "source_url": state.get("saved_source_url", ""),
                "updated_at": state.get("saved_updated_at", 0),
            },
            activation_code,
        )
    def _handle_jiedan_time_keyword(self, msg: Dict[str, Any], state: Dict[str, Any]) -> Any:
        """
        处理"接单时间"关键词，返回引导图片
        
        Args:
            msg: 消息字典
            state: 当前状态数据
            
        Returns:
            响应对象（图片或文本）
        """
        from config.config import KEYWORD_RESPONSES
        from utils.response import ImageRspMsg
        
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", "")
        keyword_responses = KEYWORD_RESPONSES.get(to_user_name, {})
        
        jiedan_time_config = keyword_responses.get("接单时间")
        
        if jiedan_time_config:
            if isinstance(jiedan_time_config, dict):
                if jiedan_time_config.get("type") == "image":
                    media_id = jiedan_time_config.get("media_id")
                    if media_id:
                        self.logger.info(f"[{account_name}] 返回接单时间图片，media_id: {media_id}")
                        img_rsp = ImageRspMsg(msg)
                        img_rsp.media_id = media_id
                        return img_rsp
            elif callable(jiedan_time_config):
                try:
                    result = jiedan_time_config(msg)
                    if isinstance(result, ImageRspMsg):
                        self.logger.info(f"[{account_name}] 返回接单时间图片（通过工厂函数）")
                        return result
                except Exception as e:
                    self.logger.warning(f"[{account_name}] 调用接单时间工厂函数失败: {e}")
        
        rsp = TextRspMsg(msg)
        rsp.content = "未配置接单时间图片"
        return rsp

    def _build_retry_message(self, msg: Dict[str, Any], error_message: str) -> TextRspMsg:
        rsp = TextRspMsg(msg)
        rsp.content = self._render_order_query_template(
            self._get_order_query_text(
                msg,
                "retry_message_template",
                "❌ {error}\n\n"
                "可发送「重试」直接重查上一次订单，或发送「取消」退出。\n"
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=重试">重试</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=取消">取消查询</a>',
            ),
            error=error_message,
        )
        return rsp

    def _build_order_query_error_message(self, msg: Dict[str, Any], raw_error: str) -> str:
        normalized_error = self._normalize_user_error(raw_error)
        template_key = "token_expired_message" if self._is_token_expired_error(raw_error) else ""
        if template_key:
            template = self._get_order_query_text(
                msg,
                template_key,
                "❌ 美团登录状态已失效，请重新复制美团链接后再查询。",
            )
            return self._render_order_query_template(template, error=normalized_error, raw_error=raw_error)
        return f"❌ {normalized_error}"

    def _set_retry_state(
        self,
        user_id: str,
        token: str,
        meituan_user_id: str,
        activation_code: str,
        error_message: str,
    ) -> None:
        self.set_user_state(
            user_id,
            self.STATE_WAITING_RETRY,
            {
                "retry_token": token,
                "retry_meituan_user_id": meituan_user_id,
                "retry_activation_code": activation_code,
                "retry_error_message": error_message,
            },
        )

    def _is_retryable_user_error(self, message: str) -> bool:
        normalized = self._normalize_user_error(message)
        return normalized in {
            "网络超时，请稍后重试",
            "网络繁忙，请稍后重试",
            self.ORDER_QUERY_QUEUE_BUSY_MESSAGE,
        }

    async def _ahandle_retry(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        command = text.strip()

        if command != "重试":
            last_error = str(state.get("retry_error_message") or "查询失败").strip()
            self.logger.info(f"[{account_name}] 用户 {user_id} 处于重试状态，收到非重试指令: {command}")
            return self._build_retry_message(msg, last_error)

        token = str(state.get("retry_token") or "").strip()
        meituan_user_id = str(state.get("retry_meituan_user_id") or "").strip()
        activation_code = str(state.get("retry_activation_code") or "").strip()
        if not token or not meituan_user_id:
            self.clear_user_state(user_id, "重试参数缺失")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_order_query_text(
                msg,
                "retry_state_expired_message",
                "❌ 重试参数已失效，请重新发送美团链接",
            )
            return rsp

        self.logger.info(f"[{account_name}] 用户 {user_id} 触发订单查询重试")
        return await self._aexecute_first_order_query(
            msg,
            token=token,
            meituan_user_id=meituan_user_id,
            activation_code=activation_code,
        )

    async def _apersist_order_query_auth(
        self,
        msg: Dict[str, Any],
        token: str,
        meituan_user_id: str,
        source_url: str,
    ) -> None:
        from utils.order_query_token_store import set_order_query_token_record

        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", "")
        await set_order_query_token_record(
            from_user_id=user_id,
            to_user_name=to_user_name,
            account_name=account_name,
            token=token,
            meituan_user_id=meituan_user_id,
            source_url=source_url,
        )
        self.logger.info(f"[{account_name}] 用户 {user_id} 订单查询认证信息已写入 Redis")
    
    async def _ahandle_url(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        activation_code = state.get("activation_code")
        activation_bypassed = activation_code == self.ACTIVATION_CODE_BYPASS_SENTINEL
        
        if not activation_code and not activation_bypassed:
            self.logger.warning(f"[{account_name}] 用户 {user_id} 未携带激活码直接输入链接")
            self.set_user_state(user_id, self.STATE_WAITING_ACTIVATION_CODE)
            rsp = TextRspMsg(msg)
            rsp.content = self._get_order_query_text(
                msg,
                "missing_activation_message",
                "❌ 请先输入激活码\n\n发送「获取激活码」可生成新的激活码。",
            )
            return rsp
        
        url = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 输入链接: {url}")
        
        token, meituan_user_id = self.extract_auth_from_url(url)
        if not token or not meituan_user_id:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_order_query_text(
                msg,
                "invalid_url_message",
                "❌ 无法从链接中提取有效信息，请检查链接格式！回复退出/取消来退出。",
            )
            return rsp

        try:
            await self._apersist_order_query_auth(msg, token, meituan_user_id, url)
        except Exception as e:
            self.logger.warning(f"[{account_name}] 写入 Redis 历史 token 失败，继续执行查询: {e}")

        self.logger.info(f"[{account_name}] 成功提取token和userId")

        return await self._aexecute_first_order_query(
            msg,
            token=token,
            meituan_user_id=meituan_user_id,
            activation_code=activation_code,
        )
    
    async def _aexecute_first_order_query(
        self,
        msg: Dict[str, Any],
        token: str,
        meituan_user_id: str,
        activation_code: str,
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        self.logger.info(f"[{account_name}] 用户 {user_id} 开始查询首个订单")
        query_context = self._build_order_query_context(account_name, user_id, msg)

        try:
            try:
                self.logger.info(
                    f"[{account_name}] 用户 {user_id} 等待订单查询并发名额: "
                    f"source=wechat, timeout={self.ORDER_QUERY_QUEUE_TIMEOUT_SECONDS:.1f}s"
                )
                async with acquire_order_query_capacity(
                    "wechat",
                    identity=f"{account_name}:{user_id}",
                    timeout=self.ORDER_QUERY_QUEUE_TIMEOUT_SECONDS,
                ) as capacity_slot:
                    waited_seconds = capacity_slot.waited_seconds
                    if waited_seconds >= self.ORDER_QUERY_SLOW_STAGE_WARN_SECONDS:
                        self.logger.warning(
                            f"[{account_name}] 用户 {user_id} 订单查询并发等待过慢: "
                            f"source=wechat, waited={waited_seconds:.2f}s, "
                            f"active_global={capacity_slot.active_global}, active_source={capacity_slot.active_source}"
                        )
                    else:
                        self.logger.info(
                            f"[{account_name}] 用户 {user_id} 已拿到订单查询并发名额: "
                            f"source=wechat, waited={waited_seconds:.2f}s, "
                            f"active_global={capacity_slot.active_global}, active_source={capacity_slot.active_source}"
                        )
                    results, success_steps, query_error = await self._aquery_top_orders(
                        token,
                        meituan_user_id,
                        1,
                        msg.get("ToUserName", ""),
                        query_context=query_context,
                    )
            except OrderQueryCapacityBusy as exc:
                global_stats = (exc.stats or {}).get("global") or {}
                source_stats = ((exc.stats or {}).get("sources") or {}).get("wechat") or {}
                self.logger.warning(
                    f"[{account_name}] 用户 {user_id} 订单查询排队超时: "
                    f"source=wechat, active={source_stats.get('active')}/{source_stats.get('limit')}, "
                    f"waiting={source_stats.get('waiting')}, global_active={global_stats.get('active')}/{global_stats.get('limit')}"
                )
                raise OrderQueryQueueTimeoutError(self.ORDER_QUERY_QUEUE_BUSY_MESSAGE) from exc
        except OrderQueryQueueTimeoutError as e:
            self.logger.warning(f"[{account_name}] 订单查询过载返回: {self._format_exception_message(e)}")
            normalized_error = self._normalize_user_error(self._format_exception_message(e))
            self._record_order_query_event(query_context, status="queue_timeout", error=normalized_error)
            self._set_retry_state(user_id, token, meituan_user_id, activation_code, normalized_error)
            return self._build_retry_message(msg, normalized_error)
        except Exception as e:
            self.logger.error(
                f"[{account_name}] 查询订单失败: {self._format_exception_message(e)}, "
                f"last_stage={query_context.get('last_stage') or 'unknown'}, "
                f"stages={self._format_query_stage_summary(query_context)}, "
                f"{self._format_proxy_usage_summary(query_context)}"
            )
            normalized_error = self._normalize_user_error(self._format_exception_message(e))
            self._record_order_query_event(query_context, status="failed", error=normalized_error)
            if self._is_retryable_user_error(self._format_exception_message(e)):
                self._set_retry_state(user_id, token, meituan_user_id, activation_code, normalized_error)
                return self._build_retry_message(msg, normalized_error)
            self.clear_user_state(user_id, "查询失败")
            rsp = TextRspMsg(msg)
            rsp.content = self._build_order_query_error_message(msg, self._format_exception_message(e))
            return rsp

        self.clear_user_state(user_id, "查询完成")

        if query_error:
            normalized_error = self._normalize_user_error(query_error)
            self._record_order_query_event(query_context, status="failed", error=normalized_error)
            if self._is_retryable_user_error(query_error):
                self._set_retry_state(user_id, token, meituan_user_id, activation_code, normalized_error)
                return self._build_retry_message(msg, normalized_error)
            rsp = TextRspMsg(msg)
            rsp.content = self._build_order_query_error_message(msg, query_error)
            return rsp

        leaderboard_hit = None
        leaderboard_task = asyncio.create_task(
            self._arecord_leaderboard_hits(msg, results, query_context=query_context)
        )
        try:
            leaderboard_hit = await asyncio.wait_for(
                asyncio.shield(leaderboard_task),
                timeout=self.LEADERBOARD_SOFT_WAIT_SECONDS,
            )
        except asyncio.TimeoutError:
            self.logger.info(
                f"[{account_name}] 排行榜写入超出软等待窗口，当前响应回退通用入口: "
                f"soft_wait={self.LEADERBOARD_SOFT_WAIT_SECONDS:.2f}s"
            )
            self._attach_background_task_exception_logger(
                leaderboard_task,
                account_name=account_name,
                stage="leaderboard_ingest",
            )
        except Exception as e:
            self.logger.error(
                f"[{account_name}] 排行榜写入失败但已降级: {self._format_exception_message(e)}, "
                f"marker={self._classify_local_sidecar_error(e, 'leaderboard_ingest')}"
            )

        try:
            rsp_content = await self._aformat_results(msg, results, leaderboard_hit, query_context=query_context)
        except Exception as e:
            self.logger.error(
                f"[{account_name}] 查询结果格式化失败: {self._format_exception_message(e)}, "
                f"last_stage={query_context.get('last_stage') or 'unknown'}, "
                f"stages={self._format_query_stage_summary(query_context)}"
            )
            normalized_error = self._normalize_user_error(self._format_exception_message(e))
            self._record_order_query_event(query_context, status="format_failed", error=normalized_error)
            if self._is_retryable_user_error(self._format_exception_message(e)):
                self._set_retry_state(user_id, token, meituan_user_id, activation_code, normalized_error)
                return self._build_retry_message(msg, normalized_error)
            rsp = TextRspMsg(msg)
            rsp.content = self._build_order_query_error_message(msg, self._format_exception_message(e))
            return rsp

        if success_steps > 0 and activation_code != self.ACTIVATION_CODE_BYPASS_SENTINEL:
            consumed_count, exhausted, consume_msg = await self._aconsume_code_uses_partial(
                activation_code,
                success_steps,
                user_id=user_id,
            )
            if consumed_count != success_steps:
                self.logger.warning(
                    f"[{account_name}] 激活码次数不足，已部分扣除: expected={success_steps}, consumed={consumed_count}, msg={consume_msg}"
                )
            elif exhausted:
                self.logger.info(f"[{account_name}] 激活码次数已刚好用完")

        rsp = TextRspMsg(msg)
        rsp.content = rsp_content
        self.logger.info(
            f"[{account_name}] 订单查询完成: elapsed={time.time() - query_context['started_at']:.2f}s, "
            f"remaining={self._get_remaining_budget_seconds(query_context):.2f}s, "
            f"stages={self._format_query_stage_summary(query_context)}, "
            f"{self._format_proxy_usage_summary(query_context)}"
        )
        self.logger.info(
            f"[{account_name}] 订单查询代理汇总: {self._format_proxy_usage_summary(query_context)}, "
            f"proxy_retries={int(query_context.get('proxy_retry_attempts') or 0)}/"
            f"{int(query_context.get('max_proxy_switches') or 0)}"
        )
        self._record_order_query_event(query_context, status="success")
        return rsp
    
    async def _adrain_background_task(self, task: asyncio.Task) -> None:
        if task.done():
            await asyncio.gather(task, return_exceptions=True)
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _attach_background_task_exception_logger(
        self,
        task: asyncio.Task,
        *,
        account_name: str,
        stage: str,
    ) -> None:
        def _done_callback(done_task: asyncio.Task) -> None:
            try:
                exc = done_task.exception()
            except asyncio.CancelledError:
                return
            except Exception as callback_error:
                self.logger.warning(
                    "[%s] 后台任务异常回收失败: stage=%s error=%s",
                    account_name,
                    stage,
                    self._format_exception_message(callback_error),
                )
                return
            if exc is None:
                return
            self.logger.warning(
                "[%s] 后台任务失败但不影响本次响应: stage=%s error=%s",
                account_name,
                stage,
                self._format_exception_message(exc),
            )

        task.add_done_callback(_done_callback)

    async def _arun_order_center_prefetch_task(
        self,
        meituan_user_id: str,
        token: str,
        query_context: Optional[Dict[str, Any]],
        stage_started_at: float,
    ) -> Dict[str, Dict[str, Any]]:
        try:
            result = await self._afetch_order_center_orders_prefetch_with_retry(
                meituan_user_id,
                token,
                query_context=query_context,
            )
            self._record_query_stage(query_context, "order_center_prefetch", stage_started_at)
            return result
        except asyncio.CancelledError as exc:
            self._record_query_stage(
                query_context,
                "order_center_prefetch",
                stage_started_at,
                error=requests.Timeout(
                    f"prefetch_cancelled:{self._format_exception_message(exc) or 'soft_wait_budget'}"
                ),
            )
            raise
        except Exception as exc:
            self._record_query_stage(
                query_context,
                "order_center_prefetch",
                stage_started_at,
                error=exc,
            )
            raise

    async def _aquery_top_orders(
        self,
        token: str,
        meituan_user_id: str,
        max_count: int,
        to_user_name: str = "",
        query_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Dict[str, Any]], int, Optional[str]]:
        insurance_stage_started_at = time.time()
        try:
            order_infos = await self._afetch_insurance_list_page_orders_with_retry(
                token,
                meituan_user_id,
                query_context=query_context,
            )
            self._record_query_stage(query_context, "insurance_list", insurance_stage_started_at)
        except Exception as insurance_error:
            self._record_query_stage(
                query_context,
                "insurance_list",
                insurance_stage_started_at,
                error=insurance_error,
            )
            raise
        success_steps = 1
        valid_infos = [item for item in order_infos if item.get("serviceOrderId")]
        if not valid_infos:
            return [], success_steps, "当前用户没有可查询的订单"

        results: List[Dict[str, Any]] = []
        fallback_result: Optional[Dict[str, Any]] = None
        for order_info in valid_infos[:max_count]:
            candidate_label = str(order_info.get("insName") or "候选订单").strip() or "候选订单"
            candidate_accept_time = order_info.get("acceptTime")
            if candidate_accept_time is None:
                self.logger.warning(
                    "[%s] 跳过未携带接单时间的订单候选: service_order_id=%s, ins_name=%s",
                    query_context.get("account_name", "") if query_context else "",
                    order_info.get("serviceOrderId", ""),
                    candidate_label,
                )
                if fallback_result is None:
                    fallback_result = {
                        "serviceOrderId": str(order_info.get("serviceOrderId") or "").strip(),
                        "orderId": "",
                        "acceptTime": None,
                        "createTime": None,
                        "poi_name": str(order_info.get("poi_name") or "").strip() or "未知商家",
                        "shopLink": "",
                        "poiIdStr": "",
                        "merchantCouponUrl": "",
                        "merchantCouponAvailable": False,
                    }
                continue
            try:
                result, step_count = await self._aquery_service_order(
                    token,
                    meituan_user_id,
                    order_info["serviceOrderId"],
                    candidate_accept_time,
                    order_info.get("poi_name"),
                    to_user_name,
                    skip_create_time_lookup=True,
                    query_context=query_context,
                    candidate_label=candidate_label,
                    raise_on_external_order_lookup_failure=True,
                )
                results.append(result)
                success_steps += step_count
                break
            except OrderCandidateLookupError as candidate_error:
                partial_result = dict(candidate_error.partial_result or {})
                if partial_result and fallback_result is None:
                    fallback_result = partial_result
                self.logger.warning(
                    "[%s] 订单候选回退: candidate=%s, service_order_id=%s, error=%s",
                    query_context.get("account_name", "") if query_context else "",
                    candidate_error.candidate_label or candidate_label,
                    order_info.get("serviceOrderId", ""),
                    self._format_exception_message(candidate_error),
                )

        if not results and fallback_result:
            results.append(fallback_result)

        lookup_targets = [
            str(item.get("orderId") or "").strip()
            for item in results
            if str(item.get("orderId") or "").strip()
        ]
        if lookup_targets:
            try:
                stage_started_at = time.time()
                order_details = await self._alookup_order_create_times_with_retry(
                    meituan_user_id,
                    token,
                    lookup_targets,
                    start_offset=0,
                    query_context=query_context,
                    retry_stage="order_center_lookup_batch",
                )
                self._record_query_stage(query_context, "order_center_lookup_batch", stage_started_at)
                found_count = len(order_details)
                if found_count:
                    success_steps += found_count
                    self._fill_batch_order_details(results, order_details, to_user_name)
            except Exception as lookup_error:
                self._record_query_stage(
                    query_context,
                    "order_center_lookup_batch",
                    stage_started_at,
                    error=lookup_error,
                )
                self.logger.warning(
                    f"批量补查订单创建时间失败，已忽略 - 错误: {self._format_exception_message(lookup_error)}"
                )

        return results, success_steps, None

    async def _aquery_service_order(
        self,
        token: str,
        meituan_user_id: str,
        service_order_id: str,
        accept_time: Optional[int],
        poi_name: Optional[str],
        to_user_name: str = "",
        skip_create_time_lookup: bool = False,
        query_context: Optional[Dict[str, Any]] = None,
        candidate_label: str = "",
        raise_on_external_order_lookup_failure: bool = False,
    ) -> Tuple[Dict[str, Any], int]:
        result = {
            "serviceOrderId": service_order_id,
            "orderId": "",
            "acceptTime": accept_time,
            "createTime": None,
            "poi_name": str(poi_name or "").strip() or "未知商家",
            "shopLink": "",
            "poiIdStr": "",
            "merchantCouponUrl": "",
            "merchantCouponAvailable": False,
        }
        success_steps = 0

        external_order_id = ""
        try:
            stage_started_at = time.time()
            external_order_id = await self._afetch_external_order_id_with_retry(
                token,
                meituan_user_id,
                service_order_id,
                query_context=query_context,
            )
            self._record_query_stage(query_context, "external_order_id", stage_started_at)
            success_steps += 1
            result["orderId"] = external_order_id
        except Exception as e:
            self._record_query_stage(query_context, "external_order_id", stage_started_at, error=e)
            error_message = self._format_exception_message(e)
            self.logger.warning(
                f"服务单号转外卖单号失败，已忽略 - 服务单号: {service_order_id}, "
                f"候选: {candidate_label or 'unknown'}, 错误: {error_message}"
            )
            if raise_on_external_order_lookup_failure:
                raise OrderCandidateLookupError(error_message, result, candidate_label or service_order_id) from e
            return result, success_steps

        if not external_order_id:
            if raise_on_external_order_lookup_failure:
                raise OrderCandidateLookupError(
                    "external_order_id_empty",
                    result,
                    candidate_label or service_order_id,
                )
            return result, success_steps

        if skip_create_time_lookup:
            return result, success_steps

        try:
            stage_started_at = time.time()
            order_details = await self._alookup_order_create_times_with_retry(
                meituan_user_id,
                token,
                [external_order_id],
                start_offset=0,
                query_context=query_context,
                retry_stage="order_center_lookup_single",
            )
            self._record_query_stage(query_context, "order_center_lookup_single", stage_started_at)
            success_steps += 1
            order_detail = order_details.get(external_order_id) or {}
            result["createTime"] = order_detail.get("createTime")
            result["shopLink"] = str(order_detail.get("shopLink") or "").strip()
            merchant_coupon_url, poi_id_str = self._build_merchant_coupon_url(
                result["shopLink"],
                msg_to_user_name=to_user_name,
            )
            result["poiIdStr"] = poi_id_str
            if merchant_coupon_url:
                result["merchantCouponUrl"] = merchant_coupon_url
                result["merchantCouponAvailable"] = True
        except Exception as lookup_error:
            self._record_query_stage(query_context, "order_center_lookup_single", stage_started_at, error=lookup_error)
            self.logger.warning(
                f"补查订单创建时间失败，已忽略 - 订单ID: {external_order_id}, 错误: {self._format_exception_message(lookup_error)}"
            )

        return result, success_steps

    def _fill_batch_order_details(
        self,
        results: List[Dict[str, Any]],
        order_details: Dict[str, Dict[str, Any]],
        to_user_name: str = "",
    ) -> None:
        for result in results:
            external_order_id = str(result.get("orderId") or "").strip()
            if not external_order_id:
                continue
            order_detail = order_details.get(external_order_id) or {}
            self._apply_order_detail_to_result(result, order_detail, to_user_name)

    def _apply_order_detail_to_result(
        self,
        result: Dict[str, Any],
        order_detail: Dict[str, Any],
        to_user_name: str = "",
    ) -> None:
        result["createTime"] = order_detail.get("createTime")
        result["shopLink"] = str(order_detail.get("shopLink") or "").strip()
        merchant_coupon_url, poi_id_str = self._build_merchant_coupon_url(
            result["shopLink"],
            msg_to_user_name=to_user_name,
        )
        result["poiIdStr"] = poi_id_str
        if merchant_coupon_url:
            result["merchantCouponUrl"] = merchant_coupon_url
            result["merchantCouponAvailable"] = True
    
    def _format_results(
        self,
        msg: Dict[str, Any],
        results: list,
        leaderboard_hit: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not results:
            return self._get_order_query_text(msg, "no_result_message", "❌ 没有查询到结果")
        
        lines = []
        success_count = 0
        
        random_millisecond_cache: Dict[Tuple[str, str], Dict[str, int]] = {}

        for item in results:
            if "error" in item:
                lines.append(
                    self._render_order_query_template(
                        self._get_order_query_text(msg, "single_result_error_template", "❌ 查询失败\n错误: {error}"),
                        error=item["error"],
                    )
                )
                continue

            success_count += 1
            poi_name = item.get("poi_name", "未知商家")
            order_info = [f"✅ 商家: {poi_name}"]
            if item.get("orderId"):
                order_info.append(f"外卖单号: {item['orderId']}")
            if item.get("createTime"):
                create_time_str = self._format_time(
                    "create",
                    item["createTime"],
                    item.get("serviceOrderId"),
                    item.get("orderId"),
                    fallback_key=f"create|{item.get('poi_name') or ''}|{item.get('createTime')}",
                    random_millisecond_cache=random_millisecond_cache,
                )
                order_info.append(f"创建时间: {create_time_str}")
            if item.get("acceptTime"):
                accept_time_str = self._format_time(
                    "accept",
                    item["acceptTime"],
                    item.get("serviceOrderId"),
                    item.get("orderId"),
                    fallback_key=f"accept|{item.get('poi_name') or ''}|{item.get('acceptTime')}",
                    random_millisecond_cache=random_millisecond_cache,
                )
                order_info.append(f"接单时间: {accept_time_str}")
            if item.get("merchantCouponAvailable") and item.get("merchantCouponUrl"):
                coupon_shop_name = str(item.get("poi_name") or "店铺").strip() or "店铺"
                order_info.append(
                    f'<a href="{item["merchantCouponUrl"]}">点击领取 {coupon_shop_name}隐藏代金券</a>'
                )

            lines.append("\n".join(order_info))
        
        header = f"📋 查询结果（成功 {success_count}/{len(results)}）\n\n"
        content = header + "\n\n".join(lines)

        leaderboard_url = (
            str((leaderboard_hit or {}).get("leaderboard_url") or "").strip()
            or self._get_leaderboard_url(msg.get("ToUserName", ""))
        )
        global_url = get_global_leaderboard_url()
        if global_url:
            content += (
                "\n\n查看排行榜："
                f'<a href="{global_url}">点击查看</a>'
            )
        elif leaderboard_url:
            if leaderboard_hit:
                personalized_url = self._build_personal_leaderboard_url(leaderboard_url, leaderboard_hit)
                content += (
                    "\n\n您的接单时间已记录排行榜，"
                    f'<a href="{personalized_url}">点击查看排行信息</a>'
                )
            else:
                content += (
                    "\n\n查看排行榜："
                    f'<a href="{leaderboard_url}">点击查看</a>'
                )
        return content

    async def _aformat_results(
        self,
        msg: Dict[str, Any],
        results: list,
        leaderboard_hit: Optional[Dict[str, Any]] = None,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        if not results:
            return self._get_order_query_text(msg, "no_result_message", "❌ 没有查询到结果")

        lines = []
        success_count = 0
        random_millisecond_cache: Dict[Tuple[str, str], Dict[str, int]] = {}

        for item in results:
            if "error" in item:
                lines.append(
                    self._render_order_query_template(
                        self._get_order_query_text(msg, "single_result_error_template", "❌ 查询失败\n错误: {error}"),
                        error=item["error"],
                    )
                )
                continue

            success_count += 1
            poi_name = item.get("poi_name", "未知商家")
            order_info = [f"✅ 商家: {poi_name}"]
            if item.get("orderId"):
                order_info.append(f"外卖单号: {item['orderId']}")
            if item.get("createTime"):
                create_time_str = await self._aformat_time(
                    "create",
                    item["createTime"],
                    item.get("serviceOrderId"),
                    item.get("orderId"),
                    fallback_key=f"create|{item.get('poi_name') or ''}|{item.get('createTime')}",
                    random_millisecond_cache=random_millisecond_cache,
                    query_context=query_context,
                )
                order_info.append(f"创建时间: {create_time_str}")
            if item.get("acceptTime"):
                accept_time_str = await self._aformat_time(
                    "accept",
                    item["acceptTime"],
                    item.get("serviceOrderId"),
                    item.get("orderId"),
                    fallback_key=f"accept|{item.get('poi_name') or ''}|{item.get('acceptTime')}",
                    random_millisecond_cache=random_millisecond_cache,
                    query_context=query_context,
                )
                order_info.append(f"接单时间: {accept_time_str}")
            if item.get("merchantCouponAvailable") and item.get("merchantCouponUrl"):
                coupon_shop_name = str(item.get("poi_name") or "店铺").strip() or "店铺"
                order_info.append(
                    f'<a href="{item["merchantCouponUrl"]}">点击领取 {coupon_shop_name}隐藏代金券</a>'
                )

            lines.append("\n".join(order_info))

        header = f"📋 查询结果（成功 {success_count}/{len(results)}）\n\n"
        content = header + "\n\n".join(lines)

        leaderboard_url = (
            str((leaderboard_hit or {}).get("leaderboard_url") or "").strip()
            or self._get_leaderboard_url(msg.get("ToUserName", ""))
        )
        global_url = get_global_leaderboard_url()
        if global_url:
            content += (
                "\n\n查看排行榜："
                f'<a href="{global_url}">点击查看</a>'
            )
        elif leaderboard_url:
            if leaderboard_hit:
                personalized_url = self._build_personal_leaderboard_url(leaderboard_url, leaderboard_hit)
                content += (
                    "\n\n您的接单时间已记录排行榜，"
                    f'<a href="{personalized_url}">点击查看排行信息</a>'
                )
            else:
                content += (
                    "\n\n查看排行榜："
                    f'<a href="{leaderboard_url}">点击查看</a>'
                )
        return content

    def _format_time(
        self,
        kind: str,
        timestamp: int,
        service_order_id: Optional[str],
        order_id: Optional[str],
        fallback_key: str = "",
        random_millisecond_cache: Optional[Dict[Tuple[str, str], Dict[str, int]]] = None,
    ) -> str:
        from datetime import datetime
        raw_value = int(timestamp)
        if raw_value > 1_000_000_000_000:
            dt = datetime.fromtimestamp(raw_value / 1000)
        else:
            dt = datetime.fromtimestamp(raw_value)
        milliseconds = self._resolve_display_millisecond(
            kind=kind,
            service_order_id=service_order_id,
            order_id=order_id,
            fallback_key=fallback_key,
            random_millisecond_cache=random_millisecond_cache,
        )
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')}.{milliseconds:03d}"

    async def _aformat_time(
        self,
        kind: str,
        timestamp: int,
        service_order_id: Optional[str],
        order_id: Optional[str],
        fallback_key: str = "",
        random_millisecond_cache: Optional[Dict[Tuple[str, str], Dict[str, int]]] = None,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        from datetime import datetime
        raw_value = int(timestamp)
        if raw_value > 1_000_000_000_000:
            dt = datetime.fromtimestamp(raw_value / 1000)
        else:
            dt = datetime.fromtimestamp(raw_value)
        milliseconds = await self._aresolve_display_millisecond(
            kind=kind,
            service_order_id=service_order_id,
            order_id=order_id,
            fallback_key=fallback_key,
            random_millisecond_cache=random_millisecond_cache,
            query_context=query_context,
        )
        return f"{dt.strftime('%Y-%m-%d %H:%M:%S')}.{milliseconds:03d}"

    async def _aresolve_display_millisecond(
        self,
        kind: str,
        service_order_id: Optional[str],
        order_id: Optional[str],
        fallback_key: str,
        random_millisecond_cache: Optional[Dict[Tuple[str, str], Dict[str, int]]] = None,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> int:
        normalized_service_order_id = str(service_order_id or "").strip()
        normalized_order_id = str(order_id or "").strip()
        if not normalized_service_order_id and not normalized_order_id:
            raise RuntimeError("random_ms_missing_identity")

        cache = random_millisecond_cache if random_millisecond_cache is not None else {}
        cache_key = (normalized_service_order_id, normalized_order_id)
        resolved = cache.get(cache_key)
        if resolved is None:
            fallback_resolved = self._build_random_millisecond_fallback(
                normalized_service_order_id,
                normalized_order_id,
                fallback_key,
            )
            if self._should_use_random_millisecond_fallback(query_context):
                cache[cache_key] = fallback_resolved
                resolved = fallback_resolved
            else:
                try:
                    timeout = self._get_required_stage_timeout(
                        self.RANDOM_MILLISECOND_TIMEOUT_SECONDS,
                        query_context,
                        "random_ms",
                    )
                except Exception:
                    cache[cache_key] = fallback_resolved
                    resolved = fallback_resolved
                    timeout = 0.0
            last_error: Optional[Exception] = None
            if resolved is None:
                for attempt in range(self.RANDOM_MILLISECOND_RETRY_ATTEMPTS):
                    stage_started_at = time.time()
                    try:
                        payload = await resolve_random_milliseconds_async(
                            service_order_id=normalized_service_order_id,
                            order_id=normalized_order_id,
                            timeout=timeout,
                        )
                        self._record_query_stage(query_context, "random_ms", stage_started_at)
                        resolved = {
                            "create": int(payload.get("create_random_millisecond") or 0),
                            "accept": int(payload.get("accept_random_millisecond") or 0),
                        }
                        cache[cache_key] = resolved
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                        self._record_query_stage(query_context, "random_ms", stage_started_at, error=exc)
                        if attempt == self.RANDOM_MILLISECOND_RETRY_ATTEMPTS - 1 or not self._is_retryable_exception(exc):
                            marker = self._classify_local_sidecar_error(exc, "random_ms")
                            self.logger.warning(
                                f"获取随机毫秒失败，使用本地稳定回退 - marker={marker}, service_order_id: {normalized_service_order_id}, "
                                f"order_id: {normalized_order_id}, error: {self._format_exception_message(exc)}"
                            )
                            cache[cache_key] = fallback_resolved
                            resolved = fallback_resolved
                            break
            if resolved is None and last_error is not None:
                cache[cache_key] = fallback_resolved
                resolved = fallback_resolved

        if not resolved:
            raise RuntimeError("random_ms_empty_result")
        if isinstance(resolved, dict) and resolved.get("_error"):
            raise RuntimeError(str(resolved.get("_error")))
        if kind not in resolved:
            raise RuntimeError(f"random_ms_missing_kind:{kind}")
        milliseconds = int((resolved or {}).get(kind) or 0)
        if milliseconds < 0 or milliseconds > 999:
            raise RuntimeError(f"random_ms_invalid_value:{milliseconds}")
        return milliseconds

    def _build_random_millisecond_fallback(self, service_order_id: str, order_id: str, fallback_key: str) -> Dict[str, int]:
        identity = service_order_id or order_id or str(fallback_key or "").strip()
        return {
            "create": fallback_random_millisecond(f"create|{identity}"),
            "accept": fallback_random_millisecond(f"accept|{identity}"),
        }

    def _should_use_random_millisecond_fallback(self, query_context: Optional[Dict[str, Any]]) -> bool:
        if query_context and self._get_remaining_budget_seconds(query_context) < self.ORDER_QUERY_SIDECAR_MIN_REMAINING_SECONDS:
            return True
        try:
            capacity = get_order_query_capacity_stats()
            global_stats = capacity.get("global") or {}
            limit = int(global_stats.get("limit") or 0)
            active = int(global_stats.get("active") or 0)
            return bool(limit and active / float(limit) >= 0.8)
        except Exception:
            return False

    def _normalize_timestamp_seconds(self, timestamp: Optional[int]) -> Optional[int]:
        return normalize_timestamp_seconds(timestamp)

    def _get_leaderboard_url(self, to_user_name: str) -> str:
        del to_user_name
        global_url = get_global_leaderboard_url()
        if global_url:
            return global_url
        if not get_active_shared_leaderboard_rules():
            return ""
        return str(self.order_leaderboard_config.get("leaderboard_url", self.DEFAULT_LEADERBOARD_URL)).strip()

    def _build_personal_leaderboard_url(self, base_url: str, leaderboard_hit: Dict[str, Any]) -> str:
        parsed = urllib.parse.urlparse(base_url)
        query_params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        rank_token = encrypt_rank_payload({
            "rule_id": str(leaderboard_hit.get("rule_id") or ""),
            "accept_time": str(leaderboard_hit.get("accept_timestamp") or ""),
            "service_order_id": str(leaderboard_hit.get("service_order_id") or ""),
            "order_id": str(leaderboard_hit.get("order_id") or ""),
        })
        query_params.update({
            "rule_id": str(leaderboard_hit.get("rule_id") or ""),
            "keyword": str(leaderboard_hit.get("keyword") or ""),
            "date": str(leaderboard_hit.get("record_date") or ""),
            "slot_time": str(leaderboard_hit.get("slot_time") or ""),
            "rank_token": rank_token,
        })
        return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query_params)))

    async def _arecord_leaderboard_hits(
        self,
        msg: Dict[str, Any],
        results: List[Dict[str, Any]],
        query_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        timeout = self._get_sidecar_timeout(
            query_context,
            0.8,
            minimum_remaining_seconds=self.ORDER_QUERY_SIDECAR_MIN_REMAINING_SECONDS,
        )
        if timeout is None:
            self.logger.info(
                "排行榜写入跳过: marker=leaderboard_budget_skipped reason=budget_exhausted remaining=%.2fs",
                self._get_remaining_budget_seconds(query_context),
            )
            return None
        stage_started_at = time.time()
        try:
            result = await arecord_leaderboard_hits(
                logger=self.logger,
                results=results,
                to_user_name=msg.get("ToUserName", ""),
                user_id=msg.get("FromUserName", ""),
                account_config=self.order_leaderboard_config,
                ingest_timeout_seconds=timeout,
                deadline_at=query_context.get("deadline_at") if query_context else None,
            )
            self._record_query_stage(query_context, "leaderboard_ingest", stage_started_at)
            return result
        except Exception as exc:
            self._record_query_stage(query_context, "leaderboard_ingest", stage_started_at, error=exc)
            raise

    async def _aget_proxy_config(self):
        from utils.proxy_utils import require_proxy_config_async
        return await require_proxy_config_async()

    async def _abuild_request_proxies(self):
        proxies = await self._aget_proxy_config()
        return proxies if proxies else None

    def _record_proxy_usage(self, query_context: Optional[Dict[str, Any]], proxy_url: str) -> None:
        if not query_context or not proxy_url:
            return
        proxy_usage = query_context.setdefault("proxy_usage", [])
        if not isinstance(proxy_usage, list):
            proxy_usage = []
            query_context["proxy_usage"] = proxy_usage
        proxy_usage.append(proxy_url)

    def _log_proxy_request(self, query_context: Optional[Dict[str, Any]], query_stage: str, proxy_url: str) -> None:
        if not query_context or not proxy_url:
            return
        proxy_usage = query_context.get("proxy_usage")
        normalized_usage = (
            [str(item or "").strip() for item in proxy_usage if str(item or "").strip()]
            if isinstance(proxy_usage, list)
            else []
        )
        unique_usage = list(dict.fromkeys(normalized_usage))
        self.logger.warning(
            "[%s] 订单查询代理使用: stage=%s attempt=%d unique=%d proxy=%s",
            query_context.get("account_name", ""),
            query_stage or "unknown",
            len(normalized_usage),
            len(unique_usage),
            proxy_url,
        )

    def _format_proxy_usage_summary(self, query_context: Optional[Dict[str, Any]]) -> str:
        if not query_context:
            return "proxy_attempts=0 unique_proxies=0"
        proxy_usage = query_context.get("proxy_usage")
        if not isinstance(proxy_usage, list) or not proxy_usage:
            return "proxy_attempts=0 unique_proxies=0"
        normalized_usage = [str(item or "").strip() for item in proxy_usage if str(item or "").strip()]
        if not normalized_usage:
            return "proxy_attempts=0 unique_proxies=0"
        unique_usage = list(dict.fromkeys(normalized_usage))
        return (
            f"proxy_attempts={len(normalized_usage)} "
            f"unique_proxies={len(unique_usage)} "
            f"proxies={','.join(unique_usage)}"
        )

    def _get_proxy_usage_counts(self, query_context: Optional[Dict[str, Any]]) -> Tuple[int, int]:
        if not query_context:
            return 0, 0
        proxy_usage = query_context.get("proxy_usage")
        if not isinstance(proxy_usage, list):
            return 0, 0
        normalized_usage = [str(item or "").strip() for item in proxy_usage if str(item or "").strip()]
        return len(normalized_usage), len(list(dict.fromkeys(normalized_usage)))

    def _classify_order_query_failure_type(self, error: str, query_context: Optional[Dict[str, Any]]) -> str:
        message = str(error or "").lower()
        stage = str((query_context or {}).get("last_stage") or "").lower()
        if not message:
            return ""
        if "460" in message or "proxy authentication" in message:
            return "proxy_auth_invalid"
        if "timeout" in message or "超时" in message or "timed out" in message or "readtimeout" in message:
            return "timeout"
        if "无法获取代理" in message or "网络繁忙" in message or "proxyunavailable" in message:
            return "proxy_unavailable"
        if self._is_token_expired_error(error):
            return "token_invalid"
        if "status=" in message or "code=" in message or "美团" in message:
            return "meituan_reject"
        if stage:
            return f"stage_{stage}"
        return "unknown"

    def _is_token_expired_error(self, message: str) -> bool:
        text = str(message or "").strip()
        lowered = text.lower()
        if not text:
            return False
        token_markers = (
            "登录已过期",
            "登录状态已失效",
            "登录失效",
            "认证失败",
            "token",
        )
        if any(marker in lowered for marker in ("token", "unauthorized")):
            return True
        if any(marker in text for marker in token_markers):
            return True
        return bool(re.search(r"(?:status|code)\s*=\s*(?:400|401)\b", lowered))

    def _resolve_proxy_switch_info(
        self,
        query_context: Optional[Dict[str, Any]],
        proxy_attempts: int,
        unique_proxy_count: int,
    ) -> Tuple[bool | None, str]:
        if not query_context:
            return None, ""
        retry_attempts = int(query_context.get("proxy_retry_attempts") or 0)
        max_switches = int(query_context.get("max_proxy_switches") or 0)
        if retry_attempts <= 0:
            return None, "no_proxy_retry"
        if unique_proxy_count > 1:
            return True, "switched_proxy"
        if proxy_attempts <= 1:
            return False, "retry_stopped_before_next_request"
        if max_switches <= 0:
            return False, "switch_disabled"
        return False, "same_proxy_reused"

    def _record_order_query_event(
        self,
        query_context: Optional[Dict[str, Any]],
        *,
        status: str,
        error: str = "",
    ) -> None:
        if not query_context:
            return
        try:
            from utils.log_event_store import append_order_query_event

            proxy_attempts, unique_proxy_count = self._get_proxy_usage_counts(query_context)
            proxy_switch_effective, proxy_switch_reason = self._resolve_proxy_switch_info(
                query_context,
                proxy_attempts,
                unique_proxy_count,
            )
            append_order_query_event(
                status=status,
                account_name=str(query_context.get("account_name") or ""),
                user_id=str(query_context.get("user_id") or ""),
                elapsed_seconds=time.time() - float(query_context.get("started_at") or time.time()),
                last_stage=str(query_context.get("last_stage") or ""),
                stage_summary=self._format_query_stage_summary(query_context),
                proxy_summary=self._format_proxy_usage_summary(query_context),
                proxy_attempts=proxy_attempts,
                unique_proxy_count=unique_proxy_count,
                proxy_retry_attempts=int(query_context.get("proxy_retry_attempts") or 0),
                max_proxy_switches=int(query_context.get("max_proxy_switches") or 0),
                error=error,
                failure_type=self._classify_order_query_failure_type(error, query_context),
                proxy_switch_effective=proxy_switch_effective,
                proxy_switch_reason=proxy_switch_reason,
            )
        except Exception as exc:
            self.logger.warning("订单查询结构化事件写入失败: %s", self._format_exception_message(exc))

    def _is_proxy_related_exception(self, exc: Exception) -> bool:
        if isinstance(exc, (ProxyUnavailableError, requests.Timeout, requests.ConnectionError)):
            return True
        message = self._format_exception_message(exc).lower()
        proxy_markers = (
            "proxy",
            "timeout",
            "timed out",
            "connect",
            "connection",
            "readtimeout",
            "connecttimeout",
        )
        return any(marker in message for marker in proxy_markers)

    def _is_proxy_auth_invalid_response(self, response: requests.Response) -> bool:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {407, 460}:
            return True
        try:
            text = str(getattr(response, "text", "") or "").lower()
        except Exception:
            text = ""
        return "proxy authentication" in text or "proxy auth" in text

    async def _aproxy_request(self, method: str, url: str, **kwargs):
        from utils.proxy_utils import report_proxy_failure_async

        query_context = kwargs.pop("query_context", None)
        query_stage = str(kwargs.pop("query_stage", "") or "").strip()
        proxies = await self._abuild_request_proxies()
        proxy_url = str((proxies or {}).get("http") or (proxies or {}).get("https") or "").strip()
        self._record_proxy_usage(query_context, proxy_url)
        self._log_proxy_request(query_context, query_stage, proxy_url)
        request_method = getattr(requests, str(method).lower())
        proxy_failure_reported = False
        try:
            response = await request_method(
                url,
                proxies=proxies,
                **kwargs,
            )
            if self._is_proxy_auth_invalid_response(response):
                status_code = int(getattr(response, "status_code", 0) or 0)
                exc = RuntimeError(f"{status_code or 460} Proxy Authentication Invalid")
                if proxy_url:
                    await report_proxy_failure_async(proxy_url, exc)
                    proxy_failure_reported = True
                raise exc
            try:
                response.raise_for_status()
            except Exception as exc:
                if proxy_url and self._is_proxy_related_exception(exc):
                    await report_proxy_failure_async(proxy_url, exc)
                    proxy_failure_reported = True
                raise
            return response, proxy_url
        except requests.LocalResourceExhausted:
            raise
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            if proxy_url and not proxy_failure_reported:
                await report_proxy_failure_async(proxy_url, exc)
            raise
        except Exception as exc:
            if proxy_url and not proxy_failure_reported and self._is_proxy_related_exception(exc):
                await report_proxy_failure_async(proxy_url, exc)
            raise

    async def _amark_proxy_success(self, proxy_url: str) -> None:
        if not proxy_url:
            return
        from utils.proxy_utils import report_proxy_success_async
        await report_proxy_success_async(proxy_url)

    async def _amark_proxy_failure(self, proxy_url: str, exc: Exception) -> None:
        if not proxy_url:
            return
        from utils.proxy_utils import report_proxy_failure_async
        await report_proxy_failure_async(proxy_url, exc)

    async def _aparse_json_payload_with_proxy(
        self,
        response: requests.Response,
        proxy_url: str,
        context: str = "请求",
    ) -> Dict[str, Any]:
        try:
            return self._parse_json_payload(response, context=context)
        except Exception as exc:
            await self._amark_proxy_failure(proxy_url, exc)
            raise

    def _build_insurance_headers(self, token: str, meituan_user_id: str) -> Dict[str, str]:
        ins_user_info = json.dumps({
            "channel": "meituan",
            "source": "meituan",
            "auth": token
        }, ensure_ascii=False, separators=(",", ":"))
        return {
            "Accept": "application/json, text/plain, */*",
            "User-Agent": self.INSURANCE_USER_AGENT,
            "Origin": self.INSURANCE_ORIGIN,
            "Referer": self.INSURANCE_REFERER,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
            "Ins-User-Info": ins_user_info,
            "Cookie": self._build_insurance_cookie(token, meituan_user_id),
        }

    def _build_insurance_cookie(self, token: str, meituan_user_id: str) -> str:
        return "; ".join([
            "ins_user_channel=meituan",
            "ins_user_source=meituan",
            f"ins_user_auth={token}",
            f"ins_bff_token={token}",
            f"ins_bff_passport_userId={meituan_user_id}",
            "ins_city_id=",
            "ins_city_name=",
            f"_lx_utm=utm_source%3D{self.INSURANCE_VISIT_SOURCE}",
        ])

    def _build_jchunuo_headers(self, token: str, meituan_user_id: str, service_order_id: str) -> Dict[str, str]:
        referer_params = urllib.parse.urlencode({
            "orderId": service_order_id,
            "notitlebar": "1",
            "insurance_source": self.INSURANCE_VISIT_SOURCE,
            "token": token,
            "userId": meituan_user_id,
            "userid": meituan_user_id,
            "mina_name": "mt-weapp",
            "noshare": "1",
        })
        return {
            "Accept": "*/*",
            "User-Agent": self.INSURANCE_USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
            "Referer": f"https://www.jchunuo.com/fe/operation/view/insurance-tying/food-safe-order/index.html?{referer_params}",
            "Cookie": self._build_insurance_cookie(token, meituan_user_id),
        }

    def _request_with_retry(self, request_fn):
        last_error = None
        for _ in range(2):
            try:
                return request_fn()
            except Exception as e:
                last_error = e
        raise last_error

    async def _async_request_with_retry(
        self,
        request_fn,
        query_context: Optional[Dict[str, Any]] = None,
        retry_stage: str = "request",
    ):
        stage_budget = self._get_stage_total_timeout_seconds(retry_stage)
        stage_deadline_at = time.time() + self._get_available_stage_total_timeout(
            query_context,
            retry_stage,
            stage_budget,
        )
        self._set_stage_deadline(query_context, retry_stage, stage_deadline_at)
        max_attempts = self._get_retry_attempts_for_stage(retry_stage)
        max_proxy_switches = 0
        proxy_retry_attempts = 0
        generic_retry_attempts = 0
        if query_context:
            max_proxy_switches = max(0, int(query_context.get("max_proxy_switches") or 0))
        last_error = None
        try:
            while True:
                try:
                    self._ensure_budget_for_required_stage(query_context, retry_stage)
                    return await request_fn()
                except Exception as e:
                    last_error = e
                    remaining_stage_budget = self._get_remaining_stage_budget_seconds(query_context, retry_stage)
                    is_proxy_error = self._is_proxy_related_exception(e)
                    if is_proxy_error:
                        if proxy_retry_attempts < max_proxy_switches:
                            proxy_retry_attempts += 1
                            if query_context is not None:
                                query_context["proxy_retry_attempts"] = proxy_retry_attempts
                            self.logger.warning(
                                "[%s] 订单查询代理重试: stage=%s retry=%d/%d error=%s",
                                query_context.get("account_name", "") if query_context else "",
                                retry_stage,
                                proxy_retry_attempts,
                                max_proxy_switches,
                                self._format_exception_message(e),
                            )
                            if remaining_stage_budget >= self.ORDER_QUERY_MIN_STAGE_TIMEOUT_SECONDS:
                                continue
                        break
                    if (
                        not self._is_retryable_exception(e)
                        or generic_retry_attempts >= max(0, max_attempts - 1)
                        or remaining_stage_budget < self.ORDER_QUERY_MIN_STAGE_TIMEOUT_SECONDS
                    ):
                        break
                    generic_retry_attempts += 1
            raise last_error or requests.Timeout(f"{retry_stage}预算不足")
        finally:
            self._clear_stage_deadline(query_context, retry_stage)

    def _get_retry_attempts_for_stage(self, retry_stage: str) -> int:
        if retry_stage == "order_center_prefetch":
            return self.ORDER_CENTER_PREFETCH_RETRY_ATTEMPTS
        if retry_stage.startswith("order_center_lookup"):
            return self.ORDER_CENTER_LOOKUP_RETRY_ATTEMPTS
        return self.REQUEST_RETRY_ATTEMPTS

    async def _afetch_insurance_list_page_orders_with_retry(
        self,
        token: str,
        meituan_user_id: str,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        return await self._async_request_with_retry(
            lambda: self._afetch_insurance_list_page_orders(token, meituan_user_id, query_context=query_context),
            query_context=query_context,
            retry_stage="insurance_list",
        )

    async def _afetch_insurance_list_page_orders(
        self,
        token: str,
        meituan_user_id: str,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        from datetime import datetime
        from utils.timezone_utils import get_timezone

        zone = get_timezone("Asia/Shanghai")
        params = {
            "pageSize": 6,
            "pageNum": 1,
            "queryMonth": datetime.now(zone).strftime("%Y-%m"),
            "monthIndex": 0,
            "statusCode": 0,
            "categoryList": 1,
        }
        response, proxy_url = await self._aproxy_request(
            "get",
            "https://insurance.meituan.com/access-api/center/listPage/orders",
            params=params,
            headers=self._build_insurance_headers(token, meituan_user_id),
            query_context=query_context,
            query_stage="insurance_list",
            timeout=self._get_required_stage_timeout(
                self.INSURANCE_REQUEST_TIMEOUT_SECONDS,
                query_context,
                "insurance_list",
            ),
        )
        payload = await self._aparse_json_payload_with_proxy(response, proxy_url, context="保险订单列表")
        status = str(payload.get("status", "")).strip()
        message = str(payload.get("msg", "")).strip()
        if status != "0":
            error = RuntimeError(f"保险订单列表查询失败(status={status}, msg={message or '空'})")
            if not self._is_token_expired_error(str(error)):
                await self._amark_proxy_failure(proxy_url, error)
            raise error
        await self._amark_proxy_success(proxy_url)

        data = payload.get("data") or {}
        order_list = data.get("orderList") or []
        if not isinstance(order_list, list):
            return []

        first_order_with_time = None
        first_fangxinchi_with_time = None
        fallback_first = None
        for item in order_list:
            if not isinstance(item, dict):
                continue
            order_info = item.get("orderInfo") or {}
            display_info = item.get("displayInfo") or {}
            service_order_id = str(order_info.get("orderId") or "").strip()
            ins_name = str(display_info.get("insName") or "").strip()
            accept_time = order_info.get("effectiveTime")
            normalized_accept_time = None
            if accept_time is not None:
                try:
                    normalized_accept_time = int(accept_time)
                except (TypeError, ValueError):
                    normalized_accept_time = None

            candidate = {
                "serviceOrderId": service_order_id,
                "acceptTime": normalized_accept_time,
                "poi_name": str(display_info.get("merchantName") or "").strip() or "未知商家",
                "insName": ins_name,
            }
            if fallback_first is None:
                fallback_first = candidate
            if service_order_id and normalized_accept_time is not None:
                if first_order_with_time is None:
                    first_order_with_time = candidate
                if ins_name == "放心吃" and first_fangxinchi_with_time is None:
                    first_fangxinchi_with_time = candidate

        primary_candidate = None
        secondary_candidate = None
        if first_order_with_time is not None:
            if str(first_order_with_time.get("insName") or "").strip() == "放心吃":
                primary_candidate = first_order_with_time
            elif first_fangxinchi_with_time is None:
                primary_candidate = first_order_with_time
            else:
                base_accept_time = int(first_order_with_time.get("acceptTime") or 0)
                fangxinchi_accept_time = int(first_fangxinchi_with_time.get("acceptTime") or 0)
                if abs(fangxinchi_accept_time - base_accept_time) <= self.INSURANCE_FANGXINCHI_BASE_DIFF_MILLISECONDS:
                    primary_candidate = first_fangxinchi_with_time
                    secondary_candidate = first_order_with_time
                else:
                    primary_candidate = first_order_with_time
                    secondary_candidate = first_fangxinchi_with_time

        ordered_candidates = [
            candidate
            for candidate in (
                primary_candidate,
                secondary_candidate,
                fallback_first,
            )
            if candidate
        ]
        unique_candidates = []
        seen_service_order_ids = set()
        for candidate in ordered_candidates:
            service_order_id = str(candidate.get("serviceOrderId") or "").strip()
            if not service_order_id or service_order_id in seen_service_order_ids:
                continue
            seen_service_order_ids.add(service_order_id)
            unique_candidates.append(candidate)

        if not unique_candidates:
            return []
        return unique_candidates

    async def _afetch_insurance_notify_infos(self, token: str, meituan_user_id: str) -> List[Dict[str, Any]]:
        url = "https://insurance.meituan.com/access-api/center/homepage/notify"
        response, proxy_url = await self._aproxy_request(
            "get",
            url,
            headers=self._build_insurance_headers(token, meituan_user_id),
            timeout=self.INSURANCE_REQUEST_TIMEOUT_SECONDS,
        )
        payload = await self._aparse_json_payload_with_proxy(response, proxy_url, context="保险通知")
        status = str(payload.get("status", "")).strip()
        message = str(payload.get("msg", "")).strip()
        if status != "0":
            error = RuntimeError(f"保险通知查询失败(status={status}, msg={message or '空'})")
            if not self._is_token_expired_error(str(error)):
                await self._amark_proxy_failure(proxy_url, error)
            raise error
        await self._amark_proxy_success(proxy_url)

        data = payload.get("data") or {}
        if isinstance(data, list):
            infos = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                service_order_id = str(item.get("orderId") or "").strip()
                if service_order_id:
                    infos.append({"serviceOrderId": service_order_id})
            return infos

        service_order_id = str(data.get("orderId") or "").strip()
        if not service_order_id:
            return []
        return [{"serviceOrderId": service_order_id}]

    async def _afetch_insurance_order(self, token: str, meituan_user_id: str, order_id: str) -> Dict[str, Any]:
        params = {
            "visitSource": self.INSURANCE_VISIT_SOURCE,
            "orderId": order_id
        }
        response, proxy_url = await self._aproxy_request(
            "get",
            "https://insurance.meituan.com/access-api/center/homepage/orders",
            params=params,
            headers=self._build_insurance_headers(token, meituan_user_id),
            timeout=self.INSURANCE_REQUEST_TIMEOUT_SECONDS,
        )
        payload = await self._aparse_json_payload_with_proxy(response, proxy_url, context="保险订单详情")
        status = str(payload.get("status", "")).strip()
        message = str(payload.get("msg", "")).strip()
        if status == "-1" and message == "系统繁忙":
            await self._amark_proxy_failure(proxy_url, Exception("订单可能不带放心吃，查询失败"))
            raise Exception("订单可能不带放心吃，查询失败")
        if status != "0":
            error = RuntimeError(f"保险订单详情查询失败(status={status}, msg={message or '空'})")
            if not self._is_token_expired_error(str(error)):
                await self._amark_proxy_failure(proxy_url, error)
            raise error
        await self._amark_proxy_success(proxy_url)
        return payload

    async def _afetch_external_order_id_with_retry(
        self,
        token: str,
        meituan_user_id: str,
        service_order_id: str,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        return await self._async_request_with_retry(
            lambda: self._afetch_external_order_id(
                token,
                meituan_user_id,
                service_order_id,
                query_context=query_context,
            ),
            query_context=query_context,
            retry_stage="external_order_id",
        )

    async def _afetch_external_order_id(
        self,
        token: str,
        meituan_user_id: str,
        service_order_id: str,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        params = {
            "timestamp": str(int(time.time() * 1000)),
            "orderId": service_order_id,
        }
        response, proxy_url = await self._aproxy_request(
            "get",
            "https://www.jchunuo.com/accessapi/access-api/queryOrderInfoNeedToken",
            params=params,
            headers=self._build_jchunuo_headers(token, meituan_user_id, service_order_id),
            query_context=query_context,
            query_stage="external_order_id",
            timeout=self._get_required_stage_timeout(
                self.JCHUNUO_REQUEST_TIMEOUT_SECONDS,
                query_context,
                "external_order_id",
            ),
        )
        payload = await self._aparse_json_payload_with_proxy(response, proxy_url, context="服务单号转外卖单号")
        status = str(payload.get("status", "")).strip()
        message = str(payload.get("msg", "")).strip()
        if status != "0":
            await self._amark_proxy_failure(proxy_url, RuntimeError(f"服务单号转外卖单号失败(status={status}, msg={message or '空'})"))
            raise RuntimeError(f"服务单号转外卖单号失败(status={status}, msg={message or '空'})")
        await self._amark_proxy_success(proxy_url)

        data = payload.get("data") or {}
        attr_list = data.get("attrList") or []
        if not isinstance(attr_list, list):
            return ""
        for item in attr_list:
            if not isinstance(item, dict):
                continue
            if str(item.get("first") or "").strip() == "外卖单号":
                return str(item.get("second") or "").strip()
        return ""

    async def _alookup_order_create_times_with_retry(
        self,
        meituan_user_id: str,
        token: str,
        order_ids,
        start_offset: int = 0,
        query_context: Optional[Dict[str, Any]] = None,
        retry_stage: str = "order_center_lookup",
    ) -> Dict[str, Dict[str, Any]]:
        return await self._async_request_with_retry(
            lambda: self._alookup_order_create_times(
                meituan_user_id,
                token,
                order_ids,
                start_offset=start_offset,
                query_context=query_context,
                retry_stage=retry_stage,
            ),
            query_context=query_context,
            retry_stage=retry_stage,
        )

    async def _afetch_order_center_orders_prefetch_with_retry(
        self,
        meituan_user_id: str,
        token: str,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        return await self._async_request_with_retry(
            lambda: self._afetch_order_center_orders_prefetch(
                meituan_user_id,
                token,
                query_context=query_context,
            ),
            query_context=query_context,
            retry_stage="order_center_prefetch",
        )

    async def _afetch_order_center_orders_prefetch(
        self,
        meituan_user_id: str,
        token: str,
        query_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        headers = self._build_order_list_headers()
        found: Dict[str, Dict[str, Any]] = {}
        offset = 0
        limit = self.ORDER_CENTER_PREFETCH_PAGE_LIMIT
        total = None

        for _ in range(self.ORDER_CENTER_PREFETCH_PAGES):
            params = {
                "userid": meituan_user_id,
                "token": token,
                "offset": offset,
                "limit": limit,
                "platformid": 6,
                "statusFilter": 0,
                "version": 0,
            }
            response, proxy_url = await self._aproxy_request(
                "get",
                "https://ordercenter.meituan.com/ordercenter/user/orders",
                params=params,
                headers=headers,
                query_context=query_context,
                query_stage="order_center_prefetch",
                timeout=self._get_required_stage_timeout(
                    self.ORDER_CENTER_PREFETCH_REQUEST_TIMEOUT_SECONDS,
                    query_context,
                    "order_center_prefetch",
                ),
            )
            payload = await self._aparse_json_payload_with_proxy(response, proxy_url, context="订单中心预取")
            if payload.get("code") in (400, 401):
                await self._amark_proxy_failure(proxy_url, RuntimeError(f"订单中心预取失败(code={payload.get('code')}, msg={payload.get('msg') or '空'})"))
                raise RuntimeError(f"订单中心预取失败(code={payload.get('code')}, msg={payload.get('msg') or '空'})")
            if payload.get("code") != 0:
                await self._amark_proxy_failure(proxy_url, RuntimeError(f"订单中心预取失败(code={payload.get('code')}, msg={payload.get('msg') or '空'})"))
                raise RuntimeError(f"订单中心预取失败(code={payload.get('code')}, msg={payload.get('msg') or '空'})")
            await self._amark_proxy_success(proxy_url)

            data = payload.get("data") or {}
            total = data.get("total", 0) if total is None else total
            orders = data.get("orders") or []
            if not isinstance(orders, list) or not orders:
                break

            for order in orders:
                if not isinstance(order, dict):
                    continue
                order_id = self._extract_order_id(order)
                if not order_id or not order.get("ordertime"):
                    continue
                found[order_id] = {
                    "createTime": int(order.get("ordertime")),
                    "shopLink": str(order.get("shopLink") or "").strip(),
                }

            offset += len(orders)
            if total is not None and offset >= int(total):
                break

        return found

    async def _alookup_order_create_times(
        self,
        meituan_user_id: str,
        token: str,
        order_ids,
        start_offset: int = 0,
        query_context: Optional[Dict[str, Any]] = None,
        retry_stage: str = "order_center_lookup",
    ) -> Dict[str, Dict[str, Any]]:
        targets = {str(order_id).strip() for order_id in order_ids if str(order_id).strip()}
        if not targets:
            return {}

        headers = self._build_order_list_headers()
        found: Dict[str, Dict[str, Any]] = {}
        remaining_targets = set(targets)
        offset = max(0, int(start_offset))
        limit = self.ORDER_CENTER_PREFETCH_PAGE_LIMIT

        for _ in range(self.ORDER_CENTER_LOOKUP_PAGES):
            if not remaining_targets:
                break
            params = {
                "userid": meituan_user_id,
                "token": token,
                "offset": offset,
                "limit": limit,
                "platformid": 6,
                "statusFilter": 0,
                "version": 0,
            }
            response, proxy_url = await self._aproxy_request(
                "get",
                "https://ordercenter.meituan.com/ordercenter/user/orders",
                params=params,
                headers=headers,
                query_context=query_context,
                query_stage=retry_stage,
                timeout=self._get_required_stage_timeout(
                    self.ORDER_CENTER_REQUEST_TIMEOUT_SECONDS,
                    query_context,
                    retry_stage,
                ),
            )
            payload = await self._aparse_json_payload_with_proxy(response, proxy_url, context="订单中心")
            if payload.get("code") in (400, 401):
                raise RuntimeError(f"订单中心查询失败(code={payload.get('code')}, msg={payload.get('msg') or '空'})")
            if payload.get("code") != 0:
                await self._amark_proxy_failure(proxy_url, RuntimeError(f"订单中心查询失败(code={payload.get('code')}, msg={payload.get('msg') or '空'})"))
                raise RuntimeError(f"订单中心查询失败(code={payload.get('code')}, msg={payload.get('msg') or '空'})")
            await self._amark_proxy_success(proxy_url)

            data = payload.get("data") or {}
            orders = data.get("orders") or []
            if not isinstance(orders, list) or not orders:
                break

            for order in orders:
                if not isinstance(order, dict):
                    continue
                order_id = self._extract_order_id(order)
                if order_id in remaining_targets and order.get("ordertime"):
                    found[order_id] = {
                        "createTime": int(order.get("ordertime")),
                        "shopLink": str(order.get("shopLink") or "").strip(),
                    }
                    remaining_targets.discard(order_id)

            if not remaining_targets:
                break

            offset += len(orders)

        return found

    def _build_merchant_coupon_url(self, shop_link: str, msg_to_user_name: str = "") -> Tuple[str, str]:
        parsed_shop_link = parse_meituan_shop_link(shop_link, self.logger)
        poi_id_str = str(parsed_shop_link.get("poi_id_str") or "").strip()
        if not poi_id_str:
            return "", ""
        try:
            full_url = f"{self.FIXED_MEITUAN_COUPON_BASE_URL}&poi_id=-100&poi_id_str={poi_id_str}"
            self.logger.info(f"拼接固定商家券主链接成功: {full_url}")
            return full_url, poi_id_str
        except Exception as e:
            self.logger.warning(f"构建商家券链接失败: {e}, shop_link={shop_link}")
            return "", poi_id_str

    def _build_order_list_headers(self) -> Dict[str, str]:
        return {
            "clientversion": "3.8.12",
            "utm_medium": "",
            "M-APPKEY": "wxmp_mt-weapp",
            "content-type": "application/json",
            "charset": "utf-8",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; SM-G996B Build/TP1A.220905.001; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
                "Chrome/134.0.6998.136 Mobile Safari/537.36 XWEB/1340105 "
                "MMWEBSDK/20250201 MMWEBID/8514 MicroMessenger/8.0.57.2800(0x28003940) "
                "WeChat/arm64 Weixin GPVersion/1 NetType/WIFI Language/zh_CN ABI/arm64 "
                "MiniProgramEnv/android"
            ),
        }

    def _parse_json_payload(self, response: requests.Response, context: str = "请求") -> Dict[str, Any]:
        response.raise_for_status()
        text = response.text.strip()
        if not text or text.startswith("<"):
            raise ValueError(f"{context}响应为空或非JSON")
        try:
            payload = response.json()
        except ValueError:
            raise ValueError(f"{context}响应JSON解析失败")
        if not isinstance(payload, dict):
            raise ValueError(f"{context}响应结构异常")
        return payload

    def _format_exception_message(self, exc: Exception) -> str:
        message = str(exc).strip()
        if not message:
            return exc.__class__.__name__
        return message

    def _is_local_resource_exhausted_exception(self, exc: Exception) -> bool:
        message = self._format_exception_message(exc).lower()
        if isinstance(exc, requests.LocalResourceExhausted):
            return True
        return "too many open files" in message or "emfile" in message or "local_resource_exhausted" in message

    def _is_retryable_exception(self, exc: Exception) -> bool:
        if self._is_local_resource_exhausted_exception(exc):
            return False
        if isinstance(exc, (ProxyUnavailableError, requests.Timeout, requests.ConnectionError)):
            return True
        if isinstance(exc, requests.HTTPError):
            return False
        message = self._format_exception_message(exc).lower()
        retryable_markers = (
            "all connection attempts failed",
            "connection",
            "connect",
            "timeout",
            "timed out",
            "proxy",
        )
        return any(marker in message for marker in retryable_markers)

    def _get_order_query_semaphore(self):
        loop = asyncio.get_running_loop()
        if self._order_query_semaphore is None or self._order_query_semaphore_loop is not loop:
            self._order_query_semaphore = asyncio.Semaphore(self.ORDER_QUERY_CONCURRENCY_LIMIT)
            self._order_query_semaphore_loop = loop
        return self._order_query_semaphore

    def _log_order_query_pressure_if_needed(
        self,
        semaphore: asyncio.Semaphore,
        account_name: str,
        user_id: str,
        stage: str,
    ) -> None:
        try:
            current_value = int(getattr(semaphore, "_value", self.ORDER_QUERY_CONCURRENCY_LIMIT))
            occupied = max(0, self.ORDER_QUERY_CONCURRENCY_LIMIT - current_value)
            usage_ratio = occupied / float(self.ORDER_QUERY_CONCURRENCY_LIMIT or 1)
            waiters = getattr(semaphore, "_waiters", None)
            estimated_queue_length = len(waiters) if waiters is not None else 0
        except Exception:
            return

        if (
            estimated_queue_length < self.ORDER_QUERY_PRESSURE_WARNING_QUEUE_LENGTH
            and occupied < self.ORDER_QUERY_PRESSURE_WARNING_OCCUPIED
        ):
            return

        self.logger.warning(
            f"[{account_name}] 订单查询并发接近打满: stage={stage}, user={user_id}, "
            f"occupied={occupied}/{self.ORDER_QUERY_CONCURRENCY_LIMIT}, "
            f"available={current_value}, estimated_queue_length={estimated_queue_length}"
        )

    def _build_order_query_context(self, account_name: str, user_id: str, msg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        started_at = time.time()
        max_proxy_switches = self._get_order_query_int_setting(msg or {}, "max_proxy_switches", default=2, minimum=0, maximum=10)
        return {
            "account_name": str(account_name or "").strip(),
            "user_id": str(user_id or "").strip(),
            "started_at": started_at,
            "deadline_at": started_at + self.ORDER_QUERY_TOTAL_BUDGET_SECONDS,
            "stage_timings": [],
            "stage_deadlines": {},
            "last_stage": "",
            "proxy_usage": [],
            "proxy_retry_attempts": 0,
            "max_proxy_switches": max_proxy_switches,
        }

    def _get_remaining_budget_seconds(self, query_context: Optional[Dict[str, Any]]) -> float:
        if not query_context:
            return float("inf")
        return max(0.0, float(query_context.get("deadline_at", 0.0)) - time.time())

    def _get_required_stage_timeout(
        self,
        default_timeout: float,
        query_context: Optional[Dict[str, Any]],
        stage: str,
    ) -> float:
        if not query_context:
            return default_timeout
        allowed = self._get_remaining_budget_seconds(query_context) - self.ORDER_QUERY_RESPONSE_SAFETY_SECONDS
        stage_remaining = self._get_remaining_stage_budget_seconds(query_context, stage)
        if stage_remaining != float("inf"):
            allowed = min(allowed, stage_remaining)
        if allowed < self.ORDER_QUERY_MIN_STAGE_TIMEOUT_SECONDS:
            raise requests.Timeout(f"{stage}预算不足")
        return min(default_timeout, allowed)

    def _get_stage_total_timeout_seconds(self, stage: str) -> float:
        if stage == "order_center_prefetch":
            return self.ORDER_CENTER_PREFETCH_REQUEST_TIMEOUT_SECONDS
        if stage == "random_ms":
            return self.RANDOM_MILLISECOND_TIMEOUT_SECONDS * self.RANDOM_MILLISECOND_RETRY_ATTEMPTS
        return self.ORDER_QUERY_STAGE_TOTAL_TIMEOUT_SECONDS

    def _get_available_stage_total_timeout(
        self,
        query_context: Optional[Dict[str, Any]],
        stage: str,
        default_timeout: float,
    ) -> float:
        if not query_context:
            return default_timeout
        allowed = self._get_remaining_budget_seconds(query_context) - self.ORDER_QUERY_RESPONSE_SAFETY_SECONDS
        if allowed < self.ORDER_QUERY_MIN_STAGE_TIMEOUT_SECONDS:
            raise requests.Timeout(f"{stage}预算不足")
        return min(default_timeout, allowed)

    def _set_stage_deadline(
        self,
        query_context: Optional[Dict[str, Any]],
        stage: str,
        deadline_at: float,
    ) -> None:
        if not query_context:
            return
        stage_deadlines = query_context.setdefault("stage_deadlines", {})
        stage_deadlines[stage] = float(deadline_at)

    def _clear_stage_deadline(
        self,
        query_context: Optional[Dict[str, Any]],
        stage: str,
    ) -> None:
        if not query_context:
            return
        stage_deadlines = query_context.get("stage_deadlines") or {}
        stage_deadlines.pop(stage, None)

    def _get_remaining_stage_budget_seconds(
        self,
        query_context: Optional[Dict[str, Any]],
        stage: str,
    ) -> float:
        if not query_context:
            return float("inf")
        stage_deadlines = query_context.get("stage_deadlines") or {}
        deadline_at = stage_deadlines.get(stage)
        if deadline_at is None:
            return float("inf")
        return max(0.0, float(deadline_at) - time.time())

    def _get_sidecar_timeout(
        self,
        query_context: Optional[Dict[str, Any]],
        default_timeout: float,
        minimum_remaining_seconds: Optional[float] = None,
    ) -> Optional[float]:
        if not query_context:
            return default_timeout
        required_remaining = (
            self.ORDER_QUERY_SIDECAR_MIN_REMAINING_SECONDS
            if minimum_remaining_seconds is None
            else minimum_remaining_seconds
        )
        allowed = self._get_remaining_budget_seconds(query_context) - self.ORDER_QUERY_RESPONSE_SAFETY_SECONDS
        if allowed < self.ORDER_QUERY_MIN_STAGE_TIMEOUT_SECONDS or self._get_remaining_budget_seconds(query_context) < required_remaining:
            return None
        return min(default_timeout, allowed)

    def _ensure_budget_for_required_stage(
        self,
        query_context: Optional[Dict[str, Any]],
        stage: str,
    ) -> None:
        self._get_required_stage_timeout(self.ORDER_QUERY_MIN_STAGE_TIMEOUT_SECONDS, query_context, stage)

    def _record_query_stage(
        self,
        query_context: Optional[Dict[str, Any]],
        stage: str,
        started_at: float,
        error: Optional[Exception] = None,
    ) -> None:
        if not query_context:
            return
        elapsed = max(0.0, time.time() - started_at)
        remaining = self._get_remaining_budget_seconds(query_context)
        query_context["last_stage"] = stage
        stage_timings = query_context.setdefault("stage_timings", [])
        stage_timings.append(
            {
                "stage": stage,
                "elapsed": round(elapsed, 3),
                "ok": error is None,
            }
        )
        warn_threshold = self.ORDER_QUERY_SLOW_STAGE_WARN_SECONDS
        if stage == "order_center_prefetch":
            warn_threshold = self.ORDER_CENTER_PREFETCH_STAGE_WARN_SECONDS
        elif stage.startswith("order_center_"):
            warn_threshold = self.ORDER_CENTER_STAGE_WARN_SECONDS
        elif stage == "insurance_list":
            warn_threshold = self.INSURANCE_LIST_STAGE_WARN_SECONDS
        elif stage == "external_order_id":
            warn_threshold = self.EXTERNAL_ORDER_ID_STAGE_WARN_SECONDS
        if error is None:
            if elapsed >= warn_threshold:
                self.logger.warning(
                    "[%s] 订单查询阶段过慢: stage=%s elapsed=%.2fs remaining=%.2fs",
                    query_context.get("account_name", ""),
                    stage,
                    elapsed,
                    remaining,
                )
            else:
                self.logger.info(
                    "[%s] 订单查询阶段完成: stage=%s elapsed=%.2fs remaining=%.2fs",
                    query_context.get("account_name", ""),
                    stage,
                    elapsed,
                    remaining,
                )
        else:
            error_message = self._format_exception_message(error)
            if self._is_local_resource_exhausted_exception(error):
                error_message = f"local_resource_exhausted:{error_message}"
            self.logger.warning(
                "[%s] 订单查询阶段失败: stage=%s elapsed=%.2fs remaining=%.2fs error=%s",
                query_context.get("account_name", ""),
                stage,
                elapsed,
                remaining,
                error_message,
            )

    def _format_query_stage_summary(self, query_context: Optional[Dict[str, Any]]) -> str:
        if not query_context:
            return ""
        parts = []
        for item in query_context.get("stage_timings", []):
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage") or "").strip()
            elapsed = item.get("elapsed")
            suffix = "" if item.get("ok", True) else "!"
            if stage:
                parts.append(f"{stage}={elapsed:.2f}s{suffix}")
        return ", ".join(parts)

    def _classify_local_sidecar_error(self, exc: Exception, sidecar: str) -> str:
        if self._is_local_resource_exhausted_exception(exc):
            return f"{sidecar}_resource_exhausted_local_fd"
        lowered = self._format_exception_message(exc).lower()
        if "timeout" in lowered or "timed out" in lowered or "readtimeout" in lowered or "connecttimeout" in lowered:
            return f"{sidecar}_timeout_local_http"
        return f"{sidecar}_failed"

    async def _aconsume_code_uses_partial(self, code: str, count: int, user_id: str = None):
        return await asyncio.to_thread(
            self.activation_manager.consume_code_uses_partial,
            code,
            count,
            user_id,
        )

    def _extract_insurance_accept_time(self, payload: Dict[str, Any]) -> Optional[int]:
        data = payload.get("data") or []
        if not isinstance(data, list) or not data:
            return None
        first_item = data[0] or {}
        order_info = first_item.get("orderInfo") or {}
        accept_time = order_info.get("effectiveTime")
        if accept_time is None:
            return None
        return int(accept_time)

    def _extract_insurance_merchant_name(self, payload: Dict[str, Any]) -> str:
        data = payload.get("data") or []
        if not isinstance(data, list) or not data:
            return ""
        first_item = data[0] or {}
        display_info = first_item.get("displayInfo") or {}
        return str(display_info.get("merchantName") or "").strip()

    def _extract_order_id(self, order: Dict[str, Any]) -> str:
        string_order_id = str(order.get("stringOrderId") or "").strip()
        if string_order_id:
            return string_order_id
        raw_order_id = order.get("orderid")
        if raw_order_id is None:
            return ""
        return str(raw_order_id).strip()

    def _normalize_user_error(self, message: str) -> str:
        message = (message or "").strip()
        lowered = message.lower()
        if message == "当前用户没有可查询的订单":
            return message
        if message == self.ORDER_QUERY_QUEUE_BUSY_MESSAGE:
            return message
        if "too many open files" in lowered or "emfile" in lowered or "local_resource_exhausted" in lowered:
            return "服务繁忙，请稍后重试"
        if "无法获取代理地址" in message:
            return "网络繁忙，请稍后重试"
        if self._is_token_expired_error(message):
            return "美团登录状态已失效"
        if (
            "超时" in message
            or "timeout" in lowered
            or "timed out" in lowered
            or "all connection attempts failed" in lowered
            or "connect" in lowered
        ):
            return "网络超时，请稍后重试"
        if message == "订单可能不带放心吃，查询失败":
            return "查询订单失败"
        return "查询失败"
