from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

ACCOUNT_STORE_FILENAME = "wechat_accounts.runtime.json"
ACCOUNT_FIELDS = (
    "name",
    "appid",
    "app_secret",
    "token",
    "encoding_aes_key",
    "zmkey",
)
ACCOUNT_SPECIFIC_TEXT_FIELDS = (
    "welcome_message",
    "default_reply",
    "meituan_base_url",
    "meituan_official_cashback_url",
    "url_mode",
    "default_code_duration",
)
ACCOUNT_SPECIFIC_LIST_FIELDS = (
    "enabled_text_processors",
    "enabled_miniprogram_appids",
    "authorized_users",
)


def _get_project_root() -> Path:
    custom_root = os.getenv("WX_SERVICE_ROOT", "").strip()
    if custom_root:
        return Path(custom_root).expanduser().resolve()
    return Path(__file__).resolve().parent


def _get_runtime_data_dir() -> Path:
    custom_dir = os.getenv("WX_SERVICE_DATA_DIR", "").strip()
    if custom_dir:
        path = Path(custom_dir).expanduser().resolve()
    elif os.getenv("STATE_DIRECTORY", "").strip():
        path = Path(os.getenv("STATE_DIRECTORY", "").strip()).expanduser().resolve()
    else:
        path = _get_project_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_wechat_account_store_path() -> Path:
    return _get_runtime_data_dir() / ACCOUNT_STORE_FILENAME


def _normalize_account_id(account_id: Any) -> str:
    return str(account_id or "").strip()


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = value.splitlines()
    elif isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        return []

    normalized_values: list[str] = []
    seen: set[str] = set()
    for item in values:
        normalized_item = _normalize_text(item)
        if not normalized_item or normalized_item in seen:
            continue
        seen.add(normalized_item)
        normalized_values.append(normalized_item)
    return normalized_values


def normalize_account_config(account_config: Any) -> dict[str, str]:
    if not isinstance(account_config, dict):
        return {}
    normalized: dict[str, str] = {}
    for field in ACCOUNT_FIELDS:
        if field in account_config:
            normalized[field] = _normalize_text(account_config.get(field))
    return normalized


def normalize_account_specific_config(account_config: Any) -> dict[str, Any]:
    if not isinstance(account_config, dict):
        return {}
    normalized: dict[str, Any] = {}
    for field in ACCOUNT_SPECIFIC_TEXT_FIELDS:
        if field in account_config:
            normalized[field] = _normalize_text(account_config.get(field))
    for field in ACCOUNT_SPECIFIC_LIST_FIELDS:
        if field in account_config:
            normalized[field] = _normalize_text_list(account_config.get(field))
    return normalized


def _normalize_keyword_response_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_text(value)
    if isinstance(value, (dict, list)):
        return deepcopy(value)
    return None


def normalize_keyword_responses(keyword_responses: Any) -> dict[str, Any]:
    if not isinstance(keyword_responses, dict):
        return {}

    normalized: dict[str, Any] = {}
    for raw_keyword, raw_response in keyword_responses.items():
        keyword = _normalize_text(raw_keyword)
        if not keyword:
            continue

        normalized_response = _normalize_keyword_response_value(raw_response)
        if normalized_response in (None, ""):
            continue

        normalized[keyword] = normalized_response

    return normalized


