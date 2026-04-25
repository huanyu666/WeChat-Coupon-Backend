"""
获取链接处理器
"""
import asyncio
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from .stateful_processor import StatefulTextProcessor
from utils.response import TextRspMsg
from link_handlers.link_recognizer import LinkRecognizer
from link_handlers.api_client import LinkConversionAPI
from utils.verification_code import (
    get_link_verification_manager,
    get_verification_manager,
    migrate_code_between_pools,
)
from utils.p_value_storage import get_p_value_storage


class GetLinkProcessor(StatefulTextProcessor):
    """
    获取链接功能处理器
    
    """
    
          
    STATE_WAITING_ACTIVATION_CODE = "waiting_activation_code"
    STATE_WAITING_P_VALUE = "waiting_p_value"
    STATE_WAITING_LINK = "waiting_link"
    STATE_WAITING_LINK_TYPE = "waiting_link_type"              
    
    def __init__(self, logger):
        """
        初始化处理器
        
        Args:
            logger: 日志记录器
        """
                           
        super().__init__(logger, state_timeout=600)
        
                         
        self.activation_manager = get_link_verification_manager()
                      
        self.unbound_activation_manager = get_verification_manager()
        
                   
        self.p_value_storage = get_p_value_storage()
        
        self._reload_configs()
        
        self.logger.info("GetLinkProcessor 初始化完成")
    
    def _reload_configs(self):
        try:
            from config.config import PROMPTS_CONFIG, LINK_CONFIG
            self.prompts_config = PROMPTS_CONFIG.get('get_link_flow', {}) 
            trigger_keyword = self.prompts_config.get('trigger_keyword', '获取链接')
            self.trigger_keywords = [trigger_keyword]
            self.link_recognizer = LinkRecognizer(LINK_CONFIG)
            
            self.logger.info("GetLinkProcessor 配置重新加载成功")
        except Exception as e:
            self.logger.error(f"GetLinkProcessor 配置重新加载失败: {e}")
    
    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        """
        异步处理触发关键词。

        该入口仅做状态初始化和提示返回，逻辑与同步 handle_trigger() 保持一致。
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        self.logger.info(f"[{account_name}] 用户 {user_id} 触发获取链接功能")

        valid_code_info = self.activation_manager.has_valid_code(user_id)

        if valid_code_info:
            self.logger.info(f"[{account_name}] 用户 {user_id} 有有效的激活码")

            current_p_value = self.p_value_storage.get_current(user_id)

            if current_p_value:
                current_id = self.p_value_storage.get_current_id(user_id)
                all_p_values = self.p_value_storage.get_all(user_id)
                p_value_info = all_p_values.get(current_id, {})
                alias = p_value_info.get("alias", "未设置")

                self.logger.info(f"[{account_name}] 用户 {user_id} 使用当前P值（ID: {current_id}, 别名: {alias}）")
                self.set_user_state(user_id, self.STATE_WAITING_LINK, {
                    "p_value": current_p_value,
                    "verified": True,
                    "activation_code": valid_code_info["code"]
                })

                rsp = TextRspMsg(msg)
                rsp.content = f"✅ 使用当前P值（{alias}）\n\n{self.prompts_config['confirm_p_value']}"
                return rsp

            self.set_user_state(user_id, self.STATE_WAITING_P_VALUE, {
                "verified": True,
                "activation_code": valid_code_info["code"]
            })

            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config['request_p_value']
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
        if current_state == self.STATE_WAITING_P_VALUE:
            return await self._ahandle_p_value(msg, text, state)
        if current_state == self.STATE_WAITING_LINK_TYPE:
            return await self._ahandle_link_type_choice(msg, text, state)
        if current_state == self.STATE_WAITING_LINK:
            return await self._ahandle_link(msg, text, state)

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
        
                        
        current_p_value = self.p_value_storage.get_current(user_id)
        
        if current_p_value:
                             
            current_id = self.p_value_storage.get_current_id(user_id)
            all_p_values = self.p_value_storage.get_all(user_id)
            p_value_info = all_p_values.get(current_id, {})
            alias = p_value_info.get("alias", "未设置")
            
            self.logger.info(f"[{account_name}] 用户 {user_id} 使用当前P值（ID: {current_id}, 别名: {alias}）")
            self.set_user_state(user_id, self.STATE_WAITING_LINK, {
                "p_value": current_p_value, 
                "verified": True,
                "activation_code": code
            })
            
            rsp = TextRspMsg(msg)
            rsp.content = f"✅ 使用当前P值（{alias}）\n\n{self.prompts_config['confirm_p_value']}"
            return rsp
        else:
                                
            self.set_user_state(user_id, self.STATE_WAITING_P_VALUE, {
                "verified": True,
                "activation_code": code
            })
            
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config['request_p_value']
            return rsp
    
    async def _ahandle_p_value(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        """
        处理P值输入
        
        当用户没有P值时，要求输入P值并保存为默认P值（别名：default）
        
        Args:
            msg: 消息字典
            text: 用户输入的P值
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        
              
        p_value = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 输入P值: {p_value}")
        
                    
        existing_id = self.p_value_storage.find_by_p_value(user_id, p_value)
        if existing_id:
                           
            self.logger.info(f"[{account_name}] 用户 {user_id} 输入的P值已存在，使用现有P值")
            p_value_id = existing_id
        else:
                               
            default_id = self.p_value_storage.find_by_alias(user_id, "default")
            if default_id:
                                     
                self.p_value_storage.update(user_id, default_id, p_value=p_value, alias="default")
                p_value_id = default_id
                self.logger.info(f"[{account_name}] 用户 {user_id} 更新默认P值")
            else:
                                     
                p_value_id = self.p_value_storage.add(user_id, p_value, alias="default")
                self.logger.info(f"[{account_name}] 用户 {user_id} 添加默认P值，ID: {p_value_id}")
        
                    
        self.p_value_storage.set_current(user_id, p_value_id)
        
                           
        activation_code = state.get("activation_code", "")
        self.set_user_state(user_id, self.STATE_WAITING_LINK, {
            "p_value": p_value,
            "activation_code": activation_code
        })
        
                
        rsp = TextRspMsg(msg)
        rsp.content = f"✅ P值已保存（别名：default）\n\n{self.prompts_config['confirm_p_value']}"
        
        return rsp
    
    async def _ahandle_link_type_choice(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")

        choice = text.strip()
        self.logger.info(f"[{account_name}] 用户 {user_id} 选择链接类型: {choice}")

        if choice not in ["1", "2"]:
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('error_invalid_link_type', '输入无效，请输入 1 或 2')
            return rsp

        link_infos = state.get("link_infos", [])
        if not link_infos:
            self.logger.error(f"[{account_name}] 状态中缺少链接信息")
            self.clear_user_state(user_id, "状态错误")
            rsp = TextRspMsg(msg)
            rsp.content = "处理错误，请重新开始"
            return rsp

        state["link_type_choice"] = "original" if choice == "1" else "mp"
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)

        original_text = state.get("original_text", "")
        if not original_text:
            self.logger.error(f"[{account_name}] 状态中缺少原始文本")
            self.clear_user_state(user_id, "状态错误")
            rsp = TextRspMsg(msg)
            rsp.content = "处理错误，请重新开始"
            return rsp

        return await self._ahandle_link(msg, original_text, state)
    
    async def _ahandle_link(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        """
        处理链接输入（支持单链接和多链接，保留原始格式）
        
        Args:
            msg: 消息字典
            text: 用户输入的链接（可以是多个）
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        account_config = msg.get("_account_config", {})
        
                 
        p_value = state.get("p_value", "")
        
        self.logger.info(f"[{account_name}] 用户 {user_id} 发送链接: {text}")
        
                                 
        original_text = state.get("original_text", "")
        if original_text and original_text != text:
                                       
            self.logger.info(f"[{account_name}] 检测到新输入，清除之前的链接信息")
            state.pop("link_infos", None)
            state.pop("link_type_choice", None)
        
                                             
        link_infos = state.get("link_infos")
        
        if not link_infos:
                              
                              
            link_infos = self.link_recognizer.recognize_with_positions(text)
            
                           
            if not link_infos:
                link_infos_old = self.link_recognizer.recognize_multiple(text)
                if not link_infos_old:
                    link_info = self.link_recognizer.recognize(text)
                    if link_info:
                        link_infos_old = [link_info]
                
                if link_infos_old:
                                    
                    link_infos = []
                    for link_info in link_infos_old:
                                      
                        content = link_info.get('content', '')
                        start_pos = text.find(content)
                        if start_pos >= 0:
                            end_pos = start_pos + len(content)
                            link_infos.append({
                                "type": link_info["type"],
                                "content": content,
                                "start": start_pos,
                                "end": end_pos,
                                "original": content,
                                "matched_pattern": link_info.get("matched_pattern", "")
                            })
        
        if not link_infos:
                                   
            self.logger.warning(f"[{account_name}] 链接格式不正确: {text}")
            rsp = TextRspMsg(msg)
            error_msg = self.prompts_config['error_invalid_link']
            continue_prompt = self.prompts_config.get('continue_prompt', '\n\n💡 可以继续输入链接进行处理，或输入"退出"结束')
            rsp.content = error_msg + continue_prompt
                                  
            self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
            return rsp
        
                      
        need_ask_link_type = False
        meituan_link_types = ["meituan_miniprogram", "meituan_general", "mp_protocol"]
        
        for link_info in link_infos:
            if link_info['type'] in meituan_link_types:
                               
                if "link_type_choice" not in state:
                    need_ask_link_type = True
                    break
        
        if need_ask_link_type:
                                
            state["link_infos"] = link_infos
            state["original_text"] = text
            self.set_user_state(user_id, self.STATE_WAITING_LINK_TYPE, state)
            
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('request_link_type', '请选择链接类型：\n1. 原始链接（带P值）\n2. MP链接（mp://格式）\n\n请输入 1 或 2')
            return rsp
        
                   
        zmkey = account_config.get("zmkey", "")
        
        if not zmkey:
            self.logger.error(f"[{account_name}] 未配置zmkey")
            self.clear_user_state(user_id, "配置错误")
            rsp = TextRspMsg(msg)
            rsp.content = "配置错误，请联系管理员"
            return rsp
        
                  
        api_client = LinkConversionAPI(zmkey, self.logger)
        
                     
        link_type_choice = state.get("link_type_choice", "original")
        
                       
                               
        result_map = {}                           
        failed_map = {}                                
        
        self.logger.info(f"[{account_name}] 识别到 {len(link_infos)} 个链接，链接类型选择: {link_type_choice}")
        
                   
        activation_code = state.get("activation_code", "")
        remaining_count = 0
        activation_code_info = None
        
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
        
                  
        count_lock = threading.Lock()
        remaining_count_ref = [remaining_count]
        
                    
        def process_single_link(idx_and_link_info):
            """处理单个链接的函数，用于并发执行"""
            idx, link_info = idx_and_link_info
            link_type = link_info['type']
            link_content = link_info['content']
            
                       
            if activation_code_info:
                with count_lock:
                    if remaining_count_ref[0] <= 0:
                        self.logger.warning(f"[{account_name}] 激活码次数已用完，跳过第 {idx} 个链接")
                        return (idx - 1, None, "激活码次数不足")
                          
                    remaining_count_ref[0] -= 1
                    current_remaining = remaining_count_ref[0]
                self.logger.info(f"[{account_name}] 处理第 {idx} 个链接，类型: {link_type}，剩余次数: {current_remaining}")
            else:
                self.logger.info(f"[{account_name}] 处理第 {idx} 个链接，类型: {link_type}")
            
            try:
                if link_type in meituan_link_types:
                    if link_type_choice == "mp":
                        result_link = api_client.convert_link(link_content, p_value)
                        result_link = self._convert_to_mp_link(result_link)
                        if result_link:
                            self.logger.info(f"[{account_name}] 第 {idx} 个链接转换为MP格式成功: {result_link}")
                            return (idx - 1, result_link, None)
                        else:
                            self.logger.error(f"[{account_name}] 第 {idx} 个链接转换为MP格式失败")
                                       
                            if activation_code_info:
                                with count_lock:
                                    remaining_count_ref[0] += 1
                            return (idx - 1, None, "处理失败")
                    else:
                                
                        result_link = api_client.convert_link(link_content, p_value)
                        if result_link:
                            self.logger.info(f"[{account_name}] 第 {idx} 个链接转换成功")
                            return (idx - 1, result_link, None)
                        else:
                            self.logger.error(f"[{account_name}] 第 {idx} 个链接转换失败")
                                       
                            if activation_code_info:
                                with count_lock:
                                    remaining_count_ref[0] += 1
                            return (idx - 1, None, "处理失败")
                else:
                                   
                    result_link = api_client.convert_link(link_content, p_value)
                    if result_link:
                        self.logger.info(f"[{account_name}] 第 {idx} 个链接转换成功")
                        return (idx - 1, result_link, None)
                    else:
                        self.logger.error(f"[{account_name}] 第 {idx} 个链接转换失败")
                                   
                        if activation_code_info:
                            with count_lock:
                                remaining_count_ref[0] += 1
                        return (idx - 1, None, f"链接{idx}")
            except Exception as e:
                self.logger.error(f"[{account_name}] 第 {idx} 个链接处理异常: {e}")
                           
                if activation_code_info:
                    with count_lock:
                        remaining_count_ref[0] += 1
                return (idx - 1, None, "处理失败")
        
                  
        max_workers = min(10, len(link_infos))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    
            future_to_idx = {
                executor.submit(process_single_link, (idx, link_info)): idx
                for idx, link_info in enumerate(link_infos, 1)
            }
            
                  
            for future in as_completed(future_to_idx):
                try:
                    idx, result_link, failed_reason = future.result()
                    if result_link:
                        result_map[idx] = result_link
                    else:
                                      
                        failed_map[idx] = failed_reason or "处理失败"
                except Exception as e:
                    original_idx = future_to_idx[future]
                    failed_map[original_idx - 1] = "处理失败"                
                    self.logger.error(f"[{account_name}] 第 {original_idx} 个链接处理异常: {e}")
        
                   
        if result_map and activation_code_info:
            success_count = len(result_map)
            consumed_count = remaining_count - remaining_count_ref[0]
            
            if consumed_count > 0:
                             
                def save_activation_code_async():
                    """异步保存激活码消耗"""
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
                            
                                    
                            matched_code["used_count"] = matched_code.get("used_count", 0) + consumed_count
                            
                                    
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
                            
                        self.logger.info(f"[{account_name}] 激活码 {activation_code} 已异步保存 {consumed_count} 次消耗（共转换 {success_count} 个链接）")
                    except Exception as e:
                        self.logger.error(f"[{account_name}] 异步保存激活码消耗失败: {e}", exc_info=True)
                
                            
                save_thread = threading.Thread(target=save_activation_code_async, daemon=True)
                save_thread.start()
                
                self.logger.info(f"[{account_name}] 激活码 {activation_code} 已消耗 {consumed_count} 次使用次数（共转换 {success_count} 个链接），正在异步保存")
        
                                                
                                                
        
                               
        rsp = TextRspMsg(msg)
        
        if result_map or failed_map:
                             
            result_text = text
                       
            sorted_indices = sorted(range(len(link_infos)), key=lambda i: link_infos[i].get('start', 0), reverse=True)
            
            for idx in sorted_indices:
                link_info = link_infos[idx]
                                             
                start_pos = link_info.get('start', -1)
                end_pos = link_info.get('end', -1)
                link_content = link_info['content']
                
                if idx in result_map:
                                  
                    processed_link = result_map[idx]
                    
                    if start_pos >= 0 and end_pos > start_pos:
                                     
                        result_text = result_text[:start_pos] + processed_link + result_text[end_pos:]
                    else:
                                                 
                        result_text = result_text.replace(link_content, processed_link, 1)
                elif idx in failed_map:
                                
                    failure_text = failed_map[idx]
                    
                    if start_pos >= 0 and end_pos > start_pos:
                                     
                        result_text = result_text[:start_pos] + failure_text + result_text[end_pos:]
                    else:
                        result_text = result_text.replace(link_content, failure_text, 1)
                else:
                                                      
                    self.logger.warning(f"[{account_name}] 链接 {idx} 未被处理，标记为失败")
                    failure_text = "处理失败"
                    
                    if start_pos >= 0 and end_pos > start_pos:
                                     
                        result_text = result_text[:start_pos] + failure_text + result_text[end_pos:]
                    else:
                                          
                        result_text = result_text.replace(link_content, failure_text, 1)
            
            rsp.content = result_text
        else:
                  
            error_msg = self.prompts_config['error_api_failed'].format(error="所有链接转换失败")
            continue_prompt = self.prompts_config.get('continue_prompt', '\n\n💡 可以继续输入链接进行处理，或输入"退出"结束')
            rsp.content = error_msg + continue_prompt
        
                                  
                                             
        state.pop("link_infos", None)
        state.pop("original_text", None)
        state.pop("link_type_choice", None)                          
        
                                                     
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
        
        return rsp

    async def _ahandle_link(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Any:
        user_id = msg.get("FromUserName", "")
        account_name = msg.get("_account_name", "")
        account_config = msg.get("_account_config", {})
        p_value = state.get("p_value", "")

        self.logger.info(f"[{account_name}] 用户 {user_id} 发送链接: {text}")

        original_text = state.get("original_text", "")
        if original_text and original_text != text:
            self.logger.info(f"[{account_name}] 检测到新输入，清除之前的链接信息")
            state.pop("link_infos", None)
            state.pop("link_type_choice", None)

        link_infos = state.get("link_infos")
        if not link_infos:
            link_infos = self.link_recognizer.recognize_with_positions(text)
            if not link_infos:
                link_infos_old = self.link_recognizer.recognize_multiple(text)
                if not link_infos_old:
                    link_info = self.link_recognizer.recognize(text)
                    if link_info:
                        link_infos_old = [link_info]
                if link_infos_old:
                    link_infos = []
                    for link_info in link_infos_old:
                        content = link_info.get('content', '')
                        start_pos = text.find(content)
                        if start_pos >= 0:
                            end_pos = start_pos + len(content)
                            link_infos.append({
                                "type": link_info["type"],
                                "content": content,
                                "start": start_pos,
                                "end": end_pos,
                                "original": content,
                                "matched_pattern": link_info.get("matched_pattern", "")
                            })

        if not link_infos:
            self.logger.warning(f"[{account_name}] 链接格式不正确: {text}")
            rsp = TextRspMsg(msg)
            error_msg = self.prompts_config['error_invalid_link']
            continue_prompt = self.prompts_config.get('continue_prompt', '\n\n💡 可以继续输入链接进行处理，或输入"退出"结束')
            rsp.content = error_msg + continue_prompt
            self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
            return rsp

        need_ask_link_type = False
        meituan_link_types = ["meituan_miniprogram", "meituan_general", "mp_protocol"]
        for link_info in link_infos:
            if link_info['type'] in meituan_link_types and "link_type_choice" not in state:
                need_ask_link_type = True
                break

        if need_ask_link_type:
            state["link_infos"] = link_infos
            state["original_text"] = text
            self.set_user_state(user_id, self.STATE_WAITING_LINK_TYPE, state)
            rsp = TextRspMsg(msg)
            rsp.content = self.prompts_config.get('request_link_type', '请选择链接类型：\n1. 原始链接（带P值）\n2. MP链接（mp://格式）\n\n请输入 1 或 2')
            return rsp

        zmkey = account_config.get("zmkey", "")
        if not zmkey:
            self.logger.error(f"[{account_name}] 未配置zmkey")
            self.clear_user_state(user_id, "配置错误")
            rsp = TextRspMsg(msg)
            rsp.content = "配置错误，请联系管理员"
            return rsp

        api_client = LinkConversionAPI(zmkey, self.logger)
        link_type_choice = state.get("link_type_choice", "original")
        result_map = {}
        failed_map = {}

        self.logger.info(f"[{account_name}] 识别到 {len(link_infos)} 个链接，链接类型选择: {link_type_choice}")

        activation_code = state.get("activation_code", "")
        remaining_count = 0
        activation_code_info = None
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

        count_lock = asyncio.Lock()
        remaining_count_ref = [remaining_count]

        async def process_single_link(idx: int, link_info: Dict[str, Any]):
            link_type = link_info['type']
            link_content = link_info['content']
            if activation_code_info:
                async with count_lock:
                    if remaining_count_ref[0] <= 0:
                        self.logger.warning(f"[{account_name}] 激活码次数已用完，跳过第 {idx} 个链接")
                        return idx - 1, None, "激活码次数不足"
                    remaining_count_ref[0] -= 1
                    current_remaining = remaining_count_ref[0]
                self.logger.info(f"[{account_name}] 处理第 {idx} 个链接，类型: {link_type}，剩余次数: {current_remaining}")
            else:
                self.logger.info(f"[{account_name}] 处理第 {idx} 个链接，类型: {link_type}")

            try:
                if link_type in meituan_link_types and link_type_choice == "mp":
                    result_link = await api_client.aconvert_link(link_content, p_value)
                    result_link = self._convert_to_mp_link(result_link) if result_link else None
                else:
                    result_link = await api_client.aconvert_link(link_content, p_value)

                if result_link:
                    self.logger.info(f"[{account_name}] 第 {idx} 个链接转换成功")
                    return idx - 1, result_link, None

                if activation_code_info:
                    async with count_lock:
                        remaining_count_ref[0] += 1
                self.logger.error(f"[{account_name}] 第 {idx} 个链接转换失败")
                return idx - 1, None, "处理失败" if link_type in meituan_link_types else f"链接{idx}"
            except Exception as e:
                self.logger.error(f"[{account_name}] 第 {idx} 个链接处理异常: {e}")
                if activation_code_info:
                    async with count_lock:
                        remaining_count_ref[0] += 1
                return idx - 1, None, "处理失败"

        tasks = [process_single_link(idx, link_info) for idx, link_info in enumerate(link_infos, 1)]
        for idx, result_link, failed_reason in await asyncio.gather(*tasks):
            if result_link:
                result_map[idx] = result_link
            else:
                failed_map[idx] = failed_reason or "处理失败"

        if result_map and activation_code_info:
            success_count = len(result_map)
            consumed_count = remaining_count - remaining_count_ref[0]
            if consumed_count > 0:
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
                            matched_code["used_count"] = matched_code.get("used_count", 0) + consumed_count
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
                        self.logger.info(f"[{account_name}] 激活码 {activation_code} 已异步保存 {consumed_count} 次消耗（共转换 {success_count} 个链接）")
                    except Exception as e:
                        self.logger.error(f"[{account_name}] 异步保存激活码消耗失败: {e}", exc_info=True)

                threading.Thread(target=save_activation_code_async, daemon=True).start()
                self.logger.info(f"[{account_name}] 激活码 {activation_code} 已消耗 {consumed_count} 次使用次数（共转换 {success_count} 个链接），正在异步保存")

        rsp = TextRspMsg(msg)
        if result_map or failed_map:
            result_text = text
            sorted_indices = sorted(range(len(link_infos)), key=lambda i: link_infos[i].get('start', 0), reverse=True)
            for idx in sorted_indices:
                link_info = link_infos[idx]
                start_pos = link_info.get('start', -1)
                end_pos = link_info.get('end', -1)
                link_content = link_info['content']
                if idx in result_map:
                    processed_link = result_map[idx]
                    result_text = result_text[:start_pos] + processed_link + result_text[end_pos:] if start_pos >= 0 and end_pos > start_pos else result_text.replace(link_content, processed_link, 1)
                elif idx in failed_map:
                    failure_text = failed_map[idx]
                    result_text = result_text[:start_pos] + failure_text + result_text[end_pos:] if start_pos >= 0 and end_pos > start_pos else result_text.replace(link_content, failure_text, 1)
                else:
                    failure_text = "处理失败"
                    result_text = result_text[:start_pos] + failure_text + result_text[end_pos:] if start_pos >= 0 and end_pos > start_pos else result_text.replace(link_content, failure_text, 1)
            rsp.content = result_text
        else:
            error_msg = self.prompts_config['error_api_failed'].format(error="所有链接转换失败")
            continue_prompt = self.prompts_config.get('continue_prompt', '\n\n💡 可以继续输入链接进行处理，或输入"退出"结束')
            rsp.content = error_msg + continue_prompt

        state.pop("link_infos", None)
        state.pop("original_text", None)
        state.pop("link_type_choice", None)
        self.set_user_state(user_id, self.STATE_WAITING_LINK, state)
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
