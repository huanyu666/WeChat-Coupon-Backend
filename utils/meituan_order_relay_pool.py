from __future__ import annotations

import threading
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from utils import http_client
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_order_relay_pool_config,
)


_runtime_lock = threading.Lock()
_round_robin_cursor = 0
_relay_runtime: dict[str, dict[str, Any]] = {}


def _now_ts() -> int:
    return int(time.time())


def _node_key(node: dict[str, Any]) -> str:
    return str(node.get("url") or "").strip().rstrip("/")


def _mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}***{text[-2:]}"


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


def _ensure_runtime_state(key: str) -> dict[str, Any]:
    state = _relay_runtime.get(key)
    if isinstance(state, dict):
        for field, value in _default_runtime_state().items():
            state.setdefault(field, value)
        return state
    state = _default_runtime_state()
    _relay_runtime[key] = state
    return state


def _load_config() -> dict[str, Any]:
    store = load_system_settings_store()
    return normalize_order_relay_pool_config(store.get("order_relay_pool_config"))


def get_order_relay_pool_config() -> dict[str, Any]:
    return _load_config()


def _node_state(node: dict[str, Any], runtime: dict[str, Any], now_ts: int | None = None) -> str:
    if not bool(node.get("enabled")):
        return "disabled"
    if int(runtime.get("cooldown_until") or 0) > int(now_ts or _now_ts()):
        return "cooling"
    return "healthy"


def get_order_relay_pool_nodes() -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in _load_config().get("nodes") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip().rstrip("/")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        nodes.append({
            "name": str(item.get("name") or "").strip(),
            "url": url,
            "secret": str(item.get("secret") or "").strip(),
            "enabled": bool(item.get("enabled")),
            "source": "settings",
        })
    return nodes


def list_order_relay_candidates() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    global _round_robin_cursor
    config = _load_config()
    nodes = [item for item in get_order_relay_pool_nodes() if bool(item.get("enabled"))]
    timeout = int(config.get("request_timeout_seconds") or 15)
    cooldown = int(config.get("failure_cooldown_seconds") or 300)
    threshold = int(config.get("consecutive_failure_threshold") or 2)
    for node in nodes:
        node["timeout_seconds"] = timeout
        node["failure_cooldown_seconds"] = cooldown
        node["consecutive_failure_threshold"] = threshold
        node["relay_options"] = {
            "proxy_mode": str(config.get("proxy_mode") or "direct"),
            "proxy_retry_count": int(config.get("proxy_retry_count") or 0),
            "queue_wait_seconds": float(config.get("queue_wait_seconds") or 3),
        }
    if len(nodes) <= 1:
        return config, nodes

    now = _now_ts()
    with _runtime_lock:
        healthy = [
            node for node in nodes
            if _node_state(node, _ensure_runtime_state(_node_key(node)), now) == "healthy"
        ]
        candidate_pool = healthy or nodes
        start = _round_robin_cursor % len(candidate_pool)
        ordered = candidate_pool[start:] + candidate_pool[:start]
        _round_robin_cursor = (_round_robin_cursor + 1) % len(candidate_pool)
    return config, ordered


def report_order_relay_result(node: dict[str, Any], *, success: bool, error: Any = None) -> None:
    key = _node_key(node)
    if not key:
        return
    now = _now_ts()
    with _runtime_lock:
        state = _ensure_runtime_state(key)
        state["last_used_at"] = now
        if success:
            state["success_count"] = int(state.get("success_count") or 0) + 1
            state["consecutive_failures"] = 0
            state["last_success_at"] = now
            state["last_error"] = ""
            state["cooldown_until"] = 0
            return
        state["failure_count"] = int(state.get("failure_count") or 0) + 1
        state["consecutive_failures"] = int(state.get("consecutive_failures") or 0) + 1
        state["last_failure_at"] = now
        state["last_error"] = str(error or "").strip()[:200]
        if int(state["consecutive_failures"]) >= max(1, int(node.get("consecutive_failure_threshold") or 2)):
            state["cooldown_until"] = now + max(30, int(node.get("failure_cooldown_seconds") or 300))


def reset_order_relay_runtime(url: str) -> bool:
    key = str(url or "").strip().rstrip("/")
    if not key:
        return False
    with _runtime_lock:
        if key not in _relay_runtime:
            return False
        _relay_runtime[key] = _default_runtime_state()
    return True


def _base_url(url: str) -> str:
    parts = urlsplit(str(url or "").strip())
    path = (parts.path or "").rstrip("/")
    suffix = "/relay/meituan/order-query"
    if path.endswith(suffix):
        path = path[:-len(suffix)]
    return urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


def _record_probe(node: dict[str, Any], *, ok: bool, status_code: int = 0, error: str = "", secret_enabled: Any = None) -> None:
    key = _node_key(node)
    if not key:
        return
    with _runtime_lock:
        state = _ensure_runtime_state(key)
        state["last_probe_at"] = _now_ts()
        state["last_probe_ok"] = bool(ok)
        state["last_probe_status_code"] = int(status_code or 0)
        state["last_probe_error"] = str(error or "").strip()[:200]
        state["last_probe_secret_enabled"] = None if secret_enabled is None else bool(secret_enabled)


