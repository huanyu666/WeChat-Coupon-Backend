"""
素材管理相关路由
"""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from typing import Any, Optional
from utils import http_client as requests
import json
from pydantic import BaseModel, Field
from config import get_config, get_wechat_accounts
from utils.auth_utils import get_current_user
from utils.wechat_utils import get_access_token, access_token_cache
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path
from utils.verification_code import (
    INFINITE_USES_THRESHOLD,
    get_mt_order_verification_manager,
    parse_duration_string,
)
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_order_relay_pool_config,
    save_system_settings_store,
)
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
SENSITIVE_ACCOUNT_FIELDS = ("app_secret", "token", "encoding_aes_key", "zmkey")
UPLOAD_TYPE_LIMITS = {
    "image": 10 * 1024 * 1024,
    "voice": 2 * 1024 * 1024,
    "video": 10 * 1024 * 1024,
    "thumb": 64 * 1024,
}
UPLOAD_TYPE_EXTENSIONS = {
    "image": {".bmp", ".png", ".jpeg", ".jpg", ".gif"},
    "voice": {".mp3", ".wma", ".wav", ".amr"},
    "video": {".mp4"},
    "thumb": {".jpg", ".jpeg"},
}
UPLOAD_TYPE_CONTENT_TYPES = {
    "image": {"image/bmp", "image/png", "image/jpeg", "image/gif"},
    "voice": {"audio/mpeg", "audio/mp3", "audio/x-ms-wma", "audio/wav", "audio/x-wav", "audio/amr"},
    "video": {"video/mp4"},
    "thumb": {"image/jpeg"},
}


def _audit_admin_action(action: str, operator: str, success: bool, **fields) -> None:
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key in {"account_id", "error", "set_default"}
    }
    log = logger.info if success else logger.warning
    log("admin_audit action=%s operator=%s success=%s fields=%s", action, operator, success, safe_fields)


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
    order_query_code_duration: str = ""
    order_query_authorized_users: list[str] = Field(default_factory=list)
    keyword_responses: list[dict[str, str]] = Field(default_factory=list)
    meituan_miniprogram_config: dict[str, Any] = Field(default_factory=dict)
    meituan_merchant_coupon_view_config: dict[str, Any] = Field(default_factory=dict)
    meituan_miniprogram_link_processor_config: dict[str, Any] = Field(default_factory=dict)
    meituan_link_config: dict[str, Any] = Field(default_factory=dict)
    merchant_coupon_prompts: dict[str, Any] = Field(default_factory=dict)
    order_query_settings: dict[str, Any] = Field(default_factory=dict)
    activation_code_settings: dict[str, Any] = Field(default_factory=dict)
    p_value_settings: dict[str, Any] = Field(default_factory=dict)
    scene_settings: dict[str, Any] = Field(default_factory=dict)
    meituan_shop_query_settings: dict[str, Any] = Field(default_factory=dict)
    click_event_responses: list[dict[str, str]] = Field(default_factory=list)


class OrderQueryCodeGeneratePayload(BaseModel):
    account_id: str
    target_user_id: str
    duration: str = ""
    max_uses: int = Field(default=1, ge=1, le=10000)
    quantity: int = Field(default=1, ge=1, le=50)


class OrderQueryProxyPayload(BaseModel):
    api_url: str = ""
    enable_proxy_pool: bool = True


class OrderRelayPoolPayload(BaseModel):
    strategy: str = "healthy_round_robin"
    request_timeout_seconds: int = Field(default=15, ge=3, le=60)
    failure_cooldown_seconds: int = Field(default=300, ge=30, le=86400)
    consecutive_failure_threshold: int = Field(default=2, ge=1, le=20)
    proxy_mode: str = "direct"
    proxy_retry_count: int = Field(default=2, ge=0, le=3)
    queue_wait_seconds: float = Field(default=3, ge=0.5, le=10)
    nodes: list[dict[str, Any]] = Field(default_factory=list)


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


def _get_effective_click_event_responses() -> dict[str, dict[str, object]]:
    from config.config import (
        CLICK_EVENT_RESPONSES_FILE,
        get_runtime_account_response_map,
        load_toml_file,
        merge_keyword_response_config,
    )

    store_data = load_wechat_account_store()
    return merge_keyword_response_config(
        load_toml_file(CLICK_EVENT_RESPONSES_FILE, {}),
        get_runtime_account_response_map(store_data, "click_event_responses"),
        replace_runtime_accounts=True,
    )


def _serialize_config_section(section_config: object) -> dict[str, Any]:
    if not isinstance(section_config, dict):
        return {}
    return dict(section_config)


