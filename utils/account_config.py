"""Helpers for resolving per-account runtime configuration."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional


SENSITIVE_ACCOUNT_FIELDS = {
    "app_secret",
    "token",
    "encoding_aes_key",
    "zmkey",
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return deepcopy(value) if isinstance(value, dict) else {}


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def merge_account_runtime_config(
    account_id: str,
    account_config: Optional[Dict[str, Any]],
    specific_config: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge base account secrets with business config for message handling."""
    merged = _as_dict(account_config)
    merged_specific = _as_dict(specific_config)

    for field, value in merged_specific.items():
        if field in SENSITIVE_ACCOUNT_FIELDS and _has_text(merged.get(field)):
            continue
        merged[field] = value

    if account_id:
        merged.setdefault("_account_id", account_id)
    return merged


def resolve_message_account_config(msg: Dict[str, Any]) -> Dict[str, Any]:
    """Return a merged config for a parsed WeChat message."""
    from config.config import ACCOUNT_SPECIFIC_CONFIGS, get_wechat_account

    account_id = str(msg.get("ToUserName") or "").strip()
    return merge_account_runtime_config(
        account_id,
        get_wechat_account(account_id) or {},
        ACCOUNT_SPECIFIC_CONFIGS.get(account_id, {}),
    )


def resolve_zmkey(msg: Dict[str, Any], account_config: Optional[Dict[str, Any]] = None) -> str:
    """Resolve zmkey without logging or exposing the value."""
    from config.config import ACCOUNT_SPECIFIC_CONFIGS, get_wechat_account

    account_id = str(msg.get("ToUserName") or "").strip()
    candidates = [
        account_config if isinstance(account_config, dict) else None,
        msg.get("_account_config") if isinstance(msg.get("_account_config"), dict) else None,
        get_wechat_account(account_id) or {},
        ACCOUNT_SPECIFIC_CONFIGS.get(account_id, {}),
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        zmkey = str(candidate.get("zmkey") or "").strip()
        if zmkey:
            return zmkey
    return ""


def has_zmkey(msg: Dict[str, Any], account_config: Optional[Dict[str, Any]] = None) -> bool:
    return bool(resolve_zmkey(msg, account_config))
