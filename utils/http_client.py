from __future__ import annotations

from collections import deque
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse
import errno
import logging
import os
import time
import uuid

try:
    import httpx as _httpx  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _httpx = None


class RequestException(Exception):
    def __init__(self, message: str = "", response: Any = None):
        super().__init__(message)
        self.response = response


class HTTPError(RequestException):
    pass


class Timeout(RequestException):
    pass


class ConnectionError(RequestException):
    pass


class LocalResourceExhausted(RequestException):
    pass


Response = Any
_CLIENTS: Dict[Tuple[Optional[str], bool, Optional[str]], Any] = {}
_CLIENT_META: Dict[Tuple[Optional[str], bool, Optional[str]], Dict[str, Any]] = {}
_CLIENT_ACTIVE: Dict[Tuple[Optional[str], bool, Optional[str]], int] = {}
_LIMITS_BY_KIND: Dict[str, Any] = {}
_LAST_CLEANUP_AT = 0.0
_CLEANUP_STATS = {
    "cleanup_runs": 0,
    "closed_idle_clients": 0,
}
_ACTIVE_REQUESTS: Dict[str, Dict[str, Any]] = {}
_RECENT_REQUESTS: deque[Dict[str, Any]] = deque(maxlen=100)
_POOL_TIMEOUT_EVENTS: Dict[Tuple[Optional[str], bool, Optional[str]], deque[float]] = {}
_CLIENT_RECOVERY_COOLDOWN_UNTIL: Dict[Tuple[Optional[str], bool, Optional[str]], float] = {}
_CLIENT_RECOVERY_STATS = {
    "direct_pool_rebuilds": 0,
    "last_direct_pool_rebuild_at": 0.0,
    "last_direct_pool_rebuild_reason": "",
}
_RETIRED_CLIENT_TASKS: set[Any] = set()
logger = logging.getLogger(__name__)


def _require_httpx():
    if _httpx is None:
        raise RuntimeError("httpx 未安装，请先安装 requirements.txt 中的依赖")
    return _httpx


def _pick_proxy(url: str, proxies: Any) -> Optional[str]:
    if not proxies:
        return None
    if isinstance(proxies, str):
        return proxies
    if not isinstance(proxies, dict):
        return None
    scheme = urlparse(url).scheme.lower()
    return (
        proxies.get(scheme)
        or proxies.get(f"{scheme}://")
        or proxies.get("all")
        or proxies.get("http")
        or proxies.get("https")
    )


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, "")).strip())
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, "")).strip())
    except Exception:
        value = default
    return min(max(value, minimum), maximum)


def _client_kind(proxy: Optional[str], uds: Optional[str]) -> str:
    if uds:
        return "uds"
    if proxy:
        return "proxy"
    return "direct"


def _safe_request_target(url: str) -> str:
    """Return only origin and path; never expose query parameters or credentials."""
    try:
        parsed = urlparse(str(url or ""))
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        path = parsed.path or "/"
        return f"{parsed.scheme or '-'}://{host}{path}"
    except Exception:
        return "invalid-url"


def is_pool_timeout(error: Any) -> bool:
    """Whether an error occurred while waiting for a shared connection-pool slot."""
    return "pooltimeout" in str(error or "").replace(" ", "").lower()


def _get_limits(kind: str):
    httpx = _require_httpx()
    limits = _LIMITS_BY_KIND.get(kind)
    if limits is not None:
        return limits
    if kind == "proxy":
        limits = httpx.Limits(
            max_connections=_env_int("WX_HTTP_CLIENT_PROXY_MAX_CONNECTIONS", 8, 1, 100),
            max_keepalive_connections=_env_int("WX_HTTP_CLIENT_PROXY_MAX_KEEPALIVE", 2, 0, 50),
            keepalive_expiry=_env_float("WX_HTTP_CLIENT_PROXY_KEEPALIVE_SECONDS", 15.0, 1.0, 300.0),
        )
    elif kind == "uds":
        limits = httpx.Limits(
            max_connections=_env_int("WX_HTTP_CLIENT_UDS_MAX_CONNECTIONS", 64, 1, 500),
            max_keepalive_connections=_env_int("WX_HTTP_CLIENT_UDS_MAX_KEEPALIVE", 20, 0, 200),
            keepalive_expiry=_env_float("WX_HTTP_CLIENT_UDS_KEEPALIVE_SECONDS", 30.0, 1.0, 300.0),
        )
    else:
        limits = httpx.Limits(
            max_connections=_env_int("WX_HTTP_CLIENT_DIRECT_MAX_CONNECTIONS", 64, 1, 500),
            max_keepalive_connections=_env_int("WX_HTTP_CLIENT_DIRECT_MAX_KEEPALIVE", 20, 0, 200),
            keepalive_expiry=_env_float("WX_HTTP_CLIENT_DIRECT_KEEPALIVE_SECONDS", 30.0, 1.0, 300.0),
        )
    _LIMITS_BY_KIND[kind] = limits
    return limits


