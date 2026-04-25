"""
微信响应消息 Lambda 工厂函数
用于创建延迟执行的消息响应对象
"""
from .response import (
    TextRspMsg,
    ImageRspMsg,
    VoiceRspMsg,
    VideoRspMsg,
    MusicRspMsg,
    NewsRspMsg,
    EmptyRspMsg
)


def create_text_lambda(req_msg, content):
    """
    创建文本消息的 lambda 函数
    
    Args:
        req_msg: 请求消息对象
        content: 回复文本内容
    
    Returns:
        lambda 函数，调用时返回 TextRspMsg 对象
    
    Example:
        >>> lambda_func = create_text_lambda(req_msg, "你好")
        >>> rsp_msg = lambda_func()
        >>> xml = rsp_msg.dump_xml()
    """
    def _inner():
        text_rsp = TextRspMsg(req_msg=req_msg)
        text_rsp.content = content
        return text_rsp
    
    return _inner


def create_image_lambda(req_msg, media_id):
    """
    创建图片消息的 lambda 函数
    
    Args:
        req_msg: 请求消息对象
        media_id: 图片素材ID
    
    Returns:
        lambda 函数，调用时返回 ImageRspMsg 对象
    
    Example:
        >>> lambda_func = create_image_lambda(req_msg, "MEDIA_ID_123")
        >>> rsp_msg = lambda_func()
        >>> xml = rsp_msg.dump_xml()
    """
    def _inner():
        img_rsp = ImageRspMsg(req_msg=req_msg)
        img_rsp.media_id = media_id
        return img_rsp
    
    return _inner


def create_voice_lambda(req_msg, media_id):
    """
    创建语音消息的 lambda 函数
    
    Args:
        req_msg: 请求消息对象
        media_id: 语音素材ID
    
    Returns:
        lambda 函数，调用时返回 VoiceRspMsg 对象
    
    Example:
        >>> lambda_func = create_voice_lambda(req_msg, "MEDIA_ID_456")
        >>> rsp_msg = lambda_func()
        >>> xml = rsp_msg.dump_xml()
    """
    def _inner():
        voice_rsp = VoiceRspMsg(req_msg=req_msg)
        voice_rsp.media_id = media_id
        return voice_rsp
    
    return _inner


def create_video_lambda(req_msg, media_id, title=None, description=None):
    """
    创建视频消息的 lambda 函数
    
    Args:
        req_msg: 请求消息对象
        media_id: 视频素材ID
        title: 视频标题（可选）
        description: 视频描述（可选）
    
    Returns:
        lambda 函数，调用时返回 VideoRspMsg 对象
    
    Example:
        >>> lambda_func = create_video_lambda(
        ...     req_msg, 
        ...     "MEDIA_ID_789",
        ...     title="精彩视频",
        ...     description="这是一个精彩的视频"
        ... )
        >>> rsp_msg = lambda_func()
        >>> xml = rsp_msg.dump_xml()
    """
    def _inner():
        video_rsp = VideoRspMsg(req_msg=req_msg)
        video_rsp.media_id = media_id
        video_rsp.title = title
        video_rsp.description = description
        return video_rsp
    
    return _inner


def create_music_lambda(req_msg, title=None, description=None, music_url=None, 
                       hq_music_url=None, thumb_media_id=None):
    """
    创建音乐消息的 lambda 函数
    
    Args:
        req_msg: 请求消息对象
        title: 音乐标题
        description: 音乐描述
        music_url: 音乐链接
        hq_music_url: 高质量音乐链接
        thumb_media_id: 缩略图素材ID
    
    Returns:
        lambda 函数，调用时返回 MusicRspMsg 对象
    
    Example:
        >>> lambda_func = create_music_lambda(
        ...     req_msg,
        ...     title="美妙音乐",
        ...     description="一首好听的歌",
        ...     music_url="http://example.com/music.mp3",
        ...     thumb_media_id="THUMB_MEDIA_ID"
        ... )
        >>> rsp_msg = lambda_func()
        >>> xml = rsp_msg.dump_xml()
    """
    def _inner():
        music_rsp = MusicRspMsg(req_msg=req_msg)
        music_rsp.title = title
        music_rsp.description = description
        music_rsp.music_url = music_url
        music_rsp.hq_music_url = hq_music_url
        music_rsp.thumb_media_id = thumb_media_id
        return music_rsp
    
    return _inner


def create_news_lambda(req_msg, articles):
    """
    创建图文消息的 lambda 函数
    
    Args:
        req_msg: 请求消息对象
        articles: 图文列表，每个元素是包含 (title, description, pic_url, url) 的元组或字典
    
    Returns:
        lambda 函数，调用时返回 NewsRspMsg 对象
    
    Example:
        >>> articles = [
        ...     ("标题1", "描述1", "http://pic1.jpg", "http://url1.com"),
        ...     ("标题2", "描述2", "http://pic2.jpg", "http://url2.com"),
        ... ]
        >>> lambda_func = create_news_lambda(req_msg, articles)
        >>> rsp_msg = lambda_func()
        >>> xml = rsp_msg.dump_xml()
        
        或使用字典:
        >>> articles = [
        ...     {"title": "标题1", "description": "描述1", "pic_url": "http://pic1.jpg", "url": "http://url1.com"},
        ... ]
        >>> lambda_func = create_news_lambda(req_msg, articles)
    """
    def _inner():
        news_rsp = NewsRspMsg(req_msg=req_msg)
        
        for article in articles:
            if isinstance(article, dict):
                      
                news_rsp.add_article(
                    title=article.get('title', ''),
                    description=article.get('description', ''),
                    pic_url=article.get('pic_url', ''),
                    url=article.get('url', '')
                )
            elif isinstance(article, (tuple, list)):
                         
                news_rsp.add_article(
                    title=article[0],
                    description=article[1],
                    pic_url=article[2],
                    url=article[3]
                )
            else:
                raise ValueError("文章格式错误，应为字典或元组")
        
        return news_rsp
    
    return _inner


def create_empty_lambda():
    """
    创建空响应消息的 lambda 函数
    
    Returns:
        lambda 函数，调用时返回 EmptyRspMsg 对象
    
    Example:
        >>> lambda_func = create_empty_lambda()
        >>> rsp_msg = lambda_func()
        >>> result = rsp_msg.dump_xml()  # 返回 "success"
    """
    def _inner():
        return EmptyRspMsg()
    
    return _inner


                                                


def quick_image(req_msg, media_id):
    """
    快速创建并执行图片响应（语法糖）
    
    Args:
        req_msg: 请求消息对象
        media_id: 图片素材ID
    
    Returns:
        ImageRspMsg 对象
    """
    return create_image_lambda(req_msg, media_id)()


def quick_news(req_msg, articles):
    """
    快速创建并执行图文响应（语法糖）
    
    Args:
        req_msg: 请求消息对象
        articles: 图文列表
    
    Returns:
        NewsRspMsg 对象
    """
    return create_news_lambda(req_msg, articles)()


                                              

__all__ = [
            
    'create_text_lambda',
    'create_image_lambda',
    'create_voice_lambda',
    'create_video_lambda',
    'create_music_lambda',
    'create_news_lambda',
    'create_empty_lambda',
    
          
    'quick_image',
    'quick_news',
]

