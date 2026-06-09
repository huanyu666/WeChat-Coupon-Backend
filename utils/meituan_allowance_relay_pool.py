from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from utils import http_client
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


def _default_runtime_state() -> dict[str, Any]:
    return {
        "success_count": 0,
        "failure_count": 0,
        "consecutive_failures": 0,
        "last_used_at": 0,
        "last_success_at": 0,
        "last_failure_at": 0,
        "last_error": "",
        "cooldown_until": 0,
        "last_probe_at": 0,
        "last_probe_ok": None,
        "last_probe_status_code": 0,
        "last_probe_error": "",
        "last_probe_secret_enabled": None,
    }


def _normalize_base_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    path = parts.path or ""
    if path.endswith("/relay/meituan/allowance"):
        path = path[: -len("/relay/meituan/allowance")]
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def _ensure_runtime_state(key: str) -> dict[str, Any]:
    state = _relay_runtime.get(key)
    if isinstance(state, dict):
        for field, value in _default_runtime_state().items():
            state.setdefault(field, value)
        return state
    state = _default_runtime_state()
    _relay_runtime[key] = state
    return state


def _resolve_node_state(node: dict[str, Any], runtime: dict[str, Any], *, now_ts: int | None = None) -> str:
    if not bool(node.get("enabled")):
        return "disabled"
    now_value = int(now_ts or _now_ts())
    cooldown_until = int(runtime.get("cooldown_until") or 0)
    if cooldown_until > now_value:
        return "cooling"
    return "healthy"


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


def list_allowance_relay_candidates() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    global _round_robin_cursor
    config = _load_store_config()
    enabled_nodes = [node for node in get_allowance_relay_pool_nodes() if bool(node.get("enabled"))]
    if not enabled_nodes:
        return config, []

    strategy = str(config.get("strategy") or "healthy_round_robin").strip() or "healthy_round_robin"
    timeout_seconds = int(config.get("request_timeout_seconds") or 15)
    cooldown_seconds = int(config.get("failure_cooldown_seconds") or 300)
    failure_threshold = int(config.get("consecutive_failure_threshold") or 2)
    for node in enabled_nodes:
        node["timeout_seconds"] = timeout_seconds
        node["failure_cooldown_seconds"] = cooldown_seconds
        node["consecutive_failure_threshold"] = failure_threshold

    if len(enabled_nodes) <= 1:
        return config, enabled_nodes

    now_ts = _now_ts()
    with _runtime_lock:
        for node in enabled_nodes:
            _ensure_runtime_state(_node_key(node))
        healthy_nodes = [
            node
            for node in enabled_nodes
            if _resolve_node_state(node, _ensure_runtime_state(_node_key(node)), now_ts=now_ts) == "healthy"
        ]
        candidate_pool = healthy_nodes or enabled_nodes
        if strategy != "healthy_round_robin":
            ordered_nodes = list(candidate_pool)
        else:
            start_index = _round_robin_cursor % len(candidate_pool)
            ordered_nodes = candidate_pool[start_index:] + candidate_pool[:start_index]
            _round_robin_cursor = (_round_robin_cursor + 1) % len(candidate_pool)

    return config, ordered_nodes


def pick_allowance_relay_node() -> tuple[dict[str, Any], dict[str, Any] | None]:
    config, candidates = list_allowance_relay_candidates()
    if not candidates:
        return config, None
    return config, candidates[0]


def reset_allowance_relay_runtime(url: str) -> bool:
    key = str(url or "").strip().rstrip("/")
    if not key:
        return False
    global _round_robin_cursor
    with _runtime_lock:
        if key in _relay_runtime:
            _relay_runtime[key] = _default_runtime_state()
            return True
    return False


def record_allowance_relay_probe_result(
    url: str,
    *,
    ok: bool,
    status_code: int = 0,
    error: Any = None,
    secret_enabled: bool | None = None,
) -> dict[str, Any] | None:
    key = str(url or "").strip().rstrip("/")
    if not key:
        return None
    now_ts = _now_ts()
    with _runtime_lock:
        state = _ensure_runtime_state(key)
        state["last_probe_at"] = now_ts
        state["last_probe_ok"] = bool(ok)
        state["last_probe_status_code"] = int(status_code or 0)
        state["last_probe_error"] = str(error or "").strip()[:200]
        if secret_enabled is None:
            state["last_probe_secret_enabled"] = None
        else:
            state["last_probe_secret_enabled"] = bool(secret_enabled)
        return dict(state)


