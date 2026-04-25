from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse
import errno

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
_DEFAULT_LIMITS = None


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


def _get_limits():
    httpx = _require_httpx()
    global _DEFAULT_LIMITS
    if _DEFAULT_LIMITS is None:
        _DEFAULT_LIMITS = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=20,
            keepalive_expiry=30.0,
        )
    return _DEFAULT_LIMITS


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
    if client is None:
        if uds:
            client = httpx.AsyncClient(
                follow_redirects=follow_redirects,
                transport=httpx.AsyncHTTPTransport(
                    uds=uds,
                    limits=_get_limits(),
                ),
            )
        else:
            client = httpx.AsyncClient(
                follow_redirects=follow_redirects,
                proxy=proxy,
                limits=_get_limits(),
            )
        _CLIENTS[key] = client
    return client


async def request(method: str, url: str, **kwargs) -> Response:
    httpx = _require_httpx()
    timeout = kwargs.pop("timeout", None)
    allow_redirects = kwargs.pop("allow_redirects", False)
    proxies = kwargs.pop("proxies", None)
    uds = kwargs.pop("uds", None)
    proxy = _pick_proxy(url, proxies)
    client = _get_client(proxy, allow_redirects, uds)
    try:
        return await client.request(
            method,
            url,
            timeout=httpx.Timeout(timeout) if timeout is not None else None,
            **kwargs,
        )
    except Exception as exc:
        raise _translate_httpx_exception(exc) from exc


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


def get_client_stats() -> Dict[str, int]:
    return {
        "client_pool_size": len(_CLIENTS),
    }


async_get = get
async_post = post
async_head = head
async_request = request