def _get_business_config_defaults() -> dict[str, dict[str, Any]]:
    from config.config import (
        get_default_meituan_link_config,
        get_default_meituan_merchant_coupon_view_config,
        get_default_meituan_miniprogram_config,
        get_default_meituan_miniprogram_link_processor_config,
    )

    return {
        "meituan_miniprogram_config": get_default_meituan_miniprogram_config(),
        "meituan_merchant_coupon_view_config": get_default_meituan_merchant_coupon_view_config(),
        "meituan_miniprogram_link_processor_config": get_default_meituan_miniprogram_link_processor_config(),
        "meituan_link_config": get_default_meituan_link_config(),
        "merchant_coupon_prompts": {"list_custom_text": ""},
        "order_query_settings": {
            "enabled": False,
            "bypass_activation_code": False,
            "authorized_users": [],
            "code_duration": "",
            "max_proxy_switches": "2",
            "trigger_keywords": ["查询订单", "订单查询", "查订单", "美团接单时间"],
            "activation_prompt": "请输入激活码",
            "url_request_message": (
                "请输入美团链接：https://passport.meituan.com/useraccount/ilogin登录后右上角复制\n"
                "1⃣团团有20-10\n"
                "👉http://dpurl.cn/jDkjQsAz\n"
                "2⃣这里领商家券\n"
                "👉https://kzurl08.cn/7b6FJRR"
            ),
            "cancel_message": "已取消查询",
            "account_choice_message_template": (
                "检测到你最近 7 天内查询过订单。\n"
                "最近记录时间：{updated_text}\n"
                "最近帐号：{masked_meituan_user_id}\n\n"
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=使用上次记录帐号">1.使用上次记录帐号</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=使用新帐号">2.使用新帐号</a>'
            ),
            "activation_invalid_format_message": "❌ 激活码格式不正确，请重新输入激活码\n\n回复“取消”可退出当前流程",
            "activation_verify_failed_message": "❌ 激活码验证失败: {error}\n\n请重新输入激活码",
            "retry_message_template": (
                "❌ {error}\n\n"
                "可发送「重试」直接重查上一次订单，或发送「取消」退出。\n"
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=重试">重试</a>\n'
                '<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=取消">取消查询</a>'
            ),
            "retry_state_expired_message": "❌ 重试参数已失效，请重新发送美团链接",
            "missing_activation_message": "❌ 请先输入激活码\n\n发送「获取激活码」可生成新的激活码。",
            "invalid_url_message": "❌ 无法从链接中提取有效信息，请检查链接格式！回复退出/取消来退出。",
            "token_expired_message": "❌ 美团登录状态已失效，请重新复制美团链接后再查询。",
            "no_result_message": "❌ 没有查询到结果",
            "single_result_error_template": "❌ 查询失败\n错误: {error}",
        },
        "activation_code_settings": {
            "trigger_keywords": ["获取激活码"],
            "query_keywords": ["查询激活码", "查看激活码", "我的激活码"],
            "delete_keywords": ["删除激活码", "移除激活码", "1+"],
            "activation_help_message": "请在使用\"获取链接\"或\"美团订单查询\"功能时输入此激活码。",
            "no_permission_generate_message": "❌ 您没有权限获取激活码",
            "no_permission_query_message": "❌ 您没有权限查询激活码",
            "no_permission_delete_message": "❌ 您没有权限删除激活码",
            "empty_query_message": "您暂时没有激活码\n\n发送「获取激活码」可生成新的激活码",
            "delete_missing_code_message": "❌ 请指定要删除的激活码\n\n格式: 删除激活码 XXX",
            "generate_single_success_template": (
                "✅ 激活码生成成功！\n\n"
                "激活码: {code}\n"
                "有效期: {expire_text}\n"
                "使用次数: {uses_text}\n\n"
                "类型: {manager_label}\n\n"
                "{activation_help_message}"
            ),
            "generate_multi_success_template": (
                "✅ 已生成 {num_codes} 个激活码！\n\n"
                "有效期: {expire_text}\n"
                "使用次数: {uses_text}\n"
                "类型: {manager_label}\n\n"
                "激活码列表：\n"
                "{code_lines}\n\n"
                "使用任意一个激活码即可进行「获取链接」或「美团订单查询」等操作。"
            ),
        },
        "p_value_settings": {
            "add_keywords": ["添加P值", "新增P值", "绑定P值"],
            "query_keywords": ["查询P值", "查看P值", "我的P值", "P值列表"],
            "update_keywords": ["更新P值", "修改P值", "编辑P值"],
            "delete_keywords": ["删除P值", "移除P值"],
            "switch_keywords": ["切换P值", "使用P值", "设置P值"],
            "activation_required_message": "❌ 当前还没有有效激活码\n\n请先完成激活后再使用P值管理功能。",
            "add_missing_value_message": "❌ 请提供P值\n\n格式：添加P值 <P值> [别名]\n例如：添加P值 123456 别名",
            "duplicate_value_template": "❌ 该P值已存在\n\n别名：{alias}\nP值：{p_value}",
            "duplicate_alias_template": "❌ 别名「{alias}」已被使用\n\n请使用其他别名",
            "add_success_with_alias_template": "✅ P值添加成功\n\n别名：{alias}\nP值：{p_value}\n当前已设置为使用此P值",
            "add_success_without_alias_template": "✅ P值添加成功\n\nP值：{p_value}\n当前已设置为使用此P值",
            "empty_list_message": "您暂时没有绑定P值\n\n发送「添加P值 <P值> [别名]」可添加新的P值",
            "list_header_template": "📋 您的P值列表（共{count}个）：\n",
            "list_current_marker": "⭐",
            "list_alias_fallback": "未设置",
            "list_time_fallback": "未知",
            "list_current_tag": "【当前使用】",
            "list_tips_header": "💡 提示:",
            "list_tip_switch": "• 发送「切换P值 <别名或P值>」切换当前使用的P值",
            "list_tip_delete": "• 发送「删除P值 <别名或P值>」删除指定P值",
            "list_tip_update": "• 发送「更新P值 <别名或P值> <新P值> [新别名]」更新P值",
            "update_missing_args_message": "❌ 请提供别名/P值和新P值\n\n格式：更新P值 <别名或P值> <新P值> [新别名]\n例如：更新P值 别名 123456 新别名",
            "not_found_template": "❌ 未找到P值：{identifier}\n\n请使用别名或P值",
            "update_failed_message": "❌ 更新失败",
            "update_success_with_alias_template": "✅ P值更新成功\n\n新别名：{alias}\n新P值：{p_value}",
            "update_success_without_alias_template": "✅ P值更新成功\n\n新P值：{p_value}",
            "delete_missing_identifier_message": "❌ 请指定要删除的P值\n\n格式：删除P值 <别名或P值>\n例如：删除P值 别名",
            "delete_failed_message": "❌ 删除失败",
            "delete_success_template": "✅ P值删除成功\n\n已删除：{alias}\nP值：{p_value}",
            "switch_missing_identifier_message": "❌ 请指定要切换的P值\n\n格式：切换P值 <别名或P值>\n例如：切换P值 别名",
            "switch_failed_message": "❌ 切换失败",
            "switch_success_template": "✅ 已切换P值\n\n当前使用：{alias}\nP值：{p_value}",
        },
        "scene_settings": {
            "add_keywords": ["添加scene", "新增scene", "绑定scene"],
            "query_keywords": ["查询scene", "查看scene", "我的scene", "scene列表"],
            "update_keywords": ["更新scene", "修改scene", "编辑scene"],
            "delete_keywords": ["删除scene", "移除scene"],
            "switch_keywords": ["切换scene", "使用scene", "设置scene"],
            "activation_required_message": "❌ 当前还没有有效激活码\n\n请先完成激活后再使用scene管理功能。",
            "add_missing_value_message": "❌ 请提供scene\n\n格式：添加scene <scene> [别名]\n例如：添加scene abc123 常用",
            "duplicate_value_template": "❌ 该scene已存在\n\n别名：{alias}\nscene：{scene}",
            "duplicate_alias_template": "❌ 别名「{alias}」已被使用\n\n请使用其他别名",
            "add_success_with_alias_template": "✅ scene添加成功\n\n别名：{alias}\nscene：{scene}\n当前已设置为使用此scene",
            "add_success_without_alias_template": "✅ scene添加成功\n\nscene：{scene}\n当前已设置为使用此scene",
            "empty_list_message": "您暂时没有绑定scene\n\n发送「添加scene <scene> [别名]」可添加新的scene",
            "list_header_template": "📋 您的scene列表（共{count}个）：\n",
            "list_current_marker": "⭐",
            "list_alias_fallback": "未设置",
            "list_time_fallback": "未知",
            "list_current_tag": "【当前使用】",
            "list_tips_header": "💡 提示:",
            "list_tip_switch": "• 发送「切换scene <别名或scene>」切换当前使用的scene",
            "list_tip_delete": "• 发送「删除scene <别名或scene>」删除指定scene",
            "list_tip_update": "• 发送「更新scene <别名或scene> <新scene> [新别名]」更新scene",
            "update_missing_args_message": "❌ 请提供别名/scene和新scene\n\n格式：更新scene <别名或scene> <新scene> [新别名]",
            "not_found_template": "❌ 未找到scene：{identifier}\n\n请使用别名或scene",
            "update_failed_message": "❌ 更新失败",
            "update_success_with_alias_template": "✅ scene更新成功\n\n新别名：{alias}\n新scene：{scene}",
            "update_success_without_alias_template": "✅ scene更新成功\n\n新scene：{scene}",
            "delete_missing_identifier_message": "❌ 请指定要删除的scene\n\n格式：删除scene <别名或scene>",
            "delete_failed_message": "❌ 删除失败",
            "delete_success_template": "✅ scene删除成功\n\n已删除：{alias}\nscene：{scene}",
            "switch_missing_identifier_message": "❌ 请指定要切换的scene\n\n格式：切换scene <别名或scene>",
            "switch_failed_message": "❌ 切换失败",
            "switch_success_template": "✅ 已切换scene\n\n当前使用：{alias}\nscene：{scene}",
        },
        "meituan_shop_query_settings": {
            "enabled": False,
            "trigger_keywords": ["外卖商家查询", "商家查询", "店铺查询"],
            "cancel_keywords": ["取消", "退出", "返回", "quit", "cancel", "back"],
            "intro_message": (
                "1⃣团团40-20和58-25\n\n"
                " 👉http://dpurl.cn/JqA3NkRz\n\n"
                "2⃣团团有20-10\n\n"
                "👉http://dpurl.cn/35yy5s7z\n\n"
                "3⃣大众点评领45-20 38-18等\n\n"
                "👉mp://tIzKEWghrQtKHUv\n\n"
                "📋 外卖商家查询\n\n"
                "请按以下步骤准备信息：\n\n"
                "1 访问以下链接获取坐标：\n"
                "<a href=\"https://www.mapchaxun.cn/Regeo\">点击获取坐标</a>\n\n"
                "2 访问以下链接登录美团：\n"
                "<a href=\"https://passport.meituan.com/useraccount/ilogin\">点击登录美团</a>\n\n"
                "3 准备好后，请发送以下格式的信息：\n"
                "坐标 链接 关键词\n\n"
                "示例格式：\n"
                "999.24591547908291,99.510910831008815 https://i.meituan.com/mttouch/page/account?userId=xxx&token=xxx 华莱士\n\n"
                "（坐标、链接、关键词之间可以用空格、逗号分隔，也可以没有分隔符）"
            ),
            "cancel_message": "已取消查询",
            "return_to_results_message": "已返回商铺选择，您可以继续浏览其他店铺",
            "waiting_miniprogram_message": (
                "⏳ 正在等待您发送小程序或链接...\n\n"
                "请按照以下方式操作：\n\n"
                "方式一：mp://iBLSS1Aa2kGrE5B发送小程序卡片\n"
                "1. 点击刚才的免配链接进入小程序\n"
                "2. 选择店铺并收藏\n"
                "3. 发送收藏的小程序卡片给我mp://lXSt9beJdGtUNin\n\n"
                "方式二：发送链接格式\n"
                "直接发送复制链接得到的 #小程序:// 或 mp:// 格式的链接\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "parse_error_message": (
                "❌ 无法解析输入，请检查格式\n\n"
                "格式：坐标 链接 关键词\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=111.11111111111111,11.111111111111111 https://i.meituan.com/mttouch/page/account?userId=xxx&token=xxx 华莱士\">📤 点击查看示例格式</a>"
            ),
            "missing_info_template": "❌ 缺少必要信息：{missing}\n\n请确保输入包含坐标和用户信息链接",
            "query_failed_template": (
                "❌ 查询失败：{error}\n\n"
                "请检查登录是否过期，或稍后重试\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=商家查询\">🔄 重试</a>"
            ),
            "empty_page_message": (
                "⚠️ 当前页没有店铺\n\n"
                "💡 提示：可能是登录已过期，请重新登录获取链接后重试\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=商家查询\">🔄 重新查询</a>"
            ),
            "no_free_delivery_page_message": (
                "⚠️ 当前页没有免配送费店铺\n\n"
                "💡 提示：可能是登录已过期，请重新登录后查询\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=重新查询\">🔄 重新查询</a>\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=下一页\">➡️ 点击查看下一页</a>"
            ),
            "first_page_message": "已经是第一页了",
            "last_page_message": "已经是最后一页了",
            "sort_help_message": (
                "请选择排序方式：\n"
                "1. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 1\">智能排序</a>\n"
                "2. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 2\">销量优先</a>\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 3\">速度优先</a>\n"
                "4. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 4\">评分优先</a>"
            ),
            "free_delivery_instruction_message": (
                "🚚 获取免配链接\n\n"
                "请按照以下步骤操作：\n\n"
                "方式一：发送小程序卡片\n"
                "1️⃣ 点击下方链接进入小程序：\n"
                "{free_delivery_miniprogram_link}\n"
                "2️⃣ 在小程序中选择店铺并收藏\n"
                "3️⃣ 发送收藏的小程序卡片给我\n\n"
                "方式二：发送链接格式\n"
                "点击上方链接进入小程序后复制 #小程序:// 或 mp:// 格式的链接发送给我\n\n"
                "💡 提示：系统会自动识别并生成免配链接\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "clear_failed_message": "❌ 清空查询条件失败，请重新查询",
            "restart_query_message": (
                "🔄 已清空查询条件，请重新输入查询信息\n\n"
                "格式：坐标 链接 关键词\n\n"
                "示例格式：\n"
                "999.24591547908291,99.510910831008815 https://i.meituan.com/mttouch/page/account?userId=xxx&token=xxx 华莱士\n\n"
                "（坐标、链接、关键词之间可以用空格、逗号分隔，也可以没有分隔符）"
            ),
            "unknown_action_message": (
                "❓ 未识别的操作\n\n"
                "可用操作：\n"
                "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=上一页\">⬅️ 上一页</a> / "
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=下一页\">➡️ 下一页</a>：翻页\n"
                "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 1\">排序 1</a> / "
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 2\">排序 2</a> / "
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 3\">排序 3</a> / "
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 4\">排序 4</a>：修改排序方式\n"
                "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=免配\">🚚 免配</a>：筛选免配送费\n"
                "- 输入关键词：重新搜索"
            ),
            "results_header_template": "📋 查询结果（第 {current_page} 页）{filter_status}\n\n",
            "results_empty_message": "未找到相关店铺\n\n",
            "results_action_title": "📌 操作提示：\n",
            "results_prev_link_text": "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=上一页\">⬅️ 上一页</a>\n",
            "results_next_link_text": "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=下一页\">➡️ 下一页</a>\n",
            "results_sort_links_text": (
                "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 1\">🔢 智能排序</a> | "
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 2\">销量优先</a> | "
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 3\">速度优先</a> | "
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 4\">评分优先</a>\n"
            ),
            "results_toggle_filter_text": "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=免配\">🚚 切换免配筛选</a>\n",
            "results_clear_and_restart_text": (
                "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=清空\">🗑️ 清空查询条件</a> | "
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=重新查询\">🔄 重新查询（更换关键词）</a>\n"
            ),
            "results_cancel_text": "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=取消\">❌ 退出查询</a>\n",
            "shop_link_with_extra_template": (
                "【{shop_name}】\n\n"
                "点击下方链接查看详情：\n"
                "<a href=\"{full_url}\">立即查看商家券,打开后置顶第一个店铺就是你选择的店铺，使用商家卷点外卖更优惠，然后在去下面美团津贴免单跳转美团APP</a>\n\n"
                "{miniprogram_link}\n\n"
                "使用浏览器打开App：\n"
                "<a href=\"{extra_params_url}\">{button_name}</a>\n\n"
                "<a href=\"{free_delivery_link}\">🚚 获取免配链接</a>"
            ),
            "shop_link_without_extra_template": (
                "【{shop_name}】\n\n"
                "点击下方链接查看详情：\n"
                "<a href=\"{full_url}\">立即查看商家券</a>\n\n"
                "{miniprogram_link}\n\n"
                "<a href=\"{free_delivery_link}\">🚚 获取免配链接</a>"
            ),
            "miniprogram_missing_shop_message": (
                "❌ 识别失败：无法从小程序中提取店铺信息\n\n"
                "请确保您发送的是收藏的店铺小程序，而不是其他小程序\n\n"
                "您可以：\n"
                "1. 重新发送小程序卡片\n"
                "2. 发送 #小程序:// 或 mp:// 格式的链接\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "miniprogram_missing_poi_message": "❌ 识别失败：无法提取店铺ID\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>",
            "miniprogram_missing_allowance_message": (
                "❌ 识别失败：无法从小程序中提取allowance信息\n\n"
                "请确保您发送的是收藏的店铺小程序\n\n"
                "您可以：\n"
                "1. 重新发送小程序卡片\n"
                "2. 发送 #小程序:// 或 mp:// 格式的链接\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "miniprogram_missing_token_message": (
                "❌ 识别失败：无法从小程序中提取信息\n\n"
                "请确保您：\n"
                "1. 从发送的链接进入小程序\n"
                "2. 发送的是收藏的店铺小程序\n"
                "3. 小程序中包含完整的店铺信息\n\n"
                "您可以：\n"
                "1. 重新发送小程序卡片\n"
                "2. 发送 #小程序:// 或 mp:// 格式的链接\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "free_delivery_build_failed_message": "❌ 构建免配链接失败\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>",
            "free_delivery_success_template": (
                "✅ 识别成功！\n\n"
                "点击下方链接跳转到免配页面：\n"
                "<a href=\"{free_delivery_url}\">🚚 立即跳转免配</a>\n\n"
                "💡 提示：点击链接后会自动跳转到{button_name}App，享受免配送费优惠\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "link_missing_zmkey_message": (
                "❌ 系统配置错误：请联系管理员\n\n"
                "请联系管理员配置\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "link_unrecognized_message": (
                "❌ 无法识别链接类型\n\n"
                "请确保链接格式正确（#小程序://或mp://开头）\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "link_parse_failed_message": (
                "❌ 解析链接失败\n\n"
                "请确保链接格式正确且有效\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "link_missing_page_message": (
                "❌ 解析链接失败：未获取到参数\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "link_missing_shop_message": (
                "❌ 识别失败：无法从链接中提取店铺信息\n\n"
                "请确保链接包含完整的店铺信息\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "link_missing_poi_message": "❌ 识别失败：无法提取店铺ID\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>",
            "link_missing_allowance_message": (
                "❌ 识别失败：无法从链接中提取信息\n\n"
                "请确保链接包含完整的店铺信息\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
            "link_missing_token_message": (
                "❌ 识别失败：无法从链接中提取信息\n\n"
                "请确保您：\n"
                "1. 从发送的链接进入小程序\n"
                "2. 链接中包含完整的店铺信息\n"
                "3. 链接是从收藏的店铺中获取的\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ),
        },
    }


def _serialize_order_query_code_items(code_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    serialized: list[dict[str, Any]] = []
    for item in code_items:
        if not isinstance(item, dict):
            continue
        serialized.append({
            "code": str(item.get("code") or "").strip(),
            "status": str(item.get("status") or "").strip(),
            "expires_at": str(item.get("expires_at") or "").strip(),
            "remaining_hours": item.get("remaining_hours"),
            "remaining_minutes": item.get("remaining_minutes"),
            "remaining_seconds": item.get("remaining_seconds"),
            "max_uses": item.get("max_uses"),
            "used_count": int(item.get("used_count") or 0),
            "remaining_uses": item.get("remaining_uses"),
            "created_at": str(item.get("created_at") or "").strip(),
            "bound_user": str(item.get("bound_user") or "").strip(),
        })
    return serialized


def _collect_order_query_codes(authorized_users: list[str]) -> list[dict[str, Any]]:
    manager = get_mt_order_verification_manager()
    collected: list[dict[str, Any]] = []
    for user_id in authorized_users:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            continue
        for code_info in manager.get_all_codes_for_user(normalized_user_id):
            enriched = dict(code_info)
            enriched.setdefault("bound_user", normalized_user_id)
            collected.append(enriched)
    collected.sort(
        key=lambda item: (
            0 if str(item.get("status") or "") == "有效" else 1,
            str(item.get("created_at") or ""),
            str(item.get("code") or ""),
        )
    )
    return collected


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
    effective_click_event_responses = _get_effective_click_event_responses()
    from config.config import (
        MEITUAN_LINK_CONFIG,
        MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG,
        MERCHANT_COUPON_PROMPTS,
        MINIPROGRAM_CONFIG,
    )

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
            "app_secret_configured": bool(str(config.get("app_secret", "") or "").strip()),
            "token_configured": bool(str(config.get("token", "") or "").strip()),
            "encoding_aes_key_configured": bool(str(config.get("encoding_aes_key", "") or "").strip()),
            "zmkey_configured": bool(str(config.get("zmkey", "") or "").strip()),
            "welcome_message": specific_config.get("welcome_message", ""),
            "default_reply": specific_config.get("default_reply", ""),
            "enabled_text_processors": list(specific_config.get("enabled_text_processors", [])),
            "enabled_miniprogram_appids": list(specific_config.get("enabled_miniprogram_appids", [])),
            "meituan_base_url": specific_config.get("meituan_base_url", ""),
            "meituan_official_cashback_url": specific_config.get("meituan_official_cashback_url", ""),
            "url_mode": specific_config.get("url_mode", "all"),
            "authorized_users": list(specific_config.get("authorized_users", [])),
            "default_code_duration": specific_config.get("default_code_duration", ""),
            "order_query_code_duration": specific_config.get("order_query_code_duration", ""),
            "keyword_responses": _serialize_keyword_response_items(effective_keyword_responses.get(account_id, {})),
            "click_event_responses": _serialize_keyword_response_items(effective_click_event_responses.get(account_id, {})),
            "meituan_miniprogram_config": _serialize_config_section(MINIPROGRAM_CONFIG.get(account_id, {})),
            "meituan_merchant_coupon_view_config": _serialize_config_section(
                MINIPROGRAM_CONFIG.get(f"{account_id}_merchant_coupon_view", {})
            ),
            "meituan_miniprogram_link_processor_config": _serialize_config_section(
                MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG.get(account_id, {})
            ),
            "meituan_link_config": _serialize_config_section(MEITUAN_LINK_CONFIG.get(account_id, {})),
            "merchant_coupon_prompts": _serialize_config_section(MERCHANT_COUPON_PROMPTS.get(account_id, {})),
            "order_query_settings": {
                **_get_business_config_defaults()["order_query_settings"],
                **_serialize_config_section(specific_config.get("order_query_settings", {})),
            },
            "activation_code_settings": {
                **_get_business_config_defaults()["activation_code_settings"],
                **_serialize_config_section(specific_config.get("activation_code_settings", {})),
            },
            "p_value_settings": {
                **_get_business_config_defaults()["p_value_settings"],
                **_serialize_config_section(specific_config.get("p_value_settings", {})),
            },
            "scene_settings": {
                **_get_business_config_defaults()["scene_settings"],
                **_serialize_config_section(specific_config.get("scene_settings", {})),
            },
            "meituan_shop_query_settings": {
                **_get_business_config_defaults()["meituan_shop_query_settings"],
                **_serialize_config_section(specific_config.get("meituan_shop_query_settings", {})),
            },
            "is_default": account_id == default_account_id,
        })
    for account in accounts:
        order_query_settings = account.get("order_query_settings", {})
        order_query_authorized_users = list(order_query_settings.get("authorized_users", []))
        account["order_query_codes"] = _serialize_order_query_code_items(
            _collect_order_query_codes(order_query_authorized_users)
        )
    accounts.sort(key=lambda item: (not item["is_default"], item["name"], item["id"]))
    return {
        "default_account_id": default_account_id,
        "accounts": accounts,
        "processor_options": _get_text_processor_options(),
        "miniprogram_options": _get_miniprogram_options(),
        "url_mode_options": URL_MODE_OPTIONS,
        "business_config_defaults": _get_business_config_defaults(),
    }


def _build_account_config_payload(payload: WechatAccountPayload, existing_config: dict | None = None) -> dict[str, str]:
    existing = existing_config if isinstance(existing_config, dict) else {}
    account_config = {
        "name": str(payload.name or "").strip(),
        "appid": str(payload.appid or "").strip(),
    }
    for field in SENSITIVE_ACCOUNT_FIELDS:
        raw_value = getattr(payload, field, "")
        value = str(raw_value or "").strip()
        if not value and existing:
            value = str(existing.get(field, "") or "").strip()
        account_config[field] = value
    return account_config


def _json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": message}, status_code=status_code)


def _normalize_order_query_settings(payload: WechatAccountPayload) -> dict[str, Any]:
    raw_settings = payload.order_query_settings if isinstance(payload.order_query_settings, dict) else {}
    authorized_users = raw_settings.get("authorized_users", payload.order_query_authorized_users)
    if not isinstance(authorized_users, list):
        authorized_users = payload.order_query_authorized_users
    raw_section = dict(raw_settings)
    raw_section["authorized_users"] = authorized_users
    raw_section["code_duration"] = str(raw_settings.get("code_duration", payload.order_query_code_duration) or "").strip()
    return _normalize_section_payload(raw_section, "order_query_settings")


def _normalize_section_payload(raw_section: Any, section_name: str) -> dict[str, Any]:
    from wechat_account_store import ACCOUNT_SPECIFIC_SECTION_FIELD_SPECS, normalize_account_specific_section_config

    field_spec = ACCOUNT_SPECIFIC_SECTION_FIELD_SPECS.get(section_name, {})
    return normalize_account_specific_section_config(
        raw_section,
        text_fields=field_spec.get("text", ()),
        bool_fields=field_spec.get("bool", ()),
        list_fields=field_spec.get("list", ()),
    )


def _normalize_enabled_processors(
    enabled_text_processors: list[str],
    order_query_enabled: bool,
    meituan_shop_query_enabled: bool,
) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in enabled_text_processors:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)

    managed_processors = {
        "meituan_order_query": order_query_enabled,
        "meituan_shop_query": meituan_shop_query_enabled,
    }
    for processor_key, is_enabled in managed_processors.items():
        if is_enabled:
            if processor_key not in seen:
                normalized.append(processor_key)
        else:
            normalized = [item for item in normalized if item != processor_key]
    return normalized


def _normalize_upload_filename(filename: str | None) -> str:
    normalized = Path(str(filename or "media")).name.strip()
    return normalized or "media"


async def _read_and_validate_material_upload(file: UploadFile, material_type: str) -> tuple[bytes, str, str]:
    normalized_type = str(material_type or "").strip().lower()
    if normalized_type not in UPLOAD_TYPE_LIMITS:
        raise ValueError("素材类型不支持")

    filename = _normalize_upload_filename(file.filename)
    extension = Path(filename).suffix.lower()
    content_type = str(file.content_type or "").split(";", 1)[0].strip().lower()
    valid_extensions = UPLOAD_TYPE_EXTENSIONS[normalized_type]
    valid_content_types = UPLOAD_TYPE_CONTENT_TYPES[normalized_type]
    if extension not in valid_extensions and content_type not in valid_content_types:
        supported = ", ".join(sorted(valid_extensions))
        raise ValueError(f"{normalized_type} 素材格式不支持，仅支持: {supported}")

    max_size = UPLOAD_TYPE_LIMITS[normalized_type]
    file_content = await file.read(max_size + 1)
    if not file_content:
        raise ValueError("上传文件不能为空")
    if len(file_content) > max_size:
        raise ValueError(f"上传文件过大，{normalized_type} 最大允许 {max_size // 1024}KB")

    return file_content, filename, content_type or "application/octet-stream"


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
        _audit_admin_action("wechat_account_save", current_user, False, error="missing_account_id")
        return JSONResponse({
            "success": False,
            "error": "公众号原始ID不能为空"
        }, status_code=400)

    if not str(payload.appid or "").strip():
        _audit_admin_action("wechat_account_save", current_user, False, account_id=account_id, error="missing_appid")
        return JSONResponse({
            "success": False,
            "error": "AppID 不能为空"
        }, status_code=400)

    try:
        parsed_keyword_responses = _parse_keyword_response_items(payload.keyword_responses)
        parsed_click_event_responses = _parse_keyword_response_items(payload.click_event_responses)
    except ValueError as exc:
        _audit_admin_action("wechat_account_save", current_user, False, account_id=account_id, error=str(exc))
        return JSONResponse({
            "success": False,
            "error": str(exc)
        }, status_code=400)

    normalized_order_query_settings = _normalize_order_query_settings(payload)
    normalized_activation_code_settings = _normalize_section_payload(
        payload.activation_code_settings,
        "activation_code_settings",
    )
    normalized_p_value_settings = _normalize_section_payload(
        payload.p_value_settings,
        "p_value_settings",
    )
    normalized_scene_settings = _normalize_section_payload(
        payload.scene_settings,
        "scene_settings",
    )
    normalized_meituan_shop_query_settings = _normalize_section_payload(
        payload.meituan_shop_query_settings,
        "meituan_shop_query_settings",
    )
    normalized_enabled_processors = _normalize_enabled_processors(
        payload.enabled_text_processors,
        bool(normalized_order_query_settings.get("enabled")),
        bool(normalized_meituan_shop_query_settings.get("enabled")),
    )

    store_data_before = load_wechat_account_store()
    existing_config = {}
    if isinstance(store_data_before.get("accounts"), dict):
        existing_config = store_data_before["accounts"].get(account_id, {})

    store_data = upsert_wechat_account(
        account_id,
        _build_account_config_payload(payload, existing_config),
        set_default=payload.set_as_default,
    )
    upsert_wechat_account_specific_config(
        account_id,
        {
            "welcome_message": payload.welcome_message,
            "default_reply": payload.default_reply,
            "enabled_text_processors": normalized_enabled_processors,
            "enabled_miniprogram_appids": payload.enabled_miniprogram_appids,
            "meituan_base_url": payload.meituan_base_url,
            "meituan_official_cashback_url": payload.meituan_official_cashback_url,
            "url_mode": payload.url_mode,
            "authorized_users": payload.authorized_users,
            "default_code_duration": payload.default_code_duration,
            "order_query_code_duration": payload.order_query_code_duration,
            "order_query_authorized_users": payload.order_query_authorized_users,
            "meituan_miniprogram_config": payload.meituan_miniprogram_config,
            "meituan_merchant_coupon_view_config": payload.meituan_merchant_coupon_view_config,
            "meituan_miniprogram_link_processor_config": payload.meituan_miniprogram_link_processor_config,
            "meituan_link_config": payload.meituan_link_config,
            "merchant_coupon_prompts": payload.merchant_coupon_prompts,
            "order_query_settings": normalized_order_query_settings,
            "activation_code_settings": normalized_activation_code_settings,
            "p_value_settings": normalized_p_value_settings,
            "scene_settings": normalized_scene_settings,
            "meituan_shop_query_settings": normalized_meituan_shop_query_settings,
            "click_event_responses": parsed_click_event_responses,
        },
    )
    upsert_wechat_account_keyword_responses(account_id, parsed_keyword_responses)
    access_token_cache.pop(account_id, None)
    _reload_wechat_runtime_configs()
    _audit_admin_action(
        "wechat_account_save",
        current_user,
        True,
        account_id=account_id,
        set_default=payload.set_as_default,
    )
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
        _audit_admin_action("wechat_account_set_default", current_user, False, account_id=account_id, error="not_found")
        return JSONResponse({
            "success": False,
            "error": "设置默认公众号失败，账号不存在"
        }, status_code=404)

    _reload_wechat_runtime_configs()
    _audit_admin_action("wechat_account_set_default", current_user, True, account_id=account_id)
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
        _audit_admin_action("wechat_account_delete", current_user, False, account_id=account_id, error="not_found")
        return JSONResponse({
            "success": False,
            "error": "公众号不存在"
        }, status_code=404)

    delete_wechat_account(account_id)
    access_token_cache.pop(str(account_id or "").strip(), None)
    _reload_wechat_runtime_configs()
    _audit_admin_action("wechat_account_delete", current_user, True, account_id=account_id)
    return JSONResponse({
        "success": True,
        "message": "公众号配置已删除",
        **_serialize_account_store(),
    })


@router.post("/api/wechat/account-settings/order-query-codes")
async def generate_order_query_codes(
    payload: OrderQueryCodeGeneratePayload,
    current_user: str = Depends(get_current_user),
):
    account_id = str(payload.account_id or "").strip()
    target_user_id = str(payload.target_user_id or "").strip()
    if not account_id:
        return _json_error("公众号原始ID不能为空", status_code=400)
    if not target_user_id:
        return _json_error("目标用户不能为空", status_code=400)

    store_data = load_wechat_account_store()
    account_specific_configs = store_data.get("account_specific_configs", {})
    if account_id not in store_data.get("accounts", {}):
        return _json_error("公众号不存在", status_code=404)

    account_specific_config = account_specific_configs.get(account_id, {})
    order_query_settings = (
        account_specific_config.get("order_query_settings", {})
        if isinstance(account_specific_config, dict)
        else {}
    )
    authorized_users = list(order_query_settings.get("authorized_users", []))
    if target_user_id not in authorized_users:
        return _json_error("目标用户不在订单查询授权用户列表中，请先保存公众号配置", status_code=400)

    duration_text = str(payload.duration or "").strip() or str(order_query_settings.get("code_duration") or "").strip()
    if not duration_text:
        duration_text = "24h"
    try:
        duration_hours = parse_duration_string(duration_text)
    except ValueError as exc:
        return _json_error(f"激活码有效期格式不正确: {exc}", status_code=400)

    manager = get_mt_order_verification_manager()
    codes: list[str] = []
    for _ in range(int(payload.quantity)):
        code = manager.generate_code(
            target_user_id,
            duration_hours=duration_hours,
            max_uses=int(payload.max_uses),
        )
        bind_ok, bind_message = manager.bind_code_to_user(code, target_user_id)
        if not bind_ok:
            logger.warning(
                "订单查询激活码绑定失败: account=%s target_user=%s code=%s error=%s",
                account_id,
                target_user_id,
                code,
                bind_message,
            )
            return _json_error(f"生成成功但绑定失败: {bind_message}", status_code=500)
        codes.append(code)

    _reload_wechat_runtime_configs()
    _audit_admin_action(
        "wechat_order_query_code_generate",
        current_user,
        True,
        account_id=account_id,
    )
    return JSONResponse({
        "success": True,
        "message": "订单查询激活码已生成",
        "generated_codes": codes,
        **_serialize_account_store(),
    })


@router.get("/api/wechat/order-query-proxy-config")
async def get_order_query_proxy_config(current_user: str = Depends(get_current_user)):
    del current_user
    from utils.proxy_utils import get_effective_proxy_api_url

    runtime_store = load_system_settings_store()
    proxy_config = dict((runtime_store or {}).get("proxy_config") or {})
    return JSONResponse({
        "success": True,
        "proxy_config": {
            "api_url": str(proxy_config.get("api_url") or "").strip(),
            "enable_proxy_pool": bool(proxy_config.get("enable_proxy_pool", True)),
        },
        "effective_api_url": get_effective_proxy_api_url(),
    })


@router.post("/api/wechat/order-query-proxy-config")
async def save_order_query_proxy_config(
    payload: OrderQueryProxyPayload,
    current_user: str = Depends(get_current_user),
):
    from utils.proxy_utils import get_effective_proxy_api_url

    try:
        runtime_store = load_system_settings_store()
        runtime_store["proxy_config"] = {
            "api_url": str(payload.api_url or "").strip(),
            "enable_proxy_pool": bool(payload.enable_proxy_pool),
        }
        save_system_settings_store(runtime_store)
        _reload_wechat_runtime_configs()
    except Exception as exc:
        _audit_admin_action("order_query_proxy_save", current_user, False, error=str(exc))
        return _json_error("保存订单查询代理配置失败", status_code=500)

    _audit_admin_action("order_query_proxy_save", current_user, True)
    return JSONResponse({
        "success": True,
        "message": "订单查询代理配置已保存",
        "proxy_config": dict(runtime_store.get("proxy_config") or {}),
        "effective_api_url": get_effective_proxy_api_url(),
    })


@router.post("/api/wechat/order-query-proxy-test")
async def test_order_query_proxy_config(current_user: str = Depends(get_current_user)):
    from utils.proxy_utils import test_proxy_api_async

    try:
        result = await test_proxy_api_async(number=1)
    except ValueError as exc:
        _audit_admin_action("order_query_proxy_test", current_user, False, error=str(exc))
        return _json_error(str(exc), status_code=400)
    except Exception as exc:
        _audit_admin_action("order_query_proxy_test", current_user, False, error=str(exc))
        return _json_error(str(exc) or "订单查询代理测试失败", status_code=500)

    _audit_admin_action("order_query_proxy_test", current_user, True)
    return JSONResponse({"success": True, **result})


async def _probe_order_relay_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from utils.meituan_order_relay_pool import probe_order_relay_node

    targets = [item for item in nodes if isinstance(item, dict) and str(item.get("url") or "").strip()]
    if not targets:
        return []
    return list(await asyncio.gather(*(probe_order_relay_node(item) for item in targets)))


@router.get("/api/wechat/order-relay-config")
async def get_order_relay_config(current_user: str = Depends(get_current_user)):
    del current_user
    from utils.meituan_order_relay_pool import get_order_relay_pool_runtime

    config = normalize_order_relay_pool_config(
        load_system_settings_store().get("order_relay_pool_config", {})
    )
    return JSONResponse({"success": True, "relay_pool": {"config": config, "runtime": get_order_relay_pool_runtime()}})


@router.post("/api/wechat/order-relay-config")
async def save_order_relay_config(
    payload: OrderRelayPoolPayload,
    current_user: str = Depends(get_current_user),
):
    from utils.meituan_order_relay_pool import get_order_relay_pool_runtime

    try:
        config = normalize_order_relay_pool_config(payload.model_dump())
        store = load_system_settings_store()
        store["order_relay_pool_config"] = config
        save_system_settings_store(store)
        probes = await _probe_order_relay_nodes(list(config.get("nodes") or []))
    except Exception as exc:
        _audit_admin_action("order_relay_save", current_user, False, error=str(exc))
        return _json_error("保存订单 Relay 设置失败", status_code=500)

    _audit_admin_action("order_relay_save", current_user, True)
    return JSONResponse({
        "success": True,
        "message": "订单 Relay 设置已保存，并已测试节点",
        "relay_pool": {"config": config, "runtime": get_order_relay_pool_runtime()},
        "probe_results": probes,
    })


@router.post("/api/wechat/order-relay-probe")
async def probe_order_relay_config(request: Request, current_user: str = Depends(get_current_user)):
    del current_user
    try:
        payload = await request.json()
        raw_nodes = payload.get("nodes") if isinstance(payload, dict) else []
        nodes = raw_nodes if isinstance(raw_nodes, list) else []
        normalized_nodes = normalize_order_relay_pool_config({"nodes": nodes}).get("nodes") or []
        if not normalized_nodes:
            return _json_error("请先填写至少一个订单 Relay 节点", status_code=400)
        probes = await _probe_order_relay_nodes(normalized_nodes)
        from utils.meituan_order_relay_pool import get_order_relay_pool_runtime
        return JSONResponse({"success": True, "probe_results": probes, "relay_pool": {"runtime": get_order_relay_pool_runtime()}})
    except Exception as exc:
        return _json_error(f"订单 Relay 测试失败: {str(exc)}", status_code=500)


@router.post("/api/wechat/order-relay-reset")
async def reset_order_relay_config(request: Request, current_user: str = Depends(get_current_user)):
    del current_user
    try:
        payload = await request.json()
        url = str((payload or {}).get("url") or "").strip()
        from utils.meituan_order_relay_pool import get_order_relay_pool_runtime, reset_order_relay_runtime
        if not reset_order_relay_runtime(url):
            return _json_error("未找到对应订单 Relay 节点状态", status_code=404)
        return JSONResponse({"success": True, "message": "订单 Relay 节点失败状态已清空", "relay_pool": {"runtime": get_order_relay_pool_runtime()}})
    except Exception as exc:
        return _json_error(f"清空订单 Relay 状态失败: {str(exc)}", status_code=500)


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
        normalized_type = str(type or "").strip().lower()
        file_content, filename, content_type = await _read_and_validate_material_upload(file, normalized_type)
        if str(account_id or "").strip() not in get_wechat_accounts():
            return _json_error("公众号不存在或未启用", status_code=404)

        access_token = await get_access_token(account_id)
        if not access_token:
            return JSONResponse({
                "success": False,
                "error": "获取 access_token 失败，请检查配置"
            }, status_code=500)

        url = f"https://api.weixin.qq.com/cgi-bin/media/upload?access_token={access_token}&type={normalized_type}"
        
        files = {
            'media': (filename, file_content, content_type)
        }
        
        logger.info("上传临时素材: filename=%s type=%s size=%d operator=%s", filename, normalized_type, len(file_content), current_user)
        
        response = await requests.post(url, files=files, timeout=30)
        result = response.json()
        
        if "media_id" in result:
            logger.info(f"上传成功: media_id={result['media_id']}, 操作人: {current_user}")
            return JSONResponse({
                "success": True,
                "data": result
            })
        else:
            logger.error("上传失败: errcode=%s errmsg=%s", result.get("errcode"), result.get("errmsg"))
            error_msg = result.get("errmsg", "上传失败")
            return JSONResponse({
                "success": False,
                "error": f"微信API错误: {error_msg} (错误码: {result.get('errcode', 'unknown')})"
            }, status_code=400)
            
    except ValueError as e:
        return _json_error(str(e), status_code=400)
    except Exception as e:
        logger.error("上传临时素材异常: %s", e)
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
        normalized_type = str(type or "").strip().lower()
        file_content, filename, content_type = await _read_and_validate_material_upload(file, normalized_type)
        if str(account_id or "").strip() not in get_wechat_accounts():
            return _json_error("公众号不存在或未启用", status_code=404)

        access_token = await get_access_token(account_id)
        if not access_token:
            return JSONResponse({
                "success": False,
                "error": "获取 access_token 失败，请检查配置"
            }, status_code=500)

        url = f"https://api.weixin.qq.com/cgi-bin/material/add_material?access_token={access_token}&type={normalized_type}"
        
        files = {
            'media': (filename, file_content, content_type)
        }
        
                          
        data = {}
        if normalized_type == 'video' and (title or introduction):
            description = {}
            if title:
                description['title'] = title
            if introduction:
                description['introduction'] = introduction
            data['description'] = json.dumps(description)
        
        logger.info("上传永久素材: filename=%s type=%s size=%d operator=%s", filename, normalized_type, len(file_content), current_user)
        
        response = await requests.post(url, files=files, data=data, timeout=30)
        result = response.json()
        
        if "media_id" in result:
            logger.info(f"上传成功: media_id={result['media_id']}, 操作人: {current_user}")
            return JSONResponse({
                "success": True,
                "data": result
            })
        else:
            logger.error("上传失败: errcode=%s errmsg=%s", result.get("errcode"), result.get("errmsg"))
            error_msg = result.get("errmsg", "上传失败")
            return JSONResponse({
                "success": False,
                "error": f"微信API错误: {error_msg} (错误码: {result.get('errcode', 'unknown')})"
            }, status_code=400)
            
    except ValueError as e:
        return _json_error(str(e), status_code=400)
    except Exception as e:
        logger.error("上传永久素材异常: %s", e)
        import traceback
        logger.error(traceback.format_exc())
        return JSONResponse({
            "success": False,
            "error": f"服务器错误: {str(e)}"
        }, status_code=500)


@router.get("/api/wechat/access_token/{account_id}")
async def get_access_token_api(account_id: str, current_user: str = Depends(get_current_user)):
    """检查指定公众号能否获取 access_token，不返回 token 明文。"""
    access_token = await get_access_token(account_id)
    if access_token:
        return JSONResponse({
            "success": True,
            "access_token_available": True,
            "account_id": account_id,
        })
    else:
        return JSONResponse({
            "success": False,
            "error": "获取 access_token 失败"
        }, status_code=500)
