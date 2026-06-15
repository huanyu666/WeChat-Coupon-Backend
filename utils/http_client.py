from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse
import errno
import os
import time

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


async def request(method: str, url: str, **kwargs) -> Response:
    httpx = _require_httpx()
    timeout = kwargs.pop("timeout", None)
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
    try:
        return await client.request(
            method,
            url,
            timeout=httpx.Timeout(timeout) if timeout is not None else None,
            **kwargs,
        )
    except Exception as exc:
        raise _translate_httpx_exception(exc) from exc
    finally:
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


def get_client_stats() -> Dict[str, int]:
    by_kind: Dict[str, int] = {"direct": 0, "proxy": 0, "uds": 0}
    for meta in _CLIENT_META.values():
        kind = str(meta.get("kind") or "direct")
        by_kind[kind] = int(by_kind.get(kind) or 0) + 1
    return {
        "client_pool_size": len(_CLIENTS),
        "client_pool_direct_size": by_kind.get("direct", 0),
        "client_pool_proxy_size": by_kind.get("proxy", 0),
        "client_pool_uds_size": by_kind.get("uds", 0),
        "client_pool_active_requests": sum(int(value or 0) for value in _CLIENT_ACTIVE.values()),
        "client_pool_cleanup_runs": int(_CLEANUP_STATS["cleanup_runs"]),
        "client_pool_closed_idle": int(_CLEANUP_STATS["closed_idle_clients"]),
    }


async_get = get
async_post = post
async_head = head
async_request = request