async def probe_order_relay_node(node: dict[str, Any], *, timeout_seconds: int | None = None) -> dict[str, Any]:
    relay_url = _node_key(node)
    if not relay_url:
        return {"ok": False, "name": "", "url": "", "message": "节点地址为空"}
    timeout = max(3, int(timeout_seconds or node.get("timeout_seconds") or 15))
    base_url = _base_url(relay_url)
    secret = str(node.get("secret") or "").strip()
    relay_options = node.get("relay_options")
    if not isinstance(relay_options, dict):
        config = _load_config()
        relay_options = {
            "proxy_mode": str(config.get("proxy_mode") or "direct"),
            "proxy_retry_count": int(config.get("proxy_retry_count") or 0),
            "queue_wait_seconds": float(config.get("queue_wait_seconds") or 3),
        }
    try:
        health_response = await http_client.get(f"{base_url}/healthz", timeout=timeout)
        health_payload = health_response.json()
    except Exception as exc:
        message = f"healthz 不可用: {exc}"
        _record_probe(node, ok=False, error=message)
        return {"ok": False, "name": node.get("name", ""), "url": relay_url, "message": message}
    status = int(getattr(health_response, "status_code", 0) or 0)
    if status != 200 or not isinstance(health_payload, dict) or not bool(health_payload.get("ok")):
        message = f"healthz 异常: status={status}"
        _record_probe(node, ok=False, status_code=status, error=message)
        return {"ok": False, "name": node.get("name", ""), "url": relay_url, "message": message, "status_code": status}

    secret_enabled = bool(health_payload.get("order_relay_secret_enabled"))
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Order-Relay-Secret"] = secret
    try:
        response = await http_client.post(
            f"{base_url}/relay/meituan/order-query/probe",
            json={"relay_options": relay_options},
            headers=headers,
            timeout=timeout,
        )
        status = int(getattr(response, "status_code", 0) or 0)
        payload = response.json() if getattr(response, "content", b"") else {}
    except Exception as exc:
        message = f"Relay 探测失败: {exc}"
        _record_probe(node, ok=False, error=message, secret_enabled=secret_enabled)
        return {"ok": False, "name": node.get("name", ""), "url": relay_url, "message": message}
    message = str(payload.get("message") or payload.get("detail") or "").strip() if isinstance(payload, dict) else ""
    ok = status == 200 and isinstance(payload, dict) and bool(payload.get("success", True))
    if status == 401:
        message = "Relay 密钥校验失败，请检查后台 Secret"
    elif not ok and not message:
        message = f"Relay 探测异常: status={status}"
    _record_probe(node, ok=ok, status_code=status, error="" if ok else message, secret_enabled=secret_enabled)
    return {
        "ok": ok,
        "name": str(node.get("name") or ""),
        "url": relay_url,
        "message": message or "订单 Relay 与代理接口正常",
        "status_code": status,
        "secret_enabled": secret_enabled,
    }


def get_order_relay_pool_runtime() -> dict[str, Any]:
    global _round_robin_cursor
    config = _load_config()
    nodes = get_order_relay_pool_nodes()
    now = _now_ts()
    with _runtime_lock:
        snapshot = {key: dict(_ensure_runtime_state(key)) for key in {_node_key(node) for node in nodes if _node_key(node)} | set(_relay_runtime)}
        next_index = _round_robin_cursor
    payload_nodes = []
    healthy = 0
    cooling = 0
    for index, node in enumerate(nodes):
        runtime = snapshot.get(_node_key(node), {})
        state = _node_state(node, runtime, now)
        healthy += 1 if state == "healthy" else 0
        cooling += 1 if state == "cooling" else 0
        payload_nodes.append({
            "index": index,
            "name": str(node.get("name") or ""),
            "url": _node_key(node),
            "secret_masked": _mask_secret(str(node.get("secret") or "")),
            "enabled": bool(node.get("enabled")),
            "state": state,
            **runtime,
        })
    return {
        "config": config,
        "strategy": str(config.get("strategy") or "healthy_round_robin"),
        "request_timeout_seconds": int(config.get("request_timeout_seconds") or 15),
        "failure_cooldown_seconds": int(config.get("failure_cooldown_seconds") or 300),
        "consecutive_failure_threshold": int(config.get("consecutive_failure_threshold") or 2),
        "proxy_mode": str(config.get("proxy_mode") or "direct"),
        "proxy_retry_count": int(config.get("proxy_retry_count") or 0),
        "queue_wait_seconds": float(config.get("queue_wait_seconds") or 3),
        "total_count": len(nodes),
        "enabled_count": sum(1 for node in nodes if node.get("enabled")),
        "healthy_count": healthy,
        "cooling_count": cooling,
        "next_index": next_index if nodes else 0,
        "nodes": payload_nodes,
    }
