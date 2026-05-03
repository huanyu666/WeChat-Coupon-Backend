"""
微信消息响应类
参考: https://developers.weixin.qq.com/doc/offiaccount/Message_Management/Passive_user_reply_message.html
"""
import xml.etree.cElementTree as ET
import time


class WxRspMsg(object):
    """微信响应消息基类"""
    
    def __init__(self, req_msg=None):
        self.to_user_name = None
        self.from_user_name = None
        self.create_time = int(time.time())
        self.msg_type = None
        self.xml_tree = ET.Element("xml")
        if req_msg is not None:
                       
            self.to_user_name = req_msg.get("FromUserName")
            self.from_user_name = req_msg.get("ToUserName")

    @staticmethod
    def create_msg(msg_type, req_msg=None):
        """工厂方法：创建指定类型的响应消息"""
        if msg_type == "text":
            return TextRspMsg(req_msg)
        elif msg_type == "image":
            return ImageRspMsg(req_msg)
        elif msg_type == "voice":
            return VoiceRspMsg(req_msg)
        elif msg_type == "video":
            return VideoRspMsg(req_msg)
        elif msg_type == "music":
            return MusicRspMsg(req_msg)
        elif msg_type == "news":
            return NewsRspMsg(req_msg)
        else:
            raise Exception("unknown msg type: " + msg_type)

    def insert_elem(self, name, value):
        """插入XML元素"""
        if value is None:
            return
            
        curr_node = self.xml_tree

        for n in name.split("/"):
            if curr_node.find(n) is None:
                e = ET.Element(n)
                curr_node.append(e)
            curr_node = curr_node.find(n)
        curr_node.text = str(value)

    def update_xml(self):
        """更新XML结构"""
        self.insert_elem("ToUserName", self.to_user_name)
        self.insert_elem("FromUserName", self.from_user_name)
        self.insert_elem("CreateTime", str(self.create_time))
        self.insert_elem("MsgType", self.msg_type)

    def dump_xml(self):
        """生成XML字符串 - 修复中文编码问题"""
        self.update_xml()
                                      
        xml_str = ET.tostring(self.xml_tree, encoding="utf-8", method="xml")
                         
        if isinstance(xml_str, bytes):
            return xml_str
        return xml_str.encode('utf-8')


class EmptyRspMsg(WxRspMsg):
    """空响应消息（返回success）"""
    
    def __init__(self):
        super().__init__()

    def update_xml(self):
        pass

    def dump_xml(self):
        return "success".encode('utf-8')


class TextRspMsg(WxRspMsg):
    """文本响应消息"""
    
    def __init__(self, req_msg=None):
        super().__init__(req_msg)
        self.msg_type = "text"
        self.content = None
        self.shortlink_fallback_content = None
        self.shortlink_fallback_reason = None

    def update_xml(self):
        super(TextRspMsg, self).update_xml()
        self.insert_elem("Content", self.content)


class ImageRspMsg(WxRspMsg):
    """图片响应消息
    <xml>
      <ToUserName><![CDATA[toUser]]></ToUserName>
      <FromUserName><![CDATA[fromUser]]></FromUserName>
      <CreateTime>12345678</CreateTime>
      <MsgType><![CDATA[image]]></MsgType>
      <Image>
        <MediaId><![CDATA[media_id]]></MediaId>
      </Image>
    </xml>
    """
    
    def __init__(self, req_msg=None):
        super().__init__(req_msg)
        self.msg_type = "image"
        self.media_id = None

    def update_xml(self):
        super(ImageRspMsg, self).update_xml()
        self.insert_elem("Image/MediaId", self.media_id)


class VoiceRspMsg(WxRspMsg):
    """语音响应消息
    <xml>
      <ToUserName><![CDATA[toUser]]></ToUserName>
      <FromUserName><![CDATA[fromUser]]></FromUserName>
      <CreateTime>12345678</CreateTime>
      <MsgType><![CDATA[voice]]></MsgType>
      <Voice>
        <MediaId><![CDATA[media_id]]></MediaId>
      </Voice>
    </xml>
    """
    
    def __init__(self, req_msg=None):
        super().__init__(req_msg)
        self.msg_type = "voice"
        self.media_id = None

    def update_xml(self):
        super(VoiceRspMsg, self).update_xml()
        self.insert_elem("Voice/MediaId", self.media_id)


