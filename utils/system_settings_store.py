from __future__ import annotations

import json
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


def get_system_settings_store_path() -> Path:
    return resolve_runtime_data_path(SYSTEM_SETTINGS_FILENAME)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").strip()


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
            "leaderboard_url": _normalize_text(section_value.get("leaderboard_url")),
            "timezone": _normalize_text(section_value.get("timezone")),
        }
        if any(
            section_result[key]
            for key in ("trigger_keyword", "times", "keywords", "leaderboard_url", "timezone")
        ):
            normalized[section_key] = section_result

    return normalized


def normalize_system_settings_store(raw_value: Any) -> dict[str, Any]:
    if not isinstance(raw_value, dict):
        return {
            "prompts_config": {},
            "link_config": {},
            "order_leaderboard_config": {},
            "shortlink_config": normalize_shortlink_config({}),
            "proxy_config": {
                "api_url": "",
            },
        }

    return {
        "prompts_config": _normalize_prompt_sections(raw_value.get("prompts_config")),
        "link_config": _normalize_link_config(raw_value.get("link_config")),
        "order_leaderboard_config": _normalize_order_leaderboard_config(raw_value.get("order_leaderboard_config")),
        "shortlink_config": normalize_shortlink_config(raw_value.get("shortlink_config")),
        "proxy_config": {
            "api_url": _normalize_text((raw_value.get("proxy_config") or {}).get("api_url")),
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
