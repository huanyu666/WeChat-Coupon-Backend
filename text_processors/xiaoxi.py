from flask import Flask, request, make_response
import hashlib
import xml.etree.ElementTree as ET
import time

                                                      
TOKEN = "123456"                 
                            
REPLY_TEXT = {
    "DIDI_TEXT": "🔥 滴滴优惠来啦！\n新用户立减10元：https://didi.com/2026\n老用户满30减8，速领！",
    "T3_TEXT": "💡 T3出行优惠说明：\n1. 每周五打车5折（最高减20元）\n2. 需提前在APP领券\n3. 有效期7天",
    "MEITUAN_TEXT": "🧧 美团外卖红包：\n满20减8、满35减12，点击领取：https://meituan.com/redpacket",
                              
}

app = Flask(__name__)


             
def check_signature(signature, timestamp, nonce):
    params = [TOKEN, timestamp, nonce]
    params.sort()
    temp_str = "".join(params).encode("utf-8")
    return hashlib.sha1(temp_str).hexdigest() == signature


                
def parse_xml(xml_data):
    root = ET.fromstring(xml_data)
    return {child.tag: child.text for child in root}


              
def gen_reply_xml(recv_dict, content):
    return f"""
    <xml>
        <ToUserName><![CDATA[{recv_dict['FromUserName']}]]></ToUserName>
        <FromUserName><![CDATA[{recv_dict['ToUserName']}]]></FromUserName>
        <CreateTime>{int(time.time())}</CreateTime>
        <MsgType><![CDATA[text]]></MsgType>
        <Content><![CDATA[{content}]]></Content>
    </xml>
    """


               
@app.route("/wechat", methods=["GET", "POST"])
def wechat_handler():
                 
    if request.method == "GET":
        if check_signature(request.args.get("signature"), request.args.get("timestamp"), request.args.get("nonce")):
            return request.args.get("echostr")
        return "Invalid", 403

                   
    xml_dict = parse_xml(request.data.decode("utf-8"))
                    
    if xml_dict.get("MsgType") == "event" and xml_dict.get("Event") == "CLICK":
        reply_content = REPLY_TEXT.get(xml_dict["EventKey"], "暂无相关内容哦～")
    else:
        reply_content = "欢迎使用本服务号～"

    resp = make_response(gen_reply_xml(xml_dict, reply_content))
    resp.headers["Content-Type"] = "application/xml"
    return resp


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)