def _translate_httpx_exception(exc: Exception) -> RequestException:
    httpx = _require_httpx()
    message = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.EMFILE:
        return LocalResourceExhausted(message or "Too many open files")
    lowered_message = message.lower()
    if "too many open files" in lowered_message or "emfile" in lowered_message:
        return LocalResourceExhausted(message)
    if isinstance(exc, httpx.TimeoutException):
        return Timeout(message)
    if isinstance(exc, httpx.ConnectError):
        return ConnectionError(message)
    if isinstance(exc, httpx.HTTPStatusError):
        return HTTPError(message, response=getattr(exc, "response", None))
    if isinstance(exc, httpx.HTTPError):
        return RequestException(message, response=getattr(exc, "response", None))
    return RequestException(message)


def _client_key(proxy: Optional[str], follow_redirects: bool, uds: Optional[str]) -> Tuple[Optional[str], bool, Optional[str]]:
    return proxy, follow_redirects, uds


def _get_client(proxy: Optional[str], follow_redirects: bool, uds: Optional[str]):
    httpx = _require_httpx()
    key = _client_key(proxy, follow_redirects, uds)
    client = _CLIENTS.get(key)
    now = time.time()
    kind = _client_kind(proxy, uds)
    if client is None:
        if uds:
            client = httpx.AsyncClient(
                follow_redirects=follow_redirects,
                transport=httpx.AsyncHTTPTransport(
                    uds=uds,
                    limits=_get_limits(kind),
                ),
            )
        else:
            client = httpx.AsyncClient(
                follow_redirects=follow_redirects,
                proxy=proxy,
                limits=_get_limits(kind),
            )
        _CLIENTS[key] = client
        _CLIENT_META[key] = {
            "kind": kind,
            "created_at": now,
            "last_used_at": now,
        }
    else:
        _CLIENT_META.setdefault(key, {"kind": kind, "created_at": now})["last_used_at"] = now
    return client


