"""
美团小程序处理器
"""
from typing import Dict, Any, Optional
from .base_processor import BaseMiniprogramProcessor
from utils.meituan_utils import (
    generate_miniprogram_link,
    build_extra_params_url,
    build_meituan_coupon_variant_url,
    render_clickable_link_html,
    append_link_suffix,
    abuild_meituan_official_cashback_shortlink_url,
)
from utils.merchant_coupon_utils import aencrypt_merchant_coupon_data, extract_page_params


class MeituanProcessor(BaseMiniprogramProcessor):
    """美团小程序处理器 - 根据 ToUserName 使用不同公众号的配置和处理逻辑"""

    async def aprocess(self, msg: Dict[str, Any]) -> Optional[str]:
        return await self._aprocess_impl(msg)

    async def _aprocess_impl(self, msg: Dict[str, Any]) -> Optional[str]:
        from utils.response import TextRspMsg
        from config.config import ACCOUNT_SPECIFIC_CONFIGS
        
                 
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        account_config = ACCOUNT_SPECIFIC_CONFIGS.get(to_user_name, {})
        
                       
        meituan_base_url = account_config.get("meituan_base_url", "")
        
                                  
        from config.config import MINIPROGRAM_CONFIG
        miniprogram_config = MINIPROGRAM_CONFIG.get(to_user_name, {})
        
        if not meituan_base_url:
            self.logger.warning(f"[{account_name}] 未配置美团链接")
            rsp = TextRspMsg(msg)
            no_config_message = miniprogram_config.get("no_config_message", "该公众号暂未配置美团优惠功能")
            rsp.content = no_config_message
            return rsp
        
        page_path = msg.get("PagePath", "")
        title = msg.get("Title", "")
        
                                  
        poi_id_str = self._extract_poi_id_str(page_path)

                  
        rsp = TextRspMsg(msg)

        if poi_id_str: 
            full_url = f"{meituan_base_url}&poi_id=-100{poi_id_str}"
            full_url_2 = build_meituan_coupon_variant_url(
                full_url,
                variant="v5",
                logger_instance=self.logger,
            )
            self.logger.info(f"[{account_name}] 美团小程序处理 - 标题: {title}, poi: {poi_id_str}")
            poi_value = poi_id_str.split('poi_id_str=', 1)[1] if 'poi_id_str=' in poi_id_str else ''
            cashback_activity_link_text = miniprogram_config.get("cashback_activity_link_text", "点击报名该商家「官方返现」活动")
            cashback_activity_link = ""
            if poi_value:
                try:
                    cashback_activity_link = await abuild_meituan_official_cashback_shortlink_url(
                        to_user_name,
                        poi_value,
                        cashback_activity_link_text,
                        self.logger,
                    )
                except Exception as e:
                    self.logger.warning(f"[{account_name}] 生成返现活动入口失败: {e}")
            
                              
            extra_params_url = None
            token_is_null = False
            if miniprogram_config.get("build_extra_params", False):
                self.logger.info("开始构建链接")
                if poi_value:
                    extra_params_url, token_is_null = build_extra_params_url(poi_value, page_path, self.logger, to_user_name)
            
                        
            click_detail_link = miniprogram_config.get("click_detail_link", "点击下方链接查看详情：")
            merchant_coupon_link = miniprogram_config.get("merchant_coupon_link", "立即查看商家券,打开后置顶第一个店铺就是你选择的店铺，使用商家卷点外卖更优惠，然后在去下面美团津贴免单跳转美团APP")
            merchant_coupon_link_2 = miniprogram_config.get("merchant_coupon_link_2", "领取商家券2(可切号，不一定有)")
            copy_to_browser = miniprogram_config.get("copy_to_browser", "或者复制到浏览器打开：")
            token_null_message = miniprogram_config.get("token_null_message", "\n\n💡 提示：当前小程序未包含免配信息，当前链接并无免配内容若要获取免配链接\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=免配链接\">🚚 点击获取免配链接</a>")
            no_link_message = miniprogram_config.get("no_link_message", "收到美团小程序分享，暂无法获取详情链接~")
            merchant_coupon_link_suffix = str(miniprogram_config.get("merchant_coupon_link_suffix", "") or "")
            merchant_coupon_link_2_suffix = str(miniprogram_config.get("merchant_coupon_link_2_suffix", "") or "")
            cashback_activity_link_suffix = str(miniprogram_config.get("cashback_activity_link_suffix", "") or "")
            miniprogram_link_suffix = str(miniprogram_config.get("miniprogram_link_suffix", "") or "")
            extra_params_link_suffix = str(miniprogram_config.get("extra_params_link_suffix", "") or "")
            save_merchant_coupon_link_suffix = str(miniprogram_config.get("save_merchant_coupon_link_suffix", "") or "")
            
                               
            show_merchant_coupon_link = miniprogram_config.get("show_merchant_coupon_link", False)
            show_miniprogram_link = miniprogram_config.get("show_miniprogram_link", False)
            show_token_null_message = miniprogram_config.get("show_token_null_message", False)

                     
            miniprogram_link = ""
            if show_miniprogram_link:
                miniprogram_link = generate_miniprogram_link(full_url, to_user_name)
            
                    
            content_parts = [f"【{title}】"]
            
            if click_detail_link:
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
                content_parts.append(
                    append_link_suffix(
                        miniprogram_link,
                        miniprogram_link_suffix,
                    )
                )
            
            red_packet_links = miniprogram_config.get("red_packet_links")
            if red_packet_links:
                content_parts.append(red_packet_links)
            
            rsp.content = "\n".join(content_parts)
            
                                  
            if extra_params_url:
                             
                rsp.content += (
                    f"{copy_to_browser}\n"
                    f"{append_link_suffix(render_clickable_link_html(extra_params_url, miniprogram_config.get('button_name', '大众点评/美团外卖')), extra_params_link_suffix).rstrip()}"
                )
                                    
                if token_is_null and show_token_null_message:
                    rsp.content += token_null_message
            else:
                                    
                if miniprogram_config.get("show_dianping_links", False):
                    dianping_links = miniprogram_config.get("dianping_links", "")
                    if dianping_links:
                        rsp.content += f"{dianping_links}"
            
                                   
            show_save_merchant_coupon_link = miniprogram_config.get("show_save_merchant_coupon_link", True)
            if show_save_merchant_coupon_link:
                if poi_value:
                    try:
                        allowance, ad_activity_flag = extract_page_params(page_path)
                        encrypted_data = await aencrypt_merchant_coupon_data(
                            poi_value,
                            allowance,
                            ad_activity_flag,
                            title,
                            self.logger,
                        )
                        save_link_text = miniprogram_config.get("save_merchant_coupon_link_text", "查看获取链接教程")
                        rsp.content += (
                            "\n\n"
                            + render_clickable_link_html(
                                f"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=保存商家券 - {encrypted_data}",
                                save_link_text,
                                save_merchant_coupon_link_suffix,
                            )
                        )
                    except Exception as e:
                        self.logger.warning(f"[{account_name}] 生成保存商家券链接失败: {e}")
        else:
            self.logger.warning(f"[{account_name}] 未能从PagePath中提取poi_id_str: {page_path}")
                     
            no_link_message = miniprogram_config.get("no_link_message", "收到美团小程序分享，暂无法获取详情链接~")
            rsp.content = f"【{title}】\n\n{no_link_message}"

        return rsp
    
    def _extract_poi_id_str(self, page_path: str) -> Optional[str]:
                            
        poi_id_str_pos = page_path.find('poi_id_str=')
        if poi_id_str_pos == -1:
            self.logger.warning(f"未找到poi_id_str参数: {page_path}")
            return None
        
                                    
        end_pos = page_path.find('&', poi_id_str_pos)
        if end_pos == -1:
                                               
            end_pos = len(page_path)
            extracted = page_path[poi_id_str_pos:end_pos]
        else:
                                  
            extracted = page_path[poi_id_str_pos:end_pos]
        
                 
        extracted = extracted.replace('\r', '').replace('\n', '')
        
                 
        if not extracted.startswith('&'):
            extracted = '&' + extracted
        
              
        self.logger.info(f"截取的参数字符串: {extracted}")
        
        return extracted
    
