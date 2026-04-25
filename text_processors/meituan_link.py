"""
美团短链接转长链接处理器
"""
import re
from utils import http_client as requests
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor
from utils.meituan_utils import (
    generate_miniprogram_link,
    build_meituan_coupon_variant_url,
    render_clickable_link_html,
    append_link_suffix,
    abuild_meituan_official_cashback_shortlink_url,
    abuild_go_shortlink_html,
)
from utils.merchant_coupon_utils import aencrypt_merchant_coupon_data, extract_page_params


class MeituanLinkProcessor(BaseTextProcessor):
    """美团短链接转长链接处理器"""
    
    def __init__(self, logger, pattern=None):
        """
        初始化美团短链接处理器
        
        Args:
            logger: 日志记录器
            pattern: 关键词匹配模式（在main.py中配置）
        """
        super().__init__(logger, pattern)
        self.timeout = 5          
    
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

        shop_title = self._extract_shop_title(text)
        rsp = TextRspMsg(msg)

        for short_link in short_links:
            long_link = await self._aconvert_to_long_link(short_link, meituan_base_url)
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
                    link_html = await abuild_go_shortlink_html(
                        long_link,
                        merchant_coupon_link,
                        self.logger,
                    )
                    self.logger.info(f"[{account_name}] 构建的链接HTML: {link_html}")
                    content_parts.append(
                        append_link_suffix(
                            link_html or render_clickable_link_html(long_link, merchant_coupon_link),
                            merchant_coupon_link_suffix,
                        )
                    )
                    if long_link_2:
                        link_html_2 = await abuild_go_shortlink_html(
                            long_link_2,
                            merchant_coupon_link_2,
                            self.logger,
                        )
                        content_parts.append(
                            append_link_suffix(
                                link_html_2 or render_clickable_link_html(long_link_2, merchant_coupon_link_2),
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
                else:
                    self.logger.warning(f"[{account_name}] long_link为空，跳过链接构建")
                content_parts.append(' <a href="http://"> </a> <a href="http://"> </a> <a href="http://"> </a>')
                content_parts.append(
                    append_link_suffix(miniprogram_link, miniprogram_link_suffix)
                )

                show_save_merchant_coupon_link = link_config.get("show_save_merchant_coupon_link", True)
                if show_save_merchant_coupon_link:
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
    
    async def _aconvert_to_long_link(self, short_link: str, meituan_base_url: str) -> Optional[str]:
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
            
            response = await requests.head(
                short_link,
                allow_redirects=True,
                timeout=self.timeout,
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
