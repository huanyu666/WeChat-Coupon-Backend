from __future__ import annotations

import json
import re
import urllib.parse
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from utils.path_utils import resolve_runtime_data_path
from utils.shortlink_service import normalize_shortlink_config

SYSTEM_SETTINGS_FILENAME = "system_settings.runtime.json"

PROMPTS_CONFIG_LIST_FIELDS = {
    "get_link_flow": ("cancel_keywords",),
    "generate_link_flow": ("cancel_keywords",),
    "get_tuangou_flow": ("cancel_keywords",),
    "get_meituan_flow": ("cancel_keywords",),
    "get_jd_flow": ("cancel_keywords",),
    "shortlink_generator_flow": ("cancel_keywords",),
}

LINK_CONFIG_SECTIONS = ("meituan_miniprogram", "meituan_general", "mp_protocol")
ORDER_LEADERBOARD_SECTIONS = ("global",)
BACKUP_SCHEDULE_DEFAULTS = {
    "enabled": False,
    "frequency": "weekly",
    "weekday": "6",
    "time": "03:00",
    "timezone": "Asia/Shanghai",
    "include_env": False,
    "include_redis_shortlinks": True,
    "include_redis_runtime": True,
    "retention_count": "30",
}
ALLOWANCE_SCHEDULE_DEFAULTS = {
    "enabled": False,
    "time": "",
    "times": [],
    "timezone": "Asia/Shanghai",
    "address_scope": "all",
}
ALLOWANCE_RELAY_POOL_DEFAULTS = {
    "strategy": "healthy_round_robin",
    "request_timeout_seconds": 15,
    "failure_cooldown_seconds": 300,
    "consecutive_failure_threshold": 2,
    "allow_proxy_fallback": True,
    "nodes": [],
}
ORDER_RELAY_POOL_DEFAULTS = {
    "route_mode": "third_then_relay_then_local",
    "third_party_url": "https://mt.liliabc.fun/api/acceptOrders4",
    "third_party_timeout_seconds": 15,
    "third_party_concurrency_limit": 30,
    "strategy": "healthy_round_robin",
    "request_timeout_seconds": 15,
    "failure_cooldown_seconds": 300,
    "consecutive_failure_threshold": 2,
    # The order Relay runs on a small domestic host. Fetch a fresh proxy for
    # each request by default; a cached proxy pool remains an explicit option.
    "proxy_mode": "direct",
    "proxy_retry_count": 2,
    "queue_wait_seconds": 3.0,
    "nodes": [],
}
WEB_USER_REGISTRATION_DEFAULTS = {
    "auto_approve": False,
    "initial_query_count": 100,
}
PUSHPLUS_DEFAULTS = {
    "enabled": False,
    "platform_token": "",
    "secret_key": "",
    "app_id": "",
    "public_base_url": "",
    "callback_secret": "",
    "account_tier": "standard",
}
ALLOWANCE_SCHEDULE_TYPES = ("large", "small_free_order")
LEADERBOARD_DEFAULT_TIMEZONE = "Asia/Shanghai"
LEGACY_DEFAULT_LEADERBOARD_URLS = {
    "http://waimaiyouhui.top/order-rankings",
    "https://waimaiyouhui.top/order-rankings",
}


def get_system_settings_store_path() -> Path:
    return resolve_runtime_data_path(SYSTEM_SETTINGS_FILENAME)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


