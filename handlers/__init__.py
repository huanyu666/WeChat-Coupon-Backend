"""
消息处理器模块
"""
from .base_handler import BaseHandler
from .text_handler import TextHandler
from .image_handler import ImageHandler
from .voice_handler import VoiceHandler
from .video_handler import VideoHandler
from .location_handler import LocationHandler
from .link_handler import LinkHandler
from .event_handler import EventHandler
from .miniprogram_handler import MiniprogramHandler

__all__ = [
    'BaseHandler',
    'TextHandler',
    'ImageHandler',
    'VoiceHandler',
    'VideoHandler',
    'LocationHandler',
    'LinkHandler',
    'EventHandler',
    'MiniprogramHandler',
]

