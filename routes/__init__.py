"""
API 路由模块
"""
from .auth import router as auth_router
from .material import router as material_router
from .wechat import router as wechat_router
from .christmas_hat import router as christmas_hat_router
from .waimai import router as waimai_router
from .order_rankings import router as order_rankings_router
from .sbti import router as sbti_router
from .site_verification import router as site_verification_router
from .migration import router as migration_router
from .system_settings import router as system_settings_router
from .shortlink import router as shortlink_router

__all__ = ['auth_router', 'material_router', 'wechat_router', 'christmas_hat_router', 'waimai_router', 'order_rankings_router', 'sbti_router', 'site_verification_router', 'migration_router', 'system_settings_router', 'shortlink_router']
