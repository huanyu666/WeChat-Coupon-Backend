"""
美团短链接转长链接处理器
"""
import re
import time
from utils import http_client as requests
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor
from utils.meituan_utils import (
    generate_miniprogram_link,
    build_meituan_coupon_variant_url,
    render_clickable_link_html,
    append_link_suffix,
    abuild_meituan_official_cashback_shortlink_url,
)
from utils.merchant_coupon_utils import aencrypt_merchant_coupon_data, extract_page_params
from utils.merchant_benefits import (
    aquery_benefits_for_wechat,
    format_benefits_for_wechat,
    format_benefits_pending_for_wechat,
)


class MeituanLinkProcessor(BaseTextProcessor):
    """美团短链接转长链接处理器"""
    REPLY_BUDGET_SECONDS = 4.2
    RESPONSE_SAFETY_SECONDS = 0.8
    MIN_OPTIONAL_STAGE_SECONDS = 0.25
    REQUIRED_SECONDARY_LINK_REMAINING_SECONDS = 1.2
    REQUIRED_SAVE_LINK_REMAINING_SECONDS = 1.0
    
    def __init__(self, logger, pattern=None):
        """
        初始化美团短链接处理器
        
        Args:
            logger: 日志记录器
            pattern: 关键词匹配模式（在main.py中配置）
        """
        super().__init__(logger, pattern)
        self.timeout = 2.0

    def _get_remaining_budget_seconds(self, deadline_at: Optional[float]) -> float:
        if deadline_at is None:
            return float("inf")
        return max(0.0, float(deadline_at) - time.time())

    def _has_optional_budget(self, deadline_at: Optional[float], required_remaining_seconds: float) -> bool:
        if deadline_at is None:
            return True
        remaining = self._get_remaining_budget_seconds(deadline_at)
        return remaining >= max(self.MIN_OPTIONAL_STAGE_SECONDS, required_remaining_seconds)

    def _get_resolve_timeout(self, deadline_at: Optional[float]) -> float:
        if deadline_at is None:
            return self.timeout
        allowed = self._get_remaining_budget_seconds(deadline_at) - self.RESPONSE_SAFETY_SECONDS
        if allowed < 0.5:
            return 0.5
        return min(self.timeout, allowed)
    
    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        from utils.response import TextRspMsg
        from config.config import ACCOUNT_SPECIFIC_CONFIGS, MEITUAN_LINK_CONFIG

        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
        meituan_base_url = account_config.get("meituan_base_url", "")

        if not meituan_base_url:
            self.logger.info(f"[{account_name}] 未配置美团链接，跳过处理")
            return None

        link_config = MEITUAN_LINK_CONFIG.get(to_user_name, {})
        click_detail_link = link_config.get("click_detail_link", "点击下方链接查看详情：")
        merchant_coupon_link = link_config.get("merchant_coupon_link", "立即查看商家券")
        merchant_coupon_link_2 = link_config.get("merchant_coupon_link_2", "领取商家券2(可切号，不一定有)")
        miniprogram_open_prefix = link_config.get("miniprogram_open_prefix", "领取商家券 (小程序版)")
        meituan_link_title_text = link_config.get("meituan_link_title_text", "🔗 美团优惠链接:")
        meituan_link_response_title_text = link_config.get("meituan_link_response_title_text", "【美团优惠链接】")
        cashback_activity_link_text = link_config.get("cashback_activity_link_text", "点击报名该商家「官方返现」活动")
        merchant_coupon_link_suffix = str(link_config.get("merchant_coupon_link_suffix", "") or "")
        merchant_coupon_link_2_suffix = str(link_config.get("merchant_coupon_link_2_suffix", "") or "")
        cashback_activity_link_suffix = str(link_config.get("cashback_activity_link_suffix", "") or "")
        miniprogram_link_suffix = str(link_config.get("miniprogram_link_suffix", "") or "")
        save_merchant_coupon_link_suffix = str(link_config.get("save_merchant_coupon_link_suffix", "") or "")
        short_links = self._extract_short_links(text)
        if not short_links:
            self.logger.warning(f"[{account_name}] 未找到dpurl.cn短链接: {text}")
            return None

        deadline_at = time.time() + self.REPLY_BUDGET_SECONDS
        shop_title = self._extract_shop_title(text)
        rsp = TextRspMsg(msg)

        for short_link in short_links:
            resolve_timeout = self._get_resolve_timeout(deadline_at)
            long_link = await self._aconvert_to_long_link(short_link, meituan_base_url, timeout=resolve_timeout)
            if long_link:
                long_link_2 = build_meituan_coupon_variant_url(
                    long_link,
                    variant="v5",
                    logger_instance=self.logger,
                )
                self.logger.info(f"[{account_name}] 构建链接HTML，long_link: {long_link}")
                miniprogram_link = generate_miniprogram_link(
                    long_link,
                    to_user_name,
                    {"miniprogram_open_prefix": miniprogram_open_prefix},
                )
                content_parts = [meituan_link_title_text]
                if click_detail_link:
                    content_parts.append(click_detail_link)
                if long_link:
                    link_html = render_clickable_link_html(long_link, merchant_coupon_link)
                    self.logger.info(f"[{account_name}] 构建的链接HTML: {link_html}")
                    content_parts.append(
                        append_link_suffix(
                            link_html,
                            merchant_coupon_link_suffix,
                        )
                    )
                    if long_link_2 and self._has_optional_budget(deadline_at, self.REQUIRED_SECONDARY_LINK_REMAINING_SECONDS):
                        link_html_2 = render_clickable_link_html(long_link_2, merchant_coupon_link_2)
                        content_parts.append(
                            append_link_suffix(
                                link_html_2,
                                merchant_coupon_link_2_suffix,
                            )
                        )
                    poi_id_str = self._extract_poi_id(long_link)
                    if poi_id_str:
                        try:
                            cashback_activity_link = await abuild_meituan_official_cashback_shortlink_url(
                                to_user_name,
                                poi_id_str,
                                cashback_activity_link_text,
                                self.logger,
                            )
                            if cashback_activity_link:
                                content_parts.append(
                                    append_link_suffix(
                                        cashback_activity_link,
                                        cashback_activity_link_suffix,
                                    )
                                )
                        except Exception as e:
                            self.logger.warning(f"[{account_name}] 生成返现活动入口失败: {e}")
                        benefits = await aquery_benefits_for_wechat(
                            poi_id_str=poi_id_str,
                            merchant_name="" if shop_title == "未知商家" else shop_title,
                            account_id=to_user_name,
                            source="wechat_dpurl",
                            timeout_seconds=max(0.5, self._get_remaining_budget_seconds(deadline_at)),
                        )
                        benefit_lines = format_benefits_for_wechat(benefits or {})
                        if benefit_lines:
                            content_parts.extend(benefit_lines)
                        else:
                            content_parts.extend(format_benefits_pending_for_wechat())
                else:
                    self.logger.warning(f"[{account_name}] long_link为空，跳过链接构建")
                content_parts.append(
                    append_link_suffix(miniprogram_link, miniprogram_link_suffix)
                )

                show_save_merchant_coupon_link = link_config.get("show_save_merchant_coupon_link", True)
                if show_save_merchant_coupon_link and self._has_optional_budget(deadline_at, self.REQUIRED_SAVE_LINK_REMAINING_SECONDS):
                    poi_id_str = self._extract_poi_id(long_link)
                    if poi_id_str:
                        try:
                            allowance, ad_activity_flag = extract_page_params(long_link)
                            encrypted_data = await aencrypt_merchant_coupon_data(
                                poi_id_str, allowance, ad_activity_flag, shop_title, self.logger
                            )
                            save_link_text = link_config.get("save_merchant_coupon_link_text", "查看获取链接教程")
                            content_parts.append(
                                render_clickable_link_html(
                                    f"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=保存商家券 - {encrypted_data}",
                                    save_link_text,
                                    save_merchant_coupon_link_suffix,
                                )
                            )
                        except Exception as e:
                            self.logger.warning(f"[{account_name}] 生成保存商家券链接失败: {e}")

                if rsp.content:
                    rsp.content += "\n" + "\n".join(content_parts)
                else:
                    rsp.content = f"{meituan_link_response_title_text}\n" + "\n".join(content_parts)
            else:
                error_msg = f"❌ 短链接: {short_link}\n无法转换"
                if rsp.content:
                    rsp.content += "\n" + error_msg
                else:
                    rsp.content = f"{meituan_link_response_title_text}\n" + error_msg

        return rsp
    
    @staticmethod
    def _convert_miniprogram_to_h5_link(
        miniprogram_link_html: str,
        fallback_h5_url: str,
        link_text: str,
    ) -> str:
        """将 data-miniprogram-appid 链接转为被动回复兼容的普通 H5 链接。

        微信被动回复文本消息不支持 data-miniprogram-appid 属性，
        只有客服消息才支持。被动回复中包含该属性会导致微信静默丢弃整条回复。
        """
        import re
        # 如果不包含 data-miniprogram-appid，原样返回
        if 'data-miniprogram-appid' not in miniprogram_link_html:
            return miniprogram_link_html
        # 提取 appid 和 path
        appid_match = re.search(r'data-miniprogram-appid="([^"]+)"', miniprogram_link_html)
        mp_match = re.search(r'data-miniprogram-path="([^"]+)"', miniprogram_link_html)
        if mp_match:
            mp_path = mp_match.group(1)
            # 尝试从 path 中提取 webviewUrl（webview 类型的小程序链接）
            url_match = re.search(r'webviewUrl=([^&]+)', mp_path)
            if url_match:
                import urllib.parse
                h5_url = urllib.parse.unquote(url_match.group(1))
                return render_clickable_link_html(h5_url, link_text)
        # 回退到传入的 H5 URL
        if fallback_h5_url:
            return render_clickable_link_html(fallback_h5_url, link_text)
        # 无法转为 H5 链接时，跳过该链接（返回空字符串）
        return ""

    def _extract_short_links(self, text: str) -> list:
        """
        从文本中提取所有dpurl.cn短链接
        
        Args:
            text: 文本内容
            
        Returns:
            短链接列表
        """
                                                    
                                                           
                              
        pattern = r"https?://dpurl\.cn/[a-zA-Z0-9\-._~]+"
        links = re.findall(pattern, text)
        
        self.logger.info(f"提取到{len(links)}个短链接: {links}")
        return links
    
    async def _aconvert_to_long_link(self, short_link: str, meituan_base_url: str, timeout: Optional[float] = None) -> Optional[str]:
        """
        将短链接转换为美团优惠链接
        
        Args:
            short_link: 短链接
            meituan_base_url: 美团基础URL（从公众号配置中获取）
            
        Returns:
            美团优惠链接，如果转换失败则返回None
        """
        try:
            self.logger.info(f"正在转换短链接: {short_link}")
            effective_timeout = self.timeout if timeout is None else max(0.5, float(timeout))
            
            response = await requests.head(
                short_link,
                allow_redirects=True,
                timeout=effective_timeout,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            )
            
            long_link = str(response.url)
            
            self.logger.info(f"转换成功: {short_link} -> {long_link}")
            
                                            
            poi_id_str = self._extract_poi_id(long_link)
            
            if not poi_id_str:
                self.logger.warning(f"无法从长链接中提取poi_id: {long_link}")
                return long_link           
            
                      
            meituan_link = f"{meituan_base_url}&poi_id=-100&poi_id_str={poi_id_str}"
            
            self.logger.info(f"生成美团优惠链接: {meituan_link}")
            return meituan_link
            
        except requests.Timeout:
            self.logger.error(f"请求超时: {short_link}")
            return None
        except requests.RequestException as e:
            self.logger.error(f"请求失败: {short_link}, 错误: {e}")
            return None
        except Exception as e:
            self.logger.error(f"转换失败: {short_link}, 错误: {e}")
            return None

    def _extract_poi_id(self, url: str) -> Optional[str]:
        """
        从美团URL中提取poi_id_str
        
        Args:
            url: 美团长链接
            
        Returns:
            poi_id_str，如果提取失败则返回None

        """
                                     
        match = re.search(r'[?&]poi_id_str=([^&]+)', url)
        if match:
            poi_id = match.group(1)
            self.logger.info(f"从查询参数提取到poi_id_str: {poi_id}")
            return poi_id
        
                                    
        match = re.search(r'/poi/([^?]+)', url)
        if match:
            poi_id = match.group(1)
            self.logger.info(f"从路径提取到poi_id_str: {poi_id}")
            return poi_id
        
        return None
    
    def _extract_shop_title(self, text: str) -> str:
        """
        从文本中提取店铺名称（「」之间的内容）
        
        Args:
            text: 用户输入的文本
            
        Returns:
            店铺名称，如果未找到则返回"未知商家"
        """
                   
        match = re.search(r'「([^」]+)」', text)
        if match:
            shop_title = match.group(1).strip()
            self.logger.info(f"提取到店铺名称: {shop_title}")
            return shop_title
        
        return "未知商家"
