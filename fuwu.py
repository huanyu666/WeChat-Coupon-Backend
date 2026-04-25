import asyncio
from utils import http_client as requests
import json

                                                       
APP_ID = "wxef0fcfcea0563eb5"
APP_SECRET = "129b56070f24b363ddae02414101280d"

                                                          
                            
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
    """获取微信接口调用凭证（access_token）"""
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
    """删除所有自定义菜单（谨慎使用）"""
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


                                                     
async def main():
    print("===== 微信服务号自定义菜单管理工具 =====")
    print("1. 创建菜单\n2. 查询菜单\n3. 删除菜单")

            
    choice = input("\n请选择操作（输入数字1/2/3）：")
    if choice not in ["1", "2", "3"]:
        print("❌ 无效选择，程序退出")
        return

                             
    access_token = await get_access_token()
    if not access_token:
        print("❌ 程序终止：access_token获取失败")
        return

            
    if choice == "1":
        await create_menu(access_token)
                      
        await asyncio.sleep(1)
        await query_menu(access_token)
        print("\n📢 重要提示：")
        print("1. 请取消关注【你的服务号名称】后重新关注（立即刷新菜单）")
        print("2. 未显示则等待5-10分钟，或检查服务号是否已认证")
    elif choice == "2":
        await query_menu(access_token)
    elif choice == "3":
        await delete_menu(access_token)


if __name__ == "__main__":
    asyncio.run(main())
