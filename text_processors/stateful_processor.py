"""
有状态文本处理器基类

支持多步骤交互，可以记住用户的会话状态
支持状态超时自动清理
"""
import asyncio
import time
import threading
from typing import Dict, Any, Optional
from .base_processor import BaseTextProcessor


                      
USER_STATES: Dict[str, Dict[str, Any]] = {}

             
DEFAULT_STATE_TIMEOUT = 300       

                     
MAX_STATE_TIMEOUT = 600        

             
CLEANUP_INTERVAL = 300           

          
_cleanup_lock = threading.Lock()
_cleanup_task = None


class StatefulTextProcessor(BaseTextProcessor):
    """
    有状态文本处理器基类
    
    当用户触发特定关键词后，进入状态机模式，
    后续消息将优先被该处理器处理，直到状态结束
    """
    
    def __init__(self, logger, pattern=None, state_timeout: int = DEFAULT_STATE_TIMEOUT):
        """
        初始化有状态处理器
        
        Args:
            logger: 日志记录器
            pattern: 触发关键词
            state_timeout: 状态超时时间（秒），默认300秒（5分钟）
        """
        super().__init__(logger, pattern)
        self.trigger_keywords = []           
        self.state_timeout = state_timeout          
    
    def get_user_state(self, user_id: str, check_timeout: bool = True) -> Optional[Dict[str, Any]]:
        """
        获取用户当前状态（自动检查超时）
        
        Args:
            user_id: 用户ID（FromUserName）
            check_timeout: 是否检查超时，默认True
            
        Returns:
            用户状态字典，如果不存在或已超时则返回None
        """
        state = USER_STATES.get(user_id)
        
        if not state:
            return None
        
                
        if check_timeout:
            last_update = state.get("last_update_time", 0)
            current_time = time.time()
            
            if current_time - last_update > self.state_timeout:
                            
                self.logger.warning(f"用户 {user_id} 的状态已超时（超过{self.state_timeout}秒），自动清除")
                self.clear_user_state(user_id)
                return None
        
        return state
    
    def set_user_state(self, user_id: str, state_name: str, data: Dict[str, Any] = None):
        """
        设置用户状态（自动记录时间戳）
        
        Args:
            user_id: 用户ID
            state_name: 状态名称
            data: 状态数据
        """
        current_time = time.time()
        
        if user_id not in USER_STATES:
            USER_STATES[user_id] = {}
        
        USER_STATES[user_id]["state"] = state_name
        USER_STATES[user_id]["processor"] = self.__class__.__name__
        USER_STATES[user_id]["last_update_time"] = current_time          
        USER_STATES[user_id]["created_time"] = USER_STATES[user_id].get("created_time", current_time)          
        
        if data:
            USER_STATES[user_id].update(data)
        
        self.logger.info(f"用户 {user_id} 进入状态: {state_name}")
    
    def clear_user_state(self, user_id: str, reason: str = "正常退出"):
        """
        清除用户状态（退出状态机）
        
        Args:
            user_id: 用户ID
            reason: 清除原因
        """
        if user_id in USER_STATES:
            del USER_STATES[user_id]
            self.logger.info(f"用户 {user_id} 退出状态机 - 原因: {reason}")
    
    def is_trigger(self, text: str) -> bool:
        """
        检查文本是否触发该处理器
        
        Args:
            text: 用户发送的文本
            
        Returns:
            是否触发
        """
        text_lower = text.lower().strip()
        for keyword in self.trigger_keywords:
            if keyword.lower() in text_lower:
                return True
        return False
    
    async def aprocess(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")
        user_state = self.get_user_state(user_id, check_timeout=True)

        if user_state and user_state.get("processor") == self.__class__.__name__:
            return await self.ahandle_state(msg, text, user_state)

        if self.is_trigger(text):
            return await self.ahandle_trigger(msg, text)

        return None
    
    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        """
        处理触发关键词（子类实现）
        
        Args:
            msg: 消息字典
            text: 文本内容
            
        Returns:
            响应对象
        """
        raise NotImplementedError
    
    async def ahandle_state(self, msg: Dict[str, Any], text: str, state: Dict[str, Any]) -> Optional[Any]:
        """
        处理状态中的消息（子类实现）
        
        Args:
            msg: 消息字典
            text: 文本内容
            state: 当前状态数据
            
        Returns:
            响应对象
        """
        raise NotImplementedError
    
    def get_all_states(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有用户状态（调试/管理功能）
        
        Returns:
            所有用户状态字典
        """
                
        current_time = time.time()
        expired_users = []
        
        for user_id, state in USER_STATES.items():
            last_update = state.get("last_update_time", 0)
            if current_time - last_update > self.state_timeout:
                expired_users.append(user_id)
        
        for user_id in expired_users:
            self.logger.info(f"清理过期状态: 用户 {user_id}")
            del USER_STATES[user_id]
        
        return USER_STATES.copy()


def _cleanup_expired_states(logger=None):
    """
    清理所有过期的状态（定时任务调用）
    
    Args:
        logger: 日志记录器（可选）
    """
    global USER_STATES
    
    if not USER_STATES:
        return
    
    current_time = time.time()
    expired_users = []
    
    for user_id, state in list(USER_STATES.items()):
        last_update = state.get("last_update_time", 0)
        if current_time - last_update > MAX_STATE_TIMEOUT:
            expired_users.append(user_id)
    
    if expired_users:
        for user_id in expired_users:
            if user_id in USER_STATES:
                del USER_STATES[user_id]
                if logger:
                    logger.info(f"定时清理：用户 {user_id} 的状态已超时（超过{MAX_STATE_TIMEOUT}秒），已自动清除")


async def _cleanup_worker_async(logger=None):
    while True:
        try:
            await asyncio.sleep(CLEANUP_INTERVAL)
            _cleanup_expired_states(logger)
        except asyncio.CancelledError:
            break
        except Exception as e:
            if logger:
                logger.error(f"定时清理任务出错: {e}")


def start_cleanup_task(logger=None):
    global _cleanup_task

    with _cleanup_lock:
        if _cleanup_task is None or _cleanup_task.done():
            _cleanup_task = asyncio.create_task(_cleanup_worker_async(logger))
            if logger:
                logger.info("✅ 状态机定时清理任务已启动")


async def stop_cleanup_task():
    global _cleanup_task

    with _cleanup_lock:
        task = _cleanup_task
        _cleanup_task = None

    if task is None:
        return

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