async def probe_allowance_relay_node(
    node: dict[str, Any],
    *,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    relay_url = str((node or {}).get("url") or "").strip().rstrip("/")
    if not relay_url:
        result = {
            "ok": False,
            "message": "节点地址为空",
            "status_code": 0,
            "healthz_ok": False,
            "relay_auth_ok": False,
            "secret_enabled": None,
        }
        record_allowance_relay_probe_result("", ok=False, error=result["message"])
        return result

    base_url = _normalize_base_url(relay_url)
    if not base_url:
        result = {
            "ok": False,
            "message": "节点地址格式无效",
            "status_code": 0,
            "healthz_ok": False,
            "relay_auth_ok": False,
            "secret_enabled": None,
        }
        record_allowance_relay_probe_result(relay_url, ok=False, error=result["message"])
        return result

    timeout_value = max(3, int(timeout_seconds or node.get("timeout_seconds") or 15))
    secret = str((node or {}).get("secret") or "").strip()
    healthz_url = f"{base_url}/healthz"
    probe_url = f"{base_url}/relay/meituan/allowance"
    secret_enabled: bool | None = None

    try:
        healthz_response = await http_client.get(healthz_url, timeout=timeout_value)
        healthz_status = int(getattr(healthz_response, "status_code", 0) or 0)
        healthz_payload = healthz_response.json()
    except Exception as exc:
        message = f"healthz 不可用: {exc}"
        record_allowance_relay_probe_result(relay_url, ok=False, status_code=0, error=message)
        return {
            "ok": False,
            "message": message,
            "status_code": 0,
            "healthz_ok": False,
            "relay_auth_ok": False,
            "secret_enabled": None,
        }

    if healthz_status != 200 or not isinstance(healthz_payload, dict) or not bool(healthz_payload.get("ok")):
        message = f"healthz 异常: status={healthz_status}"
        record_allowance_relay_probe_result(
            relay_url,
            ok=False,
            status_code=healthz_status,
            error=message,
            secret_enabled=bool(healthz_payload.get("secret_enabled")) if isinstance(healthz_payload, dict) else None,
        )
        return {
            "ok": False,
            "message": message,
            "status_code": healthz_status,
            "healthz_ok": False,
            "relay_auth_ok": False,
            "secret_enabled": bool(healthz_payload.get("secret_enabled")) if isinstance(healthz_payload, dict) else None,
        }

    secret_enabled = bool(healthz_payload.get("secret_enabled")) if isinstance(healthz_payload, dict) else None
    probe_headers = {"Content-Type": "application/json"}
    if secret:
        probe_headers["X-Allowance-Relay-Secret"] = secret

    try:
        relay_response = await http_client.post(
            probe_url,
            timeout=timeout_value,
            headers=probe_headers,
            json={
                "endpoint": "https://example.invalid/not-supported",
                "params": {},
                "headers": {},
            },
        )
        relay_status = int(getattr(relay_response, "status_code", 0) or 0)
        relay_payload = relay_response.json() if getattr(relay_response, "content", b"") else {}
    except Exception as exc:
        message = f"relay 探测失败: {exc}"
        record_allowance_relay_probe_result(
            relay_url,
            ok=False,
            status_code=0,
            error=message,
            secret_enabled=secret_enabled,
        )
        return {
            "ok": False,
            "message": message,
            "status_code": 0,
            "healthz_ok": True,
            "relay_auth_ok": False,
            "secret_enabled": secret_enabled,
        }

    detail_text = ""
    if isinstance(relay_payload, dict):
        detail_text = str(relay_payload.get("detail") or relay_payload.get("error") or "").strip()

    if relay_status == 401:
        message = "Relay 密钥校验失败，请检查后台 Secret 是否和挂机宝一致"
        record_allowance_relay_probe_result(
            relay_url,
            ok=False,
            status_code=relay_status,
            error=message,
            secret_enabled=secret_enabled,
        )
        return {
            "ok": False,
            "message": message,
            "status_code": relay_status,
            "healthz_ok": True,
            "relay_auth_ok": False,
            "secret_enabled": secret_enabled,
        }

    if relay_status not in {200, 400}:
        message = f"relay 探测异常: status={relay_status}"
        if detail_text:
            message = f"{message} {detail_text}"
        record_allowance_relay_probe_result(
            relay_url,
            ok=False,
            status_code=relay_status,
            error=message,
            secret_enabled=secret_enabled,
        )
        return {
            "ok": False,
            "message": message,
            "status_code": relay_status,
            "healthz_ok": True,
            "relay_auth_ok": False,
            "secret_enabled": secret_enabled,
        }

    message = "healthz 正常，Relay 探测通过"
    if secret_enabled and not secret:
        message = "healthz 正常，但节点启用了密钥，后台当前未填写 Secret"
        record_allowance_relay_probe_result(
            relay_url,
            ok=False,
            status_code=relay_status,
            error=message,
            secret_enabled=secret_enabled,
        )
        return {
            "ok": False,
            "message": message,
            "status_code": relay_status,
            "healthz_ok": True,
            "relay_auth_ok": False,
            "secret_enabled": secret_enabled,
        }

    record_allowance_relay_probe_result(
        relay_url,
        ok=True,
        status_code=relay_status,
        error="",
        secret_enabled=secret_enabled,
    )
    return {
        "ok": True,
        "message": message,
        "status_code": relay_status,
        "healthz_ok": True,
        "relay_auth_ok": True,
        "secret_enabled": secret_enabled,
    }


def get_allowance_relay_runtime_node(url: str) -> dict[str, Any] | None:
    key = str(url or "").strip().rstrip("/")
    if not key:
        return None
    with _runtime_lock:
        state = _relay_runtime.get(key)
        if not isinstance(state, dict):
            return None
        return dict(state)


def report_allowance_relay_result(node: dict[str, Any], *, success: bool, error: Any = None) -> None:
    key = _node_key(node)
    if not key:
        return
    now_ts = _now_ts()
    with _runtime_lock:
        state = _ensure_runtime_state(key)
        state["last_used_at"] = now_ts
        if success:
            state["success_count"] = int(state.get("success_count") or 0) + 1
            state["consecutive_failures"] = 0
            state["last_success_at"] = now_ts
            state["last_error"] = ""
            state["cooldown_until"] = 0
        else:
            state["failure_count"] = int(state.get("failure_count") or 0) + 1
            state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
            state["last_failure_at"] = now_ts
            state["last_error"] = str(error or "").strip()[:200]
            threshold = max(1, int(node.get("consecutive_failure_threshold") or 2))
            if int(state.get("consecutive_failures") or 0) >= threshold:
                cooldown_seconds = max(30, int(node.get("failure_cooldown_seconds") or 300))
                state["cooldown_until"] = now_ts + cooldown_seconds


def get_allowance_relay_pool_runtime() -> dict[str, Any]:
    config = _load_store_config()
    nodes = get_allowance_relay_pool_nodes()
    enabled_count = sum(1 for item in nodes if item.get("enabled"))
    now_ts = _now_ts()
    with _runtime_lock:
        runtime_snapshot = {
            key: dict(_ensure_runtime_state(key))
            for key in set(_relay_runtime.keys()) | {_node_key(node) for node in nodes if _node_key(node)}
        }
        next_index = _round_robin_cursor

    payload_nodes: list[dict[str, Any]] = []
    healthy_count = 0
    cooling_count = 0
    for index, node in enumerate(nodes):
        key = _node_key(node)
        runtime = runtime_snapshot.get(key) or {}
        state = _resolve_node_state(node, runtime, now_ts=now_ts)
        if state == "healthy":
            healthy_count += 1
        elif state == "cooling":
            cooling_count += 1
        payload_nodes.append({
            "index": index,
            "name": str(node.get("name") or "").strip(),
            "url": str(node.get("url") or "").strip(),
            "secret_masked": _mask_secret(str(node.get("secret") or "").strip()),
            "enabled": bool(node.get("enabled")),
            "source": str(node.get("source") or "settings"),
            "success_count": int(runtime.get("success_count") or 0),
            "failure_count": int(runtime.get("failure_count") or 0),
            "consecutive_failures": int(runtime.get("consecutive_failures") or 0),
            "last_used_at": int(runtime.get("last_used_at") or 0),
            "last_success_at": int(runtime.get("last_success_at") or 0),
            "last_failure_at": int(runtime.get("last_failure_at") or 0),
            "last_error": str(runtime.get("last_error") or ""),
            "cooldown_until": int(runtime.get("cooldown_until") or 0),
            "last_probe_at": int(runtime.get("last_probe_at") or 0),
            "last_probe_ok": runtime.get("last_probe_ok"),
            "last_probe_status_code": int(runtime.get("last_probe_status_code") or 0),
            "last_probe_error": str(runtime.get("last_probe_error") or ""),
            "last_probe_secret_enabled": runtime.get("last_probe_secret_enabled"),
            "state": state,
        })

    return {
        "config": config,
        "strategy": str(config.get("strategy") or "healthy_round_robin"),
        "request_timeout_seconds": int(config.get("request_timeout_seconds") or 15),
        "failure_cooldown_seconds": int(config.get("failure_cooldown_seconds") or 300),
        "consecutive_failure_threshold": int(config.get("consecutive_failure_threshold") or 2),
        "allow_proxy_fallback": bool(config.get("allow_proxy_fallback", True)),
        "total_count": len(nodes),
        "enabled_count": enabled_count,
        "healthy_count": healthy_count,
        "cooling_count": cooling_count,
        "next_index": next_index if enabled_count > 0 else 0,
        "nodes": payload_nodes,
    }
