"""
微信公众号配置文件 - 动态加载 TOML 格式
"""
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Callable
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None
from utils.path_utils import resolve_project_path
from utils.system_settings_store import load_system_settings_store
from wechat_account_store import load_wechat_account_store, merge_wechat_account_config


def _resolve_config_file() -> str:
    raw_path = str(os.getenv("WX_SERVICE_CONFIG_FILE") or os.getenv("CONFIG_FILE") or "").strip()
    if raw_path:
        return str(Path(raw_path).expanduser())

    runtime_data_dir = str(os.getenv("WX_SERVICE_DATA_DIR") or "").strip()
    if runtime_data_dir:
        runtime_config_path = Path(runtime_data_dir).expanduser() / "config.toml"
        if runtime_config_path.exists():
            return str(runtime_config_path)

    host_runtime_config_path = resolve_project_path("runtime-data", "config.toml")
    if host_runtime_config_path.exists():
        return str(host_runtime_config_path)

    return str(resolve_project_path('config.toml'))


CONFIG_FILE = _resolve_config_file()
MINIPROGRAM_CONFIG_FILE = str(resolve_project_path('miniprogram', 'miniprogram_config.toml'))
MEITUAN_LINK_CONFIG_FILE = str(resolve_project_path('text_processors', 'meituan_link.toml'))
MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG_FILE = str(
    resolve_project_path('text_processors', 'meituan_miniprogram_link_processor.toml')
)
KEYWORD_RESPONSES_FILE = str(resolve_project_path('text_processors', 'keyword_responses.toml'))
CLICK_EVENT_RESPONSES_FILE = str(resolve_project_path('text_processors', 'click_event_responses.toml'))
PROMPTS_FILE = str(resolve_project_path('link_handlers', 'prompts.toml'))
LINK_CONFIG_FILE = str(resolve_project_path('link_handlers', 'link_config.toml'))
MERCHANT_COUPON_PROMPTS_FILE = str(resolve_project_path('text_processors', 'merchant_coupon_prompts.toml'))
ORDER_LEADERBOARD_CONFIG_FILE = str(resolve_project_path('text_processors', 'order_leaderboard.toml'))

DEFAULT_MEITUAN_MINIPROGRAM_CONFIG: Dict[str, Any] = {
    "show_dianping_links": True,
    "build_extra_params": True,
    "show_merchant_coupon_link": True,
    "show_miniprogram_link": True,
    "show_token_null_message": False,
    "show_save_merchant_coupon_link": True,
    "button_name": "大众点评/美团外卖",
    "dianping_links": "",
    "no_config_message": "该公众号暂未配置美团优惠功能",
    "no_link_message": "收到美团小程序分享，暂无法获取详情链接~",
    "no_link_message_text": "收到美团小程序链接，暂无法获取详情链接~",
    "click_detail_link": "点击下方链接查看详情：",
    "merchant_coupon_link": "领取商家券① (可切号)",
    "merchant_coupon_link_2": "领取商家券2(可切号，不一定有)",
    "merchant_coupon_link_2_suffix": "",
    "copy_to_browser": "然后复制到浏览器打开：",
    "red_packet_links": "",
    "miniprogram_open_prefix": "领取商家券 (小程序版)",
    "token_null_message": "\n\n提示：当前小程序未包含免配信息，可发送“免配链接”获取入口",
    "save_merchant_coupon_link_text": "保存商家券",
    "default_title": "美团商家",
    "cashback_activity_link_text": "点击报名该商家「官方返现」活动",
    "merchant_coupon_link_suffix": "",
    "cashback_activity_link_suffix": "",
    "miniprogram_link_suffix": "",
    "extra_params_link_suffix": "",
    "save_merchant_coupon_link_suffix": "",
}
DEFAULT_MEITUAN_MERCHANT_COUPON_VIEW_CONFIG: Dict[str, Any] = {
    **DEFAULT_MEITUAN_MINIPROGRAM_CONFIG,
    "show_dianping_links": False,
    "click_detail_link": "点击下方链接查看详情：",
}
DEFAULT_MEITUAN_LINK_CONFIG: Dict[str, Any] = {
    "click_detail_link": "点击下方链接查看详情：",
    "merchant_coupon_link": "领取商家券① (可切号)",
    "merchant_coupon_link_2": "领取商家券2(可切号，不一定有)",
    "merchant_coupon_link_2_suffix": "",
    "save_merchant_coupon_link_text": "保存商家券",
    "show_save_merchant_coupon_link": True,
    "miniprogram_open_prefix": "领取商家券 (小程序版)",
    "meituan_link_title_text": "美团优惠链接：",
    "meituan_link_response_title_text": "【美团优惠链接】",
    "cashback_activity_link_text": "点击报名该商家「官方返现」活动",
    "merchant_coupon_link_suffix": "",
    "cashback_activity_link_suffix": "",
    "miniprogram_link_suffix": "",
    "save_merchant_coupon_link_suffix": "",
}


