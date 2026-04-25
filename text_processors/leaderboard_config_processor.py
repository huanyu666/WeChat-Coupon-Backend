"""
接单时间排行榜配置处理器
"""
from __future__ import annotations

import re
import sys
from typing import Dict, Any, Optional, List, Tuple

from .stateful_processor import StatefulTextProcessor
from utils.response import TextRspMsg


class LeaderboardConfigProcessor(StatefulTextProcessor):
    STATE_WAITING_CONFIG = "waiting_leaderboard_config"
    TARGET_ACCOUNT_ID = "gh_81203cdf19a5"
    GLOBAL_CONFIG_KEY = "global"
    DEFAULT_TRIGGER_KEYWORD = "设置排行榜"

    def __init__(self, logger):
        super().__init__(logger, state_timeout=600)
        self._reload_configs()
        self.logger.info("LeaderboardConfigProcessor 初始化完成")

    def _reload_configs(self):
        try:
            from config.config import ORDER_LEADERBOARD_CONFIG

            account_config = self._get_shared_config(ORDER_LEADERBOARD_CONFIG)
            trigger_keyword = str(
                account_config.get("trigger_keyword", self.DEFAULT_TRIGGER_KEYWORD)
            ).strip() or self.DEFAULT_TRIGGER_KEYWORD
            self.trigger_keywords = [trigger_keyword]
            self.logger.info("LeaderboardConfigProcessor 配置重新加载成功")
        except Exception as e:
            self.trigger_keywords = [self.DEFAULT_TRIGGER_KEYWORD]
            self.logger.error(f"LeaderboardConfigProcessor 配置重新加载失败: {e}")

    async def ahandle_trigger(self, msg: Dict[str, Any], text: str) -> Optional[Any]:
        if not self._is_authorized(msg):
            return None

        user_id = msg.get("FromUserName", "")
        normalized_text = text.strip()
        trigger_keyword = self.trigger_keywords[0]

        if normalized_text == trigger_keyword:
            self.set_user_state(user_id, self.STATE_WAITING_CONFIG)
            rsp = TextRspMsg(msg)
            rsp.content = (
                "请输入排行榜配置\n"
                "示例：设置排行榜 时间 13:30 14:30 关键词 沪上阿姨 霸王别姬"
            )
            return rsp

        return self._save_config_from_text(msg, normalized_text)

    async def ahandle_state(
        self, msg: Dict[str, Any], text: str, state: Dict[str, Any]
    ) -> Optional[Any]:
        user_id = msg.get("FromUserName", "")

        if not self._is_authorized(msg):
            self.clear_user_state(user_id, "非授权用户")
            return None

        if text.strip() in {"取消", "退出", "返回", "quit", "cancel", "back"}:
            self.clear_user_state(user_id, "用户取消操作")
            rsp = TextRspMsg(msg)
            rsp.content = "已取消设置排行榜"
            return rsp

        return self._save_config_from_text(msg, text.strip())

    def _is_authorized(self, msg: Dict[str, Any]) -> bool:
        to_user_name = msg.get("ToUserName", "")
        user_id = msg.get("FromUserName", "")
        if to_user_name != self.TARGET_ACCOUNT_ID:
            return False

        from config.config import ACCOUNT_SPECIFIC_CONFIGS

        account_config = ACCOUNT_SPECIFIC_CONFIGS.get(self.TARGET_ACCOUNT_ID, {})
        authorized_users = set(account_config.get("authorized_users", []))
        return user_id in authorized_users

    def _save_config_from_text(self, msg: Dict[str, Any], text: str) -> Any:
        user_id = msg.get("FromUserName", "")

        try:
            times, keywords = self._parse_config_text(text)
            self._write_config(times, keywords)
            self._reload_runtime_configs()
        except ValueError as e:
            rsp = TextRspMsg(msg)
            rsp.content = f"❌ {e}"
            return rsp
        except Exception as e:
            self.logger.error(f"保存排行榜配置失败: {e}", exc_info=True)
            rsp = TextRspMsg(msg)
            rsp.content = "❌ 保存排行榜配置失败"
            return rsp

        self.clear_user_state(user_id, "排行榜配置已保存")
        rsp = TextRspMsg(msg)
        rsp.content = (
            "✅ 排行榜配置已更新\n"
            f"时间：{'、'.join(times)}\n"
            f"关键词：{'、'.join(keywords)}"
        )
        return rsp

    def _parse_config_text(self, text: str) -> Tuple[List[str], List[str]]:
        working_text = text.strip()
        trigger_keyword = self.trigger_keywords[0]
        if working_text.startswith(trigger_keyword):
            working_text = working_text[len(trigger_keyword):].strip()

        if not working_text:
            raise ValueError("请按“设置排行榜 时间 13:30 关键词 沪上阿姨”格式输入")

        matches = list(re.finditer(r"(时间|关键词)", working_text))
        if len(matches) < 2:
            raise ValueError("缺少“时间”或“关键词”字段")

        sections: Dict[str, str] = {}
        for index, match in enumerate(matches):
            label = match.group(1)
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(working_text)
            sections[label] = working_text[start:end].strip()

        raw_times = sections.get("时间", "")
        raw_keywords = sections.get("关键词", "")
        if not raw_times:
            raise ValueError("请至少设置一个时间")
        if not raw_keywords:
            raise ValueError("请至少设置一个关键词")

        times = self._parse_times(raw_times)
        keywords = self._parse_keywords(raw_keywords)

        if not times:
            raise ValueError("未识别到有效时间，请使用 HH:MM 格式")
        if not keywords:
            raise ValueError("未识别到有效关键词")

        return times, keywords

    def _parse_times(self, raw_text: str) -> List[str]:
        times: List[str] = []
        seen = set()
        for hour_text, minute_text in re.findall(r"(\d{1,2})[:：](\d{1,2})", raw_text):
            hour = int(hour_text)
            minute = int(minute_text)
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                continue
            normalized = f"{hour:02d}:{minute:02d}"
            if normalized not in seen:
                seen.add(normalized)
                times.append(normalized)
        return sorted(times)

    def _parse_keywords(self, raw_text: str) -> List[str]:
        keywords: List[str] = []
        seen = set()
        for item in re.split(r"[\s,，]+", raw_text):
            keyword = item.strip()
            if not keyword or keyword in seen:
                continue
            seen.add(keyword)
            keywords.append(keyword)
        return keywords

    def _write_config(self, times: List[str], keywords: List[str]) -> None:
        from config.config import ORDER_LEADERBOARD_CONFIG, ORDER_LEADERBOARD_CONFIG_FILE

        target_config = self._get_shared_config(ORDER_LEADERBOARD_CONFIG)
        target_config["trigger_keyword"] = self.trigger_keywords[0]
        target_config["times"] = times
        target_config["keywords"] = keywords
        target_config["leaderboard_url"] = "http://waimaiyouhui.top/order-rankings"
        target_config["timezone"] = "Asia/Shanghai"

        lines = [
            "# 接单时间排行榜配置",
            "# 仅允许指定公众号的授权用户修改，但配置对所有公众号共享生效",
            "",
        ]
        lines.append(f"[{self.GLOBAL_CONFIG_KEY}]")
        lines.append(
            f'trigger_keyword = {self._format_toml_string(target_config.get("trigger_keyword", self.DEFAULT_TRIGGER_KEYWORD))}'
        )
        lines.append(
            f'times = {self._format_toml_array(target_config.get("times", []))}'
        )
        lines.append(
            f'keywords = {self._format_toml_array(target_config.get("keywords", []))}'
        )
        lines.append(
            f'leaderboard_url = {self._format_toml_string(target_config.get("leaderboard_url", "http://waimaiyouhui.top/order-rankings"))}'
        )
        lines.append(
            f'timezone = {self._format_toml_string(target_config.get("timezone", "Asia/Shanghai"))}'
        )
        lines.append("")

        with open(ORDER_LEADERBOARD_CONFIG_FILE, "w", encoding="utf-8") as file:
            file.write("\n".join(lines).rstrip() + "\n")

    def _reload_runtime_configs(self) -> None:
        from config.config import reload_config

        reload_config()

        wechat_module = sys.modules.get("routes.wechat")
        if not wechat_module or not hasattr(wechat_module, "_all_text_processors"):
            self._reload_configs()
            return

        for _, processor in wechat_module._all_text_processors:
            if hasattr(processor, "_reload_configs"):
                try:
                    processor._reload_configs()
                except Exception as e:
                    self.logger.error(f"处理器配置热加载失败: {processor.__class__.__name__}: {e}")

    def _format_toml_string(self, value: str) -> str:
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _format_toml_array(self, values: List[str]) -> str:
        return "[" + ", ".join(self._format_toml_string(value) for value in values) + "]"

    def _get_shared_config(self, config_map: Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(config_map.get(self.GLOBAL_CONFIG_KEY), dict):
            return dict(config_map[self.GLOBAL_CONFIG_KEY])
        if isinstance(config_map.get(self.TARGET_ACCOUNT_ID), dict):
            return dict(config_map[self.TARGET_ACCOUNT_ID])
        return {}
