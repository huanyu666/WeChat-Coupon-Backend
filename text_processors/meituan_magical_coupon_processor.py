from __future__ import annotations

import json
import re
import secrets
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from .meituan_shop_query_processor import MeituanShopQueryProcessor
from .stateful_processor import StatefulTextProcessor
from utils import http_client as requests
from utils.meituan_mtgsig import (
    MeituanMtgsigError,
    build_browser_env,
    generate_mtgsig,
)
from utils.response import TextRspMsg


ANDROID_USER_AGENT = (
    "Mozilla/5.0 (Linux; U; Android 13; zh-cn; PBBM30 Build/TP1A.220905.001) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.61 "
    "Mobile Safari/537.36 HeyTapBrowser/40.8.24.1"
)
SESSION_CONTEXT_TTL_SECONDS = 1800
BROWSER_PROFILES: Tuple[Dict[str, Any], ...] = (
    {
        "user_agent": "Mozilla/5.0 (Linux; U; Android 13; zh-cn; PBBM30 Build/TP1A.220905.001) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/90.0.4430.61 Mobile Safari/537.36 HeyTapBrowser/40.8.24.1",
        "screen": {"width": 390, "height": 844, "availWidth": 390, "availHeight": 844, "colorDepth": 24, "pixelDepth": 24},
        "navigator": {"platform": "Linux armv8l", "vendor": "Google Inc.", "hardwareConcurrency": 8, "maxTouchPoints": 5},
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; U; Android 14; zh-cn; PJD110 Build/UKQ1.231108.001) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/118.0.5993.112 Mobile Safari/537.36 HeyTapBrowser/40.9.28.2",
        "screen": {"width": 412, "height": 915, "availWidth": 412, "availHeight": 915, "colorDepth": 24, "pixelDepth": 24},
        "navigator": {"platform": "Linux armv8l", "vendor": "Google Inc.", "hardwareConcurrency": 8, "maxTouchPoints": 5},
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; U; Android 13; zh-cn; PHK110 Build/TP1A.220905.001) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/117.0.5938.153 Mobile Safari/537.36 HeyTapBrowser/40.8.26.1",
        "screen": {"width": 393, "height": 873, "availWidth": 393, "availHeight": 873, "colorDepth": 24, "pixelDepth": 24},
        "navigator": {"platform": "Linux armv8l", "vendor": "Google Inc.", "hardwareConcurrency": 8, "maxTouchPoints": 5},
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; U; Android 14; zh-cn; PJB110 Build/UKQ1.231207.002) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.6099.144 Mobile Safari/537.36 HeyTapBrowser/41.0.2.1",
        "screen": {"width": 430, "height": 932, "availWidth": 430, "availHeight": 932, "colorDepth": 24, "pixelDepth": 24},
        "navigator": {"platform": "Linux armv8l", "vendor": "Google Inc.", "hardwareConcurrency": 8, "maxTouchPoints": 10},
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; U; Android 13; zh-cn; PGKM10 Build/TP1A.220905.001) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.5845.172 Mobile Safari/537.36 HeyTapBrowser/40.7.30.1",
        "screen": {"width": 360, "height": 800, "availWidth": 360, "availHeight": 800, "colorDepth": 24, "pixelDepth": 24},
        "navigator": {"platform": "Linux armv8l", "vendor": "Google Inc.", "hardwareConcurrency": 6, "maxTouchPoints": 5},
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; U; Android 14; zh-cn; PKQ110 Build/UKQ1.230924.001) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/119.0.6045.194 Mobile Safari/537.36 HeyTapBrowser/40.9.16.3",
        "screen": {"width": 412, "height": 892, "availWidth": 412, "availHeight": 892, "colorDepth": 24, "pixelDepth": 24},
        "navigator": {"platform": "Linux armv8l", "vendor": "Google Inc.", "hardwareConcurrency": 8, "maxTouchPoints": 10},
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; U; Android 14; zh-cn; PHY110 Build/UKQ1.231108.001) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.6099.230 Mobile Safari/537.36 HeyTapBrowser/41.1.0.2",
        "screen": {"width": 393, "height": 852, "availWidth": 393, "availHeight": 852, "colorDepth": 24, "pixelDepth": 24},
        "navigator": {"platform": "Linux armv8l", "vendor": "Google Inc.", "hardwareConcurrency": 8, "maxTouchPoints": 10},
    },
    {
        "user_agent": "Mozilla/5.0 (Linux; U; Android 13; zh-cn; PERM10 Build/TP1A.220905.001) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/117.0.5938.149 Mobile Safari/537.36 HeyTapBrowser/40.8.18.5",
        "screen": {"width": 360, "height": 780, "availWidth": 360, "availHeight": 780, "colorDepth": 24, "pixelDepth": 24},
        "navigator": {"platform": "Linux armv8l", "vendor": "Google Inc.", "hardwareConcurrency": 6, "maxTouchPoints": 5},
    },
)
USER_SESSION_CONTEXTS: Dict[str, Dict[str, Any]] = {}
H5_ORIGIN = "https://h5.waimai.meituan.com"
SHOP_LIST_REQUEST_URL_TEMPLATE = (
    "https://i.waimai.meituan.com/tsp/open/openh5/home/shopList"
    "?set_name=waimai-zhongnan"
    "&region_id={region_id}"
    "&_={req_time}"
    "&yodaReady=h5"
    "&csecplatform=4"
    "&csecversion=4.2.0"
)
FIRST_REQUEST_URL = (
    "https://i.waimai.meituan.com/openh5/order/trade/v3/getvalidV2"
    "?yodaReady=h5&csecplatform=4&csecversion=4.2.0"
)
SECOND_REQUEST_URL_TEMPLATE = (
    "https://i.waimai.meituan.com/v6/poi/coupon/exchange_for_magical_coupon"
    "?region_id=1000120100"
    "&region_version=1776160372814"
    "&wmUserIdDeregistration=0"
    "&wmUuidDeregistration=0"
    "&wm_actual_latitude={wm_actual_latitude}"
    "&wm_actual_longitude={wm_actual_longitude}"
    "&partner=4"
    "&personalized=1"
    "&req_time={req_time}"
    "&utm_campaign=openapi"
    "&utm_content=openapi"
    "&utm_medium=openapi"
    "&utm_source=openapi"
    "&utm_term=openapi"
    "&wm_appversion=4.0.0"
    "&wm_ctype=openapi"
    "&wm_dversion=4.0.0"
    "&wm_dtype=IPhone"
    "&yodaReady=h5"
    "&csecplatform=4"
    "&csecversion=4.2.0"
)


