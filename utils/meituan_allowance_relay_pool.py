from __future__ import annotations

import threading
import time
from typing import Any

from utils.system_settings_store import (
    load_system_settings_store,
    normalize_allowance_relay_pool_config,
)

_runtime_lock = threading.Lock()
_round_robin_cursor = 0
_relay_runtime: dict[str, dict[str, Any]] = {}


def _now_ts() -> int:
    return int(time.time())


def _node_key(node: dict[str, Any]) -> str:
    return str(node.get("url") or "").strip()


def _mask_secret(secret: str) -> str:
    text = str(secret or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}***{text[-2:]}"


def _load_store_config() -> dict[str, Any]:
    store = load_system_settings_store()
    return normalize_allowance_relay_pool_config(store.get("allowance_relay_pool_config"))


def get_allowance_relay_pool_config() -> dict[str, Any]:
    return _load_store_config()

def get_allowance_relay_pool_nodes() -> list[dict[str, Any]]:
    config = _load_store_config()
    nodes: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in config.get("nodes") or []:
        if not isinstance(item, dict):
            continue
        node = {
            "name": str(item.get("name") or "").strip(),
            "url": str(item.get("url") or "").strip().rstrip("/"),
            "secret": str(item.get("secret") or "").strip(),
            "enabled": bool(item.get("enabled")),
            "source": "settings",
        }
        if not node["url"] or node["url"] in seen_urls:
            continue
        seen_urls.add(node["url"])
        nodes.append(node)

    return nodes


def pick_allowance_relay_node() -> tuple[dict[str, Any], dict[str, Any] | None]:
    config = _load_store_config()
    enabled_nodes = [node for node in get_allowance_relay_pool_nodes() if bool(node.get("enabled"))]
    if not enabled_nodes:
        return config, None

    strategy = str(config.get("strategy") or "round_robin").strip() or "round_robin"
    timeout_seconds = int(config.get("request_timeout_seconds") or 15)
    for node in enabled_nodes:
        node["timeout_seconds"] = timeout_seconds

    if strategy != "round_robin" or len(enabled_nodes) <= 1:
        return config, enabled_nodes[0]

    global _round_robin_cursor
    with _runtime_lock:
        start_index = _round_robin_cursor % len(enabled_nodes)
        _round_robin_cursor = (_round_robin_cursor + 1) % len(enabled_nodes)

    return config, enabled_nodes[start_index]


def report_allowance_relay_result(node: dict[str, Any], *, success: bool, error: Any = None) -> None:
    key = _node_key(node)
    if not key:
        return
    now_ts = _now_ts()
    with _runtime_lock:
        state = _relay_runtime.setdefault(key, {
            "success_count": 0,
            "failure_count": 0,
            "last_used_at": 0,
            "last_success_at": 0,
            "last_failure_at": 0,
            "last_error": "",
        })
        state["last_used_at"] = now_ts
        if success:
            state["success_count"] = int(state.get("success_count") or 0) + 1
            state["last_success_at"] = now_ts
            state["last_error"] = ""
        else:
            state["failure_count"] = int(state.get("failure_count") or 0) + 1
            state["last_failure_at"] = now_ts
            state["last_error"] = str(error or "").strip()[:200]


def get_allowance_relay_pool_runtime() -> dict[str, Any]:
    config = _load_store_config()
    nodes = get_allowance_relay_pool_nodes()
    enabled_count = sum(1 for item in nodes if item.get("enabled"))
    with _runtime_lock:
        runtime_snapshot = {key: dict(value) for key, value in _relay_runtime.items()}
        next_index = _round_robin_cursor

    payload_nodes: list[dict[str, Any]] = []
    for index, node in enumerate(nodes):
        key = _node_key(node)
        runtime = runtime_snapshot.get(key) or {}
        payload_nodes.append({
            "index": index,
            "name": str(node.get("name") or "").strip(),
            "url": str(node.get("url") or "").strip(),
            "secret_masked": _mask_secret(str(node.get("secret") or "").strip()),
            "enabled": bool(node.get("enabled")),
            "source": str(node.get("source") or "settings"),
            "success_count": int(runtime.get("success_count") or 0),
            "failure_count": int(runtime.get("failure_count") or 0),
            "last_used_at": int(runtime.get("last_used_at") or 0),
            "last_success_at": int(runtime.get("last_success_at") or 0),
            "last_failure_at": int(runtime.get("last_failure_at") or 0),
            "last_error": str(runtime.get("last_error") or ""),
        })

    return {
        "config": config,
        "strategy": str(config.get("strategy") or "round_robin"),
        "request_timeout_seconds": int(config.get("request_timeout_seconds") or 15),
        "total_count": len(nodes),
        "enabled_count": enabled_count,
        "next_index": next_index if enabled_count > 0 else 0,
        "nodes": payload_nodes,
    }