def normalize_account_keyword_responses(keyword_responses: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(keyword_responses, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for raw_account_id, raw_account_keyword_responses in keyword_responses.items():
        account_id = _normalize_account_id(raw_account_id)
        if not account_id:
            continue

        normalized_responses = normalize_keyword_responses(raw_account_keyword_responses)
        if normalized_responses:
            normalized[account_id] = normalized_responses

    return normalized


def _resolve_default_account_id(
    accounts: dict[str, dict[str, Any]],
    default_wechat_config: dict[str, Any] | None = None,
) -> str:
    normalized_default = normalize_account_config(default_wechat_config or {})
    if normalized_default:
        for account_id, account_config in accounts.items():
            matched = True
            for field in ("appid", "token", "encoding_aes_key"):
                expected = normalized_default.get(field, "")
                if expected and _normalize_text(account_config.get(field)) != expected:
                    matched = False
                    break
            if matched:
                return account_id
    return next(iter(accounts.keys()), "")


def load_wechat_account_store() -> dict[str, Any]:
    store_path = get_wechat_account_store_path()
    if not store_path.exists():
        return {"default_account_id": "", "accounts": {}, "account_specific_configs": {}, "keyword_responses": {}}

    try:
        with store_path.open("r", encoding="utf-8") as f:
            raw_data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {"default_account_id": "", "accounts": {}, "account_specific_configs": {}, "keyword_responses": {}}

    raw_accounts = raw_data.get("accounts", {})
    accounts: dict[str, dict[str, str]] = {}
    if isinstance(raw_accounts, dict):
        for raw_account_id, raw_account_config in raw_accounts.items():
            account_id = _normalize_account_id(raw_account_id)
            if not account_id:
                continue
            normalized_config = normalize_account_config(raw_account_config)
            if normalized_config:
                accounts[account_id] = normalized_config

    raw_account_specific_configs = raw_data.get("account_specific_configs", {})
    account_specific_configs: dict[str, dict[str, Any]] = {}
    if isinstance(raw_account_specific_configs, dict):
        for raw_account_id, raw_account_config in raw_account_specific_configs.items():
            account_id = _normalize_account_id(raw_account_id)
            if not account_id:
                continue
            normalized_config = normalize_account_specific_config(raw_account_config)
            if normalized_config:
                account_specific_configs[account_id] = normalized_config

    keyword_responses = normalize_account_keyword_responses(raw_data.get("keyword_responses", {}))

    return {
        "default_account_id": _normalize_account_id(raw_data.get("default_account_id")),
        "accounts": accounts,
        "account_specific_configs": account_specific_configs,
        "keyword_responses": keyword_responses,
    }


def save_wechat_account_store(store_data: dict[str, Any]) -> dict[str, Any]:
    normalized_accounts: dict[str, dict[str, str]] = {}
    for raw_account_id, raw_account_config in dict(store_data.get("accounts", {})).items():
        account_id = _normalize_account_id(raw_account_id)
        if not account_id:
            continue
        normalized_config = normalize_account_config(raw_account_config)
        if normalized_config:
            normalized_accounts[account_id] = normalized_config

    default_account_id = _normalize_account_id(store_data.get("default_account_id"))
    if default_account_id and default_account_id not in normalized_accounts:
        default_account_id = ""

    normalized_account_specific_configs: dict[str, dict[str, Any]] = {}
    for raw_account_id, raw_account_config in dict(store_data.get("account_specific_configs", {})).items():
        account_id = _normalize_account_id(raw_account_id)
        if not account_id:
            continue
        normalized_config = normalize_account_specific_config(raw_account_config)
        if normalized_config:
            normalized_account_specific_configs[account_id] = normalized_config

    normalized_keyword_responses = normalize_account_keyword_responses(store_data.get("keyword_responses", {}))

    normalized_store = {
        "default_account_id": default_account_id,
        "accounts": normalized_accounts,
        "account_specific_configs": normalized_account_specific_configs,
        "keyword_responses": normalized_keyword_responses,
    }

    store_path = get_wechat_account_store_path()
    temp_path: Path | None = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=str(store_path.parent), delete=False) as temp_file:
            json.dump(normalized_store, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)
        os.replace(temp_path, store_path)
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    return normalized_store


def upsert_wechat_account(account_id: str, account_config: dict[str, Any], set_default: bool = False) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    if not normalized_account_id:
        raise ValueError("account_id 不能为空")

    normalized_config = normalize_account_config(account_config)
    if not normalized_config:
        raise ValueError("公众号配置不能为空")

    store_data = load_wechat_account_store()
    store_data["accounts"][normalized_account_id] = normalized_config

    if set_default or (not store_data.get("default_account_id") and len(store_data["accounts"]) == 1):
        store_data["default_account_id"] = normalized_account_id

    return save_wechat_account_store(store_data)


def upsert_wechat_account_specific_config(account_id: str, account_config: dict[str, Any]) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    if not normalized_account_id:
        raise ValueError("account_id 不能为空")

    normalized_config = normalize_account_specific_config(account_config)
    store_data = load_wechat_account_store()
    if normalized_config:
        store_data.setdefault("account_specific_configs", {})[normalized_account_id] = normalized_config
    else:
        store_data.setdefault("account_specific_configs", {}).pop(normalized_account_id, None)
    return save_wechat_account_store(store_data)


def upsert_wechat_account_keyword_responses(account_id: str, keyword_responses: dict[str, Any]) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    if not normalized_account_id:
        raise ValueError("account_id 不能为空")

    normalized_keyword_responses = normalize_keyword_responses(keyword_responses)
    store_data = load_wechat_account_store()
    if normalized_keyword_responses:
        store_data.setdefault("keyword_responses", {})[normalized_account_id] = normalized_keyword_responses
    else:
        store_data.setdefault("keyword_responses", {}).pop(normalized_account_id, None)
    return save_wechat_account_store(store_data)


def delete_wechat_account(account_id: str) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    store_data = load_wechat_account_store()
    store_data["accounts"].pop(normalized_account_id, None)
    store_data.setdefault("account_specific_configs", {}).pop(normalized_account_id, None)
    store_data.setdefault("keyword_responses", {}).pop(normalized_account_id, None)

    if store_data.get("default_account_id") == normalized_account_id:
        store_data["default_account_id"] = next(iter(store_data["accounts"].keys()), "")

    return save_wechat_account_store(store_data)


def set_default_wechat_account(account_id: str) -> dict[str, Any]:
    normalized_account_id = _normalize_account_id(account_id)
    store_data = load_wechat_account_store()
    if normalized_account_id and normalized_account_id in store_data["accounts"]:
        store_data["default_account_id"] = normalized_account_id
    return save_wechat_account_store(store_data)


def bootstrap_wechat_account_store_from_config(config: dict[str, Any]) -> dict[str, Any]:
    store_data = load_wechat_account_store()
    store_updated = False

    raw_accounts = config.get("wechat_accounts", {})
    if isinstance(raw_accounts, dict):
        for raw_account_id, raw_account_config in raw_accounts.items():
            account_id = _normalize_account_id(raw_account_id)
            if not account_id or account_id in store_data["accounts"]:
                continue
            normalized_config = normalize_account_config(raw_account_config)
            if normalized_config:
                store_data["accounts"][account_id] = normalized_config
                store_updated = True

    if not store_data.get("default_account_id") and store_data["accounts"]:
        store_data["default_account_id"] = _resolve_default_account_id(
            store_data["accounts"],
            config.get("default_wechat_config", {}),
        )
        store_updated = True

    raw_account_specific_configs = config.get("account_specific_configs", {})
    if isinstance(raw_account_specific_configs, dict):
        for raw_account_id, raw_account_config in raw_account_specific_configs.items():
            account_id = _normalize_account_id(raw_account_id)
            if not account_id or account_id in store_data.get("account_specific_configs", {}):
                continue
            normalized_config = normalize_account_specific_config(raw_account_config)
            if normalized_config:
                store_data.setdefault("account_specific_configs", {})[account_id] = normalized_config
                store_updated = True

    if store_updated:
        return save_wechat_account_store(store_data)
    return store_data


def merge_wechat_account_config(config: dict[str, Any]) -> dict[str, Any]:
    merged_config = deepcopy(config)
    store_data = bootstrap_wechat_account_store_from_config(merged_config)

    merged_accounts: dict[str, dict[str, Any]] = {}
    for account_id, account_config in store_data["accounts"].items():
        merged_accounts[account_id] = deepcopy(account_config)

    merged_config["wechat_accounts"] = merged_accounts

    raw_default_config = merged_config.get("default_wechat_config", {})
    default_wechat_config = deepcopy(raw_default_config) if isinstance(raw_default_config, dict) else {}
    default_account_id = _normalize_account_id(store_data.get("default_account_id"))

    if default_account_id and default_account_id in merged_accounts:
        default_wechat_config.update(deepcopy(merged_accounts[default_account_id]))
    elif not default_wechat_config and merged_accounts:
        first_account_config = next(iter(merged_accounts.values()))
        default_wechat_config.update(deepcopy(first_account_config))

    raw_account_specific_configs = merged_config.get("account_specific_configs", {})
    merged_account_specific_configs = deepcopy(raw_account_specific_configs) if isinstance(raw_account_specific_configs, dict) else {}
    for account_id, account_config in store_data.get("account_specific_configs", {}).items():
        current_config = merged_account_specific_configs.get(account_id, {})
        if not isinstance(current_config, dict):
            current_config = {}
        current_config.update(deepcopy(account_config))
        merged_account_specific_configs[account_id] = current_config

    merged_config["account_specific_configs"] = merged_account_specific_configs
    merged_config["default_wechat_config"] = default_wechat_config
    return merged_config
