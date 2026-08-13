"""
美团外卖商家查询处理器

"""
import re
import urllib.parse
from utils import http_client as requests
from typing import Dict, Any, Optional, List
from link_handlers.api_client import LinkConversionAPI
from .stateful_processor import StatefulTextProcessor
from utils.response import TextRspMsg
from utils.account_config import resolve_zmkey
from utils.logger import setup_logger
from utils.meituan_utils import generate_miniprogram_link, build_extra_params_url
from utils.merchant_benefits import aquery_benefits_for_wechat, format_benefits_for_wechat
from config.config import ACCOUNT_SPECIFIC_CONFIGS


class MeituanShopQueryProcessor(StatefulTextProcessor):
    
          
    STATE_WAITING_QUERY = "waiting_query"
    STATE_SHOWING_RESULTS = "showing_results" 
    STATE_WAITING_MINIPROGRAM = "waiting_miniprogram" 
    
               
    SCHEME_QUERY_KEYWORD = "查看"
    
             
    FREE_DELIVERY_MINIPROGRAM_LINK = "mp://iBLSS1Aa2kGrE5B"
    
    def __init__(self, logger):
        """
        初始化处理器
        
        Args:
            logger: 日志记录器
        """
        super().__init__(logger, state_timeout=120)
        self.logger.info("MeituanShopQueryProcessor 初始化完成")

    def _get_settings(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        account_id = msg.get("ToUserName", "")
        specific_config = ACCOUNT_SPECIFIC_CONFIGS.get(account_id, {})
        settings = specific_config.get("meituan_shop_query_settings", {})
        return settings if isinstance(settings, dict) else {}

    def _get_text(self, msg: Dict[str, Any], key: str, default: str) -> str:
        value = self._get_settings(msg).get(key)
        return str(value) if value not in (None, "") else default

    def _render_text(self, msg: Dict[str, Any], key: str, default: str, **kwargs: Any) -> str:
        template = self._get_text(msg, key, default)
        try:
            return template.format(**kwargs)
        except Exception:
            return template

    def _get_keywords(self, msg: Dict[str, Any], key: str, default: list[str]) -> list[str]:
        raw_value = self._get_settings(msg).get(key, default)
        if isinstance(raw_value, str):
            raw_items = raw_value.splitlines()
        elif isinstance(raw_value, (list, tuple, set)):
            raw_items = list(raw_value)
        else:
            raw_items = list(default)
        return [str(item or "").strip() for item in raw_items if str(item or "").strip()]

    def _get_results_sort_links_text(self, msg: Dict[str, Any]) -> str:
        return self._get_text(msg, "results_sort_links_text", (
            "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 1\">🔢 智能排序</a> | "
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 2\">销量优先</a> | "
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 3\">速度优先</a> | "
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 4\">评分优先</a>\n"
        ))

    def is_trigger_for_message(self, text: str, msg: Dict[str, Any]) -> bool:
        trigger_keywords = self._get_keywords(msg, "trigger_keywords", ["外卖商家查询", "商家查询", "店铺查询"])
        text_lower = text.lower().strip()
        return any(keyword.lower() in text_lower for keyword in trigger_keywords)

    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")
        user_state = self.get_user_state(user_id, check_timeout=True)

        if user_state and user_state.get("processor") == self.__class__.__name__:
            return await self.ahandle_state(msg, text, user_state)

        if self.is_trigger_for_message(text, msg):
            return await self.ahandle_trigger(msg, text)

        return None
    
    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        """
        处理触发关键词
        
        Args:
            msg: 消息字典
            text: 文本内容
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 触发外卖商家查询功能")
        
                  
        self.set_user_state(user_id, self.STATE_WAITING_QUERY)
        
        rsp = TextRspMsg(msg)
        rsp.content = self._get_text(msg, "intro_message", (
            "1⃣团团40-20和58-25\n\n"
            " 👉http://dpurl.cn/JqA3NkRz\n\n"
            "2⃣团团有20-10\n\n"
            "👉http://dpurl.cn/35yy5s7z\n\n"
            "3⃣大众点评领45-20 38-18等\n\n"
            "👉mp://tIzKEWghrQtKHUv\n\n"
            "📋 外卖商家查询\n\n"
            "请按以下步骤准备信息：\n\n"
            "1 访问以下链接获取坐标：\n"
            "<a href=\"https://www.mapchaxun.cn/Regeo\">点击获取坐标</a>\n\n"
            "2 访问以下链接登录美团：\n"
            "<a href=\"https://passport.meituan.com/useraccount/ilogin\">点击登录美团</a>\n\n"
            "3 准备好后，请发送以下格式的信息：\n"
            "坐标 链接 关键词\n\n"
            "示例格式：\n"
            "999.24591547908291,99.510910831008815 https://i.meituan.com/mttouch/page/account?userId=xxx&token=xxx 华莱士\n\n"
            "（坐标、链接、关键词之间可以用空格、逗号分隔，也可以没有分隔符）"
        ))
        
        return rsp
    
    async def ahandle_state(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Optional[Any]:
        import time
        from .stateful_processor import USER_STATES

        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        current_state = state.get("state")

        msg_type = msg.get("MsgType", "")
        if msg_type == "miniprogrampage" and current_state == self.STATE_WAITING_MINIPROGRAM:
            return self._handle_miniprogram_message(msg, state)

        if current_state == self.STATE_WAITING_MINIPROGRAM and text.strip():
            if text.strip().startswith("#小程序://") or text.strip().startswith("mp://"):
                return await self._ahandle_link_message(msg, text, state)

        if current_state in [self.STATE_WAITING_QUERY, self.STATE_SHOWING_RESULTS]:
            scheme_response = await self._ahandle_shop_number_query(msg, text, state)
            if scheme_response:
                return scheme_response

        cancel_keywords = self._get_keywords(msg, "cancel_keywords", ["取消", "退出", "返回", "quit", "cancel", "back"])
        if text.strip() in cancel_keywords:
            if current_state == self.STATE_WAITING_MINIPROGRAM:
                state["state"] = self.STATE_SHOWING_RESULTS
                self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
                rsp = TextRspMsg(msg)
                rsp.content = self._get_text(msg, "return_to_results_message", "已返回商铺选择，您可以继续浏览其他店铺")
                return rsp
            self.clear_user_state(user_id, "用户取消操作")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "cancel_message", "已取消查询")
            return rsp

        if user_id in USER_STATES:
            USER_STATES[user_id]["last_update_time"] = time.time()

        if current_state == self.STATE_WAITING_QUERY:
            return await self._ahandle_query(msg, text, state)
        if current_state == self.STATE_SHOWING_RESULTS:
            return await self._ahandle_results_action(msg, text, state)
        if current_state == self.STATE_WAITING_MINIPROGRAM:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "waiting_miniprogram_message", (
                "⏳ 正在等待您发送小程序或链接...\n\n"
                "请按照以下方式操作：\n\n"
                "方式一：mp://iBLSS1Aa2kGrE5B发送小程序卡片\n"
                "1. 点击刚才的免配链接进入小程序\n"
                "2. 选择店铺并收藏\n"
                "3. 发送收藏的小程序卡片给我mp://lXSt9beJdGtUNin\n\n"
                "方式二：发送链接格式\n"
                "直接发送复制链接得到的 #小程序:// 或 mp:// 格式的链接\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp

        self.logger.warning(f"[{account_name}] 未知状态: {current_state}")
        self.clear_user_state(user_id, "未知状态")
        return None
    
    
    def _handle_query(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
                        
        parsed = self._parse_query_input(text, state)
        
        if not parsed:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "parse_error_message", (
                "❌ 无法解析输入，请检查格式\n\n"
                "格式：坐标 链接 关键词\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=111.11111111111111,11.111111111111111 https://i.meituan.com/mttouch/page/account?userId=xxx&token=xxx 华莱士\">📤 点击查看示例格式</a>"
            ))
            return rsp
        
                      
        userId = parsed.get("userId")
        token = parsed.get("token")
        longitude = parsed.get("longitude")
        latitude = parsed.get("latitude")
        keyword = parsed.get("keyword", "")
        
        if not userId or not token or not longitude or not latitude:
            missing = []
            if not userId or not token:
                missing.append("用户信息（链接）")
            if not longitude or not latitude:
                missing.append("坐标")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "missing_info_template", "❌ 缺少必要信息：{missing}\n\n请确保输入包含坐标和用户信息链接").format(
                missing=", ".join(missing)
            )
            return rsp
        
                   
        wm_longitude = self._convert_coordinate_to_int(longitude)
        wm_latitude = self._convert_coordinate_to_int(latitude)
        
        self.logger.info(f"[{account_name}] 查询参数 - userId: {userId}, keyword: {keyword}, 坐标: ({wm_longitude}, {wm_latitude})")
        
                                              
        result = self._query_shops(userId, token, wm_longitude, wm_latitude, keyword, page_num=1, page_size=10, free_delivery_only=False)
        
        if not result or not result.get("success"):
                            
            error_msg = result.get('error', '网络错误') if result else '网络错误'
            self.clear_user_state(user_id, f"查询失败: {error_msg}")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "query_failed_template", (
                "❌ 查询失败：{error}\n\n"
                "请检查登录是否过期，或稍后重试\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=商家查询\">🔄 重试</a>"
            )).format(error=error_msg)
            return rsp
        
                
        shops = self._extract_shops_from_result(result["data"])
        
        if not shops:
            self.logger.info(f"[{account_name}] 当前页没有店铺（未筛选免配），直接返回错误")
            self.clear_user_state(user_id, "第一页没有店铺")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "empty_page_message", (
                "⚠️ 当前页没有店铺\n\n"
                "💡 提示：可能是登录已过期，请重新登录获取链接后重试\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=商家查询\">🔄 重新查询</a>"
            ))
            return rsp
        
                               
        free_delivery_shops = [s for s in shops if self._is_free_delivery(s)]
        
                                         
        if not free_delivery_shops:
            self.logger.info(f"[{account_name}] 当前页没有免配店铺，增大页面大小至20")
            result = self._query_shops(userId, token, wm_longitude, wm_latitude, keyword, page_num=1, page_size=20, free_delivery_only=False)
            
            if result and result.get("success"):
                shops = self._extract_shops_from_result(result["data"])
                free_delivery_shops = [s for s in shops if self._is_free_delivery(s)]
                
                                      
                if not free_delivery_shops:
                    return self._create_no_shops_response(
                        msg, state, userId, token, wm_longitude, wm_latitude,
                        keyword, result["data"], page_num=1, is_free_delivery=True
                    )
        
                      
        state["userId"] = userId
        state["token"] = token
        state["wm_longitude"] = str(wm_longitude)
        state["wm_latitude"] = str(wm_latitude)
        state["keyword"] = keyword
        state["current_page"] = 1
        state["sortType"] = 0
        state["filterInfo"] = ""
        state["free_delivery_only"] = False
        state["query_result"] = result["data"]
        
                                 
        shops = self._extract_shops_from_result(result["data"])
        state["cached_shops"] = shops               
        state["cached_page"] = 1             
        
        self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
        
                          
        return self._format_results(msg, state)

    async def _ahandle_query(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        parsed = self._parse_query_input(text, state)
        if not parsed:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "parse_error_message", (
                "❌ 无法解析输入，请检查格式\n\n"
                "格式：坐标 链接 关键词\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=111.11111111111111,11.111111111111111 https://i.meituan.com/mttouch/page/account?userId=xxx&token=xxx 华莱士\">📤 点击查看示例格式</a>"
            ))
            return rsp

        userId = parsed.get("userId")
        token = parsed.get("token")
        longitude = parsed.get("longitude")
        latitude = parsed.get("latitude")
        keyword = parsed.get("keyword", "")

        if not userId or not token or not longitude or not latitude:
            missing = []
            if not userId or not token:
                missing.append("用户信息（链接）")
            if not longitude or not latitude:
                missing.append("坐标")
            rsp = TextRspMsg(msg)
            rsp.content = self._render_text(msg, "missing_info_template", "❌ 缺少必要信息：{missing}\n\n请确保输入包含坐标和用户信息链接", missing=", ".join(missing))
            return rsp

        wm_longitude = self._convert_coordinate_to_int(longitude)
        wm_latitude = self._convert_coordinate_to_int(latitude)
        self.logger.info(f"[{account_name}] 查询参数 - userId: {userId}, keyword: {keyword}, 坐标: ({wm_longitude}, {wm_latitude})")

        result = await self._aquery_shops(userId, token, wm_longitude, wm_latitude, keyword, page_num=1, page_size=10, free_delivery_only=False)
        if not result or not result.get("success"):
            error_msg = result.get('error', '网络错误') if result else '网络错误'
            self.clear_user_state(user_id, f"查询失败: {error_msg}")
            rsp = TextRspMsg(msg)
            rsp.content = self._render_text(msg, "query_failed_template", (
                "❌ 查询失败：{error}\n\n"
                "请检查登录是否过期，或稍后重试\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=商家查询\">🔄 重试</a>"
            ), error=error_msg)
            return rsp

        shops = self._extract_shops_from_result(result["data"])
        if not shops:
            self.logger.info(f"[{account_name}] 当前页没有店铺（未筛选免配），直接返回错误")
            self.clear_user_state(user_id, "第一页没有店铺")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "empty_page_message", (
                "⚠️ 当前页没有店铺\n\n"
                "💡 提示：可能是登录已过期，请重新登录获取链接后重试\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=商家查询\">🔄 重新查询</a>"
            ))
            return rsp

        free_delivery_shops = [s for s in shops if self._is_free_delivery(s)]
        if not free_delivery_shops:
            self.logger.info(f"[{account_name}] 当前页没有免配店铺，增大页面大小至20")
            result = await self._aquery_shops(userId, token, wm_longitude, wm_latitude, keyword, page_num=1, page_size=20, free_delivery_only=False)
            if result and result.get("success"):
                shops = self._extract_shops_from_result(result["data"])
                free_delivery_shops = [s for s in shops if self._is_free_delivery(s)]
                if not free_delivery_shops:
                    return self._create_no_shops_response(
                        msg, state, userId, token, wm_longitude, wm_latitude,
                        keyword, result["data"], page_num=1, is_free_delivery=True
                    )

        state["userId"] = userId
        state["token"] = token
        state["wm_longitude"] = str(wm_longitude)
        state["wm_latitude"] = str(wm_latitude)
        state["keyword"] = keyword
        state["current_page"] = 1
        state["sortType"] = 0
        state["filterInfo"] = ""
        state["free_delivery_only"] = False
        state["query_result"] = result["data"]
        shops = self._extract_shops_from_result(result["data"])
        state["cached_shops"] = shops
        state["cached_page"] = 1

        self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
        return self._format_results(msg, state)
    
    def _create_no_shops_response(self, msg: Dict[str, Any], state: Dict[str, Any], 
                                   userId: str, token: str, wm_longitude: int, wm_latitude: int,
                                   keyword: str, query_result: Dict[str, Any], 
                                   page_num: int = 1, is_free_delivery: bool = False) -> TextRspMsg:
        """
        创建"没有店铺"的响应消息（公共方法）
        
        Args:
            msg: 消息字典
            state: 状态字典
            userId: 用户ID
            token: 认证token
            wm_longitude: 经度
            wm_latitude: 纬度
            keyword: 关键词
            query_result: 查询结果数据
            page_num: 页码（默认1）
            is_free_delivery: 是否是免配查询（默认False）
            
        Returns:
            响应消息对象
        """
                      
        state["userId"] = userId
        state["token"] = token
        state["wm_longitude"] = str(wm_longitude)
        state["wm_latitude"] = str(wm_latitude)
        state["keyword"] = keyword
        state["current_page"] = page_num
        state["sortType"] = state.get("sortType", 0)
        state["filterInfo"] = state.get("filterInfo", "")
        state["free_delivery_only"] = is_free_delivery
        state["query_result"] = query_result
        
        user_id = msg.get("FromUserName", "")
        self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
        
                            
        rsp = TextRspMsg(msg)
        if is_free_delivery:
            rsp.content = self._get_text(msg, "no_free_delivery_page_message", (
                "⚠️ 当前页没有免配送费店铺\n\n"
                "💡 提示：可能是登录已过期，请重新登录后查询\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=重新查询\">🔄 重新查询</a>\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=下一页\">➡️ 点击查看下一页</a>"
            ))
        else:
            rsp.content = self._get_text(msg, "empty_page_message", (
                "⚠️ 当前页没有店铺\n\n"
                "💡 提示：可能是登录已过期，请重新登录获取链接后重试\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=商家查询\">🔄 重新查询</a>"
            ))
        return rsp
    
    def _handle_results_action(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        """
        处理结果页面的操作（翻页、排序、筛选等）
        
        Args:
            msg: 消息字典
            text: 用户输入的操作
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        text_lower = text.strip().lower()
        
              
        if text_lower in ["上一页", "上一页", "prev", "p"]:
            current_page = state.get("current_page", 1)
            if current_page > 1:
                new_page = current_page - 1
                state["current_page"] = new_page
                               
                self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
                self.logger.info(f"[{account_name}] 翻页：从第 {current_page} 页到第 {new_page} 页")
                return self._refresh_results(msg, state)
            else:
                rsp = TextRspMsg(msg)
                rsp.content = self._get_text(msg, "first_page_message", "已经是第一页了")
                return rsp
        
        elif text_lower in ["下一页", "下一页", "next", "n"]:
                      
            query_result = state.get("query_result", {})
            data = query_result.get("data", {})
            json_data = data.get("json_data", {})
            page_info = json_data.get("page", {})
            has_next = page_info.get("hasNextPage", False)
            
            if has_next:
                current_page = state.get("current_page", 1)
                new_page = current_page + 1
                state["current_page"] = new_page
                               
                user_id = msg.get("FromUserName", "")
                self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
                self.logger.info(f"[{account_name}] 翻页：从第 {current_page} 页到第 {new_page} 页")
                return self._refresh_results(msg, state)
            else:
                rsp = TextRspMsg(msg)
                rsp.content = self._get_text(msg, "last_page_message", "已经是最后一页了")
                return rsp
        
              
        elif text_lower.startswith("排序") or text_lower.startswith("sort"):
                    
            sort_type = self._parse_sort_type(text)
            if sort_type is not None:
                state["sortType"] = sort_type
                state["current_page"] = 1          
                return self._refresh_results(msg, state)
            else:
                rsp = TextRspMsg(msg)
                rsp.content = self._get_text(msg, "sort_help_message", (
                    "请选择排序方式：\n"
                    "1. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 1\">智能排序</a>\n"
                    "2. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 2\">销量优先</a>\n"
                    "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 3\">速度优先</a>\n"
                    "4. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 4\">评分优先</a>"
                ))
                return rsp

    async def _ahandle_results_action(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        text_lower = text.strip().lower()

        if text_lower in ["上一页", "上一页", "prev", "p"]:
            current_page = state.get("current_page", 1)
            if current_page > 1:
                new_page = current_page - 1
                state["current_page"] = new_page
                self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
                self.logger.info(f"[{account_name}] 翻页：从第 {current_page} 页到第 {new_page} 页")
                return await self._arefresh_results(msg, state)
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "first_page_message", "已经是第一页了")
            return rsp

        if text_lower in ["下一页", "下一页", "next", "n"]:
            query_result = state.get("query_result", {})
            data = query_result.get("data", {})
            json_data = data.get("json_data", {})
            page_info = json_data.get("page", {})
            has_next = page_info.get("hasNextPage", False)
            if has_next:
                current_page = state.get("current_page", 1)
                new_page = current_page + 1
                state["current_page"] = new_page
                self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
                self.logger.info(f"[{account_name}] 翻页：从第 {current_page} 页到第 {new_page} 页")
                return await self._arefresh_results(msg, state)
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "last_page_message", "已经是最后一页了")
            return rsp

        if text_lower.startswith("排序") or text_lower.startswith("sort"):
            sort_type = self._parse_sort_type(text)
            if sort_type is not None:
                state["sortType"] = sort_type
                state["current_page"] = 1
                return await self._arefresh_results(msg, state)
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "sort_help_message", (
                "请选择排序方式：\n"
                "1. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 1\">智能排序</a>\n"
                "2. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 2\">销量优先</a>\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 3\">速度优先</a>\n"
                "4. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 4\">评分优先</a>"
            ))
            return rsp

        if text_lower in ["免配", "免配送", "free_delivery", "fd"]:
            state["free_delivery_only"] = not state.get("free_delivery_only", False)
            state["current_page"] = 1
            return await self._arefresh_results(msg, state)

        if text_lower.startswith("免配_"):
            shop_name = text[3:]
            self.logger.info(f"[{account_name}] 用户 {user_id} 点击免配按钮 - 店铺: {shop_name}")
            state["pending_free_delivery_shop_name"] = shop_name
            self.set_user_state(user_id, self.STATE_WAITING_MINIPROGRAM, state)
            rsp = TextRspMsg(msg)
            rsp.content = self._render_text(msg, "free_delivery_instruction_message", (
                "🚚 获取免配链接\n\n"
                "请按照以下步骤操作：\n\n"
                "方式一：发送小程序卡片\n"
                "1️⃣ 点击下方链接进入小程序：\n"
                "{free_delivery_miniprogram_link}\n"
                "2️⃣ 在小程序中选择店铺并收藏\n"
                "3️⃣ 发送收藏的小程序卡片给我\n\n"
                "方式二：发送链接格式\n"
                "点击上方链接进入小程序后复制 #小程序:// 或 mp:// 格式的链接发送给我\n\n"
                "💡 提示：系统会自动识别并生成免配链接\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ), free_delivery_miniprogram_link=self.FREE_DELIVERY_MINIPROGRAM_LINK)
            return rsp

        if text_lower in ["清空", "清空条件", "clear", "reset"]:
            self.logger.info(f"[{account_name}] 用户 {user_id} 清空查询条件")
            state["keyword"] = ""
            state["current_page"] = 1
            state["sortType"] = 0
            state["filterInfo"] = ""
            state["free_delivery_only"] = False
            userId = state.get("userId")
            token = state.get("token")
            wm_longitude = int(state.get("wm_longitude", 0))
            wm_latitude = int(state.get("wm_latitude", 0))
            if userId and token and wm_longitude and wm_latitude:
                result = await self._aquery_shops(userId, token, wm_longitude, wm_latitude, "", page_num=1, page_size=10, free_delivery_only=False)
                if result and result.get("success"):
                    state["query_result"] = result["data"]
                    shops = self._extract_shops_from_result(result["data"])
                    state["cached_shops"] = shops
                    state["cached_page"] = 1
                    self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
                    return self._format_results(msg, state)
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "clear_failed_message", "❌ 清空查询条件失败，请重新查询")
            return rsp

        if text_lower in ["重新查询", "重新搜索", "新查询", "new_query", "restart"]:
            self.logger.info(f"[{account_name}] 用户 {user_id} 重新查询")
            self.clear_user_state(user_id, "用户选择重新查询")
            self.set_user_state(user_id, self.STATE_WAITING_QUERY)
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "restart_query_message", (
                "🔄 已清空查询条件，请重新输入查询信息\n\n"
                "格式：坐标 链接 关键词\n\n"
                "示例格式：\n"
                "999.24591547908291,99.510910831008815 https://i.meituan.com/mttouch/page/account?userId=xxx&token=xxx 华莱士\n\n"
                "（坐标、链接、关键词之间可以用空格、逗号分隔，也可以没有分隔符）"
            ))
            return rsp

        parsed = self._parse_query_input(text, state)
        if parsed and (parsed.get("keyword") or parsed.get("longitude")):
            if parsed.get("keyword"):
                state["keyword"] = parsed["keyword"]
            if parsed.get("longitude") and parsed.get("latitude"):
                state["wm_longitude"] = str(self._convert_coordinate_to_int(parsed["longitude"]))
                state["wm_latitude"] = str(self._convert_coordinate_to_int(parsed["latitude"]))
            state["current_page"] = 1
            return await self._arefresh_results(msg, state)

        rsp = TextRspMsg(msg)
        rsp.content = self._get_text(msg, "unknown_action_message", (
            "❓ 未识别的操作\n\n"
            "可用操作：\n"
            "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=上一页\">⬅️ 上一页</a> / "
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=下一页\">➡️ 下一页</a>：翻页\n"
            "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 1\">排序 1</a> / "
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 2\">排序 2</a> / "
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 3\">排序 3</a> / "
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=排序 4\">排序 4</a>：修改排序方式\n"
            "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=免配\">🚚 免配</a>：筛选免配送费\n"
            "- 输入关键词：重新搜索"
        ))
        return rsp
    
    def _build_free_delivery_url(self, poi_value: str, token: str, allowance: str, to_user_name: str) -> Optional[str]:
        """
        构建免配链接URL（统一方法，使用配置）
        
        Args:
            poi_value: poi_id_str的值
            token: token值
            allowance: allowance值
            to_user_name: 公众号ID
            
        Returns:
            构建的URL或None
        """
        import json
        import urllib.parse
        
        try:
                   
            i_param = {
                "token": token,
                "allowance": allowance,
                "poi": poi_value
            }
            
                               
            i_param_json = json.dumps(i_param, ensure_ascii=False)
            i_param_encoded = urllib.parse.quote(i_param_json)
            
            from config.config import ACCOUNT_SPECIFIC_CONFIGS
            
                      
            mode = "a"       
            account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
            url_mode = account_config.get("url_mode", "all")
            mode_mapping = {
                "all": "a",
                "meituan": "b",
                "dianping": "c"
            }
            if url_mode in mode_mapping:
                mode = mode_mapping[url_mode]
            
                     
            base_url = "http://waimaiyouhui.top/dianping"
            full_url = f"{base_url}?i={i_param_encoded}&mode={mode}"
            
            self.logger.info(f"构建免配链接URL成功: {full_url} (mode={mode})")
            return full_url
        except Exception as e:
            self.logger.error(f"构建免配链接URL失败: {e}")
            return None
    
    def _get_button_name(self, to_user_name: str) -> str:
        """
        从配置中获取按钮名称文本（替代"大众点评/美团外卖"）
        
        Args:
            to_user_name: 公众号ID
            
        Returns:
            按钮名称文本，默认返回"大众点评/美团外卖"
        """
        try:
            from config.config import MINIPROGRAM_CONFIG

            miniprogram_config = MINIPROGRAM_CONFIG.get(to_user_name, {})
            button_name = miniprogram_config.get("button_name", "大众点评/美团外卖")
            return button_name
        except Exception:
            return "大众点评/美团外卖"
    
    def _extract_user_info(self, text: str) -> tuple:
        """
        从链接中提取userId和token
        
        Args:
            text: 包含链接的文本
            
        Returns:
            (userId, token) 元组，如果提取失败则返回 (None, None)
        """
        try:
                                                
                              
                                   
            url_pattern = r'https?://[^\s\u4e00-\u9fa5，。、；：！？\u3000]+'
            url_matches = re.finditer(url_pattern, text)
            
            for match in url_matches:
                url = match.group(0)
                if 'mttouch/page/account' in url:
                                            
                                          
                    parsed = urllib.parse.urlparse(url)
                    params = urllib.parse.parse_qs(parsed.query)
                    
                    userId = params.get('userId', [None])[0]
                    token = params.get('token', [None])[0]
                    
                    if userId and token:
                                             
                        if not re.search(r'[\u4e00-\u9fa5]', userId) and not re.search(r'[\u4e00-\u9fa5]', token):
                            return userId, token
            
            return None, None
        except Exception as e:
            self.logger.error(f"提取用户信息失败: {e}")
            return None, None
    
    def _extract_coordinates(self, text: str) -> Optional[tuple]:
        """
        从文本中提取坐标
        
        Args:
            text: 包含坐标的文本
            
        Returns:
            (longitude, latitude) 元组，如果提取失败则返回 None
        """
        try:
                                  
            pattern = r'(-?\d+\.?\d*)[,\s]+(-?\d+\.?\d*)'
            match = re.search(pattern, text)
            
            if match:
                longitude = float(match.group(1))
                latitude = float(match.group(2))
                return longitude, latitude
            
            return None
        except Exception as e:
            self.logger.error(f"提取坐标失败: {e}")
            return None
    
    def _parse_query_input(self, text: str, state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        解析查询输入（坐标、链接、关键词）
        
        Args:
            text: 用户输入的查询信息
            state: 当前状态数据（用于获取已保存的信息）
            
        Returns:
            解析后的字典，包含 userId, token, longitude, latitude, keyword
        """
        result = {}
        
        coordinates = self._extract_coordinates(text)
        if coordinates:
            result["longitude"], result["latitude"] = coordinates
        userId, token = self._extract_user_info(text)
        if userId and token:
            result["userId"] = userId
            result["token"] = token
        keyword_text = text
        
               
        if coordinates:
            coords_pattern = rf"{re.escape(str(coordinates[0]))}[,\s]+{re.escape(str(coordinates[1]))}"
            keyword_text = re.sub(coords_pattern, "", keyword_text, count=1)
        
               
                     
        url_pattern = r'https?://[^\s\u4e00-\u9fa5，。、；：！？\u3000]+'
                                   
        url_matches = list(re.finditer(url_pattern, keyword_text))
        if url_matches:
            for match in reversed(url_matches):
                keyword_text = keyword_text[:match.start()] + keyword_text[match.end():]
        
                     
        keyword = re.sub(r'[,\s]+', ' ', keyword_text).strip()
        if keyword and keyword.strip():
            has_content = bool(re.search(r'[\u4e00-\u9fa5a-zA-Z0-9]', keyword))
            if has_content:
                result["keyword"] = keyword.strip()
        
        return result if result else None
    
    def _convert_coordinate_to_int(self, coordinate: float) -> int:
                           
                         
        return int(coordinate * 1000000)
    
    async def _aquery_shops(self, userId: str, token: str, wm_longitude: int, wm_latitude: int, 
                     keyword: str = "", page_num: int = 1, page_size: int = 10, 
                     sortType: int = 0, filterInfo: str = "", free_delivery_only: bool = False,
                     session_context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        查询商家
        
        Args:
            userId: 用户ID
            token: 认证token
            wm_longitude: 经度（整数形式）
            wm_latitude: 纬度（整数形式）
            keyword: 搜索关键词
            page_num: 页码
            page_size: 每页数量
            sortType: 排序类型
            filterInfo: 筛选信息
            
        Returns:
            查询结果字典
        """
        try:
                    
            session_context = dict(session_context or {})
            client_uuid = str(session_context.get("client_uuid") or "")
            browser_profile = dict(session_context.get("browser_profile") or {})
            user_agent = str(
                browser_profile.get("shop_query_user_agent")
                or browser_profile.get("user_agent")
                or "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 TitansX/20.0.1.old KNB/1.0 iOS/26.1 meituangroup/com.meituan.imeituan/12.46.402 meituangroup/12.46.402 App/10110/12.46.402 iPhone/iPhone17Pro WKWebView"
            )

            params = {
                "page_num": str(page_num),
                "notitleba": "1",
                "app_model": "0",
                "platform": "5",
                "address": "",
                "partner": "4",
                "version": "12.45.402",
                "wm_dversion": "26.0.1",
                "content_personalized_switch": "0",
                "mt_back_rci": "",
                "wm_visitid": "",
                "app": "0",
                "wmUserIdDeregistration": "0",
                "region_version": "1763486325573",
                "future": "2",
                "region_id": "1000100000",
                "wm_appversion": "12.45.402",
                "wm_ctype": "mtiphone",
                "scene_id": "344",
                "wm_logintoken": token,
                "wm_dtype": "iPhone4S",
                "wm_did": "",
                "poilist_wm_cityid": "",
                "wmUuidDeregistration": "0",
                "poilist_mt_cityid": "",
                "wm_uuid": client_uuid,
                "entry": "tuansousuo",
                "personalized": "1",
                "ad_personalized_switch": "0",
                "utm_campaign": "AgroupBgroupG",
                "userid": userId,
                "uuid": client_uuid,
                "utm_term": "12.45.402",
                "utm_source": "AppStore",
                "utm_content": "",
                "version_name": "12.45.402",
                "utm_medium": "iphone",
                "token": token,
                "language": "zh-CN",
                "regionid": "",
                "f": "iphone",
                "ci": "",
                "msid": "",
                "wm_longitude": str(wm_longitude),
                "wm_latitude": str(wm_latitude),
                "wm_actual_longitude": str(wm_longitude),
                "wm_actual_latitude": str(wm_latitude),
                "entry_channel": "2",
                "page_size": str(page_size),
                "filterInfo": filterInfo,
                "sortType": str(sortType),
                "clicked_poi_str": "",
                "clicked_poi_channel": "",
                "wm_context": "",
                "ad_page_type": "0"
            }
            
                                          
            if keyword:
                params["query"] = str(keyword) if keyword else ""
            
                                                  
            if free_delivery_only:
                                               
                                      
                                                 
                pass
            
                                      
            if filterInfo:
                params["filterInfo"] = str(filterInfo)
            
                            
            params = {k: str(v) if v is not None else "" for k, v in params.items()}
            
                      
            cookie_items = [
                ("mt_c_token", token),
                ("userId", userId),
                ("oops", token),
            ]
            if client_uuid:
                cookie_items.extend(
                    [
                        ("uuid", client_uuid),
                        ("openh5_uuid", client_uuid),
                        ("w_uuid", client_uuid),
                    ]
                )
            cookie = "; ".join(f"{key}={value}" for key, value in cookie_items if value)
            
                   
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": cookie,
                "User-Agent": user_agent,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Origin": "https://h5.waimai.meituan.com",
                "Referer": "https://h5.waimai.meituan.com/"
            }
            
            from utils.proxy_utils import (
                ProxyUnavailableError,
                report_proxy_failure_async,
                report_proxy_success_async,
                require_proxy_config_async,
            )
            if session_context.get("proxies"):
                proxies = dict(session_context["proxies"])
            else:
                try:
                    proxies = await require_proxy_config_async()
                except ProxyUnavailableError:
                    return {
                        "success": False,
                        "error": "网络繁忙，请稍后重试"
                    }
            proxy_url = str(proxies.get("http") or proxies.get("https") or "").strip()
            
                  
            url = "https://adapi.waimai.meituan.com/api/ad/landingPage"
            try:
                response = await requests.post(
                    url,
                    data=urllib.parse.urlencode(params),
                    headers=headers,
                    proxies=proxies,
                    timeout=4
                )
                response.raise_for_status()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                if proxy_url:
                    await report_proxy_failure_async(proxy_url, exc)
                raise
            try:
                result = response.json()
            except Exception as exc:
                if proxy_url:
                    await report_proxy_failure_async(proxy_url, exc)
                raise
            if not isinstance(result, dict):
                if proxy_url:
                    await report_proxy_failure_async(proxy_url, RuntimeError("查询商家响应结构异常"))
                raise RuntimeError("查询商家响应结构异常")
            if proxy_url:
                await report_proxy_success_async(proxy_url)
            
            return {
                "success": True,
                "data": result
            }
            
        except requests.Timeout:
            self.logger.error("查询商家超时")
            return {
                "success": False,
                "error": "请求超时，请检查登录是否过期"
            }
        except requests.ConnectionError as e:
            error_msg = str(e)
            self.logger.error(f"查询商家连接失败: {error_msg}")
                       
            if proxies:
                self.logger.warning(f"使用代理时连接失败，代理配置: {proxies}")
            return {
                "success": False,
                "error": "网络连接失败，请稍后重试"
            }
        except requests.HTTPError as e:
                                            
            status_code = e.response.status_code if e.response else 0
            error_msg = str(e)
                                           
            if status_code == 0 and "403" in error_msg:
                status_code = 403
            elif status_code == 0 and "Forbidden" in error_msg:
                status_code = 403
            
            self.logger.error(f"查询商家失败: HTTP {status_code}, 错误: {error_msg}")
            
            if status_code == 403:
                                  
                return {
                    "success": False,
                    "error": "登录失败，请重新登录获取链接后重试"
                }
            elif status_code == 0:
                                            
                return {
                    "success": False,
                    "error": "网络连接失败，请稍后重试"
                }
            return {
                "success": False,
                "error": f"HTTP {status_code}"
            }
        except requests.RequestException as e:
                            
            error_msg = str(e)
            self.logger.error(f"查询商家请求异常: {error_msg}")
            return {
                "success": False,
                "error": "网络请求失败"
            }
        except Exception as e:
            error_msg = str(e)
            self.logger.error(f"查询商家失败: {error_msg}", exc_info=True)
            return {
                "success": False,
                "error": "网络错误"
            }

    def _refresh_results(self, msg: Dict[str, Any], state: Dict[str, Any]) -> Any:
        """
        刷新查询结果
        
        Args:
            msg: 消息字典
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        userId = state.get("userId")
        token = state.get("token")
        wm_longitude = int(state.get("wm_longitude", 0))
        wm_latitude = int(state.get("wm_latitude", 0))
        keyword = state.get("keyword", "")
        page_num = state.get("current_page", 1)
        sortType = state.get("sortType", 0)
        filterInfo = state.get("filterInfo", "")
        free_delivery_only = state.get("free_delivery_only", False)
        
        self.logger.info(f"[{account_name}] 刷新结果 - 页码: {page_num}, 关键词: {keyword}, 排序: {sortType}, 免配: {free_delivery_only}")
        
                     
        is_odd_page = (page_num % 2 == 1)
        
        if is_odd_page:
                               
            self.logger.info(f"[{account_name}] 单数页 {page_num}，从API获取新数据")
            result = self._query_shops(
                userId, token, wm_longitude, wm_latitude, 
                keyword, (page_num + 1) // 2, page_size=10, sortType=sortType, 
                filterInfo=filterInfo, free_delivery_only=free_delivery_only
            )
        else:
                         
            self.logger.info(f"[{account_name}] 双数页 {page_num}，使用缓存数据")
            cached_shops = state.get("cached_shops", [])
            cached_page = state.get("cached_page", 0)
            
                      
            if cached_shops and cached_page == (page_num - 1):
                                       
                result = {
                    "success": True,
                    "data": {
                        "data": {
                            "module_list": []
                        }
                    }
                }
                                        
                old_query_result = state.get("query_result", {})
                if old_query_result:
                    result["data"]["data"]["json_data"] = old_query_result.get("data", {}).get("json_data", {})
            else:
                                       
                self.logger.warning(f"[{account_name}] 缓存无效，重新获取数据")
                result = self._query_shops(
                    userId, token, wm_longitude, wm_latitude, 
                    keyword, (page_num + 1) // 2, page_size=10, sortType=sortType, 
                    filterInfo=filterInfo, free_delivery_only=free_delivery_only
                )
                                
                if result and result.get("success"):
                    shops = self._extract_shops_from_result(result["data"])
                    state["cached_shops"] = shops
                    state["cached_page"] = page_num - 1
                    is_odd_page = True 
        
        if result and result.get("success"):
                                
            if free_delivery_only:
                if is_odd_page:
                    shops = self._extract_shops_from_result(result["data"])
                else:
                    shops = state.get("cached_shops", [])
                
                free_delivery_shops = [s for s in shops if self._is_free_delivery(s)]
                
                if not free_delivery_shops:
                    self.logger.info(f"[{account_name}] 筛选免配但当前页没有，增大页面大小至20")
                    result = self._query_shops(
                        userId, token, wm_longitude, wm_latitude, 
                        keyword, (page_num + 1) // 2, page_size=20, sortType=sortType, 
                        filterInfo=filterInfo, free_delivery_only=free_delivery_only
                    )
                    
                    if result and result.get("success"):
                        shops = self._extract_shops_from_result(result["data"])
                        free_delivery_shops = [s for s in shops if self._is_free_delivery(s)]
                        
                        if not free_delivery_shops:
                            return self._create_no_shops_response(
                                msg, state, userId, token, wm_longitude, wm_latitude,
                                keyword, result["data"], page_num=page_num, is_free_delivery=True
                            )
            
            if is_odd_page:
                state["query_result"] = result["data"]
                shops = self._extract_shops_from_result(result["data"])
                state["cached_shops"] = shops
                state["cached_page"] = page_num
            else:
                pass
            
            state["current_page"] = page_num 
            self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
            
            if is_odd_page:
                self.logger.info(f"[{account_name}] 查询结果已更新（单数页），当前页码: {page_num}, 缓存店铺数量: {len(state.get('cached_shops', []))}")
            else:
                self.logger.info(f"[{account_name}] 使用缓存数据（双数页），当前页码: {page_num}, 缓存店铺数量: {len(state.get('cached_shops', []))}")
            
            return self._format_results(msg, state)
        else:
                            
            error_msg = result.get('error', '网络错误') if result else '网络错误'
            self.clear_user_state(user_id, f"查询失败: {error_msg}")
            rsp = TextRspMsg(msg)
            rsp.content = self._render_text(msg, "query_failed_template", "❌ 查询失败：{error}\n\n请检查登录是否过期，或稍后重试", error=error_msg)
            return rsp

    async def _arefresh_results(self, msg: Dict[str, Any], state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        userId = state.get("userId")
        token = state.get("token")
        wm_longitude = int(state.get("wm_longitude", 0))
        wm_latitude = int(state.get("wm_latitude", 0))
        keyword = state.get("keyword", "")
        page_num = state.get("current_page", 1)
        sortType = state.get("sortType", 0)
        filterInfo = state.get("filterInfo", "")
        free_delivery_only = state.get("free_delivery_only", False)

        self.logger.info(f"[{account_name}] 刷新结果 - 页码: {page_num}, 关键词: {keyword}, 排序: {sortType}, 免配: {free_delivery_only}")
        is_odd_page = (page_num % 2 == 1)
        if is_odd_page:
            self.logger.info(f"[{account_name}] 单数页 {page_num}，从API获取新数据")
            result = await self._aquery_shops(
                userId, token, wm_longitude, wm_latitude,
                keyword, (page_num + 1) // 2, page_size=10, sortType=sortType,
                filterInfo=filterInfo, free_delivery_only=free_delivery_only
            )
        else:
            self.logger.info(f"[{account_name}] 双数页 {page_num}，使用缓存数据")
            cached_shops = state.get("cached_shops", [])
            cached_page = state.get("cached_page", 0)
            if cached_shops and cached_page == (page_num - 1):
                result = {
                    "success": True,
                    "data": {"data": {"module_list": []}}
                }
                old_query_result = state.get("query_result", {})
                if old_query_result:
                    result["data"]["data"]["json_data"] = old_query_result.get("data", {}).get("json_data", {})
            else:
                self.logger.warning(f"[{account_name}] 缓存无效，重新获取数据")
                result = await self._aquery_shops(
                    userId, token, wm_longitude, wm_latitude,
                    keyword, (page_num + 1) // 2, page_size=10, sortType=sortType,
                    filterInfo=filterInfo, free_delivery_only=free_delivery_only
                )
                if result and result.get("success"):
                    shops = self._extract_shops_from_result(result["data"])
                    state["cached_shops"] = shops
                    state["cached_page"] = page_num - 1
                    is_odd_page = True

        if result and result.get("success"):
            if free_delivery_only:
                shops = self._extract_shops_from_result(result["data"]) if is_odd_page else state.get("cached_shops", [])
                free_delivery_shops = [s for s in shops if self._is_free_delivery(s)]
                if not free_delivery_shops:
                    self.logger.info(f"[{account_name}] 筛选免配但当前页没有，增大页面大小至20")
                    result = await self._aquery_shops(
                        userId, token, wm_longitude, wm_latitude,
                        keyword, (page_num + 1) // 2, page_size=20, sortType=sortType,
                        filterInfo=filterInfo, free_delivery_only=free_delivery_only
                    )
                    if result and result.get("success"):
                        shops = self._extract_shops_from_result(result["data"])
                        free_delivery_shops = [s for s in shops if self._is_free_delivery(s)]
                        if not free_delivery_shops:
                            return self._create_no_shops_response(
                                msg, state, userId, token, wm_longitude, wm_latitude,
                                keyword, result["data"], page_num=page_num, is_free_delivery=True
                            )

            if is_odd_page:
                state["query_result"] = result["data"]
                shops = self._extract_shops_from_result(result["data"])
                state["cached_shops"] = shops
                state["cached_page"] = page_num

            state["current_page"] = page_num
            self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)

            if is_odd_page:
                self.logger.info(f"[{account_name}] 查询结果已更新（单数页），当前页码: {page_num}, 缓存店铺数量: {len(state.get('cached_shops', []))}")
            else:
                self.logger.info(f"[{account_name}] 使用缓存数据（双数页），当前页码: {page_num}, 缓存店铺数量: {len(state.get('cached_shops', []))}")
            return self._format_results(msg, state)

        error_msg = result.get('error', '网络错误') if result else '网络错误'
        self.clear_user_state(user_id, f"查询失败: {error_msg}")
        rsp = TextRspMsg(msg)
        rsp.content = self._render_text(msg, "query_failed_template", "❌ 查询失败：{error}\n\n请检查登录是否过期，或稍后重试", error=error_msg)
        return rsp
    
    def _extract_shops_from_result(self, query_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        从查询结果中提取店铺信息
        
        Args:
            query_result: 查询结果字典
            
        Returns:
            店铺信息列表
        """
        import json
        
        shops = []
        data = query_result.get("data", {})
        module_list = data.get("module_list", [])
        
        self.logger.info(f"开始提取店铺信息，module_list 长度: {len(module_list)}")
        
        template_ids = [module.get("template_id") for module in module_list]
        self.logger.info(f"找到的模板ID: {template_ids}")
        
        for module in module_list:
            template_id = module.get("template_id")
            
            if template_id in ["module_allowance_list", "module_poi_list", "module_list"]:
                string_data = module.get("string_data", {})
                if isinstance(string_data, str):
                    try:
                        string_data = json.loads(string_data)
                    except (json.JSONDecodeError, TypeError) as e:
                        self.logger.warning(f"解析 string_data JSON 失败: {e}")
                        continue
                if not isinstance(string_data, dict):
                    continue
                
                ad_data = string_data.get("ad_data", {})
                if isinstance(ad_data, str):
                    try:
                        ad_data = json.loads(ad_data)
                    except (json.JSONDecodeError, TypeError) as e:
                        self.logger.warning(f"解析 ad_data JSON 失败: {e}")
                        continue
                if not isinstance(ad_data, dict):
                    continue
                
                if ad_data:
                    shop_info = {
                        "poi_name": ad_data.get("poi_name", "未知店铺"),
                        "shipping_fee_tip": ad_data.get("shipping_fee_tip", ""),
                        "distance": ad_data.get("distance", ""),
                        "delivery_time_tip": ad_data.get("delivery_time_tip", ""),
                        "min_price_tip": ad_data.get("min_price_tip", ""),
                        "scheme": ad_data.get("scheme", "")
                    }
                    shops.append(shop_info)
                    self.logger.info(f"提取到店铺: {shop_info.get('poi_name')}")
        
        self.logger.info(f"总共提取到 {len(shops)} 个店铺")
        return shops
    
    def _is_free_delivery(self, shop: Dict[str, Any]) -> bool:
        """
        判断店铺是否免配送费
        
        Args:
            shop: 店铺信息字典
            
        Returns:
            是否免配送费
        """
        shipping_fee_tip = shop.get("shipping_fee_tip", "")
        return "¥0" in shipping_fee_tip or "约¥0" in shipping_fee_tip or "免配" in shipping_fee_tip
    
    def _format_results(self, msg: Dict[str, Any], state: Dict[str, Any]) -> Any:
        """
        格式化查询结果
        
        Args:
            msg: 消息字典
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        current_page = state.get("current_page", 1)
        is_odd_page = (current_page % 2 == 1)
        
                                    
        query_result = state.get("query_result", {})
        
                            
        if is_odd_page:
                                         
            all_shops = self._extract_shops_from_result(query_result)
            shops = all_shops[:5]          
        else:
                           
            cached_shops = state.get("cached_shops", [])
            shops = cached_shops[5:10] if len(cached_shops) > 5 else []
        
                        
        free_delivery_only = state.get("free_delivery_only", False)
        if free_delivery_only:
            shops = [s for s in shops if self._is_free_delivery(s)]
        
        shop_number_map = {} 
        if "shop_number_map" not in state:
            state["shop_number_map"] = {}
        
        page_size = 5 
        start_number = (current_page - 1) * page_size + 1
        
        for idx, shop in enumerate(shops):
            shop_number = start_number + idx
            scheme = shop.get("scheme", "")
            if scheme:
                shop_number_map[str(shop_number)] = scheme
                state["shop_number_map"][str(shop_number)] = scheme
        
                    
        state["shop_number_map"].update(shop_number_map)
        
                                  
        data = query_result.get("data", {})
        json_data = data.get("json_data", {})
        page_info = json_data.get("page", {})
        has_next = page_info.get("hasNextPage", False)
        
               
        rsp = TextRspMsg(msg)
        filter_status = "（仅免配）" if free_delivery_only else ""
        content = self._render_text(msg, "results_header_template", "📋 查询结果（第 {current_page} 页）{filter_status}\n\n", current_page=current_page, filter_status=filter_status)
        
        if shops:
            for idx, shop in enumerate(shops):
                shop_number = start_number + idx
                shipping_fee_display = "免配" if self._is_free_delivery(shop) else shop["shipping_fee_tip"]
                if shop.get("scheme"):
                    query_text = f"{self.SCHEME_QUERY_KEYWORD}{shop_number}"
                    content += f"{shop_number}. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent={query_text}\">{shop['poi_name']}</a>\n"
                else:
                    content += f"{shop_number}. {shop['poi_name']}\n"
                content += f"{shipping_fee_display} | {shop['distance']} | {shop['delivery_time_tip']} | {shop['min_price_tip']}\n"
        else:
            content += self._get_text(msg, "results_empty_message", "未找到相关店铺\n\n")

        content += self._get_text(msg, "results_action_title", "📌 操作提示：\n")
        if current_page > 1:
            content += self._get_text(msg, "results_prev_link_text", "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=上一页\">⬅️ 上一页</a>\n")
        if has_next:
            content += self._get_text(msg, "results_next_link_text", "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=下一页\">➡️ 下一页</a>\n")
        content += self._get_results_sort_links_text(msg)
        content += self._get_text(msg, "results_toggle_filter_text", "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=免配\">🚚 切换免配筛选</a>\n")
        content += self._get_text(msg, "results_clear_and_restart_text", "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=清空\">🗑️ 清空查询条件</a> | <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=重新查询\">🔄 重新查询（更换关键词）</a>\n")
        content += self._get_text(msg, "results_cancel_text", "- <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=取消\">❌ 退出查询</a>\n")
        
        rsp.content = content
        return rsp
    
    async def _ahandle_shop_number_query(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Optional[Any]:
        """
        处理商家编号查询（独立功能，只要在查询状态就能使用）
        需要包含关键词才能进入此功能
        
        Args:
            msg: 消息字典
            text: 用户输入的文本（应包含关键词+编号）
            state: 当前状态数据
            
        Returns:
            如果找到对应的scheme则返回响应对象，否则返回None继续正常处理
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        text_stripped = text.strip()
        
                    
        if not text_stripped.startswith(self.SCHEME_QUERY_KEYWORD):
            return None
        
                           
        number_text = text_stripped[len(self.SCHEME_QUERY_KEYWORD):].strip()
        
                  
        if not number_text.isdigit():
            return None
        
                            
        shop_number_map = state.get("shop_number_map", {})
        
                     
        scheme = shop_number_map.get(number_text)
        
        if scheme:
            self.logger.info(f"[{account_name}] 用户 {user_id} 查询商家编号 {number_text}，scheme: {scheme}")
            
                               
            rsp = await self._abuild_shop_links_from_scheme(msg, scheme, state)
            if rsp:
                return rsp
            else:
                                     
                rsp = TextRspMsg(msg)
                rsp.content = scheme
                return rsp
        
                                
        return None
    
    async def _abuild_shop_links_from_scheme(self, msg: Dict[str, Any], scheme: str, state: Dict[str, Any]) -> Optional[Any]:
        """
        从scheme中提取参数并构建两个链接（美团优惠链接和额外参数链接）
        
        Args:
            msg: 消息字典
            scheme: scheme字符串（包含allowance_alliance_scenes和poi_id_str等参数）
            state: 当前状态数据
            
        Returns:
            响应对象，如果构建失败返回None
        """
        from utils.response import TextRspMsg
        from config.config import ACCOUNT_SPECIFIC_CONFIGS
        import urllib.parse
        import json
        
        try:
                     
            to_user_name = msg.get("ToUserName", "")
            account_name = msg.get("_account_name", to_user_name)
            account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
            meituan_base_url = account_config.get("meituan_base_url", "")
            
            if not meituan_base_url:
                self.logger.warning(f"[{account_name}] 未配置美团链接，无法构建优惠链接")
                return None
            
                          
                                                                                                                                                  
                                                                                               
            
            poi_id_str = None
            allowance = None
            
                                                         
            if '://' in scheme or scheme.startswith('?'):
                                 
                if scheme.startswith('?'):
                                       
                    scheme = 'http://dummy' + scheme
                
                parsed_url = urllib.parse.urlparse(scheme)
                params = urllib.parse.parse_qs(parsed_url.query)
                poi_id_str = params.get('poi_id_str', [None])[0]
                allowance = params.get('allowance_alliance_scenes', [None])[0]
            else:
                               
                                                                                           
                params = urllib.parse.parse_qs(scheme)
                poi_id_str = params.get('poi_id_str', [None])[0]
                allowance = params.get('allowance_alliance_scenes', [None])[0]
            
                                    
            if not poi_id_str:
                poi_match = re.search(r'poi_id_str=([^&]+)', scheme)
                if poi_match:
                    poi_id_str = poi_match.group(1)
            
            if not allowance:
                allowance_match = re.search(r'allowance_alliance_scenes=([^&]+)', scheme)
                if allowance_match:
                    allowance = allowance_match.group(1)
            
            if not poi_id_str:
                self.logger.warning(f"无法从scheme中提取poi_id_str: {scheme}")
                return None
            
                                               
            full_url = f"{meituan_base_url}&poi_id=-100&poi_id_str={poi_id_str}"
            
                                    
            extra_params_url = None
            if allowance:
                                                    
                poi_value = poi_id_str.split('=', 1)[1] if '=' in poi_id_str else poi_id_str
                                                      
                extra_params_url = self._build_free_delivery_url(poi_value, None, allowance, to_user_name)
            
                            
            shop_name = "商家"
            query_result = state.get("query_result", {})
            shops = self._extract_shops_from_result(query_result)
                                          
            for shop in shops:
                if shop.get("scheme") == scheme:
                    shop_name = shop.get("poi_name", "商家")
                    break
            
                    
            rsp = TextRspMsg(msg)
            
                                         
            user_id = msg.get("FromUserName", "")
            free_delivery_link = f"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=免配_{shop_name}"
            
                     
            miniprogram_link = generate_miniprogram_link(full_url, to_user_name)
            
                         
            button_name = self._get_button_name(to_user_name)
            
            if extra_params_url:
                rsp.content = self._render_text(msg, "shop_link_with_extra_template", (
                    "【{shop_name}】\n\n"
                    "点击下方链接查看详情：\n"
                    "<a href=\"{full_url}\">立即查看商家券,打开后置顶第一个店铺就是你选择的店铺，使用商家卷点外卖更优惠，然后在去下面美团津贴免单跳转美团APP</a>\n\n"
                    "{miniprogram_link}\n\n"
                    "使用浏览器打开App：\n"
                    "<a href=\"{extra_params_url}\">{button_name}</a>\n\n"
                    "<a href=\"{free_delivery_link}\">🚚 获取免配链接</a>"
                ), shop_name=shop_name, full_url=full_url, miniprogram_link=miniprogram_link, extra_params_url=extra_params_url, button_name=button_name, free_delivery_link=free_delivery_link)
            else:
                rsp.content = self._render_text(msg, "shop_link_without_extra_template", (
                    "【{shop_name}】\n\n"
                    "点击下方链接查看详情：\n"
                    "<a href=\"{full_url}\">立即查看商家券</a>\n\n"
                    "{miniprogram_link}\n\n"
                    "<a href=\"{free_delivery_link}\">🚚 获取免配链接</a>"
                ), shop_name=shop_name, full_url=full_url, miniprogram_link=miniprogram_link, free_delivery_link=free_delivery_link)

            benefits = await aquery_benefits_for_wechat(
                poi_id_str=poi_id_str,
                merchant_name="" if shop_name == "商家" else shop_name,
                account_id=to_user_name,
                source="wechat_shop_query",
            )
            if benefits:
                benefit_lines = format_benefits_for_wechat(benefits)
                if benefit_lines:
                    rsp.content += "\n\n" + "\n".join(benefit_lines)
            
            self.logger.info(f"[{account_name}] 成功构建商家链接 - 店铺: {shop_name}, poi_id_str: {poi_id_str}")
            return rsp
            
        except Exception as e:
            self.logger.error(f"构建商家链接失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None
    
    def _parse_sort_type(self, text: str) -> Optional[int]:
        """
        解析排序类型
        
        Args:
            text: 用户输入的排序命令
            
        Returns:
            排序类型（0-3），如果解析失败返回None
        """
              
        match = re.search(r'(\d)', text)
        if match:
            sort_num = int(match.group(1))
            if 0 <= sort_num <= 3:
                return sort_num
        return None
    
    def _handle_miniprogram_message(self, msg: Dict[str, Any], state: Dict[str, Any]) -> Any:
        """
        处理小程序消息（用于免配流程）
        
        使用 meituan.py 的逻辑直接从 page_path 中提取所有参数，
        然后构建包含 token 的免配链接
        
        Args:
            msg: 小程序消息字典
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        from utils.response import TextRspMsg
        import json
        import urllib.parse
        
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", "")
        page_path = msg.get("PagePath", "")
        title = msg.get("Title", "")
        
        self.logger.info(f"[{account_name}] 收到小程序消息 - 标题: {title}, page_path: {page_path[:100]}...")
        
                               
                                                               
        poi_id_str = self._extract_poi_id_str_from_page_path(page_path)
        
        if not poi_id_str:
            self.logger.warning(f"[{account_name}] 无法从page_path中提取poi_id_str")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "miniprogram_missing_shop_message", (
                "❌ 识别失败：无法从小程序中提取店铺信息\n\n"
                "请确保您发送的是收藏的店铺小程序，而不是其他小程序\n\n"
                "您可以：\n"
                "1. 重新发送小程序卡片\n"
                "2. 发送 #小程序:// 或 mp:// 格式的链接\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                                    
        if '=' in poi_id_str:
            poi_value = poi_id_str.split('=', 1)[1]
        else:
            poi_value = poi_id_str
        self.logger.info(f"[{account_name}] 提取到的poi_value: {poi_value}")
        
        if not poi_value:
            self.logger.warning(f"[{account_name}] 无法从poi_id_str中提取poi值")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "miniprogram_missing_poi_message", "❌ 识别失败：无法提取店铺ID\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>")
            return rsp
        
                                                                                   
        allowance = self._extract_parameter_value_from_page_path(page_path, 'allowance_alliance_scenes')
        if not allowance:
            self.logger.warning(f"[{account_name}] 无法从小程序中提取allowance_alliance_scenes")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "miniprogram_missing_allowance_message", (
                "❌ 识别失败：无法从小程序中提取allowance信息\n\n"
                "请确保您发送的是收藏的店铺小程序\n\n"
                "您可以：\n"
                "1. 重新发送小程序卡片\n"
                "2. 发送 #小程序:// 或 mp:// 格式的链接\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                                                             
        token = None
        ad_activity_flag_str = self._extract_parameter_value_from_page_path(page_path, 'ad_activity_flag')
        if ad_activity_flag_str:
            try:
                             
                ad_activity_flag = json.loads(ad_activity_flag_str)
            except json.JSONDecodeError:
                                      
                try:
                    decoded_flag = urllib.parse.unquote(ad_activity_flag_str)
                    ad_activity_flag = json.loads(decoded_flag)
                except (json.JSONDecodeError, Exception) as e:
                    self.logger.warning(f"解析ad_activity_flag失败: {e}")
                    ad_activity_flag = None
            
                                       
            if ad_activity_flag:
                token = ad_activity_flag.get('token', None)
                if not token:
                    self.logger.warning("ad_activity_flag中未找到token")
        else:
            self.logger.info("未找到ad_activity_flag参数，token将设为null")
        
                    
        if not token:
            self.logger.warning(f"[{account_name}] 无法从小程序中提取token")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "miniprogram_missing_token_message", (
                "❌ 识别失败：无法从小程序中提取信息\n\n"
                "请确保您：\n"
                "1. 从发送的链接进入小程序\n"
                "2. 发送的是收藏的店铺小程序\n"
                "3. 小程序中包含完整的店铺信息\n\n"
                "您可以：\n"
                "1. 重新发送小程序卡片\n"
                "2. 发送 #小程序:// 或 mp:// 格式的链接\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                      
        free_delivery_url = self._build_free_delivery_url(poi_value, token, allowance, to_user_name)
        
        if not free_delivery_url:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "free_delivery_build_failed_message", "❌ 构建免配链接失败\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>")
            return rsp
        
                   
        state.pop("pending_free_delivery_scheme", None)
        state.pop("pending_free_delivery_shop_name", None)
        self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
        
        self.logger.info(f"[{account_name}] 成功构建免配链接 - token: {token[:20]}...")
        
                     
        button_name = self._get_button_name(to_user_name)
        
        rsp = TextRspMsg(msg)
        rsp.content = self._render_text(msg, "free_delivery_success_template", (
            "✅ 识别成功！\n\n"
            "点击下方链接跳转到免配页面：\n"
            "<a href=\"{free_delivery_url}\">🚚 立即跳转免配</a>\n\n"
            "💡 提示：点击链接后会自动跳转到{button_name}App，享受免配送费优惠\n\n"
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
        ), free_delivery_url=free_delivery_url, button_name=button_name)
        return rsp
    
    def _extract_poi_id_str_from_page_path(self, page_path: str) -> Optional[str]:
        """
        从page_path中提取poi_id_str参数（类似meituan.py的逻辑）
        
        Args:
            page_path: 小程序page_path
            
        Returns:
            poi_id_str参数字符串（包含&poi_id_str=前缀）或None
        """
                            
        poi_id_str_pos = page_path.find('poi_id_str=')
        if poi_id_str_pos == -1:
            self.logger.warning(f"未找到poi_id_str参数: {page_path[:100]}")
            return None
        
                                    
        value_start = poi_id_str_pos + len('poi_id_str=')
        end_pos = page_path.find('&', value_start)
        if end_pos == -1:
                                               
            end_pos = len(page_path)
        
                         
        poi_value = page_path[value_start:end_pos]
        
                 
        poi_value = poi_value.replace('\r', '').replace('\n', '').strip()
        
                      
        if not poi_value:
            self.logger.warning(f"poi_id_str的值为空")
            return None
        
                            
        extracted = f"&poi_id_str={poi_value}"
        self.logger.info(f"提取poi_id_str成功: {extracted}")
        
        return extracted
    
    def _extract_parameter_value_from_page_path(self, page_path: str, param_name: str) -> Optional[str]:
        """
        从page_path中提取指定参数的值（类似meituan.py的逻辑）
        
        Args:
            page_path: 小程序page_path
            param_name: 参数名
            
        Returns:
            参数值或None
        """
                
        param_pos = page_path.find(f'{param_name}=')
        if param_pos == -1:
            return None
        
                  
        value_start = param_pos + len(param_name) + 1
        
                              
        value_end = page_path.find('&', value_start)
        if value_end == -1:
            value_end = len(page_path)
        
             
        value = page_path[value_start:value_end]
        
                 
        value = value.replace('\r', '').replace('\n', '')
        
        return value
    
    async def _ahandle_link_message(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        """
        处理链接消息（#小程序://或mp://格式）
        
        使用 link_handlers 解析链接，然后通过 api_client 的 step1_parse_link 获取 page，
        解码后提取参数并构建免配链接
        
        Args:
            msg: 消息字典
            text: 链接文本
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        from utils.response import TextRspMsg
        from config.config import ACCOUNT_SPECIFIC_CONFIGS, LINK_CONFIG
        from link_handlers.link_recognizer import LinkRecognizer
        import json
        import urllib.parse
        
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        to_user_name = msg.get("ToUserName", "")
        
        self.logger.info(f"[{account_name}] 收到链接消息 - 链接: {text[:100]}...")
        
                                                                           
        account_config = msg.get("_account_config", {})
        if not account_config:
            account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
        zmkey = resolve_zmkey(msg, account_config)
        
        if not zmkey:
            self.logger.error(f"[{account_name}] 未配置zmkey，无法解析链接")
                              
            state.pop("pending_free_delivery_scheme", None)
            state.pop("pending_free_delivery_shop_name", None)
            self.clear_user_state(user_id, "系统配置错误：未配置zmkey")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_zmkey_message", (
                "❌ 系统配置错误：请联系管理员\n\n"
                "请联系管理员配置\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                
        link_recognizer = LinkRecognizer(LINK_CONFIG)
        link_info = link_recognizer.recognize(text.strip())
        
        if not link_info:
            self.logger.warning(f"[{account_name}] 无法识别链接类型: {text[:100]}")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_unrecognized_message", (
                "❌ 无法识别链接类型\n\n"
                "请确保链接格式正确（#小程序://或mp://开头）\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                                   
        api_client = LinkConversionAPI(zmkey, self.logger)
        step1_result = api_client.step1_parse_link(text.strip())
        
        if not step1_result:
            self.logger.warning(f"[{account_name}] 解析链接失败")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_parse_failed_message", (
                "❌ 解析链接失败\n\n"
                "请确保链接格式正确且有效\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                   
        page = step1_result.get("page", "")
        if not page:
            self.logger.warning(f"[{account_name}] 未获取到page参数")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_page_message", (
                "❌ 解析链接失败：未获取到参数\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                                     
        try:
            decoded_page = urllib.parse.unquote(page)
            self.logger.info(f"[{account_name}] 解码后的page: {decoded_page[:200]}...")
        except Exception as e:
            self.logger.warning(f"[{account_name}] URL解码失败，使用原始page: {e}")
            decoded_page = page
        
                                      
                          
        poi_id_str = self._extract_poi_id_str_from_page_path(decoded_page)
        self.logger.info(f"[{account_name}] 提取到的poi_id_str: {poi_id_str}, 类型: {type(poi_id_str)}")
        
        if not poi_id_str:
            self.logger.warning(f"[{account_name}] 无法从page中提取poi_id_str")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_shop_message", (
                "❌ 识别失败：无法从链接中提取店铺信息\n\n"
                "请确保链接包含完整的店铺信息\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                               
        if '=' in poi_id_str:
            poi_value = poi_id_str.split('=', 1)[1]
        else:
            poi_value = poi_id_str
        self.logger.info(f"[{account_name}] 提取到的poi_value: {poi_value}")
        
        if not poi_value:
            self.logger.warning(f"[{account_name}] 无法从poi_id_str中提取poi值")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_poi_message", "❌ 识别失败：无法提取店铺ID\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>")
            return rsp
        
                                         
        allowance = self._extract_parameter_value_from_page_path(decoded_page, 'allowance_alliance_scenes')
        if not allowance:
            self.logger.warning(f"[{account_name}] 无法从page中提取allowance_alliance_scenes")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_allowance_message", (
                "❌ 识别失败：无法从链接中提取信息\n\n"
                "请确保链接包含完整的店铺信息\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
        
                                           
        token = None
        ad_activity_flag_str = self._extract_parameter_value_from_page_path(decoded_page, 'ad_activity_flag')
        if ad_activity_flag_str:
            try:
                             
                ad_activity_flag = json.loads(ad_activity_flag_str)
            except json.JSONDecodeError:
                                      
                try:
                    decoded_flag = urllib.parse.unquote(ad_activity_flag_str)
                    ad_activity_flag = json.loads(decoded_flag)
                except (json.JSONDecodeError, Exception) as e:
                    self.logger.warning(f"解析ad_activity_flag失败: {e}")
                    ad_activity_flag = None
            
                                       
            if ad_activity_flag:
                token = ad_activity_flag.get('token', None)
                if not token:
                    self.logger.warning("ad_activity_flag中未找到token")
        else:
            self.logger.info("未找到ad_activity_flag参数，token将设为null")
        
                    
        if not token:
            self.logger.warning(f"[{account_name}] 无法从链接中提取token")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_token_message", (
                "❌ 识别失败：无法从链接中提取信息\n\n"
                "请确保您：\n"
                "1. 从发送的链接进入小程序\n"
                "2. 链接中包含完整的店铺信息\n"
                "3. 链接是从收藏的店铺中获取的\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp
                      
        free_delivery_url = self._build_free_delivery_url(poi_value, token, allowance, to_user_name)
        
        if not free_delivery_url:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "free_delivery_build_failed_message", "❌ 构建免配链接失败\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>")
            return rsp
        
        state.pop("pending_free_delivery_scheme", None)
        state.pop("pending_free_delivery_shop_name", None)
        self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
        
        self.logger.info(f"[{account_name}] 成功构建免配链接（从链接） - token: {token[:20]}...")
        
                     
        button_name = self._get_button_name(to_user_name)
        
        rsp = TextRspMsg(msg)
        rsp.content = self._render_text(msg, "free_delivery_success_template", (
            "✅ 识别成功！\n\n"
            "点击下方链接跳转到免配页面：\n"
            "<a href=\"{free_delivery_url}\">🚚 立即跳转免配</a>\n\n"
            "💡 提示：点击链接后会自动跳转到{button_name}App，享受免配送费优惠\n\n"
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
        ), free_delivery_url=free_delivery_url, button_name=button_name)
        return rsp

    async def _ahandle_link_message(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        from utils.response import TextRspMsg
        from config.config import ACCOUNT_SPECIFIC_CONFIGS, LINK_CONFIG
        from link_handlers.link_recognizer import LinkRecognizer
        import json
        import urllib.parse

        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        to_user_name = msg.get("ToUserName", "")

        self.logger.info(f"[{account_name}] 收到链接消息 - 链接: {text[:100]}...")
        account_config = msg.get("_account_config", {})
        if not account_config:
            account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
        zmkey = resolve_zmkey(msg, account_config)
        if not zmkey:
            self.logger.error(f"[{account_name}] 未配置zmkey，无法解析链接")
            state.pop("pending_free_delivery_scheme", None)
            state.pop("pending_free_delivery_shop_name", None)
            self.clear_user_state(user_id, "系统配置错误：未配置zmkey")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_zmkey_message", (
                "❌ 系统配置错误：请联系管理员\n\n"
                "请联系管理员配置\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp

        link_recognizer = LinkRecognizer(LINK_CONFIG)
        link_info = link_recognizer.recognize(text.strip())
        if not link_info:
            self.logger.warning(f"[{account_name}] 无法识别链接类型: {text[:100]}")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_unrecognized_message", (
                "❌ 无法识别链接类型\n\n"
                "请确保链接格式正确（#小程序://或mp://开头）\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp

        api_client = LinkConversionAPI(zmkey, self.logger)
        step1_result = await api_client.astep1_parse_link(text.strip())
        if not step1_result:
            self.logger.warning(f"[{account_name}] 解析链接失败")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_parse_failed_message", (
                "❌ 解析链接失败\n\n"
                "请确保链接格式正确且有效\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp

        page = step1_result.get("page", "")
        if not page:
            self.logger.warning(f"[{account_name}] 未获取到page参数")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_page_message", (
                "❌ 解析链接失败：未获取到参数\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp

        try:
            decoded_page = urllib.parse.unquote(page)
            self.logger.info(f"[{account_name}] 解码后的page: {decoded_page[:200]}...")
        except Exception as e:
            self.logger.warning(f"[{account_name}] URL解码失败，使用原始page: {e}")
            decoded_page = page

        poi_id_str = self._extract_poi_id_str_from_page_path(decoded_page)
        self.logger.info(f"[{account_name}] 提取到的poi_id_str: {poi_id_str}, 类型: {type(poi_id_str)}")
        if not poi_id_str:
            self.logger.warning(f"[{account_name}] 无法从page中提取poi_id_str")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_shop_message", (
                "❌ 识别失败：无法从链接中提取店铺信息\n\n"
                "请确保链接包含完整的店铺信息\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp

        poi_value = poi_id_str.split('=', 1)[1] if '=' in poi_id_str else poi_id_str
        self.logger.info(f"[{account_name}] 提取到的poi_value: {poi_value}")
        if not poi_value:
            self.logger.warning(f"[{account_name}] 无法从poi_id_str中提取poi值")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_poi_message", "❌ 识别失败：无法提取店铺ID\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>")
            return rsp

        allowance = self._extract_parameter_value_from_page_path(decoded_page, 'allowance_alliance_scenes')
        if not allowance:
            self.logger.warning(f"[{account_name}] 无法从page中提取allowance_alliance_scenes")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_allowance_message", (
                "❌ 识别失败：无法从链接中提取信息\n\n"
                "请确保链接包含完整的店铺信息\n\n"
                "您可以：\n"
                "1. 重新发送链接\n"
                "2. 发送小程序卡片\n"
                "3. <a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp

        token = None
        ad_activity_flag_str = self._extract_parameter_value_from_page_path(decoded_page, 'ad_activity_flag')
        if ad_activity_flag_str:
            try:
                ad_activity_flag = json.loads(ad_activity_flag_str)
            except json.JSONDecodeError:
                try:
                    decoded_flag = urllib.parse.unquote(ad_activity_flag_str)
                    ad_activity_flag = json.loads(decoded_flag)
                except (json.JSONDecodeError, Exception) as e:
                    self.logger.warning(f"解析ad_activity_flag失败: {e}")
                    ad_activity_flag = None
            if ad_activity_flag:
                token = ad_activity_flag.get('token', None)
                if not token:
                    self.logger.warning("ad_activity_flag中未找到token")
        else:
            self.logger.info("未找到ad_activity_flag参数，token将设为null")

        if not token:
            self.logger.warning(f"[{account_name}] 无法从链接中提取token")
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "link_missing_token_message", (
                "❌ 识别失败：无法从链接中提取信息\n\n"
                "请确保您：\n"
                "1. 从发送的链接进入小程序\n"
                "2. 链接中包含完整的店铺信息\n"
                "3. 链接是从收藏的店铺中获取的\n\n"
                "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
            ))
            return rsp

        free_delivery_url = self._build_free_delivery_url(poi_value, token, allowance, to_user_name)
        if not free_delivery_url:
            rsp = TextRspMsg(msg)
            rsp.content = self._get_text(msg, "free_delivery_build_failed_message", "❌ 构建免配链接失败\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>")
            return rsp

        state.pop("pending_free_delivery_scheme", None)
        state.pop("pending_free_delivery_shop_name", None)
        self.set_user_state(user_id, self.STATE_SHOWING_RESULTS, state)
        self.logger.info(f"[{account_name}] 成功构建免配链接（从链接） - token: {token[:20]}...")
        button_name = self._get_button_name(to_user_name)
        rsp = TextRspMsg(msg)
        rsp.content = self._render_text(msg, "free_delivery_success_template", (
            "✅ 识别成功！\n\n"
            "点击下方链接跳转到免配页面：\n"
            "<a href=\"{free_delivery_url}\">🚚 立即跳转免配</a>\n\n"
            "💡 提示：点击链接后会自动跳转到{button_name}App，享受免配送费优惠\n\n"
            "<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=返回\">⬅️ 返回商铺选择</a>"
        ), free_delivery_url=free_delivery_url, button_name=button_name)
        return rsp
