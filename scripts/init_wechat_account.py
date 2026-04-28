from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ACCOUNT_STORE_FILENAME = "wechat_accounts.runtime.json"
ACCOUNT_FIELDS = ("name", "appid", "app_secret", "token", "encoding_aes_key", "zmkey")
ACCOUNT_SPECIFIC_TEXT_FIELDS = (
    "welcome_message",
    "default_reply",
    "meituan_base_url",
    "meituan_official_cashback_url",
    "url_mode",
    "default_code_duration",
)
ACCOUNT_SPECIFIC_LIST_FIELDS = ("enabled_text_processors", "enabled_miniprogram_appids", "authorized_users")


def _get_data_dir(value: str) -> Path:
    normalized = str(value or "").strip()
    if normalized:
        return Path(normalized).expanduser().resolve()
    env_value = os.getenv("WX_SERVICE_DATA_DIR", "").strip()
    if env_value:
        return Path(env_value).expanduser().resolve()
    return PROJECT_ROOT / "runtime-data"


def _get_store_path(data_dir: Path) -> Path:
    return data_dir / ACCOUNT_STORE_FILENAME


def _normalize_required(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} 不能为空")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError(f"{label} 不能包含换行")
    return normalized


def _normalize_optional(value: str) -> str:
    normalized = str(value or "").strip()
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("单行字段不能包含换行")
    return normalized


def _split_lines(value: str) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for item in str(value or "").replace(",", "\n").splitlines():
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


def _normalize_account_config(account_config: Any) -> dict[str, str]:
    if not isinstance(account_config, dict):
        return {}
    normalized: dict[str, str] = {}
    for field in ACCOUNT_FIELDS:
        if field in account_config:
            normalized[field] = str(account_config.get(field) or "").strip()
    return normalized


def _normalize_account_specific_config(account_config: Any) -> dict[str, Any]:
    if not isinstance(account_config, dict):
        return {}
    normalized: dict[str, Any] = {}
    for field in ACCOUNT_SPECIFIC_TEXT_FIELDS:
        if field in account_config:
            normalized[field] = str(account_config.get(field) or "").strip()
    for field in ACCOUNT_SPECIFIC_LIST_FIELDS:
        if field in account_config:
            normalized[field] = _split_lines("\n".join(str(item) for item in account_config.get(field) or []))
    return normalized


def _empty_store() -> dict[str, Any]:
    return {"default_account_id": "", "accounts": {}, "account_specific_configs": {}, "keyword_responses": {}}


def _load_store(store_path: Path) -> dict[str, Any]:
    if not store_path.exists():
        return _empty_store()
    with store_path.open("r", encoding="utf-8") as f:
        raw_data = json.load(f)
    if not isinstance(raw_data, dict):
        return _empty_store()
    store_data = _empty_store()
    if isinstance(raw_data.get("accounts"), dict):
        for raw_account_id, raw_config in raw_data["accounts"].items():
            account_id = str(raw_account_id or "").strip()
            account_config = _normalize_account_config(raw_config)
            if account_id and account_config:
                store_data["accounts"][account_id] = account_config
    if isinstance(raw_data.get("account_specific_configs"), dict):
        for raw_account_id, raw_config in raw_data["account_specific_configs"].items():
            account_id = str(raw_account_id or "").strip()
            account_config = _normalize_account_specific_config(raw_config)
            if account_id and account_config:
                store_data["account_specific_configs"][account_id] = account_config
    if isinstance(raw_data.get("keyword_responses"), dict):
        store_data["keyword_responses"] = raw_data["keyword_responses"]
    default_account_id = str(raw_data.get("default_account_id") or "").strip()
    if default_account_id in store_data["accounts"]:
        store_data["default_account_id"] = default_account_id
    return store_data