def _normalize_leaderboard_url(value: Any) -> str:
    url = _normalize_text(value).rstrip("/")
    if url in LEGACY_DEFAULT_LEADERBOARD_URLS:
        return ""
    return url


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.splitlines()
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _normalize_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_prompt_sections(raw_value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_value, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for section_name, section_value in raw_value.items():
        if not isinstance(section_value, dict):
            continue
        section_key = str(section_name or "").strip()
        if not section_key:
            continue

        list_fields = set(PROMPTS_CONFIG_LIST_FIELDS.get(section_key, ()))
        section_result: dict[str, Any] = {}
        for field_name, field_value in section_value.items():
            field_key = str(field_name or "").strip()
            if not field_key:
                continue
            if field_key in list_fields:
                section_result[field_key] = _normalize_text_list(field_value)
            else:
                section_result[field_key] = _normalize_text(field_value)

        if section_result:
            normalized[section_key] = section_result

    return normalized


def _normalize_link_config(raw_value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_value, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for section_name, section_value in raw_value.items():
        if not isinstance(section_value, dict):
            continue
        section_key = str(section_name or "").strip()
        if not section_key:
            continue

        patterns = section_value.get("patterns")
        if patterns in (None, "") and "pattern" in section_value:
            patterns = [section_value.get("pattern")]

        section_result = {
            "description": _normalize_text(section_value.get("description")),
            "patterns": _normalize_text_list(patterns),
        }
        if section_result["patterns"] or section_result["description"]:
            normalized[section_key] = section_result

    return normalized


def _normalize_order_leaderboard_config(raw_value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw_value, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for section_name, section_value in raw_value.items():
        if not isinstance(section_value, dict):
            continue
        section_key = str(section_name or "").strip()
        if not section_key:
            continue

        section_result = {
            "trigger_keyword": _normalize_text(section_value.get("trigger_keyword")),
            "times": _normalize_text_list(section_value.get("times")),
            "keywords": _normalize_text_list(section_value.get("keywords")),
            "leaderboard_url": _normalize_leaderboard_url(section_value.get("leaderboard_url")),
            "timezone": _normalize_text(section_value.get("timezone")),
        }
        if any(
            section_result[key]
            for key in ("trigger_keyword", "times", "keywords", "leaderboard_url", "timezone")
        ):
            normalized[section_key] = section_result

    return normalized


def _normalize_leaderboard_time_list(value: Any) -> list[str]:
    normalized = _normalize_text_list(value)
    valid_times: list[str] = []
    seen: set[str] = set()
    for item in normalized:
        match = re.fullmatch(r"(\d{1,2}):(\d{1,2})", item)
        if not match:
            continue
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 23 or minute > 59:
            continue
        normalized_time = f"{hour:02d}:{minute:02d}"
        if normalized_time in seen:
            continue
        seen.add(normalized_time)
        valid_times.append(normalized_time)
    return valid_times


def _normalize_leaderboard_date_overrides(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    items: list[Any]
    if isinstance(value, dict):
        items = [{"date": key, "times": val} for key, val in value.items()]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    elif isinstance(value, str):
        items = value.splitlines()
    else:
        return []

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        override_date = ""
        override_times: Any = []
        if isinstance(item, dict):
            override_date = _normalize_text(item.get("date"))
            override_times = item.get("times")
            if not override_times and "time" in item:
                override_times = item.get("time")
        else:
            text = _normalize_text(item)
            if not text:
                continue
            if " " in text:
                override_date, override_times = text.split(" ", 1)
            elif "|" in text:
                override_date, override_times = text.split("|", 1)
            elif "=" in text:
                override_date, override_times = text.split("=", 1)
            else:
                override_date = text
                override_times = ""
        override_date = _normalize_text(override_date)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", override_date):
            continue
        times = _normalize_leaderboard_time_list(override_times)
        if not times or override_date in seen:
            continue
        seen.add(override_date)
        normalized.append({
            "date": override_date,
            "times": times,
        })
    normalized.sort(key=lambda item: str(item.get("date") or ""))
    return normalized


def _normalize_leaderboard_rule(raw_value: Any, *, fallback_id: str = "") -> dict[str, Any]:
    if not isinstance(raw_value, dict):
        raw_value = {}

    raw_id = _normalize_text(raw_value.get("id")) or _normalize_text(raw_value.get("rule_id")) or fallback_id
    raw_name = _normalize_text(raw_value.get("name")) or _normalize_text(raw_value.get("title"))
    if not raw_name:
        raw_name = "默认排行榜" if not raw_id or raw_id == "global" else raw_id
    sort_order_value = _normalize_text(raw_value.get("sort_order")) or "0"
    try:
        sort_order = int(sort_order_value)
    except ValueError:
        sort_order = 0

    timezone = _normalize_text(raw_value.get("timezone")) or LEADERBOARD_DEFAULT_TIMEZONE
    if timezone != LEADERBOARD_DEFAULT_TIMEZONE and not timezone:
        timezone = LEADERBOARD_DEFAULT_TIMEZONE

    leaderboard_url = _normalize_leaderboard_url(raw_value.get("leaderboard_url"))

    return {
        "id": raw_id or "default",
        "name": raw_name,
        "enabled": _normalize_bool(raw_value.get("enabled", True)),
        "archived": _normalize_bool(raw_value.get("archived", False)),
        "sort_order": sort_order,
        "keywords": _normalize_text_list(raw_value.get("keywords")),
        "default_times": _normalize_leaderboard_time_list(raw_value.get("default_times") or raw_value.get("times")),
        "date_overrides": _normalize_leaderboard_date_overrides(raw_value.get("date_overrides") or raw_value.get("overrides")),
        "timezone": timezone,
        "leaderboard_url": leaderboard_url,
        "description": _normalize_text(raw_value.get("description")),
        "created_at": int(raw_value.get("created_at") or 0),
        "updated_at": int(raw_value.get("updated_at") or 0),
    }


def _normalize_leaderboard_rules(raw_value: Any) -> list[dict[str, Any]]:
    if raw_value is None:
        return []
    if isinstance(raw_value, dict):
        normalized: list[dict[str, Any]] = []
        for rule_id, rule_value in raw_value.items():
            if not isinstance(rule_value, dict):
                continue
            normalized.append(_normalize_leaderboard_rule(rule_value, fallback_id=str(rule_id or "").strip()))
        return normalized
    if not isinstance(raw_value, (list, tuple, set)):
        return []
    normalized_rules: list[dict[str, Any]] = []
    for index, item in enumerate(list(raw_value)):
        rule = _normalize_leaderboard_rule(item, fallback_id=f"rule-{index + 1}")
        if not rule["id"]:
            rule["id"] = f"rule-{index + 1}"
        normalized_rules.append(rule)
    return normalized_rules


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _normalize_global_leaderboard_config(raw_value: Any) -> dict[str, Any]:
    if not isinstance(raw_value, dict):
        return {"enabled": False, "leaderboard_url": ""}
    return {
        "enabled": _normalize_bool(raw_value.get("enabled")),
        "leaderboard_url": _normalize_leaderboard_url(raw_value.get("leaderboard_url")),
    }


def normalize_backup_schedule_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    frequency = _normalize_text(raw_config.get("frequency")) or BACKUP_SCHEDULE_DEFAULTS["frequency"]
    if frequency not in {"hourly", "daily", "weekly"}:
        frequency = BACKUP_SCHEDULE_DEFAULTS["frequency"]

    weekday = _normalize_text(raw_config.get("weekday")) or BACKUP_SCHEDULE_DEFAULTS["weekday"]
    try:
        weekday_int = int(weekday)
    except ValueError:
        weekday_int = int(BACKUP_SCHEDULE_DEFAULTS["weekday"])
    weekday_int = min(max(weekday_int, 0), 6)

    backup_time = _normalize_text(raw_config.get("time")) or BACKUP_SCHEDULE_DEFAULTS["time"]
    if not re.fullmatch(r"\d{2}:\d{2}", backup_time):
        backup_time = BACKUP_SCHEDULE_DEFAULTS["time"]
    hour, minute = [int(part) for part in backup_time.split(":", 1)]
    if hour > 23 or minute > 59:
        backup_time = BACKUP_SCHEDULE_DEFAULTS["time"]

    retention_count = _normalize_text(raw_config.get("retention_count")) or BACKUP_SCHEDULE_DEFAULTS["retention_count"]
    try:
        retention_int = int(retention_count)
    except ValueError:
        retention_int = int(BACKUP_SCHEDULE_DEFAULTS["retention_count"])
    retention_int = min(max(retention_int, 1), 365)

    timezone = _normalize_text(raw_config.get("timezone")) or BACKUP_SCHEDULE_DEFAULTS["timezone"]
    if timezone != "Asia/Shanghai":
        timezone = "Asia/Shanghai"

    return {
        "enabled": _normalize_bool(raw_config.get("enabled")),
        "frequency": frequency,
        "weekday": str(weekday_int),
        "time": backup_time,
        "timezone": timezone,
        "include_env": _normalize_bool(raw_config.get("include_env")),
        "include_redis_shortlinks": (
            BACKUP_SCHEDULE_DEFAULTS["include_redis_shortlinks"]
            if "include_redis_shortlinks" not in raw_config
            else _normalize_bool(raw_config.get("include_redis_shortlinks"))
        ),
        "include_redis_runtime": (
            BACKUP_SCHEDULE_DEFAULTS["include_redis_runtime"]
            if "include_redis_runtime" not in raw_config
            else _normalize_bool(raw_config.get("include_redis_runtime"))
        ),
        "retention_count": str(retention_int),
    }


def normalize_allowance_schedule_type_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    raw_times = raw_config.get("times")
    candidate_times: list[str] = []
    if isinstance(raw_times, (list, tuple, set)):
        candidate_times = [str(item or "").strip() for item in raw_times]
    elif raw_times not in (None, ""):
        candidate_times = [str(raw_times or "").strip()]

    legacy_time = _normalize_text(raw_config.get("time"))
    if legacy_time:
        candidate_times.append(legacy_time)

    normalized_times: list[str] = []
    seen_times: set[str] = set()
    for candidate in candidate_times:
        if not re.fullmatch(r"\d{2}:\d{2}", candidate):
            continue
        hour, minute = [int(part) for part in candidate.split(":", 1)]
        if hour > 23 or minute > 59:
            continue
        normalized_time = f"{hour:02d}:{minute:02d}"
        if normalized_time in seen_times:
            continue
        seen_times.add(normalized_time)
        normalized_times.append(normalized_time)
    normalized_times.sort()

    timezone = _normalize_text(raw_config.get("timezone")) or ALLOWANCE_SCHEDULE_DEFAULTS["timezone"]
    if timezone != "Asia/Shanghai":
        timezone = "Asia/Shanghai"

    address_scope = _normalize_text(raw_config.get("address_scope")) or ALLOWANCE_SCHEDULE_DEFAULTS["address_scope"]
    if address_scope not in {"all", "latest"}:
        address_scope = ALLOWANCE_SCHEDULE_DEFAULTS["address_scope"]

    return {
        "enabled": _normalize_bool(raw_config.get("enabled")),
        "time": normalized_times[0] if normalized_times else "",
        "times": normalized_times,
        "timezone": timezone,
        "address_scope": address_scope,
    }


def normalize_allowance_schedule_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    raw_types = raw_config.get("types")

    if isinstance(raw_types, dict):
        normalized_types: dict[str, dict[str, Any]] = {}
        for allowance_type in ALLOWANCE_SCHEDULE_TYPES:
            normalized_types[allowance_type] = normalize_allowance_schedule_type_config(
                raw_types.get(allowance_type)
            )
        return {
            "types": normalized_types,
        }

    # Legacy flat structure: migrate the old single config into large,
    # and create an empty config for small_free_order.
    return {
        "types": {
            "large": normalize_allowance_schedule_type_config(raw_config),
            "small_free_order": normalize_allowance_schedule_type_config({}),
        }
    }


def normalize_allowance_relay_pool_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    strategy = _normalize_text(raw_config.get("strategy")) or ALLOWANCE_RELAY_POOL_DEFAULTS["strategy"]
    if strategy != "healthy_round_robin":
        strategy = ALLOWANCE_RELAY_POOL_DEFAULTS["strategy"]

    timeout_value = raw_config.get("request_timeout_seconds")
    try:
        request_timeout_seconds = int(timeout_value)
    except (TypeError, ValueError):
        request_timeout_seconds = int(ALLOWANCE_RELAY_POOL_DEFAULTS["request_timeout_seconds"])
    request_timeout_seconds = min(max(request_timeout_seconds, 3), 60)

    cooldown_value = raw_config.get("failure_cooldown_seconds")
    try:
        failure_cooldown_seconds = int(cooldown_value)
    except (TypeError, ValueError):
        failure_cooldown_seconds = int(ALLOWANCE_RELAY_POOL_DEFAULTS["failure_cooldown_seconds"])
    failure_cooldown_seconds = min(max(failure_cooldown_seconds, 30), 86400)

    threshold_value = raw_config.get("consecutive_failure_threshold")
    try:
        consecutive_failure_threshold = int(threshold_value)
    except (TypeError, ValueError):
        consecutive_failure_threshold = int(ALLOWANCE_RELAY_POOL_DEFAULTS["consecutive_failure_threshold"])
    consecutive_failure_threshold = min(max(consecutive_failure_threshold, 1), 20)

    raw_nodes = raw_config.get("nodes")
    if not isinstance(raw_nodes, (list, tuple)):
        raw_nodes = []

    normalized_nodes: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        url = _normalize_text(item.get("url"))
        if not url:
            continue
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            url = f"http://{url}"
        url = url.rstrip("/")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        name = _normalize_text(item.get("name")) or f"挂机宝节点 {index + 1}"
        normalized_nodes.append({
            "name": name,
            "url": url,
            "secret": _normalize_text(item.get("secret")),
            "enabled": True if "enabled" not in item else _normalize_bool(item.get("enabled")),
        })

    return {
        "strategy": strategy,
        "request_timeout_seconds": request_timeout_seconds,
        "failure_cooldown_seconds": failure_cooldown_seconds,
        "consecutive_failure_threshold": consecutive_failure_threshold,
        "allow_proxy_fallback": _normalize_bool(
            raw_config.get("allow_proxy_fallback", ALLOWANCE_RELAY_POOL_DEFAULTS["allow_proxy_fallback"])
        ),
        "nodes": normalized_nodes,
    }


def normalize_order_relay_pool_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    route_mode = _normalize_text(raw_config.get("route_mode")).lower()
    if route_mode not in {
        "third_then_relay_then_local",
        "third_party_only",
        "relay_only",
        "local_proxy",
        "relay_then_local",
    }:
        route_mode = ORDER_RELAY_POOL_DEFAULTS["route_mode"]

    third_party_url = _normalize_text(raw_config.get("third_party_url")) or ORDER_RELAY_POOL_DEFAULTS["third_party_url"]
    parsed_third_party_url = urllib.parse.urlparse(third_party_url)
    third_party_host = (parsed_third_party_url.hostname or "").strip().lower()
    if (
        parsed_third_party_url.scheme.lower() != "https"
        or not third_party_host
        or third_party_host in {"localhost", "localhost.localdomain"}
        or third_party_host.startswith("127.")
        or third_party_host == "::1"
    ):
        third_party_url = ORDER_RELAY_POOL_DEFAULTS["third_party_url"]
    else:
        third_party_url = third_party_url.rstrip("/")

    strategy = _normalize_text(raw_config.get("strategy")) or ORDER_RELAY_POOL_DEFAULTS["strategy"]
    if strategy != "healthy_round_robin":
        strategy = ORDER_RELAY_POOL_DEFAULTS["strategy"]

    def normalize_int(key: str, minimum: int, maximum: int) -> int:
        try:
            value = int(raw_config.get(key))
        except (TypeError, ValueError):
            value = int(ORDER_RELAY_POOL_DEFAULTS[key])
        return min(max(value, minimum), maximum)

    def normalize_float(key: str, minimum: float, maximum: float) -> float:
        try:
            value = float(raw_config.get(key))
        except (TypeError, ValueError):
            value = float(ORDER_RELAY_POOL_DEFAULTS[key])
        return min(max(value, minimum), maximum)

    proxy_mode = _normalize_text(raw_config.get("proxy_mode")).lower()
    if proxy_mode not in {"direct", "pool"}:
        proxy_mode = ORDER_RELAY_POOL_DEFAULTS["proxy_mode"]

    raw_nodes = raw_config.get("nodes")
    if not isinstance(raw_nodes, (list, tuple)):
        raw_nodes = []

    nodes: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for index, item in enumerate(raw_nodes):
        if not isinstance(item, dict):
            continue
        url = _normalize_text(item.get("url"))
        if not url:
            continue
        if not re.match(r"^https?://", url, flags=re.IGNORECASE):
            url = f"http://{url}"
        url = url.rstrip("/")
        if url in seen_urls:
            continue
        seen_urls.add(url)
        nodes.append({
            "name": _normalize_text(item.get("name")) or f"订单挂机宝节点 {index + 1}",
            "url": url,
            "secret": _normalize_text(item.get("secret")),
            "enabled": True if "enabled" not in item else _normalize_bool(item.get("enabled")),
        })

    return {
        "route_mode": route_mode,
        "third_party_url": third_party_url,
        "third_party_timeout_seconds": normalize_int("third_party_timeout_seconds", 3, 60),
        "third_party_concurrency_limit": normalize_int("third_party_concurrency_limit", 1, 100),
        "strategy": strategy,
        "request_timeout_seconds": normalize_int("request_timeout_seconds", 3, 60),
        "failure_cooldown_seconds": normalize_int("failure_cooldown_seconds", 30, 86400),
        "consecutive_failure_threshold": normalize_int("consecutive_failure_threshold", 1, 20),
        "proxy_mode": proxy_mode,
        "proxy_retry_count": normalize_int("proxy_retry_count", 0, 3),
        "queue_wait_seconds": normalize_float("queue_wait_seconds", 0.5, 10.0),
        "nodes": nodes,
    }


def normalize_web_user_registration_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    initial_query_count = raw_config.get("initial_query_count", WEB_USER_REGISTRATION_DEFAULTS["initial_query_count"])
    try:
        normalized_initial_query_count = int(initial_query_count)
    except (TypeError, ValueError):
        normalized_initial_query_count = int(WEB_USER_REGISTRATION_DEFAULTS["initial_query_count"])
    normalized_initial_query_count = min(max(normalized_initial_query_count, 0), 1000000)
    return {
        "auto_approve": _normalize_bool(
            raw_config.get("auto_approve", WEB_USER_REGISTRATION_DEFAULTS["auto_approve"])
        ),
        "initial_query_count": normalized_initial_query_count,
    }


def normalize_pushplus_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    account_tier = _normalize_text(raw_config.get("account_tier")).lower()
    if account_tier not in {"standard", "member"}:
        account_tier = PUSHPLUS_DEFAULTS["account_tier"]
    public_base_url = _normalize_text(raw_config.get("public_base_url")).rstrip("/")
    return {
        "enabled": _normalize_bool(raw_config.get("enabled", PUSHPLUS_DEFAULTS["enabled"])),
        "platform_token": _normalize_text(raw_config.get("platform_token")),
        "secret_key": _normalize_text(raw_config.get("secret_key")),
        "app_id": _normalize_text(raw_config.get("app_id")),
        "public_base_url": public_base_url,
        "callback_secret": _normalize_text(raw_config.get("callback_secret")),
        "account_tier": account_tier,
    }


def normalize_order_rankings_v2_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    return {
        "collection_enabled": _normalize_bool(raw_config.get("collection_enabled", False)),
        "public_enabled": _normalize_bool(raw_config.get("public_enabled", False)),
        "rank_text_enabled": _normalize_bool(raw_config.get("rank_text_enabled", False)),
        "source1_enabled": _normalize_bool(raw_config.get("source1_enabled", False)),
        "source1_username": _normalize_text(raw_config.get("source1_username")),
        "source1_password": _normalize_text(raw_config.get("source1_password")),
        "source1_relay_url": _normalize_text(raw_config.get("source1_relay_url")).rstrip("/"),
        "source1_relay_secret": _normalize_text(raw_config.get("source1_relay_secret")),
        # Keep the ranking fallback settings in the canonical system store.
        # These fields are written by the admin API and must survive the
        # store-wide normalization pass on every unrelated settings update.
        "proxy_fallback_enabled": _normalize_bool(raw_config.get("proxy_fallback_enabled", False)),
        "proxy_api_url": _normalize_text(raw_config.get("proxy_api_url")),
        "proxy_validation_cache_seconds": _bounded_int(raw_config.get("proxy_validation_cache_seconds"), 60, 10, 600),
        "proxy_retry_count": _bounded_int(raw_config.get("proxy_retry_count"), 2, 0, 5),
        "window_seconds": _bounded_int(raw_config.get("window_seconds"), 600, 60, 3600),
        "announcement_enabled": _normalize_bool(raw_config.get("announcement_enabled", False)),
        "announcement_title": _normalize_text(raw_config.get("announcement_title")),
        "announcement_body": _normalize_text(raw_config.get("announcement_body")),
        "announcement_image_url": _normalize_text(raw_config.get("announcement_image_url")),
        "announcement_link_url": _normalize_text(raw_config.get("announcement_link_url")),
    }


def normalize_system_settings_store(raw_value: Any) -> dict[str, Any]:
    if not isinstance(raw_value, dict):
        return {
            "prompts_config": {},
            "link_config": {},
            "order_leaderboard_config": {},
            "leaderboard_rules": [],
            "shortlink_config": normalize_shortlink_config({}),
            "backup_schedule_config": normalize_backup_schedule_config({}),
            "allowance_schedule_config": normalize_allowance_schedule_config({}),
            "allowance_relay_pool_config": normalize_allowance_relay_pool_config({}),
            "order_relay_pool_config": normalize_order_relay_pool_config({}),
            "web_user_registration_config": normalize_web_user_registration_config({}),
            "pushplus_config": normalize_pushplus_config({}),
            "order_rankings_v2_config": normalize_order_rankings_v2_config({}),
            "global_leaderboard_config": _normalize_global_leaderboard_config({}),
            "proxy_config": {
                "api_url": "",
                "enable_proxy_pool": True,
            },
        }

    return {
        "prompts_config": _normalize_prompt_sections(raw_value.get("prompts_config")),
        "link_config": _normalize_link_config(raw_value.get("link_config")),
        "order_leaderboard_config": _normalize_order_leaderboard_config(raw_value.get("order_leaderboard_config")),
        "leaderboard_rules": _normalize_leaderboard_rules(raw_value.get("leaderboard_rules")),
        "shortlink_config": normalize_shortlink_config(raw_value.get("shortlink_config")),
        "backup_schedule_config": normalize_backup_schedule_config(raw_value.get("backup_schedule_config")),
        "allowance_schedule_config": normalize_allowance_schedule_config(raw_value.get("allowance_schedule_config")),
        "allowance_relay_pool_config": normalize_allowance_relay_pool_config(raw_value.get("allowance_relay_pool_config")),
        "order_relay_pool_config": normalize_order_relay_pool_config(raw_value.get("order_relay_pool_config")),
        "web_user_registration_config": normalize_web_user_registration_config(raw_value.get("web_user_registration_config")),
        "pushplus_config": normalize_pushplus_config(raw_value.get("pushplus_config")),
        "order_rankings_v2_config": normalize_order_rankings_v2_config(raw_value.get("order_rankings_v2_config")),
        "global_leaderboard_config": _normalize_global_leaderboard_config(raw_value.get("global_leaderboard_config")),
        "proxy_config": {
            "api_url": _normalize_text((raw_value.get("proxy_config") or {}).get("api_url")),
            "enable_proxy_pool": True
            if "enable_proxy_pool" not in (raw_value.get("proxy_config") or {})
            else _normalize_bool((raw_value.get("proxy_config") or {}).get("enable_proxy_pool")),
        },
    }


def _empty_system_settings_store() -> dict[str, Any]:
    return normalize_system_settings_store({})


def ensure_system_settings_store() -> dict[str, Any]:
    store_path = get_system_settings_store_path()
    if store_path.exists():
        return load_system_settings_store()
    return save_system_settings_store(_empty_system_settings_store())


def load_system_settings_store() -> dict[str, Any]:
    store_path = get_system_settings_store_path()
    if not store_path.exists():
        return ensure_system_settings_store()

    try:
        with store_path.open("r", encoding="utf-8") as file:
            raw_value = json.load(file)
    except (json.JSONDecodeError, OSError):
        return _empty_system_settings_store()

    return normalize_system_settings_store(raw_value)


def save_system_settings_store(store_data: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_system_settings_store(store_data)
    store_path = get_system_settings_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.dumps(normalized, ensure_ascii=False, indent=2)
    with NamedTemporaryFile("w", encoding="utf-8", dir=str(store_path.parent), delete=False) as temp_file:
        temp_file.write(payload)
        temp_path = Path(temp_file.name)

    temp_path.replace(store_path)
    return deepcopy(normalized)
