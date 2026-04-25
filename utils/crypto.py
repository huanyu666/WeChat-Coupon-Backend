"""
微信消息加解密工具类
"""
import base64
import hashlib
import time
from Crypto.Cipher import AES


class WXBizMsgCrypt:
    """微信消息加解密类"""
    
    def __init__(self, sToken: str, sEncodingAESKey: str, sAppId: str):
        self.m_sToken = sToken
        self.m_sAppId = sAppId
        self.m_sEncodingAESKey = sEncodingAESKey
        self.m_sKey = base64.b64decode(sEncodingAESKey + "=")
        
    def verify_url(self, sMsgSignature: str, sTimeStamp: str, sNonce: str, sEchoStr: str) -> str:
        """验证URL"""
        sha1 = hashlib.sha1()
        param_list = [self.m_sToken, sTimeStamp, sNonce, sEchoStr]
        param_list.sort()
        sha1.update(''.join(param_list).encode('utf-8'))
        hashcode = sha1.hexdigest()
        
        if hashcode == sMsgSignature:
            cipher = AES.new(self.m_sKey, AES.MODE_CBC, self.m_sKey[:16])
            encrypted = base64.b64decode(sEchoStr)
            decrypted = cipher.decrypt(encrypted)
            
                             
            pad = decrypted[-1]
            if pad < 1 or pad > 32:
                pad = 0
            content = decrypted[16:-pad]
            xml_len = int.from_bytes(content[:4], byteorder='big')
            xml_content = content[4:4+xml_len].decode('utf-8')
            from_appid = content[4+xml_len:].decode('utf-8')
            
            if from_appid != self.m_sAppId:
                raise Exception("AppID不匹配")
                
            return xml_content
        else:
            raise Exception("签名验证失败")
    
    def decrypt_msg(self, sPostData: str, sMsgSignature: str, sTimeStamp: str, sNonce: str) -> str:
        """解密消息"""
        import xml.etree.ElementTree as ET
        
        root = ET.fromstring(sPostData)
        encrypt = root.find('Encrypt').text
        
        sha1 = hashlib.sha1()
        param_list = [self.m_sToken, sTimeStamp, sNonce, encrypt]
        param_list.sort()
        sha1.update(''.join(param_list).encode('utf-8'))
        hashcode = sha1.hexdigest()
        
        if hashcode != sMsgSignature:
            raise Exception("消息签名验证失败")
            
        cipher = AES.new(self.m_sKey, AES.MODE_CBC, self.m_sKey[:16])
        encrypted = base64.b64decode(encrypt)
        decrypted = cipher.decrypt(encrypted)
        
                         
        pad = decrypted[-1]
        if pad < 1 or pad > 32:
            pad = 0
        content = decrypted[16:-pad]
        xml_len = int.from_bytes(content[:4], byteorder='big')
        xml_content = content[4:4+xml_len].decode('utf-8')
        from_appid = content[4+xml_len:].decode('utf-8')
        
        if from_appid != self.m_sAppId:
            raise Exception("AppID不匹配")
            
        return xml_content
    
    def encrypt_msg(self, sReplyMsg: str, sNonce: str, timestamp: str = None) -> str:
        """
        加密消息
        
        按照微信官方文档加密流程：
        FullStr = random(16B) + msg_len(4B) + msg + appid
        其中 msg_len 是 msg 的字节长度（网络字节序）
        """
        if timestamp is None:
            timestamp = str(int(time.time()))
            
                         
        random_str = ''.join([chr(ord('a') + i % 26) for i in range(16)])
        random_bytes = random_str.encode('utf-8')
        
                                              
        msg_bytes = sReplyMsg.encode('utf-8')
        msg_len = len(msg_bytes).to_bytes(4, byteorder='big')         
        
                       
        appid_bytes = self.m_sAppId.encode('utf-8')
        
                                                                 
        msg_data = random_bytes + msg_len + msg_bytes + appid_bytes
        
                                        
        block_size = 32
        padding = block_size - len(msg_data) % block_size
        msg_data += bytes([padding] * padding)
        
                                            
        cipher = AES.new(self.m_sKey, AES.MODE_CBC, self.m_sKey[:16])
        encrypted = cipher.encrypt(msg_data)
        
                     
        encrypt_str = base64.b64encode(encrypted).decode('utf-8')
        
                   
        sha1 = hashlib.sha1()
        param_list = [self.m_sToken, timestamp, sNonce, encrypt_str]
        param_list.sort()
        sha1.update(''.join(param_list).encode('utf-8'))
        msg_signature = sha1.hexdigest()
        
                     
        xml_form = """<xml>
<Encrypt><![CDATA[{encrypt}]]></Encrypt>
<MsgSignature><![CDATA[{msg_signature}]]></MsgSignature>
<TimeStamp>{timestamp}</TimeStamp>
<Nonce><![CDATA[{nonce}]]></Nonce>
</xml>""".format(
            encrypt=encrypt_str,
            msg_signature=msg_signature,
            timestamp=timestamp,
            nonce=sNonce
        )
        
        return xml_form


def verify_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    """验证微信签名（明文模式）"""
    param_list = [token, timestamp, nonce]
    param_list.sort()
    sha1 = hashlib.sha1()
    sha1.update(''.join(param_list).encode('utf-8'))
    hashcode = sha1.hexdigest()
    return hashcode == signature

