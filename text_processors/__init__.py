"""
文本处理器模块

"""
from .base_processor import BaseTextProcessor
from .meituan_link import MeituanLinkProcessor
from .keyword_reply import KeywordReplyProcessor
from .stateful_processor import StatefulTextProcessor
from .get_link_processor import GetLinkProcessor
from .generate_link_processor import GenerateLinkProcessor
from .get_tuangou_processor import GetTuangouProcessor
from .get_meituan_processor import GetMeituanProcessor
from .get_eleme_processor import GetElemeProcessor
from .get_jd_processor import GetJdProcessor
from .verification_code_processor import ActivationCodeProcessor
from .meituan_order_query_processor import MeituanOrderQueryProcessor
from .p_value_processor import PValueProcessor
from .scene_processor import SceneProcessor
from .meituan_shop_query_processor import MeituanShopQueryProcessor
from .cache_test_processor import CacheTestProcessor
from .meituan_miniprogram_link_processor import MeituanMiniprogramLinkProcessor
from .echo_user_id_processor import EchoUserIdProcessor
from .merchant_coupon_processor import MerchantCouponProcessor
from .leaderboard_config_processor import LeaderboardConfigProcessor
from .shortlink_generator_processor import ShortlinkGeneratorProcessor
from .meituan_magical_coupon_processor import MeituanMagicalCouponProcessor

                       
__all__ = [
    'BaseTextProcessor',
    'MeituanLinkProcessor',
    'KeywordReplyProcessor',
    'StatefulTextProcessor',
    'GetLinkProcessor',
    'GenerateLinkProcessor',
    'GetTuangouProcessor',
    'GetMeituanProcessor',
    'GetElemeProcessor',
    'GetJdProcessor',
    'ActivationCodeProcessor',
    'MeituanOrderQueryProcessor',
    'PValueProcessor',
    'SceneProcessor',
    'MeituanShopQueryProcessor',
    'CacheTestProcessor',
    'MeituanMiniprogramLinkProcessor',
    'EchoUserIdProcessor',
    'MerchantCouponProcessor',
    'LeaderboardConfigProcessor',
    'ShortlinkGeneratorProcessor',
    'MeituanMagicalCouponProcessor',
]
