"""
文本消息处理器
"""
import logging
import time
from typing import Dict, Any, Optional, List
from .base_handler import BaseHandler


class TextHandler(BaseHandler):
    """文本消息处理器"""
    
    def __init__(self, logger, text_processors: List[Any] = None, processor_map: Dict[str, Any] = None):
        """
        初始化文本消息处理器
        
        Args:
            logger: 日志记录器
            text_processors: 文本处理器列表，按优先级排序
            processor_map: 处理器名称到处理器的映射，用于根据配置过滤处理器
        """
        super().__init__(logger)
        self.text_processors = text_processors or []
        self.processor_map = processor_map or {}
        self.account_processors: Dict[str, List[Any]] = {}
        self.processor_class_map: Dict[str, Any] = {}

    def set_runtime_caches(
        self,
        account_processors: Optional[Dict[str, List[Any]]] = None,
        processor_class_map: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.account_processors = dict(account_processors or {})
        self.processor_class_map = dict(processor_class_map or {})

    def _get_processors_for_account(self, to_user_name: str) -> List[Any]:
        return list(self.account_processors.get(to_user_name, []))

    def _get_processor_by_class_name(self, class_name: str) -> Optional[Any]:
        return self.processor_class_map.get(class_name)

    def _append_timing(self, msg: Dict[str, Any], stage: str, started_at: float, extra: Optional[Dict[str, Any]] = None) -> None:
        timings = msg.setdefault("_text_handler_timings", [])
        item = {
            "stage": stage,
            "elapsed": round(max(0.0, time.time() - started_at), 3),
        }
        if extra:
            item.update(extra)
        timings.append(item)
    
    async def ahandle(self, msg: Dict[str, Any]) -> Optional[Any]:
        from config.config import ACCOUNT_SPECIFIC_CONFIGS
        from text_processors.stateful_processor import USER_STATES

        to_user_name = msg.get("ToUserName", "")
        account_name = msg.get("_account_name", to_user_name)
        msg_type = msg.get("MsgType", "text")
        user_id = msg.get("FromUserName", "")

        if msg_type == "miniprogrampage":
            user_state = USER_STATES.get(user_id)
            if user_state:
                processor_name = user_state.get("processor", "")
                state_name = user_state.get("state", "")
                if processor_name == "MeituanShopQueryProcessor" and state_name == "waiting_miniprogram":
                    try:
                        processor = self._get_processor_by_class_name("MeituanShopQueryProcessor")
                        if processor is not None:
                            return await processor.ahandle_state(msg, "", user_state)
                    except Exception as e:
                        self.logger.error("[%s] 查找处理器失败: %s", account_name, e, exc_info=True)
                    return None

        msg_info = {
            "msg_type": msg_type,
            "from_user_name": user_id,
            "to_user_name": to_user_name,
            "account_name": account_name,
            "create_time": msg.get("CreateTime"),
            "msg_id": msg.get("MsgId"),
            "content": msg.get("Content")
        }

        self._log_message(msg, msg_info)
        text = msg.get("Content", "")

        stage_started_at = time.time()
        processors_to_use = self._get_processors_for_account(to_user_name)
        self._append_timing(
            msg,
            "text_handler_get_processors",
            stage_started_at,
            {"processor_count": len(processors_to_use)},
        )

        for processor in processors_to_use:
            stage_started_at = time.time()
            can_handle = processor.can_handle(text)
            self._append_timing(
                msg,
                "text_handler_can_handle",
                stage_started_at,
                {"processor": processor.__class__.__name__, "matched": can_handle},
            )
            if can_handle:
                self.logger.info("[%s] 使用处理器: %s", account_name, processor.__class__.__name__)
                stage_started_at = time.time()
                response = await processor.aprocess(msg, text)
                self._append_timing(
                    msg,
                    "text_handler_processor_execute",
                    stage_started_at,
                    {"processor": processor.__class__.__name__, "responded": response is not None},
                )
                if response is not None:
                    return response

        stage_started_at = time.time()
        default_reply = """回复
        <a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=美团">1.美团</a>
        <a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=饿了么">2.饿了么</a>
        <a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=京东">3.京东</a>
        <a href="weixin://bizmsgmenu?msgmenuid=1&msgmenucontent=美团免配叠加津贴">4.美团免配叠加津贴</a>
        这四个关键词可以获取对应优惠哦"""

        if to_user_name in ACCOUNT_SPECIFIC_CONFIGS:
            specific_config = ACCOUNT_SPECIFIC_CONFIGS[to_user_name]
            default_reply = specific_config.get("default_reply", default_reply)
        self._append_timing(msg, "text_handler_default_reply", stage_started_at)

        return default_reply
