"""
美团小程序处理器 - 第二种格式
"""
from typing import Dict, Any, Optional
from .base_processor import BaseMiniprogramProcessor
from utils.meituan_utils import generate_miniprogram_link


class Meituan2Processor(BaseMiniprogramProcessor):
    """美团小程序处理器 - 处理第二种格式 - 根据 ToUserName 使用不同配置"""

    async def aprocess(self, msg: Dict[str, Any]) -> Optional[str]:
        """
        处理美团小程序消息（格式2）
        
        从PagePath中提取poi_id_str参数，拼接到美团链接后返回
        根据 ToUserName 使用不同公众号的配置和处理逻辑

        Args:
            msg: 消息字典（包含 ToUserName 和 _account_config）

        Returns:
            WxRspMsg响应对象
        """
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
            full_url = f"{meituan_base_url}&poi_id=-100&poi_id_str={poi_id_str}"
            self.logger.info(f"[{account_name}] 美团小程序处理(格式2) - 标题: {title}, poi: {poi_id_str}")
            
                        
            click_detail_link = miniprogram_config.get("click_detail_link", "点击下方链接查看详情：")
            merchant_coupon_link = miniprogram_config.get("merchant_coupon_link", "立即查看商家券")
            no_link_message = miniprogram_config.get("no_link_message", "收到美团小程序分享，暂无法获取详情链接~")
            
                               
            show_merchant_coupon_link = miniprogram_config.get("show_merchant_coupon_link", False)
            show_miniprogram_link = miniprogram_config.get("show_miniprogram_link", False)
            
                     
            miniprogram_link = ""
            if show_miniprogram_link:
                miniprogram_link = generate_miniprogram_link(full_url, to_user_name)
            
                                        
            content_parts = [f"【{title}】"]
            
            if click_detail_link:
                content_parts.append(click_detail_link)
            
                             
            if show_merchant_coupon_link:
                content_parts.append(f'<a href="{full_url}">{merchant_coupon_link}</a>')
            
                             
            if show_miniprogram_link and miniprogram_link:
                content_parts.append("")
                content_parts.append(miniprogram_link)
            
            rsp.content = "\n".join(content_parts)
        else:
            self.logger.warning(f"[{account_name}] 未能从PagePath中提取poi_id_str: {page_path}")
                     
            no_link_message = miniprogram_config.get("no_link_message", "收到美团小程序分享，暂无法获取详情链接~")
            rsp.content = f"【{title}】\n\n{no_link_message}"

        return rsp
    
    def _extract_poi_id_str(self, page_path: str) -> Optional[str]:
        """
        从PagePath中提取poi_id_str参数
        
        Args:
            page_path: 小程序页面路径
            
        Returns:
            poi_id_str值，如果未找到则返回None
        """
                            
        poi_id_str_pos = page_path.find('poi_id_str=')
        if poi_id_str_pos == -1:
            self.logger.warning(f"未找到poi_id_str参数: {page_path}")
            return None
        
                                    
        end_pos = page_path.find('&', poi_id_str_pos)
        if end_pos == -1:
                                               
            end_pos = len(page_path)
        
                                       
        extracted = page_path[poi_id_str_pos:end_pos]
        self.logger.info(f"提取到参数: {extracted}")
        
                                         
        return extracted[len('poi_id_str='):]