async def _cleanup_idle_clients_if_needed() -> None:
    global _LAST_CLEANUP_AT
    now = time.time()
    interval = _env_float("WX_HTTP_CLIENT_CLEANUP_INTERVAL_SECONDS", 15.0, 1.0, 300.0)
    if now - _LAST_CLEANUP_AT < interval:
        return
    _LAST_CLEANUP_AT = now

    proxy_idle_ttl = _env_float("WX_HTTP_CLIENT_PROXY_IDLE_TTL_SECONDS", 60.0, 5.0, 3600.0)
    max_proxy_clients = _env_int("WX_HTTP_CLIENT_MAX_PROXY_CLIENTS", 60, 1, 1000)
    max_total_clients = _env_int("WX_HTTP_CLIENT_MAX_TOTAL_CLIENTS", 120, 1, 2000)
    candidates: list[Tuple[Tuple[Optional[str], bool, Optional[str]], float]] = []
    for key, meta in list(_CLIENT_META.items()):
        if int(_CLIENT_ACTIVE.get(key) or 0) > 0:
            continue
        kind = str(meta.get("kind") or "")
        last_used_at = float(meta.get("last_used_at") or 0.0)
        if kind == "proxy" and now - last_used_at >= proxy_idle_ttl:
            candidates.append((key, last_used_at))

    proxy_keys = [
        key for key, meta in _CLIENT_META.items()
        if str(meta.get("kind") or "") == "proxy" and int(_CLIENT_ACTIVE.get(key) or 0) <= 0
    ]
    if len(proxy_keys) > max_proxy_clients:
        for key in sorted(proxy_keys, key=lambda item: float((_CLIENT_META.get(item) or {}).get("last_used_at") or 0.0))[:len(proxy_keys) - max_proxy_clients]:
            candidates.append((key, float((_CLIENT_META.get(key) or {}).get("last_used_at") or 0.0)))

    idle_keys = [
        key for key in _CLIENTS
        if int(_CLIENT_ACTIVE.get(key) or 0) <= 0
    ]
    if len(_CLIENTS) > max_total_clients:
        overflow = len(_CLIENTS) - max_total_clients
        for key in sorted(idle_keys, key=lambda item: float((_CLIENT_META.get(item) or {}).get("last_used_at") or 0.0))[:overflow]:
            candidates.append((key, float((_CLIENT_META.get(key) or {}).get("last_used_at") or 0.0)))

    closed = 0
    seen: set[Tuple[Optional[str], bool, Optional[str]]] = set()
    for key, _ in sorted(candidates, key=lambda item: item[1]):
        if key in seen or int(_CLIENT_ACTIVE.get(key) or 0) > 0:
            continue
        seen.add(key)
        client = _CLIENTS.pop(key, None)
        _CLIENT_META.pop(key, None)
        _CLIENT_ACTIVE.pop(key, None)
        if client is None:
            continue
        try:
            await client.aclose()
        except Exception:
            pass
        closed += 1
    _CLEANUP_STATS["cleanup_runs"] += 1
    _CLEANUP_STATS["closed_idle_clients"] += closed


def _record_completed_request(entry: Dict[str, Any], *, outcome: str, error: Any = None) -> None:
    completed_at = time.monotonic()
    _RECENT_REQUESTS.append({
        "_completed_monotonic": completed_at,
        "target": entry["target"],
        "method": entry["method"],
        "kind": entry["kind"],
        "timeout_seconds": entry["timeout_seconds"],
        "duration_seconds": round(max(0.0, completed_at - entry["started_at"]), 3),
        "outcome": outcome,
        "error_type": type(error).__name__ if error is not None else "",
        "pool_timeout": bool(error is not None and is_pool_timeout(error)),
    })


async def _close_retired_client_after_grace(client: Any, grace_seconds: float) -> None:
    try:
        await __import__("asyncio").sleep(grace_seconds)
        await client.aclose()
    except Exception:
        pass


def _schedule_retired_client_close(client: Any) -> None:
    try:
        import asyncio
        grace_seconds = _env_float("WX_HTTP_CLIENT_RECOVERY_GRACE_SECONDS", 3.0, 0.0, 30.0)
        task = asyncio.create_task(_close_retired_client_after_grace(client, grace_seconds))
        _RETIRED_CLIENT_TASKS.add(task)
        task.add_done_callback(_RETIRED_CLIENT_TASKS.discard)
    except Exception:
        # The caller is already handling a timeout; a later process shutdown
        # will close any remaining client if task scheduling is unavailable.
        pass


