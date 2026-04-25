import asyncio
from utils import http_client as requests
import json
from flask import Flask, request, make_response
import time

                                                         
APP_ID = "wx13702eea88b60c8d"
APP_SECRET = "5710e288c77e2729e2c482faef5fea2a"
                            
TOKEN = "123456"
                      
REPLY_CONFIG = {
          
    "JD_WAIMAI": {
        "type": "text",
        "content": "🎁 京东外卖专属福利：新用户满20减8，点击链接直达👉 https://waimai.jd.com/"
    },
    "TAOBAO_FLASH": {
        "type": "text",
        "content": "⚡ 淘宝闪购今日爆款：大牌低至1折，限时2小时👉 https://taobao.com/flash"
    },
    "MEITUAN_WAIMAI": {
        "type": "text",
        "content": "🍚 美团外卖红包天天领：满30减10，扫码立减👉 https://waimai.meituan.com/"
    },
                                   
    "ADD_GROUP": {
        "type": "image",
        "media_id": "Mq9uXCYiY6M6D83mx9jGQQb2II47X7ReUGO-mfygyVpGx2_tdcVtZBhEyAg2Y5Q1"                   
    }
}

                                                       
CUSTOM_MENU = {
    "button": [
        {
            "name": "更多",
            "sub_button": [
                {
                    "type": "click",
                    "name": "京东外卖",
                    "key": "JD_WAIMAI"
                },
                {
                    "type": "click",
                    "name": "淘宝闪购",
                    "key": "TAOBAO_FLASH"
                }
            ]
        },
        {
            "type": "click",
            "name": "美团外卖",
            "key": "MEITUAN_WAIMAI"
        },
        {
            "name": "加群",
            "sub_button": [
                {
                    "type": "click",
                    "name": "交流群",
                    "key": "ADD_GROUP"
                }
            ]
        }
    ]
}


                                                        
async def get_access_token():
    """获取微信接口调用凭证"""
    print("🔍 正在获取access_token...")
    url = f"https://api.weixin.qq.com/cgi-bin/token?grant_type=client_credential&appid={APP_ID}&secret={APP_SECRET}"
    try:
        resp = await requests.get(url, timeout=10)
        result = resp.json()
        if "access_token" in result:
            print(f"✅ access_token获取成功（有效期{result['expires_in']}秒）")
            return result["access_token"]
        else:
            print(f"❌ access_token获取失败：{result}")
            return None
    except Exception as e:
        print(f"❌ 请求异常：{str(e)}")
        return None


async def create_menu(access_token):
    """创建自定义菜单"""
    print("\n🔍 正在创建自定义菜单...")
    url = f"https://api.weixin.qq.com/cgi-bin/menu/create?access_token={access_token}"
    headers = {"Content-Type": "application/json"}
    try:
        menu_json = json.dumps(CUSTOM_MENU, ensure_ascii=False).encode("utf-8")
        resp = await requests.post(url, data=menu_json, headers=headers, timeout=10)
        result = resp.json()
        if result["errcode"] == 0:
            print("✅ 自定义菜单创建成功！")
            return True
        else:
            print(f"❌ 菜单创建失败：{result['errmsg']}（错误码：{result['errcode']}）")
            err_map = {
                40016: "菜单数量超限（一级≤3，二级≤5）",
                48001: "账号无权限（未认证/非服务号）",
                40001: "access_token无效",
                40033: "字符编码错误"
            }
            if result["errcode"] in err_map:
                print(f"💡 错误原因：{err_map[result['errcode']]}")
            return False
    except Exception as e:
        print(f"❌ 创建菜单异常：{str(e)}")
        return False


async def query_menu(access_token):
    """查询当前生效的菜单"""
    print("\n🔍 正在查询当前菜单配置...")
    url = f"https://api.weixin.qq.com/cgi-bin/menu/get?access_token={access_token}"
    try:
        resp = await requests.get(url, timeout=10)
        result = resp.json()
        if "menu" in result and result["menu"].get("button"):
            print("✅ 当前生效的菜单：")
            print(json.dumps(result["menu"]["button"], ensure_ascii=False, indent=2))
        else:
            print("❌ 未查询到有效菜单！")
            print(f"原始返回：{json.dumps(result, ensure_ascii=False)}")
    except Exception as e:
        print(f"❌ 查询菜单异常：{str(e)}")


async def delete_menu(access_token):
    """删除所有自定义菜单"""
    confirm = input("\n⚠️ 确定要删除所有菜单吗？(输入y确认，其他取消)：")
    if confirm.lower() != "y":
        print("🚫 取消删除操作")
        return
    print("🔍 正在删除自定义菜单...")
    url = f"https://api.weixin.qq.com/cgi-bin/menu/delete?access_token={access_token}"
    try:
        resp = await requests.get(url, timeout=10)
        result = resp.json()
        if result["errcode"] == 0:
            print("✅ 菜单删除成功！")
        else:
            print(f"❌ 菜单删除失败：{result['errmsg']}")
    except Exception as e:
        print(f"❌ 删除菜单异常：{str(e)}")


                                                        
