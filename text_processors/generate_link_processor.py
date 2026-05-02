"""
生成链接处理器
"""
import asyncio
from typing import Dict, Any, Optional
import threading
from .stateful_processor import StatefulTextProcessor
from utils.response import TextRspMsg
from utils.account_config import resolve_zmkey
from link_handlers.api_client import LinkConversionAPI
from utils.verification_code import (
    get_link_verification_manager,
    get_verification_manager,
    migrate_code_between_pools,
)


class GenerateLinkProcessor(StatefulTextProcessor):
    """
    生成链接功能处理器（自定义链接生成）
    
    """
    
          
    STATE_WAITING_ACTIVATION_CODE = "waiting_activation_code"
    STATE_WAITING_CUSTOM_APPID = "waiting_custom_appid"                     
    STATE_WAITING_CUSTOM_PATH = "waiting_custom_path"                    
    STATE_WAITING_MP_CHOICE = "waiting_mp_choice"                   
    
    def __init__(self, logger):
        """
        初始化处理器
        
        Args:
            logger: 日志记录器
        """
                     
        super().__init__(logger, state_timeout=600)
        
                  
        self.activation_manager = get_link_verification_manager()
                
        self.unbound_activation_manager = get_verification_manager()
        
        self._reload_configs()
        
        self.logger.info("GenerateLinkProcessor 初始化完成")
    
    def _reload_configs(self):
        try:
            from config.config import PROMPTS_CONFIG
            self.prompts_config = PROMPTS_CONFIG.get('generate_link_flow', {}) 
            trigger_keyword = self.prompts_config.get('trigger_keyword', '生成链接')
            self.trigger_keywords = [trigger_keyword]
            
            self.logger.info("GenerateLinkProcessor 配置重新加载成功")
        except Exception as e:
            self.logger.error(f"GenerateLinkProcessor 配置重新加载失败: {e}")
    
    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        """
        异步处理触发关键词。

        该入口仅做状态流转和返回提示，逻辑与同步 handle_trigger() 保持一致。
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        self.logger.info(f"[{account_name}] 用户 {user_id} 触发生成链接功能")

        valid_code_info = self.activation_manager.has_valid_code(user_id)

        if valid_code_info:
            self.logger.info(f"[{account_name}] 用户 {user_id} 有有效的激活码")
            self.set_user_state(user_id, self.STATE_WAITING_CUSTOM_APPID, {
                "verified": True,
                "activation_code": valid_code_info["code"]
            })

            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('request_custom_appid', '请输入小程序appid：')
            return rsp

        self.set_user_state(user_id, self.STATE_WAITING_ACTIVATION_CODE)
        rsp = TextRspMsg(msg)
        rsp.content = "请输入激活码"
        return rsp
    
    async def ahandle_state(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Optional[Any]:
        import time
        from .stateful_processor import USER_STATES

        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        current_state = state.get("state")

        cancel_keywords = self.prompts_config.get('cancel_keywords', [])
        default_exit_keywords = ["取消", "退出", "返回", "quit", "cancel", "back"]
        all_cancel_keywords = list(set(cancel_keywords + default_exit_keywords))

        if text.strip() in all_cancel_keywords:
            self.clear_user_state(user_id, "用户取消操作")
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('cancel_message', '已取消操作')
            return rsp

        if user_id in USER_STATES:
            USER_STATES[user_id]["last_update_time"] = time.time()

        if current_state == self.STATE_WAITING_ACTIVATION_CODE:
            return await self._ahandle_activation_code(msg, text, state)
        if current_state == self.STATE_WAITING_CUSTOM_APPID:
            return await self._ahandle_custom_appid(msg, text, state)
        if current_state == self.STATE_WAITING_CUSTOM_PATH:
            return await self._ahandle_custom_path(msg, text, state)
        if current_state == self.STATE_WAITING_MP_CHOICE:
            return await self._ahandle_mp_choice(msg, text, state)

        self.logger.warning(f"[{account_name}] 未知状态: {current_state}")
        self.clear_user_state(user_id, "未知状态")
        return None
    
    async def _ahandle_activation_code(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        """
        处理激活码输入
        
        Args:
            msg: 消息字典
            text: 用户输入的激活码
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        code = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 输入激活码: {code}")
        
        ok, msg_text = migrate_code_between_pools(
            code,
            user_id,
            self.unbound_activation_manager,
            self.activation_manager,
            logger=self.logger,
        )
        if not ok:
            self.logger.warning(f"[{account_name}] 激活码验证失败: {msg_text}")
            self.clear_user_state(user_id, "激活码验证失败")
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ 激活码验证失败: {msg_text}\n\n请重新获取激活码后再试。"
            return rsp
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 激活码验证成功并已绑定")
        
                               
        self.set_user_state(user_id, self.STATE_WAITING_CUSTOM_APPID, {
            "verified": True,
            "activation_code": code
        })
        
        rsp = TextRspMsg(msg)
        rsp.content = self.prompts_config.get('request_custom_appid', '请输入小程序appid：')
        return rsp
    
    async def _ahandle_custom_appid(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        """
        处理自定义链接的appid输入
        
        Args:
            msg: 消息字典
            text: 用户输入的appid
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        appid = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 输入appid: {appid}")
        
        if not appid:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('error_empty_appid', 'appid不能为空，请重新输入：')
            return rsp
        
                                
        state["custom_appid"] = appid
        self.set_user_state(user_id, self.STATE_WAITING_CUSTOM_PATH, state)
        
        rsp = TextRspMsg(msg)
        rsp.content = self.prompts_config.get('request_custom_path', '请输入小程序路径（path）：')
        return rsp
    
    async def _ahandle_custom_path(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        """
        处理自定义链接的path输入，询问是否需要转换为MP链接
        
        Args:
            msg: 消息字典
            text: 用户输入的path
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
        path = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 输入path: {path}")
        
        if not path:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('error_empty_path', 'path不能为空，请重新输入：')
            return rsp
        
                                 
        state["custom_path"] = path
        self.set_user_state(user_id, self.STATE_WAITING_MP_CHOICE, state)
        
        rsp = TextRspMsg(msg)
        rsp.content = self.prompts_config.get('request_mp_choice', 
            '是否需要转换为MP链接？\n<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=1">1. 原始链接</a>\n<a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=2">2. MP链接（mp://格式）</a>\n\n请输入 1 或 2')
        return rsp
    
    async def _ahandle_mp_choice(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        account_config = msg.get("_account_config", {})

        choice = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 选择链接类型: {choice}")
        if choice not in ["1", "2"]:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('error_invalid_mp_choice', '输入无效，请输入 1 或 2')
            return rsp

        appid = state.get("custom_appid", "")
        path = state.get("custom_path", "")
        if not appid or not path:
            self.logger.error(f"[{account_name}] 状态中缺少appid或path")
            self.clear_user_state(user_id, "状态错误")
            rsp = TextRspMsg(msg)
            rsp.content = "处理错误，请重新开始"
            return rsp

        activation_code = state.get("activation_code", "")
        activation_code_info = None
        remaining_count = 0
        if activation_code:
            is_valid, error_msg = self.activation_manager.verify_code(activation_code, user_id=user_id, consume=False)
            if is_valid:
                with self.activation_manager._lock:
                    code_upper = activation_code.upper().strip()
                    code_index = self.activation_manager._code_index
                    if code_upper in code_index:
                        index_entry = code_index[code_upper]
                        matched_code = index_entry["code_info"]
                        max_uses = matched_code.get("max_uses", 1)
                        used_count = matched_code.get("used_count", 0)
                        remaining_count = max_uses - used_count
                        activation_code_info = {
                            "code": activation_code,
                            "code_upper": code_upper,
                            "matched_code": matched_code,
                            "matched_user_id": index_entry["user_id"]
                        }
                        self.logger.info(f"[{account_name}] 激活码剩余次数: {remaining_count}")
                    else:
                        self.logger.warning(f"[{account_name}] 无法获取激活码信息")
            else:
                self.logger.warning(f"[{account_name}] 激活码验证失败: {error_msg}")

        if activation_code_info and remaining_count <= 0:
            self.logger.warning(f"[{account_name}] 激活码次数已用完")
            self.clear_user_state(user_id, "激活码次数不足")
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 激活码次数已用完，无法生成自定义链接"
            return rsp

        zmkey = resolve_zmkey(msg, account_config)
        if not zmkey:
            self.logger.error(f"[{account_name}] 未配置zmkey")
            self.clear_user_state(user_id, "配置错误")
            rsp = TextRspMsg(msg)
            rsp.content = "配置错误，请联系管理员"
            return rsp

        api_client = LinkConversionAPI(zmkey, self.logger)
        custom_link = await api_client.agenerate_custom_link(appid, path)
        if not custom_link:
            self.logger.error(f"[{account_name}] 生成自定义链接失败")
            self.set_user_state(user_id, self.STATE_WAITING_CUSTOM_APPID, {
                "verified": True,
                "activation_code": activation_code
            })
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 生成自定义链接失败，请检查appid和path是否正确\n\n请重新输入小程序appid："
            return rsp

        if choice == "2":
            custom_link = self._convert_to_mp_link(custom_link)
            if not custom_link:
                self.logger.error(f"[{account_name}] 转换为MP链接失败")
                self.set_user_state(user_id, self.STATE_WAITING_CUSTOM_APPID, {
                    "verified": True,
                    "activation_code": activation_code
                })
                rsp = TextRspMsg(msg)
                rsp.content = "❌ 转换为MP链接失败\n\n请重新输入小程序appid："
                return rsp

        if activation_code_info:
            def save_activation_code_async():
                try:
                    with self.activation_manager._lock:
                        code_upper = activation_code_info["code_upper"]
                        code_index = self.activation_manager._code_index
                        if code_upper not in code_index:
                            self.logger.warning(f"[{account_name}] 激活码已不存在，跳过保存")
                            return
                        index_entry = code_index[code_upper]
                        matched_code = index_entry["code_info"]
                        matched_user_id = index_entry["user_id"]
                        matched_code["used_count"] = matched_code.get("used_count", 0) + 1
                        max_uses = matched_code.get("max_uses", 1)
                        if matched_code["used_count"] >= max_uses:
                            matched_code["used"] = True
                            code_upper = matched_code["code"].upper()
                            if matched_user_id in self.activation_manager._codes:
                                if matched_code in self.activation_manager._codes[matched_user_id]:
                                    self.activation_manager._codes[matched_user_id].remove(matched_code)
                                if not self.activation_manager._codes[matched_user_id]:
                                    del self.activation_manager._codes[matched_user_id]
                            if code_upper in code_index:
                                del code_index[code_upper]
                        self.activation_manager._save_unlocked()
                    self.logger.info(f"[{account_name}] 激活码 {activation_code} 已异步保存 1 次消耗（生成自定义链接）")
                except Exception as e:
                    self.logger.error(f"[{account_name}] 异步保存激活码消耗失败: {e}", exc_info=True)

            threading.Thread(target=save_activation_code_async, daemon=True).start()
            self.logger.info(f"[{account_name}] 激活码 {activation_code} 已消耗 1 次使用次数（生成自定义链接），正在异步保存")

        self.logger.info(f"[{account_name}] 用户 {user_id} 生成自定义链接成功")
        self.set_user_state(user_id, self.STATE_WAITING_CUSTOM_APPID, {
            "verified": True,
            "activation_code": activation_code
        })
        rsp = TextRspMsg(msg)
        rsp.content = f"{custom_link}"
        return rsp
    
    def _convert_to_mp_link(self, link: str) -> Optional[str]:
        """
        将小程序链接转换为 MP 链接格式
        
        """
        try:
            last_slash_pos = link.rfind('/')
            if last_slash_pos == -1:
                self.logger.warning(f"无法找到路径分隔符: {link}")
                return None
            path_part = link[last_slash_pos + 1:]
            
            if not path_part:
                self.logger.warning(f"路径部分为空: {link}")
                return None
            mp_link = f"mp://{path_part}"
            return mp_link
        except Exception as e:
            self.logger.error(f"转换MP链接失败: {e}")
            return None