async def _recover_direct_client_after_pool_timeout(
    key: Tuple[Optional[str], bool, Optional[str]],
) -> None:
    """Detach only a repeatedly timing-out shared direct client."""
    if key[0] is not None or key[2] is not None:
        return
    now = time.monotonic()
    window_seconds = _env_float("WX_HTTP_CLIENT_POOL_TIMEOUT_WINDOW_SECONDS", 60.0, 5.0, 600.0)
    threshold = _env_int("WX_HTTP_CLIENT_POOL_TIMEOUT_REBUILD_THRESHOLD", 3, 2, 20)
    cooldown_seconds = _env_float("WX_HTTP_CLIENT_POOL_RECOVERY_COOLDOWN_SECONDS", 60.0, 5.0, 3600.0)
    events = _POOL_TIMEOUT_EVENTS.setdefault(key, deque())
    events.append(now)
    while events and now - events[0] > window_seconds:
        events.popleft()
    if len(events) < threshold or now < float(_CLIENT_RECOVERY_COOLDOWN_UNTIL.get(key) or 0.0):
        return
    client = _CLIENTS.pop(key, None)
    _CLIENT_META.pop(key, None)
    _CLIENT_RECOVERY_COOLDOWN_UNTIL[key] = now + cooldown_seconds
    events.clear()
    _CLIENT_RECOVERY_STATS["direct_pool_rebuilds"] += 1
    _CLIENT_RECOVERY_STATS["last_direct_pool_rebuild_at"] = time.time()
    _CLIENT_RECOVERY_STATS["last_direct_pool_rebuild_reason"] = (
        f"{threshold} PoolTimeouts/{int(window_seconds)}s"
    )
    logger.warning(
        "重建共享直连 HTTP 客户端池: reason=%s active_requests=%s",
        _CLIENT_RECOVERY_STATS["last_direct_pool_rebuild_reason"],
        _active_request_diagnostics(now)[:3],
    )
    if client is not None:
        _schedule_retired_client_close(client)


def _active_request_diagnostics(now: float) -> list[Dict[str, Any]]:
    entries: list[Dict[str, Any]] = []
    for entry in _ACTIVE_REQUESTS.values():
        entries.append({
            "target": entry["target"],
            "method": entry["method"],
            "kind": entry["kind"],
            "timeout_seconds": entry["timeout_seconds"],
            "elapsed_seconds": round(max(0.0, now - entry["started_at"]), 3),
        })
    return sorted(entries, key=lambda item: item["elapsed_seconds"], reverse=True)[:10]


async def request(method: str, url: str, **kwargs) -> Response:
    httpx = _require_httpx()
    timeout = kwargs.pop("timeout", None)
    # A shared client must never allow an omitted per-call timeout to turn
    # into an unbounded pool/connect/read wait.  httpx.Timeout applies this
    # value to pool acquisition, connect, write and read unless a caller
    # deliberately supplies a more specific Timeout object (not supported by
    # this wrapper at present).
    if timeout is None:
        timeout = _env_float("WX_HTTP_CLIENT_DEFAULT_TIMEOUT_SECONDS", 15.0, 1.0, 600.0)
    allow_redirects = kwargs.pop("allow_redirects", False)
    proxies = kwargs.pop("proxies", None)
    uds = kwargs.pop("uds", None)
    stateless_cookies = bool(kwargs.pop("stateless_cookies", False))
    proxy = _pick_proxy(url, proxies)
    if stateless_cookies:
        kind = _client_kind(proxy, uds)
        try:
            if uds:
                async with httpx.AsyncClient(
                    follow_redirects=allow_redirects,
                    transport=httpx.AsyncHTTPTransport(
                        uds=uds,
                        limits=_get_limits(kind),
                    ),
                ) as client:
                    return await client.request(
                        method,
                        url,
                        timeout=httpx.Timeout(timeout) if timeout is not None else None,
                        **kwargs,
                    )
            async with httpx.AsyncClient(
                follow_redirects=allow_redirects,
                proxy=proxy,
                limits=_get_limits(kind),
            ) as client:
                return await client.request(
                    method,
                    url,
                    timeout=httpx.Timeout(timeout) if timeout is not None else None,
                    **kwargs,
                )
        except Exception as exc:
            raise _translate_httpx_exception(exc) from exc

    await _cleanup_idle_clients_if_needed()
    key = _client_key(proxy, allow_redirects, uds)
    client = _get_client(proxy, allow_redirects, uds)
    _CLIENT_ACTIVE[key] = int(_CLIENT_ACTIVE.get(key) or 0) + 1
    request_id = uuid.uuid4().hex
    request_entry = {
        "client_key": key,
        "target": _safe_request_target(url),
        "method": str(method or "").upper() or "REQUEST",
        "kind": _client_kind(proxy, uds),
        "timeout_seconds": float(timeout),
        "started_at": time.monotonic(),
    }
    _ACTIVE_REQUESTS[request_id] = request_entry
    try:
        response = await client.request(
            method,
            url,
            timeout=httpx.Timeout(timeout),
            **kwargs,
        )
        _record_completed_request(request_entry, outcome="ok")
        return response
    except Exception as exc:
        translated = _translate_httpx_exception(exc)
        _record_completed_request(request_entry, outcome="error", error=translated)
        if is_pool_timeout(translated):
            await _recover_direct_client_after_pool_timeout(key)
        raise translated from exc
    finally:
        _ACTIVE_REQUESTS.pop(request_id, None)
        _CLIENT_ACTIVE[key] = max(0, int(_CLIENT_ACTIVE.get(key) or 0) - 1)
        if key in _CLIENT_META:
            _CLIENT_META[key]["last_used_at"] = time.time()