class MeituanMagicalCouponProcessor(StatefulTextProcessor):
    STATE_WAITING_INPUT = "waiting_input"
    STATE_WAITING_COUPON_SELECTION = "waiting_coupon_selection"

    def __init__(self, logger):
        super().__init__(logger, state_timeout=600)
        self.trigger_keywords = ["神券膨胀", "膨胀神券", "神券升级"]
        self.shop_query_helper = MeituanShopQueryProcessor(logger)
        self.logger.info("MeituanMagicalCouponProcessor 初始化完成")

    def _cleanup_session_contexts(self) -> None:
        now = time.time()
        expired_user_ids = [
            user_id
            for user_id, context in USER_SESSION_CONTEXTS.items()
            if now - float(context.get("updated_at") or 0) > SESSION_CONTEXT_TTL_SECONDS
        ]
        for user_id in expired_user_ids:
            USER_SESSION_CONTEXTS.pop(user_id, None)

    async def _get_or_create_session_context(
        self,
        *,
        user_id: str,
        user_id_value: str,
        token: str,
    ) -> Dict[str, Any]:
        self._cleanup_session_contexts()
        now = time.time()
        existing = USER_SESSION_CONTEXTS.get(user_id)
        if (
            existing
            and existing.get("userId") == user_id_value
            and existing.get("token") == token
        ):
            existing["updated_at"] = now
            return existing

        from utils.proxy_utils import ProxyUnavailableError, require_proxy_config_async

        try:
            proxies = await require_proxy_config_async()
        except ProxyUnavailableError as exc:
            raise RuntimeError("网络繁忙，请稍后重试") from exc

        browser_profile = dict(BROWSER_PROFILES[secrets.randbelow(len(BROWSER_PROFILES))])
        session_context = {
            "userId": user_id_value,
            "token": token,
            "browser_profile": browser_profile,
            "proxies": proxies,
            "updated_at": now,
        }
        USER_SESSION_CONTEXTS[user_id] = session_context
        return session_context

    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")
        self.set_user_state(user_id, self.STATE_WAITING_INPUT)
        rsp = TextRspMsg(msg)
        rsp.content = (
            "请输入以下信息：\n"
            "经度,纬度 账号链接 二级城市代码 三级城市代码\n\n"
            "示例：\n"
            "101.123456,21.654321 "
            "https://i.meituan.com/mttouch/page/account?userId=xxx&token=xxx "
            "111111 222222"
        )
        return rsp

    async def ahandle_state(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Optional[Any]:
        import time as _time
        from .stateful_processor import USER_STATES

        user_id = msg.get("FromUserName", "")
        cancel_keywords = {"取消", "退出", "返回", "quit", "cancel", "back"}
        if text.strip() in cancel_keywords:
            self.clear_user_state(user_id, "用户取消操作")
            rsp = TextRspMsg(msg)
            rsp.content = "已取消神券膨胀流程"
            return rsp

        if user_id in USER_STATES:
            USER_STATES[user_id]["last_update_time"] = _time.time()

        current_state = state.get("state")
        if current_state == self.STATE_WAITING_INPUT:
            return await self._ahandle_input(msg, text, state)
        if current_state == self.STATE_WAITING_COUPON_SELECTION:
            return await self._ahandle_coupon_selection(msg, text, state)
        self.clear_user_state(user_id, "未知状态")
        return None

    async def _ahandle_input(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        parsed = self._parse_user_input(text)
        if not parsed:
            rsp = TextRspMsg(msg)
            rsp.content = (
                "❌ 输入格式不正确\n\n"
                "请按：经度,纬度 账号链接 二级城市代码 三级城市代码"
            )
            return rsp

        user_id_value = parsed["userId"]
        token = parsed["token"]
        longitude = parsed["longitude"]
        latitude = parsed["latitude"]
        second_city_id = parsed["second_city_id"]
        third_city_id = parsed["third_city_id"]
        wm_longitude = self.shop_query_helper._convert_coordinate_to_int(longitude)
        wm_latitude = self.shop_query_helper._convert_coordinate_to_int(latitude)
        try:
            session_context = await self._get_or_create_session_context(
                user_id=user_id,
                user_id_value=user_id_value,
                token=token,
            )
        except RuntimeError as exc:
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ {exc}"
            return rsp

        nearest_shop = await self._find_target_shop(
            user_id_value=user_id_value,
            token=token,
            wm_longitude=wm_longitude,
            wm_latitude=wm_latitude,
            second_city_id=second_city_id,
            session_context=session_context,
        )
        if not nearest_shop:
            self.clear_user_state(user_id, "未找到可用店铺")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 当前暂时无法继续，请稍后再试"
            return rsp

        state.update(
            {
                "userId": user_id_value,
                "token": token,
                "wm_longitude": wm_longitude,
                "wm_latitude": wm_latitude,
                "second_city_id": second_city_id,
                "third_city_id": third_city_id,
                "poi_id_str": nearest_shop["poi_id_str"],
                "shop_name": nearest_shop["poi_name"],
                "session_context": session_context,
            }
        )

        coupon_result = await self._fetch_valid_coupons(state)
        if not coupon_result.get("success"):
            self.clear_user_state(user_id, "获取券包失败")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 当前暂时无法获取可用券包，请稍后再试"
            return rsp

        coupons = coupon_result["coupons"]
        if not coupons:
            self.clear_user_state(user_id, "无可膨胀券")
            rsp = TextRspMsg(msg)
            rsp.content = "⚠️ 当前没有可膨胀的神券包"
            return rsp

        state["candidate_coupons"] = coupons
        self.set_user_state(user_id, self.STATE_WAITING_COUPON_SELECTION, state)
        return self._format_coupon_candidates(msg, state)

    async def _ahandle_coupon_selection(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Any:
        user_id = msg.get("FromUserName", "")
        choice = text.strip()
        coupons = state.get("candidate_coupons", [])
        if not choice.isdigit():
            rsp = TextRspMsg(msg)
            rsp.content = "请输入券包编号"
            return rsp

        index = int(choice) - 1
        if index < 0 or index >= len(coupons):
            rsp = TextRspMsg(msg)
            rsp.content = "券包编号无效，请重新输入"
            return rsp

        selected_coupon = coupons[index]
        exchange_result = await self._exchange_coupon(state, selected_coupon)
        self.clear_user_state(user_id, "完成神券膨胀流程")
        rsp = TextRspMsg(msg)
        if exchange_result.get("success"):
            rsp.content = self._format_exchange_success(exchange_result["data"])
            return rsp

        rsp.content = self._format_exchange_failure(exchange_result)
        return rsp

    def _parse_user_input(self, text: str) -> Optional[Dict[str, Any]]:
        coordinates = self.shop_query_helper._extract_coordinates(text)
        user_id_value, token = self.shop_query_helper._extract_user_info(text)
        if not coordinates or not user_id_value or not token:
            return None

        working = text
        url_match = re.search(r"https?://[^\s\u4e00-\u9fa5]+", working)
        if url_match:
            working = working.replace(url_match.group(0), " ", 1)
        coord_match = re.search(r"-?\d+\.?\d*[,\s]+-?\d+\.?\d*", working)
        if coord_match:
            working = working.replace(coord_match.group(0), " ", 1)

        city_codes = re.findall(r"\b\d{6}\b", working)
        if len(city_codes) < 2:
            return None

        longitude, latitude = coordinates
        return {
            "longitude": longitude,
            "latitude": latitude,
            "userId": user_id_value,
            "token": token,
            "second_city_id": city_codes[0],
            "third_city_id": city_codes[1],
        }

    async def _find_target_shop(
        self,
        *,
        user_id_value: str,
        token: str,
        wm_longitude: int,
        wm_latitude: int,
        second_city_id: str,
        session_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        req_time = str(int(time.time() * 1000))
        region_id = self._build_region_id(second_city_id)
        request_url = SHOP_LIST_REQUEST_URL_TEMPLATE.format(
            region_id=region_id,
            req_time=req_time,
        )
        body_dict = {
            "optimus_code": "10",
            "optimus_risk_level": "71",
            "pageSize": "20",
            "page_index": "0",
            "offset": "0",
            "content_personalized_switch": "0",
            "sort_type": "",
            "slider_select_data": "",
            "activity_filter_codes": "",
            "wm_latitude": str(wm_latitude),
            "wm_longitude": str(wm_longitude),
            "wmUuidDeregistration": "0",
            "wmUserIdDeregistration": "0",
        }
        body = urllib.parse.urlencode(body_dict)
        cookie = self._build_cookie(
            user_id_value=user_id_value,
            token=token,
        )
        headers = await self._build_signed_headers(
            url=request_url,
            body=body,
            cookie=cookie,
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
            session_context=session_context,
        )
        result = await self._post_with_proxy(
            request_url,
            body,
            headers,
            timeout=10,
            session_context=session_context,
        )
        if not result or not result.get("success"):
            self.logger.error(
                "商家查询失败 result=%s",
                json.dumps(result, ensure_ascii=False, default=str),
            )
            return None

        shops = self._extract_coupon_shops(result["data"])
        if not shops:
            self.logger.error(
                "商家查询未解析到店铺 query_result=%s",
                json.dumps(result["data"], ensure_ascii=False, default=str),
            )
            return None
        return shops[0]

    def _build_region_id(self, second_city_id: str) -> str:
        city_code = str(second_city_id or "").strip()
        if city_code.isdigit():
            return f"1000{city_code}"
        return "1000100000"

    def _extract_coupon_shops(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_data = payload.get("data")
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                self.logger.error("shopList data 不是有效 JSON: %s", raw_data[:500])
                return []
        if not isinstance(raw_data, dict):
            self.logger.error("shopList data 结构异常: %r", raw_data)
            return []

        collected: List[Dict[str, Any]] = []
        self._walk_shop_modules(raw_data, collected)
        coupon_shops = []
        for item in collected:
            coupon_tag = item.get("coupon_tag") or {}
            coupon_text = str(coupon_tag.get("text") or "").strip()
            if coupon_text != "神券":
                continue
            poi_id_str = str(item.get("poi_id_str") or "").strip()
            if not poi_id_str:
                poi_id_str = self._extract_poi_id_str_from_scheme(str(item.get("scheme") or ""))
            if not poi_id_str:
                continue
            coupon_shops.append(
                {
                    "poi_name": str(item.get("poi_name") or ""),
                    "distance": str(item.get("distance") or ""),
                    "scheme": str(item.get("scheme") or ""),
                    "poi_id_str": poi_id_str,
                    "_distance_value": self._parse_distance_to_meters(item.get("distance")),
                }
            )

        coupon_shops.sort(
            key=lambda item: (item["_distance_value"] is None, item["_distance_value"] or 0)
        )
        return coupon_shops

    def _walk_shop_modules(self, node: Any, collected: List[Dict[str, Any]]) -> None:
        if isinstance(node, dict):
            string_data = node.get("string_data")
            if isinstance(string_data, str) and string_data:
                try:
                    parsed = json.loads(string_data)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, dict) and (
                    parsed.get("poi_name") or parsed.get("poi_id_str") or parsed.get("coupon_tag")
                ):
                    collected.append(parsed)

            for value in node.values():
                if isinstance(value, (dict, list)):
                    self._walk_shop_modules(value, collected)
        elif isinstance(node, list):
            for item in node:
                self._walk_shop_modules(item, collected)

    def _extract_poi_id_str_from_scheme(self, scheme: str) -> Optional[str]:
        if not scheme:
            return None
        candidate = scheme
        if scheme.startswith("?"):
            candidate = "http://dummy" + scheme
        if "://" in candidate:
            parsed = urllib.parse.urlparse(candidate)
            params = urllib.parse.parse_qs(parsed.query)
            poi_id_str = params.get("poi_id_str", [None])[0]
            if poi_id_str:
                return poi_id_str
        params = urllib.parse.parse_qs(scheme)
        poi_id_str = params.get("poi_id_str", [None])[0]
        if poi_id_str:
            return poi_id_str
        match = re.search(r"poi_id_str=([^&]+)", scheme)
        if match:
            return match.group(1)
        return None

    def _parse_distance_to_meters(self, value: Any) -> Optional[int]:
        text = str(value or "").strip().lower()
        if not text:
            return None
        km_match = re.search(r"(\d+(?:\.\d+)?)\s*km", text)
        if km_match:
            return int(float(km_match.group(1)) * 1000)
        m_match = re.search(r"(\d+)\s*m", text)
        if m_match:
            return int(m_match.group(1))
        number_match = re.search(r"(\d+(?:\.\d+)?)", text)
        if number_match:
            return int(float(number_match.group(1)))
        return None

    async def _fetch_valid_coupons(self, state: Dict[str, Any]) -> Dict[str, Any]:
        try:
            user_id_value = state["userId"]
            token = state["token"]
            wm_longitude = state["wm_longitude"]
            wm_latitude = state["wm_latitude"]
            second_city_id = state["second_city_id"]
            third_city_id = state["third_city_id"]
            poi_id_str = state["poi_id_str"]
            session_context = state["session_context"]

            body_dict = {
                "biz_line": "waimai",
                "business_type": "0",
                "wm_poi_id": "-100",
                "poi_id_str": poi_id_str,
                "total": "44.2",
                "original_price": "44.2",
                "can_use_coupon_price": "39.2",
                "order_token": "",
                "coupon_view_id": "-1",
                "activity_info_for_coupon": "",
                "addr_latitude": str(wm_latitude),
                "addr_longitude": str(wm_longitude),
                "phone": "",
                "wmUuidDeregistration": "0",
                "wmUserIdDeregistration": "0",
                "wm_latitude": str(wm_latitude),
                "wm_longitude": str(wm_longitude),
                "wm_actual_latitude": str(wm_latitude),
                "wm_actual_longitude": str(wm_longitude),
                "optimus_code": "10",
                "optimus_risk_level": "71",
                "preview_order_callback_info": "",
                "request_page_source": "2002",
                "extendParamJson": json.dumps({"pageVersion": 1}, ensure_ascii=False, separators=(",", ":")),
                "actualSecondCityId": second_city_id,
                "actualThirdCityId": third_city_id,
                "selectSecondCityId": second_city_id,
                "selectThirdCityId": third_city_id,
            }
            body = urllib.parse.urlencode(body_dict)
            cookie = self._build_cookie(
                user_id_value=user_id_value,
                token=token,
            )
            headers = await self._build_signed_headers(
                url=FIRST_REQUEST_URL,
                body=body,
                cookie=cookie,
                content_type="application/x-www-form-urlencoded; charset=UTF-8",
                session_context=session_context,
            )

            result = await self._post_with_proxy(
                FIRST_REQUEST_URL,
                body,
                headers,
                timeout=10,
                session_context=session_context,
            )
            if not result.get("success"):
                return result

            data = result.get("data") or {}
            coupon_data = (((data.get("data") or {}).get("coupons") or {}).get("enabledCouponList") or [])
            coupons = [coupon for coupon in (self._normalize_coupon(item) for item in coupon_data) if coupon]
            return {
                "success": True,
                "coupons": coupons,
            }
        except Exception as exc:
            self.logger.exception("获取可用券包异常")
            return {
                "success": False,
                "error": str(exc),
            }

    def _normalize_coupon(self, coupon: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            extend_info_raw = coupon.get("extendInfo") or {}
            if isinstance(extend_info_raw, str):
                extend_info = json.loads(extend_info_raw) if extend_info_raw else {}
            elif isinstance(extend_info_raw, dict):
                extend_info = extend_info_raw
            else:
                extend_info = {}
        except Exception:
            extend_info = {}

        can_inflate = (
            coupon.get("valid") is True
            and coupon.get("disabled") is False
            and coupon.get("couponPackage") is True
            and int(coupon.get("exchange_type") or 0) == 11
            and (
                bool(extend_info.get("inflateConfigId"))
                or bool(extend_info.get("inflateConfigViewId"))
                or int(extend_info.get("inflateType") or 0) > 0
            )
        )
        if not can_inflate:
            return None

        return {
            "title": str(coupon.get("title") or ""),
            "amount": coupon.get("amount"),
            "price_limit": str(coupon.get("price_limit") or ""),
            "valid_time_desc": str(coupon.get("valid_time_desc") or ""),
            "coupon_view_id": str(coupon.get("coupon_view_id") or ""),
            "exchange_type": int(coupon.get("exchange_type") or 0),
            "outer_code": str(coupon.get("outer_code") or ""),
            "extend_info": extend_info,
            "raw_coupon": coupon,
        }

    def _format_coupon_candidates(self, msg: Dict[str, Any], state: Dict[str, Any]) -> Any:
        coupons = state.get("candidate_coupons", [])
        rsp = TextRspMsg(msg)
        lines = ["📦 可用券包：", ""]
        for idx, coupon in enumerate(coupons, start=1):
            extend_info = coupon.get("extend_info") or {}
            tip = str(
                extend_info.get("inflate_coupon_layer_doc")
                or extend_info.get("asset_inflate_prefix_doc")
                or extend_info.get("ttsq_poi_y_doc")
                or ""
            )
            lines.append(f"{idx}. {coupon['title']}")
            lines.append(f"面额：{coupon.get('amount')} | 门槛：{coupon.get('price_limit') or '无'}")
            lines.append(f"有效期：{coupon.get('valid_time_desc') or '未知'}")
            if tip:
                lines.append(f"提示：{tip}")
            lines.append("")
        lines.append("请输入编号执行膨胀，或输入“取消”退出。")
        rsp.content = "\n".join(lines).strip()
        return rsp

    async def _exchange_coupon(
        self, state: Dict[str, Any], coupon: Dict[str, Any]
    ) -> Dict[str, Any]:
        try:
            user_id_value = state["userId"]
            token = state["token"]
            wm_longitude = state["wm_longitude"]
            wm_latitude = state["wm_latitude"]
            second_city_id = state["second_city_id"]
            third_city_id = state["third_city_id"]
            poi_id_str = state["poi_id_str"]
            req_time = str(int(time.time() * 1000))
            session_context = state["session_context"]

            request_url = SECOND_REQUEST_URL_TEMPLATE.format(
                wm_actual_latitude=wm_latitude,
                wm_actual_longitude=wm_longitude,
                req_time=req_time,
            )
            extend_info = json.dumps(
                {
                    "couponViewId": coupon["coupon_view_id"],
                    "strategyEvalPriceParam": {},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            body_dict = {
                "biz_line": "0",
                "exchange_type": str(coupon["exchange_type"]),
                "outer_code": coupon["outer_code"],
                "planToken": "temple_id_93",
                "poi_id_str": poi_id_str,
                "request_page_source": "2002",
                "wm_poi_id": "-100",
                "coupon_view_id": coupon["coupon_view_id"],
                "extend_info": extend_info,
                "actualSecondCityId": second_city_id,
                "actualThirdCityId": third_city_id,
                "selectSecondCityId": second_city_id,
                "selectThirdCityId": third_city_id,
                "addressSecondCityId": "0",
                "addressThirdCityId": "0",
            }
            body = urllib.parse.urlencode(body_dict)
            cookie = self._build_cookie(
                user_id_value=user_id_value,
                token=token,
            )
            headers = await self._build_signed_headers(
                url=request_url,
                body=body,
                cookie=cookie,
                content_type="application/x-www-form-urlencoded; charset=UTF-8",
                session_context=session_context,
            )

            return await self._post_with_proxy(
                request_url,
                body,
                headers,
                timeout=10,
                session_context=session_context,
            )
        except Exception as exc:
            self.logger.exception("执行券包处理异常")
            return {
                "success": False,
                "error": str(exc),
            }

    async def _build_signed_headers(
        self,
        *,
        url: str,
        body: str,
        cookie: str,
        content_type: str,
        session_context: Dict[str, Any],
    ) -> Dict[str, str]:
        browser_profile = dict(session_context.get("browser_profile") or {})
        user_agent = str(browser_profile.get("user_agent") or ANDROID_USER_AGENT)
        headers = {
            "Host": "i.waimai.meituan.com",
            "Connection": "keep-alive",
            "Accept": "application/json, text/plain, */*",
            "User-Agent": user_agent,
            "Content-Type": content_type,
            "Origin": H5_ORIGIN,
            "Sec-Fetch-Site": "same-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": f"{H5_ORIGIN}/",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cookie": cookie,
        }
        try:
            mtgsig = await generate_mtgsig(
                url=url,
                method="POST",
                body=body,
                cookie=cookie,
                headers=headers,
                env=self._build_browser_env(headers=headers, cookie=cookie, session_context=session_context),
            )
        except MeituanMtgsigError as exc:
            self.logger.exception("生成 mtgsig 失败: %s", exc)
            raise RuntimeError("签名生成失败") from exc

        headers["mtgsig"] = mtgsig
        return headers

    def _build_browser_env(
        self,
        *,
        headers: Dict[str, str],
        cookie: str,
        session_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        browser_profile = dict(session_context.get("browser_profile") or {})
        env = build_browser_env(headers=headers, cookie=cookie, fallback_url=f"{H5_ORIGIN}/")
        navigator = dict(env.get("navigator") or {})
        navigator.update(dict(browser_profile.get("navigator") or {}))
        user_agent = str(browser_profile.get("user_agent") or navigator.get("userAgent") or ANDROID_USER_AGENT)
        navigator["userAgent"] = user_agent
        navigator["appVersion"] = user_agent
        env["navigator"] = navigator
        screen = dict(env.get("screen") or {})
        screen.update(dict(browser_profile.get("screen") or {}))
        env["screen"] = screen
        return env

    def _build_cookie(self, *, user_id_value: str, token: str) -> str:
        cookie_items = [
            ("userId", user_id_value),
            ("u", user_id_value),
            ("wm_order_channel", "mtib"),
            ("swim_line", "default"),
            ("utm_source", "60030"),
            ("au_trace_key_net", "default"),
            ("isIframe", "false"),
            ("terminal", "i"),
            ("token", token),
            ("mt_c_token", token),
            ("oops", token),
            ("isid", token),
            ("w_token", token),
            ("lt", token),
            (
                "w_utmz",
                '"utm_campaign=openapi&utm_source=openapi&utm_medium=openapi&utm_content=openapi&utm_term=openapi"',
            ),
            ("channelType", '{"mtib":"0"}'),
            ("isUuidUnion", "true"),
        ]
        return "; ".join(f"{key}={value}" for key, value in cookie_items if value != "")

    async def _post_with_proxy(
        self,
        url: str,
        body: str,
        headers: Dict[str, str],
        timeout: float,
        session_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        proxies = dict(session_context.get("proxies") or {})
        proxy_url = str(proxies.get("http") or proxies.get("https") or "").strip()
        try:
            from utils.proxy_utils import (
                report_proxy_failure_async,
                report_proxy_success_async,
            )

            try:
                response = await requests.post(
                    url,
                    data=body,
                    headers=headers,
                    proxies=proxies,
                    timeout=timeout,
                )
                response.raise_for_status()
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                if proxy_url:
                    await report_proxy_failure_async(proxy_url, exc)
                raise

            try:
                data = response.json()
            except Exception as exc:
                if proxy_url:
                    await report_proxy_failure_async(proxy_url, exc)
                raise

            if proxy_url:
                await report_proxy_success_async(proxy_url)
            if not isinstance(data, dict):
                self.logger.error("神券请求返回非字典结构: %r", data)
                return {"success": False, "error": "响应结构异常"}
            if int(data.get("code") or 0) != 0:
                self.logger.error(
                    "神券请求业务失败 url=%s response=%s",
                    url,
                    json.dumps(data, ensure_ascii=False),
                )
                return {
                    "success": False,
                    "code": data.get("code"),
                    "msg": data.get("msg", ""),
                    "data": data.get("data"),
                }
            return {"success": True, "data": data}
        except RuntimeError as exc:
            self.logger.exception("神券请求运行时异常 url=%s", url)
            return {"success": False, "error": str(exc)}
        except requests.Timeout:
            self.logger.exception("神券请求超时 url=%s", url)
            return {"success": False, "error": "请求超时"}
        except requests.ConnectionError:
            self.logger.exception("神券请求连接失败 url=%s", url)
            return {"success": False, "error": "网络连接失败"}
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response else 0
            response_text = ""
            try:
                if exc.response is not None:
                    response_text = exc.response.text
            except Exception:
                response_text = ""
            self.logger.error(
                "神券请求 HTTP 错误 url=%s status=%s response=%s",
                url,
                status_code,
                response_text,
            )
            return {"success": False, "error": f"HTTP {status_code}"}
        except requests.RequestException:
            self.logger.exception("神券请求异常 url=%s", url)
            return {"success": False, "error": "网络请求失败"}
        except Exception as exc:
            self.logger.exception("神券请求未知异常 url=%s", url)
            return {"success": False, "error": str(exc)}

    def _format_exchange_success(self, payload: Dict[str, Any]) -> str:
        data = payload.get("data") or {}
        coupon_multiple_list = data.get("couponMultipleList") or []
        first_coupon = coupon_multiple_list[0] if coupon_multiple_list else {}
        title_doc = str(first_coupon.get("couponTitleDoc") or data.get("msg") or "膨胀成功")
        lines = [f"✅ {title_doc}"]
        if first_coupon.get("finalCouponAmount") is not None:
            lines.append(f"最终面额：{first_coupon.get('finalCouponAmount')}")
        if first_coupon.get("couponName"):
            lines.append(f"券名：{first_coupon.get('couponName')}")
        if first_coupon.get("orderAmountLimitDoc"):
            lines.append(f"门槛：{first_coupon.get('orderAmountLimitDoc')}")
        if first_coupon.get("subTitle"):
            lines.append(f"说明：{first_coupon.get('subTitle')}")
        elif first_coupon.get("useConditions"):
            lines.append(f"说明：{first_coupon.get('useConditions')}")
        return "\n".join(lines)

    def _format_exchange_failure(self, payload: Dict[str, Any]) -> str:
        self.logger.error("膨胀失败 payload=%s", json.dumps(payload, ensure_ascii=False, default=str))
        return "❌ 当前暂时无法完成操作，请稍后再试"
