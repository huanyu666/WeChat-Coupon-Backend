"""商家券管理处理器"""
import re
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor
from utils.response import TextRspMsg
from utils.merchant_coupon_storage import get_merchant_coupon_storage
from utils.merchant_coupon_utils import adecrypt_merchant_coupon_data
from utils.meituan_utils import (
    build_extra_params_url,
    build_meituan_coupon_url,
    build_meituan_coupon_variant_url,
    generate_miniprogram_link,
    render_clickable_link_html,
)
from utils.merchant_benefits import (
    aquery_benefits_for_wechat,
    format_benefits_for_wechat,
    format_benefits_pending_for_wechat,
)


class MerchantCouponProcessor(BaseTextProcessor):
    """商家券管理处理器"""
    
    def __init__(self, logger):
        """初始化处理器"""
        super().__init__(logger)
        self.storage = get_merchant_coupon_storage()

        self.query_keywords = ["我的商家券", "查看商家券", "商家券列表"]

    def _build_coupon_main_url(
        self,
        *,
        to_user_name: str,
        poi_value: str,
        variant: str = "v8",
    ) -> str:
        return build_meituan_coupon_url(to_user_name, poi_value, self.logger, variant=variant)
    
    def is_trigger(self, text: str) -> bool:
        """检查是否触发商家券管理"""
        text_lower = text.lower().strip()
        
        if any(keyword in text_lower for keyword in self.query_keywords):
            return True
        
        if "保存商家券 - " in text:
            return True
        
        if re.match(r'查看商家券\s*-\s*\d+', text):
            return True
        
        if "删除商家券" in text:
            return True
        
        if re.match(r'删除商家券\s*-\s*\d+', text):
            return True
        
        return False
    
    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        text_lower = text.lower().strip()

        if "保存商家券 - " in text:
            return await self._ahandle_save(msg, text)

        match = re.match(r'查看商家券\s*-\s*(\d+)', text)
        if match:
            index = int(match.group(1))
            return await self._ahandle_view(msg, index)

        match = re.match(r'删除商家券\s*-\s*(\d+)', text)
        if match:
            index = int(match.group(1))
            return self._handle_delete_confirm(msg, index)

        if any(keyword in text_lower for keyword in self.query_keywords):
            return await self._ahandle_query(msg)

        if "删除商家券" in text:
            return self._handle_delete_list(msg)

        return None
    
    async def _ahandle_save(self, msg: Dict[str, Any], text: str) -> Any:
        """异步处理保存商家券请求。"""
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 请求保存商家券")
        
        if "保存商家券 - " not in text:
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 保存失败：无效的请求格式"
            return rsp
        
                    
        self.logger.info(f"[{account_name}] 收到原始文本（前200字符）: {text[:200]}")
        
        encrypted_data = text.split("保存商家券 - ", 1)[1].strip()
        self.logger.info(f"[{account_name}] 提取的Base64字符串（清理前，长度={len(encrypted_data)}）: {encrypted_data}")
                                                                         
                      
        encrypted_data_cleaned = re.sub(r'\s+', '', encrypted_data)
        self.logger.info(f"[{account_name}] Base64字符串（清理后，长度={len(encrypted_data_cleaned)}）: {encrypted_data_cleaned}")
 
        try:
            data = await adecrypt_merchant_coupon_data(encrypted_data_cleaned, self.logger)
            poi_value = data.get("poi_value", "")
            allowance = data.get("allowance", "")
            ad_activity_flag = data.get("ad_activity_flag", "")
            title = data.get("title", "未知商家")
            
            success = self.storage.add(to_user_name, user_id, poi_value, allowance, ad_activity_flag, title)
            
            if success:
                self.logger.info(f"[{account_name}] 用户 {user_id} 保存商家券成功：{title}")
                rsp = TextRspMsg(msg)
                rsp.content = f"✅ 商家券保存成功\n\n店铺：{title}\n\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=我的商家券\">📋 查看我的商家券</a>"
                return rsp
            else:
                self.logger.info(f"[{account_name}] 用户 {user_id} 商家券已存在：{title}")
                rsp = TextRspMsg(msg)
                rsp.content = f"ℹ️ 商家券已存在\n\n店铺：{title}"
                return rsp
                
        except Exception as e:
            error_msg = str(e)
                               
            if error_msg == "商家券信息过期请重新生成":
                self.logger.warning(f"[{account_name}] 商家券信息过期: {error_msg}")
                rsp = TextRspMsg(msg)
                rsp.content = f"❌ {error_msg}"
                return rsp
            
                  
            self.logger.error(f"[{account_name}] 保存商家券失败: {e}", exc_info=True)
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 保存失败"
            return rsp
    
    async def _ahandle_query(self, msg: Dict[str, Any]) -> Any:
        """处理查询商家券列表请求"""
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 查询商家券列表")
        
        coupons = self.storage.get_all(to_user_name, user_id)
        
        if not coupons:
            rsp = TextRspMsg(msg)
            rsp.content = "您暂时没有保存的商家券\n\n在处理美团链接时，可以点击「保存商家券」进行保存"
            return rsp
        
        rsp = TextRspMsg(msg)
        content_parts = [f"📋 您的商家券列表（共{len(coupons)}个）：\n"]
        from config.config import MERCHANT_COUPON_PROMPTS

        prompts = MERCHANT_COUPON_PROMPTS.get(to_user_name, {})
        custom_text = (prompts.get("list_custom_text") or "").strip()
        if custom_text:
            content_parts.append(custom_text)

        from datetime import datetime
        for coupon in coupons:
            title = coupon.get("title", "未知商家")
            poi_value = coupon.get("poi_value", "")
            created_at = coupon.get("created_at", 0)
            if created_at:
                time_str = datetime.fromtimestamp(created_at).strftime("%Y-%m-%d %H:%M")
            else:
                time_str = "未知"
            coupon_url = self._build_coupon_main_url(to_user_name=to_user_name, poi_value=poi_value)
            if coupon_url:
                coupon_link_html = render_clickable_link_html(coupon_url, f"『{title}』")
                content_parts.append(f"\n{coupon_link_html}")
            else:
                content_parts.append(f"\n『{title}』")
            content_parts.append(f"   保存时间：{time_str}")

        content_parts.append("• 点击店铺名称直接跳转领取商家券")
        content_parts.append('• <a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=删除商家券">删除商家券</a>')
        
        rsp.content = "\n".join(content_parts)
        return rsp
    
    async def _ahandle_view(self, msg: Dict[str, Any], index: int) -> Any:
        """处理查看商家券请求"""
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 查看商家券，索引：{index}")
        
        coupon = self.storage.get_by_index(to_user_name, user_id, index)
        
        if not coupon:
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 未找到商家券（索引：{index}）"
            return rsp
        
        poi_value = coupon.get("poi_value", "")
        allowance = coupon.get("allowance", "")
        ad_activity_flag = coupon.get("ad_activity_flag", "")
        title = coupon.get("title", "未知商家")
        
        full_url = self._build_coupon_main_url(to_user_name=to_user_name, poi_value=poi_value)
        full_url_2 = build_meituan_coupon_variant_url(
            full_url,
            variant="v5",
            logger_instance=self.logger,
        )
        if not full_url:
            rsp = TextRspMsg(msg)
            rsp.content = f"【{title}】\n\n该公众号暂未配置美团优惠功能"
            return rsp
        
        from config.config import MINIPROGRAM_CONFIG

        view_config_key = f"{to_user_name}_merchant_coupon_view"
        view_config = MINIPROGRAM_CONFIG.get(view_config_key, {})
        if not view_config:
                      
            view_config = MINIPROGRAM_CONFIG.get(to_user_name, {})
        
        rsp = TextRspMsg(msg)
        
        self.logger.info(f"[{account_name}] 商家券处理 - 标题: {title}, poi: {poi_value}")
        
        page_path_parts = []
        if allowance:
            page_path_parts.append(f"allowance_alliance_scenes={allowance}")
        if ad_activity_flag:
            page_path_parts.append(f"ad_activity_flag={ad_activity_flag}")
        page_path = "?" + "&".join(page_path_parts) if page_path_parts else ""
        
        extra_params_url = None
        token_is_null = False
        if view_config.get("build_extra_params", False):
            self.logger.info("开始构建链接")
            extra_params_url, token_is_null = build_extra_params_url(poi_value, page_path, self.logger, to_user_name)
        
        click_detail_link = view_config.get("click_detail_link", "点击下方链接查看详情：")
        merchant_coupon_link = view_config.get("merchant_coupon_link", "立即查看商家券,打开后置顶第一个店铺就是你选择的店铺，使用商家卷点外卖更优惠，然后在去下面美团津贴免单跳转美团APP")
        merchant_coupon_link_2 = view_config.get("merchant_coupon_link_2", "领取商家券2(可切号，不一定有)")
        merchant_coupon_link_2_suffix = str(view_config.get("merchant_coupon_link_2_suffix", "") or "")
        copy_to_browser = view_config.get("copy_to_browser", "或者复制到浏览器打开：")
        token_null_message = view_config.get("token_null_message", "\n\n💡 提示：当前小程序未包含免配信息，当前链接并无免配内容若要获取免配链接\n<a href=\"weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=免配链接\">🚚 点击获取免配链接</a>")
        
        show_merchant_coupon_link = view_config.get("show_merchant_coupon_link", False)
        show_miniprogram_link = view_config.get("show_miniprogram_link", False)
        show_token_null_message = view_config.get("show_token_null_message", False)
        
        miniprogram_link = ""
        if show_miniprogram_link:
            miniprogram_link = generate_miniprogram_link(full_url, to_user_name)
        
        content_parts = [f"【{title}】"]
        
        if click_detail_link:
            content_parts.append(click_detail_link)
        
        if show_merchant_coupon_link:
            merchant_coupon_link_html = render_clickable_link_html(full_url, merchant_coupon_link)
            content_parts.append(merchant_coupon_link_html)
            if full_url_2:
                merchant_coupon_link_2_html = render_clickable_link_html(full_url_2, merchant_coupon_link_2)
                content_parts.append(f"{merchant_coupon_link_2_html}{merchant_coupon_link_2_suffix}")
            benefits = await aquery_benefits_for_wechat(
                poi_id_str=poi_value,
                merchant_name="" if title == "未知商家" else title,
                account_id=to_user_name,
                source="wechat_saved_coupon",
            )
            benefit_lines = format_benefits_for_wechat(benefits or {})
            if benefit_lines:
                content_parts.extend(benefit_lines)
            else:
                content_parts.extend(format_benefits_pending_for_wechat())
        
        if show_miniprogram_link and miniprogram_link:
            content_parts.append("")
            content_parts.append(miniprogram_link)
        
        red_packet_links = view_config.get("red_packet_links")
        if red_packet_links:
            content_parts.append("")
            content_parts.append(red_packet_links)
        
        rsp.content = "\n".join(content_parts)
        
        if extra_params_url:
            button_name = view_config.get("button_name", "大众点评/美团外卖")
            rsp.content += f"{copy_to_browser}\n<a href=\"{extra_params_url}\">{button_name}</a>"
            if token_is_null and show_token_null_message:
                rsp.content += token_null_message
        else:
            if view_config.get("show_dianping_links", False):
                dianping_links = view_config.get("dianping_links", "")
                if dianping_links:
                    rsp.content += f"{dianping_links}"
        
        return rsp
    
    def _handle_delete_list(self, msg: Dict[str, Any]) -> Any:
        """处理删除商家券列表请求"""
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 查看删除商家券列表")
        
        coupons = self.storage.get_all(to_user_name, user_id)
        
        if not coupons:
            rsp = TextRspMsg(msg)
            rsp.content = "您暂时没有保存的商家券"
            return rsp
        
        rsp = TextRspMsg(msg)
        content_parts = [f"🗑️ 请选择要删除的商家券（共{len(coupons)}个）：\n"]
        
        for idx, coupon in enumerate(coupons, 1):
            title = coupon.get("title", "未知商家")
            content_parts.append(f'\n<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=删除商家券 - {idx}">『{title}』</a>')
        
        rsp.content = "\n".join(content_parts)
        return rsp
    
    def _handle_delete_confirm(self, msg: Dict[str, Any], index: int) -> Any:
        """处理删除商家券确认请求"""
        user_id = msg.get("FromUserName", "")
        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 删除商家券，索引：{index}")
        
        coupon = self.storage.get_by_index(to_user_name, user_id, index)
        
        if not coupon:
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 未找到商家券（索引：{index}）"
            return rsp
        
        title = coupon.get("title", "未知商家")
        
        success = self.storage.delete_by_index(to_user_name, user_id, index)
        
        if success:
            self.logger.info(f"[{account_name}] 用户 {user_id} 删除商家券成功：{title}")
            rsp = TextRspMsg(msg)
            rsp.content = f"✅ 商家券删除成功\n\n已删除：{title}"
            return rsp
        else:
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 删除失败"
            return rsp
