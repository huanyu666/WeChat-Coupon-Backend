"""
微信公众号配置文件 - 动态加载 TOML 格式
"""
import os
from datetime import datetime
from typing import Dict, Any, Callable
try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None
from utils.path_utils import resolve_project_path
from wechat_account_store import load_wechat_account_store, merge_wechat_account_config


        
CONFIG_FILE = str(resolve_project_path('config.toml'))
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
            config = tomllib.load(f)
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
) -> Dict[str, Any]:
    merged_config = base_config.copy() if isinstance(base_config, dict) else {}

    if not isinstance(runtime_keyword_responses, dict):
        return merged_config

    for account_id, runtime_responses in runtime_keyword_responses.items():
        if not isinstance(runtime_responses, dict):
            continue

        current_responses = merged_config.get(account_id, {})
        if not isinstance(current_responses, dict):
            current_responses = {}

        merged_account_responses = current_responses.copy()
        merged_account_responses.update(runtime_responses)
        merged_config[account_id] = merged_account_responses

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
MINIPROGRAM_CONFIG = load_toml_file(MINIPROGRAM_CONFIG_FILE, {})
MEITUAN_LINK_CONFIG = load_toml_file(MEITUAN_LINK_CONFIG_FILE, {})
MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG = load_toml_file(
    MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG_FILE, {}
)
_runtime_store_data = load_wechat_account_store()
KEYWORD_RESPONSES = merge_keyword_response_config(
    load_toml_file(KEYWORD_RESPONSES_FILE, {}),
    _runtime_store_data.get('keyword_responses', {}),
)
CLICK_EVENT_RESPONSES = load_toml_file(CLICK_EVENT_RESPONSES_FILE, {})
PROMPTS_CONFIG = load_toml_file(PROMPTS_FILE, {})
LINK_CONFIG = load_toml_file(LINK_CONFIG_FILE, {})
MERCHANT_COUPON_PROMPTS = load_toml_file(MERCHANT_COUPON_PROMPTS_FILE, {})
ORDER_LEADERBOARD_CONFIG = load_toml_file(ORDER_LEADERBOARD_CONFIG_FILE, {})

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
    
    print("[INFO] 开始重新加载配置...")
    _config = load_config()
    WECHAT_ACCOUNTS = _config.get('wechat_accounts', {})
    DEFAULT_WECHAT_CONFIG = _config.get('default_wechat_config', {})
    MINIPROGRAM_APPIDS = _config.get('miniprogram_appids', {})
    ACCOUNT_SPECIFIC_CONFIGS = _config.get('account_specific_configs', {})
    ADMIN_USERS = _config.get('admin_users', {})
    
                      
    MINIPROGRAM_CONFIG = load_toml_file(MINIPROGRAM_CONFIG_FILE, {})
    MEITUAN_LINK_CONFIG = load_toml_file(MEITUAN_LINK_CONFIG_FILE, {})
    MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG = load_toml_file(
        MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG_FILE, {}
    )
    runtime_store_data = load_wechat_account_store()
    KEYWORD_RESPONSES = merge_keyword_response_config(
        load_toml_file(KEYWORD_RESPONSES_FILE, {}),
        runtime_store_data.get('keyword_responses', {}),
    )
    CLICK_EVENT_RESPONSES = load_toml_file(CLICK_EVENT_RESPONSES_FILE, {})
    PROMPTS_CONFIG = load_toml_file(PROMPTS_FILE, {})
    LINK_CONFIG = load_toml_file(LINK_CONFIG_FILE, {})
    MERCHANT_COUPON_PROMPTS = load_toml_file(MERCHANT_COUPON_PROMPTS_FILE, {})
    ORDER_LEADERBOARD_CONFIG = load_toml_file(ORDER_LEADERBOARD_CONFIG_FILE, {})

    KEYWORD_RESPONSES = process_response_factories(KEYWORD_RESPONSES)
    CLICK_EVENT_RESPONSES = process_response_factories(CLICK_EVENT_RESPONSES)
    
    print("[SUCCESS] 所有配置重新加载完成")
    print(f"[INFO] 当前公众号: {', '.join(WECHAT_ACCOUNTS.keys())}")
    return True


               
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
