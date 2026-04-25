"""
时区工具

优先使用 zoneinfo；如果运行环境未安装 tzdata，则对 Asia/Shanghai 回退到固定东八区。
"""
from __future__ import annotations

from datetime import timezone, timedelta, tzinfo


DEFAULT_SHANGHAI_TIMEZONE = "Asia/Shanghai"
_FIXED_CST = timezone(timedelta(hours=8), name="CST")


def get_timezone(timezone_name: str) -> tzinfo:
    normalized = (timezone_name or "").strip() or DEFAULT_SHANGHAI_TIMEZONE

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(normalized)
    except Exception:
        if normalized == DEFAULT_SHANGHAI_TIMEZONE:
            return _FIXED_CST
        return timezone.utc