def _save_store(store_path: Path, store_data: dict[str, Any]) -> dict[str, Any]:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    if store_data.get("default_account_id") not in store_data.get("accounts", {}):
        store_data["default_account_id"] = next(iter(store_data.get("accounts", {}).keys()), "")
    temp_path = store_path.with_name(f".{store_path.name}.tmp")
    temp_path.write_text(json.dumps(store_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, store_path)
    return store_data


def _backup_store_file(store_path: Path) -> Path | None:
    if not store_path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = PROJECT_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"wechat_accounts.runtime.{timestamp}.json"
    shutil.copy2(store_path, backup_path)
    return backup_path


def _build_account_config(args: argparse.Namespace) -> dict[str, str]:
    return {
        "name": _normalize_optional(args.name),
        "appid": _normalize_required(args.appid, "AppID"),
        "app_secret": _normalize_optional(args.app_secret),
        "token": _normalize_optional(args.token),
        "encoding_aes_key": _normalize_optional(args.encoding_aes_key),
        "zmkey": _normalize_optional(args.zmkey),
    }


def _build_specific_config(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "welcome_message": str(args.welcome_message or "").strip(),
        "default_reply": str(args.default_reply or "").strip(),
        "enabled_text_processors": _split_lines(args.enabled_text_processors),
        "enabled_miniprogram_appids": _split_lines(args.enabled_miniprogram_appids),
        "meituan_base_url": _normalize_optional(args.meituan_base_url),
        "meituan_official_cashback_url": _normalize_optional(args.meituan_official_cashback_url),
        "url_mode": _normalize_optional(args.url_mode) or "all",
        "authorized_users": _split_lines(args.authorized_users),
        "default_code_duration": _normalize_optional(args.default_code_duration),
    }


def _upsert_wechat_account(store_data: dict[str, Any], account_id: str, args: argparse.Namespace) -> dict[str, Any]:
    store_data.setdefault("accounts", {})[account_id] = _normalize_account_config(_build_account_config(args))
    specific_config = _normalize_account_specific_config(_build_specific_config(args))
    if specific_config:
        store_data.setdefault("account_specific_configs", {})[account_id] = specific_config
    else:
        store_data.setdefault("account_specific_configs", {}).pop(account_id, None)
    if args.set_default or not store_data.get("default_account_id"):
        store_data["default_account_id"] = account_id
    return store_data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--appid", required=True)
    parser.add_argument("--app-secret", default="")
    parser.add_argument("--token", default="")
    parser.add_argument("--encoding-aes-key", default="")
    parser.add_argument("--zmkey", default="")
    parser.add_argument("--set-default", action="store_true")
    parser.add_argument("--welcome-message", default="")
    parser.add_argument("--default-reply", default="")
    parser.add_argument("--enabled-text-processors", default="")
    parser.add_argument("--enabled-miniprogram-appids", default="")
    parser.add_argument("--meituan-base-url", default="")
    parser.add_argument("--meituan-official-cashback-url", default="")
    parser.add_argument("--url-mode", choices=["all", "meituan", "dianping"], default="all")
    parser.add_argument("--authorized-users", default="")
    parser.add_argument("--default-code-duration", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        account_id = _normalize_required(args.account_id, "公众号原始ID")
        data_dir = _get_data_dir(args.data_dir)
        store_path = _get_store_path(data_dir)
        store_path.parent.mkdir(parents=True, exist_ok=True)
        before_store = _load_store(store_path)
        existed = account_id in before_store.get("accounts", {})
        backup_path = _backup_store_file(store_path)
        store_data = _upsert_wechat_account(before_store, account_id, args)
        store_data = _save_store(store_path, store_data)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"INIT_WECHAT_ACCOUNT_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    payload = {
        "ok": True,
        "action": "updated_account" if existed else "created_account",
        "account_id": account_id,
        "store_path": str(store_path),
        "backup_path": str(backup_path) if backup_path is not None else "",
        "default_account_id": store_data.get("default_account_id", ""),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"INIT_WECHAT_ACCOUNT_OK action={payload['action']} account_id={account_id} store_path={store_path}")
        print(f"INIT_WECHAT_ACCOUNT_DEFAULT {payload['default_account_id']}")
        if backup_path is not None:
            print(f"INIT_WECHAT_ACCOUNT_BACKUP {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