def get_default_meituan_miniprogram_config() -> Dict[str, Any]:
    return deepcopy(DEFAULT_MEITUAN_MINIPROGRAM_CONFIG)


def get_default_meituan_merchant_coupon_view_config() -> Dict[str, Any]:
    return deepcopy(DEFAULT_MEITUAN_MERCHANT_COUPON_VIEW_CONFIG)


def get_default_meituan_miniprogram_link_processor_config() -> Dict[str, Any]:
    return deepcopy(DEFAULT_MEITUAN_MINIPROGRAM_CONFIG)


def get_default_meituan_link_config() -> Dict[str, Any]:
    return deepcopy(DEFAULT_MEITUAN_LINK_CONFIG)


def load_toml_file(file_path: str, default: Dict[str, Any] = None) -> Dict[str, Any]:
    if tomllib is None:
        return default or {}
    
    try:
        with open(file_path, 'rb') as f:
            return tomllib.load(f)
    except FileNotFoundError:
        if default is not None:
            print(f"[WARNING] 配置文件 {file_path} 不存在，使用默认值")
        return default or {}
    except Exception as e:
        print(f"[ERROR] 加载配置文件 {file_path} 失败: {e}")
        return default or {}


def _get_runtime_account_specific_configs(runtime_store_data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    account_specific_configs = runtime_store_data.get("account_specific_configs", {})
    if not isinstance(account_specific_configs, dict):
        return {}

    normalized_configs: Dict[str, Dict[str, Any]] = {}
    for raw_account_id, raw_account_config in account_specific_configs.items():
        account_id = str(raw_account_id or "").strip()
        if not account_id or not isinstance(raw_account_config, dict):
            continue
        normalized_configs[account_id] = raw_account_config
    return normalized_configs


def _get_runtime_account_ids(runtime_store_data: Dict[str, Any]) -> set[str]:
    account_ids: set[str] = set()
    accounts = runtime_store_data.get("accounts", {})
    if isinstance(accounts, dict):
        account_ids.update(str(account_id or "").strip() for account_id in accounts.keys())
    account_ids.update(_get_runtime_account_specific_configs(runtime_store_data).keys())
    return {account_id for account_id in account_ids if account_id}


def merge_runtime_section_config(
    base_config: Dict[str, Any],
    runtime_store_data: Dict[str, Any],
    runtime_field: str,
    *,
    default_config: Dict[str, Any] | None = None,
    key_builder: Callable[[str], str] | None = None,
    replace_runtime_accounts: bool = False,
) -> Dict[str, Any]:
    merged_config = deepcopy(base_config) if isinstance(base_config, dict) else {}
    key_builder = key_builder or (lambda account_id: account_id)

    if default_config:
        for account_id in _get_runtime_account_ids(runtime_store_data):
            section_key = key_builder(account_id)
            default_section = deepcopy(default_config)
            existing_section = merged_config.get(section_key, {})
            if isinstance(existing_section, dict):
                default_section.update(deepcopy(existing_section))
            merged_config[section_key] = default_section

    for account_id, account_config in _get_runtime_account_specific_configs(runtime_store_data).items():
        if runtime_field not in account_config:
            continue
        runtime_section = account_config.get(runtime_field)
        if not isinstance(runtime_section, dict):
            continue

        section_key = key_builder(account_id)
        if replace_runtime_accounts:
            merged_config[section_key] = deepcopy(runtime_section)
            continue

        existing_section = merged_config.get(section_key, {})
        if not isinstance(existing_section, dict):
            existing_section = {}
        merged_section = deepcopy(existing_section)
        merged_section.update(deepcopy(runtime_section))
        merged_config[section_key] = merged_section

    return merged_config


def get_runtime_account_response_map(
    runtime_store_data: Dict[str, Any],
    runtime_field: str,
) -> Dict[str, Dict[str, Any]]:
    runtime_responses: Dict[str, Dict[str, Any]] = {}
    for account_id, account_config in _get_runtime_account_specific_configs(runtime_store_data).items():
        if runtime_field not in account_config:
            continue
        account_responses = account_config.get(runtime_field)
        if isinstance(account_responses, dict):
            runtime_responses[account_id] = deepcopy(account_responses)
    return runtime_responses


def load_config() -> Dict[str, Any]:
    """
    从 TOML 文件加载配置
    
    Returns:
        配置字典
    """
    if tomllib is None:
        print("[ERROR] TOML 解析库未安装，使用默认配置")
        return merge_wechat_account_config(get_default_config())
    
    try:
        print(f"[INFO] 正在加载配置文件: {CONFIG_FILE}")
        
                                       
        with open(CONFIG_FILE, 'rb') as f:
            file_config = tomllib.load(f)
        config = get_default_config()
        config.update(file_config)
        config = merge_wechat_account_config(config)
        
                  
        process_message_factories(config)
        
                    
        account_count = len(config.get('wechat_accounts', {}))
        config_count = len(config.get('account_specific_configs', {}))
        print(f"[SUCCESS] 配置加载成功: {account_count} 个公众号账号, {config_count} 个特定配置")
        
                    
        if config.get('wechat_accounts'):
            print(f"[INFO] 已加载公众号: {', '.join(config['wechat_accounts'].keys())}")
        
        return config
    except FileNotFoundError:
        print(f"[WARNING] 配置文件 {CONFIG_FILE} 不存在，使用默认配置")
        return merge_wechat_account_config(get_default_config())
    except Exception as e:
        print(f"[ERROR] 配置文件解析错误: {e}")
        import traceback
        traceback.print_exc()
        return merge_wechat_account_config(get_default_config())


def create_message_factory(msg_config: Dict[str, Any]) -> Callable:
    """
    根据配置创建消息工厂函数
    
    Args:
        msg_config: 消息配置字典，包含 type 和其他参数
        
    Returns:
        lambda 函数，接收 req_msg 参数，返回响应消息对象
        
    Example TOML配置:
        [keyword_responses]
        "图片" = {type="image", media_id="MEDIA_ID_123"}
        "视频" = {type="video", media_id="VIDEO_ID", title="标题", description="描述"}
        "图文" = {type="news", articles=[
            {title="标题1", description="描述1", pic_url="http://pic.jpg", url="http://url.com"}
        ]}
        "动态时间" = {type="dynamic", generator="datetime"}
    """
    from utils.lambda_factory import (
        create_text_lambda,
        create_image_lambda,
        create_voice_lambda,
        create_video_lambda,
        create_music_lambda,
        create_news_lambda,
    )
    
    msg_type = msg_config.get('type', 'text')
    
    if msg_type == 'text':
        content = msg_config.get('content', '')
                                 
        def _text_factory(req_msg):
            text_rsp = create_text_lambda(req_msg, content)()
            return text_rsp
        return _text_factory
    
    elif msg_type == 'image':
        media_id = msg_config.get('media_id', '')
        def _image_factory(req_msg):
            return create_image_lambda(req_msg, media_id)()
        return _image_factory
    
    elif msg_type == 'voice':
        media_id = msg_config.get('media_id', '')
        def _voice_factory(req_msg):
            return create_voice_lambda(req_msg, media_id)()
        return _voice_factory
    
    elif msg_type == 'video':
        media_id = msg_config.get('media_id', '')
        title = msg_config.get('title')
        description = msg_config.get('description')
        def _video_factory(req_msg):
            return create_video_lambda(req_msg, media_id, title, description)()
        return _video_factory
    
    elif msg_type == 'music':
        config_copy = msg_config.copy()
        def _music_factory(req_msg):
            return create_music_lambda(
                req_msg,
                title=config_copy.get('title'),
                description=config_copy.get('description'),
                music_url=config_copy.get('music_url'),
                hq_music_url=config_copy.get('hq_music_url'),
                thumb_media_id=config_copy.get('thumb_media_id')
            )()
        return _music_factory
    
    elif msg_type == 'news':
        articles = msg_config.get('articles', [])
        def _news_factory(req_msg):
            return create_news_lambda(req_msg, articles)()
        return _news_factory
    
    elif msg_type == 'dynamic':
                
        generator = msg_config.get('generator', '')
        if generator == 'datetime':
            def _datetime_factory(req_msg):
                current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                from_user = req_msg.get("FromUserName", "用户")
                content = f"当前时间: {current_time}\n查询用户: {from_user}"
                return create_text_lambda(req_msg, content)()
            return _datetime_factory
        else:
            def _error_factory(req_msg):
                return create_text_lambda(req_msg, "动态内容生成错误")()
            return _error_factory
    
    else:
                   
        def _unknown_factory(req_msg):
            return create_text_lambda(req_msg, f"未知消息类型: {msg_type}")()
        return _unknown_factory


def process_message_factories(config: Dict[str, Any]) -> None:
    """
    处理配置中的消息工厂函数
    
    将配置中的消息定义转换为工厂函数
    支持字符串（文本）和字典（复杂消息）两种格式
    """
    if 'account_specific_configs' not in config:
        return
    
    for account_id, account_config in config['account_specific_configs'].items():
        if 'keyword_responses' not in account_config:
            continue
        
        for keyword, response in account_config['keyword_responses'].items():
                               
            if isinstance(response, str):
                           
                if response.startswith('__LAMBDA__:'):
                           
                    expression = response[len('__LAMBDA__:'):]
                    if expression == 'datetime.now()':
                                   
                        account_config['keyword_responses'][keyword] = create_message_factory({
                            'type': 'dynamic',
                            'generator': 'datetime'
                        })
                           
                
                          
            elif isinstance(response, dict):
                factory = create_message_factory(response)
                account_config['keyword_responses'][keyword] = factory


def process_response_factories(response_config: Dict[str, Any]) -> Dict[str, Any]:
    processed_config = response_config if isinstance(response_config, dict) else {}

    for account_id, account_response_config in processed_config.items():
        if not isinstance(account_response_config, dict):
            continue

        for keyword, response in account_response_config.items():
            if isinstance(response, str) and response.startswith('__LAMBDA__:'):
                expression = response[len('__LAMBDA__:'):]
                if expression == 'datetime.now()':
                    processed_config[account_id][keyword] = create_message_factory({
                        'type': 'dynamic',
                        'generator': 'datetime'
                    })
            elif isinstance(response, dict):
                processed_config[account_id][keyword] = create_message_factory(response)

    return processed_config


def merge_keyword_response_config(
    base_config: Dict[str, Any],
    runtime_keyword_responses: Dict[str, Any],
    *,
    replace_runtime_accounts: bool = False,
) -> Dict[str, Any]:
    merged_config = deepcopy(base_config) if isinstance(base_config, dict) else {}

    if not isinstance(runtime_keyword_responses, dict):
        return merged_config

    for account_id, runtime_responses in runtime_keyword_responses.items():
        if not isinstance(runtime_responses, dict):
            continue

        if replace_runtime_accounts:
            merged_config[account_id] = deepcopy(runtime_responses)
            continue

        current_responses = merged_config.get(account_id, {})
        if not isinstance(current_responses, dict):
            current_responses = {}

        merged_account_responses = deepcopy(current_responses)
        merged_account_responses.update(deepcopy(runtime_responses))
        merged_config[account_id] = merged_account_responses

    return merged_config


def merge_nested_section_config(
    base_config: Dict[str, Any],
    runtime_config: Dict[str, Any],
    *,
    replace_runtime_sections: bool = False,
) -> Dict[str, Any]:
    merged_config = deepcopy(base_config) if isinstance(base_config, dict) else {}

    if not isinstance(runtime_config, dict):
        return merged_config

    for section_key, runtime_section in runtime_config.items():
        normalized_section_key = str(section_key or "").strip()
        if not normalized_section_key:
            continue

        if replace_runtime_sections or not isinstance(runtime_section, dict):
            merged_config[normalized_section_key] = deepcopy(runtime_section)
            continue

        current_section = merged_config.get(normalized_section_key, {})
        if not isinstance(current_section, dict):
            current_section = {}
        merged_section = deepcopy(current_section)
        merged_section.update(deepcopy(runtime_section))
        merged_config[normalized_section_key] = merged_section

    return merged_config


def get_default_config() -> Dict[str, Any]:
    """
    获取默认配置（当配置文件不存在时使用）
    
    Returns:
        默认配置字典
    """
    return {
        "wechat_accounts": {},
        "default_wechat_config": {},
        "miniprogram_appids": {
            "wx2c348cf579062e56": "meituan",
            "wxde8ac0a21135c07d": "meituan"
        },
        "account_specific_configs": {}
    }


      
print("=" * 60)
print("[STARTUP] 微信公众号服务器 - 配置加载")
print("=" * 60)
_config = load_config()

        
WECHAT_ACCOUNTS = _config.get('wechat_accounts', {})
DEFAULT_WECHAT_CONFIG = _config.get('default_wechat_config', {})
MINIPROGRAM_APPIDS = _config.get('miniprogram_appids', {})
ACCOUNT_SPECIFIC_CONFIGS = _config.get('account_specific_configs', {})
ADMIN_USERS = _config.get('admin_users', {})

                
print("[INFO] 正在加载其他配置文件...")
_runtime_store_data = load_wechat_account_store()
_system_settings_data = load_system_settings_store()
SHORTLINK_CONFIG = _system_settings_data.get("shortlink_config", {})
MINIPROGRAM_CONFIG = merge_runtime_section_config(
    load_toml_file(MINIPROGRAM_CONFIG_FILE, {}),
    _runtime_store_data,
    "meituan_miniprogram_config",
    default_config=DEFAULT_MEITUAN_MINIPROGRAM_CONFIG,
)
MINIPROGRAM_CONFIG = merge_runtime_section_config(
    MINIPROGRAM_CONFIG,
    _runtime_store_data,
    "meituan_merchant_coupon_view_config",
    default_config=DEFAULT_MEITUAN_MERCHANT_COUPON_VIEW_CONFIG,
    key_builder=lambda account_id: f"{account_id}_merchant_coupon_view",
)
MEITUAN_LINK_CONFIG = merge_runtime_section_config(
    load_toml_file(MEITUAN_LINK_CONFIG_FILE, {}),
    _runtime_store_data,
    "meituan_link_config",
    default_config=DEFAULT_MEITUAN_LINK_CONFIG,
)
MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG = merge_runtime_section_config(
    load_toml_file(MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG_FILE, {}),
    _runtime_store_data,
    "meituan_miniprogram_link_processor_config",
    default_config=DEFAULT_MEITUAN_MINIPROGRAM_CONFIG,
)
KEYWORD_RESPONSES = merge_keyword_response_config(
    load_toml_file(KEYWORD_RESPONSES_FILE, {}),
    _runtime_store_data.get('keyword_responses', {}),
)
CLICK_EVENT_RESPONSES = merge_keyword_response_config(
    load_toml_file(CLICK_EVENT_RESPONSES_FILE, {}),
    get_runtime_account_response_map(_runtime_store_data, "click_event_responses"),
    replace_runtime_accounts=True,
)
PROMPTS_CONFIG = load_toml_file(PROMPTS_FILE, {})
PROMPTS_CONFIG = merge_nested_section_config(
    PROMPTS_CONFIG,
    _system_settings_data.get("prompts_config", {}),
)
LINK_CONFIG = merge_nested_section_config(
    load_toml_file(LINK_CONFIG_FILE, {}),
    _system_settings_data.get("link_config", {}),
)
MERCHANT_COUPON_PROMPTS = merge_runtime_section_config(
    load_toml_file(MERCHANT_COUPON_PROMPTS_FILE, {}),
    _runtime_store_data,
    "merchant_coupon_prompts",
)
ORDER_LEADERBOARD_CONFIG = merge_nested_section_config(
    load_toml_file(ORDER_LEADERBOARD_CONFIG_FILE, {}),
    _system_settings_data.get("order_leaderboard_config", {}),
    replace_runtime_sections=True,
)

KEYWORD_RESPONSES = process_response_factories(KEYWORD_RESPONSES)
CLICK_EVENT_RESPONSES = process_response_factories(CLICK_EVENT_RESPONSES)

print("[SUCCESS] 所有配置文件加载完成")
print("=" * 60)


def reload_config():
    """
    重新加载配置文件
    
    可以在运行时调用此函数重新加载配置，无需重启服务器
    """
    global _config, WECHAT_ACCOUNTS, DEFAULT_WECHAT_CONFIG, MINIPROGRAM_APPIDS, ACCOUNT_SPECIFIC_CONFIGS, ADMIN_USERS
    global MINIPROGRAM_CONFIG, MEITUAN_LINK_CONFIG, MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG
    global KEYWORD_RESPONSES, CLICK_EVENT_RESPONSES, PROMPTS_CONFIG, LINK_CONFIG, MERCHANT_COUPON_PROMPTS, ORDER_LEADERBOARD_CONFIG
    global SHORTLINK_CONFIG
    
    print("[INFO] 开始重新加载配置...")
    _config = load_config()
    WECHAT_ACCOUNTS = _config.get('wechat_accounts', {})
    DEFAULT_WECHAT_CONFIG = _config.get('default_wechat_config', {})
    MINIPROGRAM_APPIDS = _config.get('miniprogram_appids', {})
    ACCOUNT_SPECIFIC_CONFIGS = _config.get('account_specific_configs', {})
    ADMIN_USERS = _config.get('admin_users', {})
    
    runtime_store_data = load_wechat_account_store()
    system_settings_data = load_system_settings_store()
    SHORTLINK_CONFIG = system_settings_data.get("shortlink_config", {})
    MINIPROGRAM_CONFIG = merge_runtime_section_config(
        load_toml_file(MINIPROGRAM_CONFIG_FILE, {}),
        runtime_store_data,
        "meituan_miniprogram_config",
        default_config=DEFAULT_MEITUAN_MINIPROGRAM_CONFIG,
    )
    MINIPROGRAM_CONFIG = merge_runtime_section_config(
        MINIPROGRAM_CONFIG,
        runtime_store_data,
        "meituan_merchant_coupon_view_config",
        default_config=DEFAULT_MEITUAN_MERCHANT_COUPON_VIEW_CONFIG,
        key_builder=lambda account_id: f"{account_id}_merchant_coupon_view",
    )
    MEITUAN_LINK_CONFIG = merge_runtime_section_config(
        load_toml_file(MEITUAN_LINK_CONFIG_FILE, {}),
        runtime_store_data,
        "meituan_link_config",
        default_config=DEFAULT_MEITUAN_LINK_CONFIG,
    )
    MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG = merge_runtime_section_config(
        load_toml_file(MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG_FILE, {}),
        runtime_store_data,
        "meituan_miniprogram_link_processor_config",
        default_config=DEFAULT_MEITUAN_MINIPROGRAM_CONFIG,
    )
    KEYWORD_RESPONSES = merge_keyword_response_config(
        load_toml_file(KEYWORD_RESPONSES_FILE, {}),
        runtime_store_data.get('keyword_responses', {}),
    )
    CLICK_EVENT_RESPONSES = merge_keyword_response_config(
        load_toml_file(CLICK_EVENT_RESPONSES_FILE, {}),
        get_runtime_account_response_map(runtime_store_data, "click_event_responses"),
        replace_runtime_accounts=True,
    )
    PROMPTS_CONFIG = merge_nested_section_config(
        load_toml_file(PROMPTS_FILE, {}),
        system_settings_data.get("prompts_config", {}),
    )
    LINK_CONFIG = merge_nested_section_config(
        load_toml_file(LINK_CONFIG_FILE, {}),
        system_settings_data.get("link_config", {}),
    )
    MERCHANT_COUPON_PROMPTS = merge_runtime_section_config(
        load_toml_file(MERCHANT_COUPON_PROMPTS_FILE, {}),
        runtime_store_data,
        "merchant_coupon_prompts",
    )
    ORDER_LEADERBOARD_CONFIG = merge_nested_section_config(
        load_toml_file(ORDER_LEADERBOARD_CONFIG_FILE, {}),
        system_settings_data.get("order_leaderboard_config", {}),
        replace_runtime_sections=True,
    )

    KEYWORD_RESPONSES = process_response_factories(KEYWORD_RESPONSES)
    CLICK_EVENT_RESPONSES = process_response_factories(CLICK_EVENT_RESPONSES)
    _sync_config_package_exports()
    
    print("[SUCCESS] 所有配置重新加载完成")
    print(f"[INFO] 当前公众号: {', '.join(WECHAT_ACCOUNTS.keys())}")
    return True


def _sync_config_package_exports() -> None:
    import sys

    package_module = sys.modules.get("config")
    if package_module is None:
        return

    for name in (
        "WECHAT_ACCOUNTS",
        "DEFAULT_WECHAT_CONFIG",
        "MINIPROGRAM_APPIDS",
        "ACCOUNT_SPECIFIC_CONFIGS",
        "ADMIN_USERS",
        "MINIPROGRAM_CONFIG",
        "MEITUAN_LINK_CONFIG",
        "MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG",
        "KEYWORD_RESPONSES",
        "CLICK_EVENT_RESPONSES",
        "PROMPTS_CONFIG",
        "LINK_CONFIG",
        "MERCHANT_COUPON_PROMPTS",
        "ORDER_LEADERBOARD_CONFIG",
        "SHORTLINK_CONFIG",
    ):
        setattr(package_module, name, globals().get(name))


               
def get_config() -> Dict[str, Any]:
    """
    获取当前配置
    
    Returns:
        配置字典
    """
    return _config


def get_wechat_accounts() -> Dict[str, Any]:
    return _config.get('wechat_accounts', {})


def get_default_wechat_config() -> Dict[str, Any]:
    return _config.get('default_wechat_config', {})


def get_wechat_account(account_id: str) -> Dict[str, Any] | None:
    return get_wechat_accounts().get(account_id)