class VideoRspMsg(WxRspMsg):
    """视频响应消息
    <xml>
      <ToUserName><![CDATA[toUser]]></ToUserName>
      <FromUserName><![CDATA[fromUser]]></FromUserName>
      <CreateTime>12345678</CreateTime>
      <MsgType><![CDATA[video]]></MsgType>
      <Video>
        <MediaId><![CDATA[media_id]]></MediaId>
        <Title><![CDATA[title]]></Title>
        <Description><![CDATA[description]]></Description>
      </Video>
    </xml>
    """
    
    def __init__(self, req_msg=None):
        super().__init__(req_msg)
        self.msg_type = "video"
        self.media_id = None
        self.title = None
        self.description = None

    def update_xml(self):
        super(VideoRspMsg, self).update_xml()
        self.insert_elem("Video/MediaId", self.media_id)
        self.insert_elem("Video/Title", self.title)
        self.insert_elem("Video/Description", self.description)


class MusicRspMsg(WxRspMsg):
    """音乐响应消息
    <xml>
      <ToUserName><![CDATA[toUser]]></ToUserName>
      <FromUserName><![CDATA[fromUser]]></FromUserName>
      <CreateTime>12345678</CreateTime>
      <MsgType><![CDATA[music]]></MsgType>
      <Music>
        <Title><![CDATA[TITLE]]></Title>
        <Description><![CDATA[DESCRIPTION]]></Description>
        <MusicUrl><![CDATA[MUSIC_Url]]></MusicUrl>
        <HQMusicUrl><![CDATA[HQ_MUSIC_Url]]></HQMusicUrl>
        <ThumbMediaId><![CDATA[media_id]]></ThumbMediaId>
      </Music>
    </xml>
    """
    
    def __init__(self, req_msg=None):
        super().__init__(req_msg)
        self.msg_type = "music"
        self.title = None
        self.description = None
        self.music_url = None
        self.hq_music_url = None
        self.thumb_media_id = None

    def update_xml(self):
        super(MusicRspMsg, self).update_xml()
        self.insert_elem("Music/Title", self.title)
        self.insert_elem("Music/Description", self.description)
        self.insert_elem("Music/MusicUrl", self.music_url)
        self.insert_elem("Music/HQMusicUrl", self.hq_music_url)
        self.insert_elem("Music/ThumbMediaId", self.thumb_media_id)


class WxArticle(object):
    """图文消息文章"""
    
    def __init__(self, title, description, pic_url, url):
        self.title = title
        self.description = description
        self.pic_url = pic_url
        self.url = url


class NewsRspMsg(WxRspMsg):
    """图文响应消息
    <xml>
      <ToUserName><![CDATA[toUser]]></ToUserName>
      <FromUserName><![CDATA[fromUser]]></FromUserName>
      <CreateTime>12345678</CreateTime>
      <MsgType><![CDATA[news]]></MsgType>
      <ArticleCount>1</ArticleCount>
      <Articles>
        <item>
          <Title><![CDATA[title1]]></Title>
          <Description><![CDATA[description1]]></Description>
          <PicUrl><![CDATA[picurl]]></PicUrl>
          <Url><![CDATA[url]]></Url>
        </item>
      </Articles>
    </xml>
    """
    
    def __init__(self, req_msg=None):
        super().__init__(req_msg)
        self.msg_type = "news"
        self.articles = []

    def add_article(self, title, description, pic_url, url):
        """添加图文文章"""
        self.articles.append(WxArticle(title, description, pic_url, url))

    def update_xml(self):
        super(NewsRspMsg, self).update_xml()
        self.insert_elem("ArticleCount", str(len(self.articles)))
        articles = ET.Element("Articles")
        for a in self.articles:
            item = ET.Element("item")
            
            title = ET.Element("Title")
            title.text = a.title
            item.append(title)
            
            description = ET.Element("Description")
            description.text = a.description
            item.append(description)
            
            pic_url = ET.Element("PicUrl")
            pic_url.text = a.pic_url
            item.append(pic_url)
            
            url = ET.Element("Url")
            url.text = a.url
            item.append(url)
            
            articles.append(item)

        self.xml_tree.append(articles)
