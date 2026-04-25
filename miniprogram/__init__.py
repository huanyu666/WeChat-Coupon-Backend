"""
小程序处理模块
"""
from .base_processor import BaseMiniprogramProcessor
from .meituan import MeituanProcessor


__all__ = [
    'BaseMiniprogramProcessor',
    'MeituanProcessor',
]
