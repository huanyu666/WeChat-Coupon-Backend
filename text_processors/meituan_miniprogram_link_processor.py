"""
美团小程序链接文本处理器

"""
import urllib.parse
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor
from utils.meituan_utils import (
    generate_miniprogram_link,
    build_meituan_coupon_variant_url,
    build_extra_params_url,
    extract_parameter_value,
    render_clickable_link_html,
    append_link_suffix,
    abuild_meituan_official_cashback_shortlink_url,
)
from utils.merchant_coupon_utils import aencrypt_merchant_coupon_data, extract_page_params
from utils.account_config import resolve_message_account_config, resolve_zmkey
from config.config import LINK_CONFIG
from link_handlers.link_recognizer import LinkRecognizer
from link_handlers.api_client import LinkConversionAPI


class MeituanMiniprogramLinkProcessor(BaseTextProcessor):
    PASSIVE_REPLY_SOFT_LIMIT = 1200

    @staticmethod
    def _build_plain_link_line(url: str, label: str, suffix: str = "") -> str:
        clean_url = str(url or "").strip()
        clean_label = str(label or "").strip()
        clean_suffix = str(suffix or "")
        if not clean_url:
            return clean_label
        if clean_label:
            return f"{clean_label}\n{clean_url}{clean_suffix}"
        return f"{clean_url}{clean_suffix}"
    
    def __init__(self, logger):
                                        
        super().__init__(logger, pattern=None)
        
                  
        self.link_recognizer = LinkRecognizer(LINK_CONFIG)
        
        self.logger.info("MeituanMiniprogramLinkProcessor 初始化完成")

    def _reload_configs(self) -> None:
        from config.config import LINK_CONFIG as current_link_config

        self.link_recognizer = LinkRecognizer(current_link_config)
    
    def can_handle(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        
                                
        link_infos = self.link_recognizer.recognize_with_positions(text)
        
                           
        supported_types = ["meituan_miniprogram", "meituan_general", "mp_protocol"]
        count = 0
        for link_info in link_infos:
            link_type = link_info.get("type", "")
            if link_type in supported_types:
                count += 1
                if count > 1:
                                      
                    return False
        
                 
        return count == 1
    
    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        from utils.response import TextRspMsg

        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        account_config = resolve_message_account_config(msg)
        meituan_base_url = account_config.get("meituan_base_url", "")

        if not meituan_base_url:
            self.logger.info(f"[{account_name}] 未配置美团链接，跳过处理")
            return None

        link_infos = self.link_recognizer.recognize_with_positions(text)
        supported_types = ["meituan_miniprogram", "meituan_general", "mp_protocol"]
        filtered_link_infos = [
            link_info for link_info in link_infos
            if link_info.get("type", "") in supported_types
        ]
        if not filtered_link_infos:
            self.logger.warning(f"[{account_name}] 未识别到可处理的链接: {text[:100]}")
            return None
        if len(filtered_link_infos) > 1:
            self.logger.warning(f"[{account_name}] 检测到多个小程序链接，只处理第一个")

        link_info = filtered_link_infos[0]
        link_type = link_info.get("type", "")
        link_content = link_info.get("content", "")
        self.logger.info(f"[{account_name}] 处理小程序链接，类型: {link_type}")

        zmkey = resolve_zmkey(msg, account_config)
        if not zmkey:
            self.logger.warning(f"[{account_name}] 未配置zmkey，无法解析链接")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 系统配置错误：未配置ZM Key，请联系管理员"
            return rsp

        api_client = LinkConversionAPI(zmkey, self.logger)
        rsp = TextRspMsg(msg)
        try:
            step1_result = await api_client.astep1_parse_link(link_content)
            if not step1_result:
                self.logger.warning(f"[{account_name}] 链接解析失败")
                rsp.content = "❌ 链接解析失败"
                return rsp

            page = step1_result.get("page", "")
            if not page:
                self.logger.warning(f"[{account_name}] 未获取到page参数")
                rsp.content = "❌ 未获取到page参数"
                return rsp

            try:
                decoded_page = urllib.parse.unquote(page)
                self.logger.info(f"[{account_name}] 解码后的page: {decoded_page[:200]}...")
            except Exception as e:
                self.logger.warning(f"[{account_name}] URL解码失败，使用原始page: {e}")
                decoded_page = page

            response_content = await self._aprocess_single_link_like_meituan(
                msg, decoded_page, meituan_base_url, to_user_name, account_name, link_content
            )
            if response_content:
                self.logger.info(f"[{account_name}] 链接处理成功")
                self.logger.warning(
                    "[%s] 小程序链接回复预览: len=%d has_anchor=%s preview=%s",
                    account_name,
                    len(response_content),
                    "<a href=" in response_content,
                    response_content[:300].replace("\n", "\\n"),
                )
                rsp.content = response_content
            else:
                self.logger.warning(f"[{account_name}] 链接处理失败")
                rsp.content = "❌ 链接处理失败"
        except Exception as e:
            self.logger.error(f"[{account_name}] 链接处理异常: {e}", exc_info=True)
            rsp.content = f"❌ 链接处理异常: {str(e)}"

        return rsp
    
    async def _aprocess_single_link_like_meituan(self, msg: Dict[str, Any], page_path: str,
                                          meituan_base_url: str, to_user_name: str, account_name: str,
                                          original_link: str = "") -> Optional[str]:
        from config.config import MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG

        processor_config = MEITUAN_MINIPROGRAM_LINK_PROCESSOR_CONFIG.get(to_user_name, {})
        
                                      
        poi_value = extract_parameter_value(page_path, 'poi_id_str', self.logger)
        
        if not poi_value:
            self.logger.warning(f"[{account_name}] 未能从page_path中提取poi_id_str: {page_path[:100]}")
            no_link_message_text = processor_config.get("no_link_message_text", "收到美团小程序链接，暂无法获取详情链接~")
            return no_link_message_text
        
        full_url = f"{meituan_base_url}&poi_id=-100&poi_id_str={poi_value}"
        full_url_2 = build_meituan_coupon_variant_url(
            full_url,
            variant="v5",
            logger_instance=self.logger,
        )
        cashback_activity_link_text = processor_config.get("cashback_activity_link_text", "点击报名该商家「官方返现」活动")
        cashback_activity_link = ""
        try:
            cashback_activity_link = await abuild_meituan_official_cashback_shortlink_url(
                to_user_name,
                poi_value,
                cashback_activity_link_text,
                self.logger,
            )
        except Exception as e:
            self.logger.warning(f"[{account_name}] 生成返现活动入口失败: {e}")
        
                    
        default_title = processor_config.get("default_title", "美团商家")
        
        self.logger.info(f"[{account_name}] 美团小程序链接处理 - poi: {poi_value}")
        
                          
        extra_params_url = None
        token_is_null = False
        if processor_config.get("build_extra_params", False):
            self.logger.info("开始构建链接")
            extra_params_url, token_is_null = build_extra_params_url(poi_value, page_path, self.logger, to_user_name)
            
                    
        click_detail_link = processor_config.get("click_detail_link", "点击下方链接查看详情：")
        merchant_coupon_link = processor_config.get("merchant_coupon_link", "立即查看商家券")
        merchant_coupon_link_2 = processor_config.get("merchant_coupon_link_2", "领取商家券2(可切号，不一定有)")
        copy_to_browser = processor_config.get("copy_to_browser", "或者复制到浏览器打开：")
        token_null_message = processor_config.get("token_null_message", "\n\n💡 提示：当前小程序未包含免配信息，当前链接并无免配内容若要获取免配链接\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=免配链接\">🚚 点击获取免配链接</a>")
        no_link_message_text = processor_config.get("no_link_message_text", "收到美团小程序链接，暂无法获取详情链接~")
        miniprogram_open_prefix = processor_config.get("miniprogram_open_prefix", "领取商家券 (小程序版)")
        merchant_coupon_link_suffix = str(processor_config.get("merchant_coupon_link_suffix", "") or "")
        merchant_coupon_link_2_suffix = str(processor_config.get("merchant_coupon_link_2_suffix", "") or "")
        cashback_activity_link_suffix = str(processor_config.get("cashback_activity_link_suffix", "") or "")
        miniprogram_link_suffix = str(processor_config.get("miniprogram_link_suffix", "") or "")
        extra_params_link_suffix = str(processor_config.get("extra_params_link_suffix", "") or "")
        save_merchant_coupon_link_suffix = str(processor_config.get("save_merchant_coupon_link_suffix", "") or "")
        
                           
        show_merchant_coupon_link = processor_config.get("show_merchant_coupon_link", False)
        show_miniprogram_link = processor_config.get("show_miniprogram_link", False)
        show_token_null_message = processor_config.get("show_token_null_message", False)

        if extra_params_url:
            button_name = processor_config.get("button_name", "大众点评/美团外卖")
        
                 
        miniprogram_link = ""
        if show_miniprogram_link:
            miniprogram_link = generate_miniprogram_link(
                full_url,
                to_user_name,
                {"miniprogram_open_prefix": miniprogram_open_prefix},
            )
        
                
        content_parts = [default_title]
        
        if click_detail_link:
            content_parts.append("")
            content_parts.append(click_detail_link)
        
                         
        if show_merchant_coupon_link:
            merchant_coupon_link_html = render_clickable_link_html(full_url, merchant_coupon_link)
            content_parts.append(
                append_link_suffix(
                    merchant_coupon_link_html,
                    merchant_coupon_link_suffix,
                )
            )
            if full_url_2:
                merchant_coupon_link_2_html = render_clickable_link_html(full_url_2, merchant_coupon_link_2)
                content_parts.append(
                    append_link_suffix(
                        merchant_coupon_link_2_html,
                        merchant_coupon_link_2_suffix,
                    )
                )
            if cashback_activity_link:
                content_parts.append(
                    append_link_suffix(
                        cashback_activity_link,
                        cashback_activity_link_suffix,
                    )
                )
        
                         
        if show_miniprogram_link and miniprogram_link:
            content_parts.append("")
            content_parts.append(
                append_link_suffix(miniprogram_link, miniprogram_link_suffix)
            )

        red_packet_links = processor_config.get("red_packet_links")
        if red_packet_links:
            content_parts.append("")
            content_parts.append(red_packet_links)
        
        content = "\n".join(content_parts)
        
                              
        if extra_params_url:
                         
            content += (
                f"{copy_to_browser}\n"
                f"{append_link_suffix(render_clickable_link_html(extra_params_url, button_name), extra_params_link_suffix).rstrip()}"
            )
                                
            if token_is_null and show_token_null_message:
                content += token_null_message
        else:
                                
            if processor_config.get("show_dianping_links", False):
                dianping_links = processor_config.get("dianping_links", "")
                if dianping_links:
                    content += f"{dianping_links}"
        
                                                                                  
        show_save_merchant_coupon_link = processor_config.get("show_save_merchant_coupon_link", True)
        if show_save_merchant_coupon_link and poi_value:
            try:
                allowance, ad_activity_flag = extract_page_params(page_path)
                encrypted_data = await aencrypt_merchant_coupon_data(
                    poi_value,
                    allowance,
                    ad_activity_flag,
                    "未知商家",
                    self.logger,
                )
                save_link_text = processor_config.get("save_merchant_coupon_link_text", "查看获取链接教程")
                content += (
                    "\n\n"
                    + render_clickable_link_html(
                        f"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=保存商家券 - {encrypted_data}",
                        save_link_text,
                        save_merchant_coupon_link_suffix,
                    )
                )
            except Exception as e:
                self.logger.warning(f"[{account_name}] 生成保存商家券链接失败: {e}")

        content_bytes = len(content.encode("utf-8"))
        if content_bytes <= self.PASSIVE_REPLY_SOFT_LIMIT:
            self.logger.warning(
                "[%s] 小程序链接回复长度诊断: chars=%d bytes=%d concise=False",
                account_name,
                len(content),
                content_bytes,
            )
            return content

        concise_parts = [
            default_title,
            "",
            self._build_plain_link_line(
                full_url,
                merchant_coupon_link,
                merchant_coupon_link_suffix,
            ),
        ]

        concise_content = "\n".join(part for part in concise_parts if part is not None)
        concise_bytes = len(concise_content.encode("utf-8"))
        self.logger.warning(
            "[%s] 小程序链接回复长度诊断: chars=%d bytes=%d concise=True concise_chars=%d concise_bytes=%d",
            account_name,
            len(content),
            content_bytes,
            len(concise_content),
            concise_bytes,
        )
        return concise_content