app = Flask(__name__)


def verify_wx_token():
    """验证微信服务器推送的Token（公众号后台配置用）"""
    signature = request.args.get('signature')
    timestamp = request.args.get('timestamp')
    nonce = request.args.get('nonce')
    echostr = request.args.get('echostr')

                    
    temp_list = [TOKEN, timestamp, nonce]
    temp_list.sort()
    temp_str = ''.join(temp_list).encode('utf-8')
    import hashlib
    temp_str = hashlib.sha1(temp_str).hexdigest()

    if temp_str == signature:
        return make_response(echostr)
    else:
        return make_response("Token验证失败")


def build_text_reply(content, to_user, from_user):
    """构建文字回复XML"""
    xml_template = """
    <xml>
        <ToUserName><![CDATA[{to_user}]]></ToUserName>
        <FromUserName><![CDATA[{from_user}]]></FromUserName>
        <CreateTime>{create_time}</CreateTime>
        <MsgType><![CDATA[text]]></MsgType>
        <Content><![CDATA[{content}]]></Content>
    </xml>
    """
    create_time = int(time.time())
    xml = xml_template.format(
        to_user=to_user,
        from_user=from_user,
        create_time=create_time,
        content=content
    )
    return xml


def build_image_reply(media_id, to_user, from_user):
    """构建图片回复XML"""
    xml_template = """
    <xml>
        <ToUserName><![CDATA[{to_user}]]></ToUserName>
        <FromUserName><![CDATA[{from_user}]]></FromUserName>
        <CreateTime>{create_time}</CreateTime>
        <MsgType><![CDATA[image]]></MsgType>
        <Image>
            <MediaId><![CDATA[{media_id}]]></MediaId>
        </Image>
    </xml>
    """
    create_time = int(time.time())
    xml = xml_template.format(
        to_user=to_user,
        from_user=from_user,
        create_time=create_time,
        media_id=media_id
    )
    return xml


@app.route('/wx', methods=['GET', 'POST'])
def wx_handler():
    """微信消息接收与回复入口"""
                   
    if request.method == 'GET':
        return verify_wx_token()

                              
    try:
        from xml.etree import ElementTree as ET
        xml_data = ET.fromstring(request.data)

                      
        to_user = xml_data.find('ToUserName').text
        from_user = xml_data.find('FromUserName').text
        msg_type = xml_data.find('MsgType').text

                           
        if msg_type == 'event':
            event_type = xml_data.find('Event').text
            if event_type == 'CLICK':
                            
                event_key = xml_data.find('EventKey').text
                print(f"📱 用户{from_user}点击菜单：{event_key}")

                             
                if event_key in REPLY_CONFIG:
                    reply_conf = REPLY_CONFIG[event_key]
                    if reply_conf["type"] == "text":
                        reply_xml = build_text_reply(reply_conf["content"], from_user, to_user)
                    elif reply_conf["type"] == "image":
                        reply_xml = build_image_reply(reply_conf["media_id"], from_user, to_user)
                    else:
                        reply_xml = build_text_reply("暂不支持该类型回复", from_user, to_user)
                else:
                    reply_xml = build_text_reply("暂无相关回复内容", from_user, to_user)

                resp = make_response(reply_xml)
                resp.content_type = 'application/xml'
                return resp

                      
        elif msg_type == 'text':
            content = xml_data.find('Content').text
            reply_content = f"你发送的是：{content}\n点击菜单可查看更多福利～"
            reply_xml = build_text_reply(reply_content, from_user, to_user)
            resp = make_response(reply_xml)
            resp.content_type = 'application/xml'
            return resp

                      
        else:
            return make_response("success")

    except Exception as e:
        print(f"❌ 消息处理异常：{str(e)}")
        return make_response("success")


                                                     
async def main():
    print("===== 微信服务号管理工具 =====")
    print("1. 创建菜单\n2. 查询菜单\n3. 删除菜单\n4. 启动消息回复服务")
    choice = input("\n请选择操作（输入数字1/2/3/4）：")

                            
    access_token = await get_access_token() if choice in ["1", "2", "3"] else None

    if choice == "1" and access_token:
        await create_menu(access_token)
        await asyncio.sleep(1)
        await query_menu(access_token)
        print("\n📢 菜单创建后，取消关注再关注服务号即可刷新！")
    elif choice == "2" and access_token:
        await query_menu(access_token)
    elif choice == "3" and access_token:
        await delete_menu(access_token)
    elif choice == "4":
        print("\n🚀 启动消息回复服务（端口5000）...")
        print("⚠️ 请确保已将公众号后台「服务器配置」的URL设置为：http://你的服务器IP:5000/wx")
        app.run(host='0.0.0.0', port=5000, debug=False)
    else:
        print("❌ 无效选择或access_token获取失败")


if __name__ == "__main__":
    asyncio.run(main())