async def get(url: str, **kwargs) -> Response:
    return await request("GET", url, **kwargs)


async def post(url: str, **kwargs) -> Response:
    return await request("POST", url, **kwargs)


async def head(url: str, **kwargs) -> Response:
    return await request("HEAD", url, **kwargs)


async def put(url: str, **kwargs) -> Response:
    return await request("PUT", url, **kwargs)


async def delete(url: str, **kwargs) -> Response:
    return await request("DELETE", url, **kwargs)


async def aclose() -> None:
    for client in list(_CLIENTS.values()):
        try:
            await client.aclose()
        except Exception:
            pass
    _CLIENTS.clear()
    _CLIENT_META.clear()
    _CLIENT_ACTIVE.clear()
    _ACTIVE_REQUESTS.clear()
    _POOL_TIMEOUT_EVENTS.clear()
    _CLIENT_RECOVERY_COOLDOWN_UNTIL.clear()
    for task in list(_RETIRED_CLIENT_TASKS):
        task.cancel()
    _RETIRED_CLIENT_TASKS.clear()


def get_client_stats() -> Dict[str, Any]:
    by_kind: Dict[str, int] = {"direct": 0, "proxy": 0, "uds": 0}
    for meta in _CLIENT_META.values():
        kind = str(meta.get("kind") or "direct")
        by_kind[kind] = int(by_kind.get(kind) or 0) + 1
    now = time.monotonic()
    active_requests = _active_request_diagnostics(now)
    timeout_window_seconds = _env_float("WX_HTTP_CLIENT_POOL_TIMEOUT_WINDOW_SECONDS", 60.0, 5.0, 600.0)
    pool_timeout_recent_count = sum(
        1
        for item in _RECENT_REQUESTS
        if item.get("pool_timeout")
        and now - float(item.get("_completed_monotonic") or 0.0) <= timeout_window_seconds
    )
    recent_requests = []
    for item in list(_RECENT_REQUESTS)[-10:]:
        # The monotonic completion timestamp is used only for in-process
        # windowing and has no diagnostic value outside this process.
        recent_requests.append({key: value for key, value in item.items() if key != "_completed_monotonic"})
    return {
        "client_pool_size": len(_CLIENTS),
        "client_pool_direct_size": by_kind.get("direct", 0),
        "client_pool_proxy_size": by_kind.get("proxy", 0),
        "client_pool_uds_size": by_kind.get("uds", 0),
        "client_pool_active_requests": len(_ACTIVE_REQUESTS),
        "client_pool_cleanup_runs": int(_CLEANUP_STATS["cleanup_runs"]),
        "client_pool_closed_idle": int(_CLEANUP_STATS["closed_idle_clients"]),
        "client_pool_active_request_details": active_requests,
        "client_pool_recent_request_count": len(_RECENT_REQUESTS),
        "client_pool_recent_pool_timeout_count": pool_timeout_recent_count,
        "client_pool_recent_requests": recent_requests,
        "client_pool_direct_rebuild_count": int(_CLIENT_RECOVERY_STATS["direct_pool_rebuilds"]),
        "client_pool_last_direct_rebuild_at": int(_CLIENT_RECOVERY_STATS["last_direct_pool_rebuild_at"] or 0),
        "client_pool_last_direct_rebuild_reason": str(_CLIENT_RECOVERY_STATS["last_direct_pool_rebuild_reason"] or ""),
    }


async_get = get
async_post = post
async_head = head
async_request = request
