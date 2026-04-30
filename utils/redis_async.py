from __future__ import annotations

import asyncio
import os
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

import msgpack
from redis.asyncio import Redis


DEFAULT_REDIS_SOCKET_PATH = "/run/redis/redis-server.sock"
DEFAULT_REDIS_PASSWORD = ""
DEFAULT_REDIS_DB = 0
DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS = 1.5
DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SECONDS = 30


def _get_env_setting(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None:
            return value.strip()
    return None


def _get_env_int(default: int, *names: str) -> int:
    value = _get_env_setting(*names)
    if not value:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _get_env_int_setting(*names: str) -> int | None:
    value = _get_env_setting(*names)
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_env_float(default: float, *names: str) -> float:
    value = _get_env_setting(*names)
    if not value:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mask_redis_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        if "@" not in parts.netloc:
            return url
        userinfo, hostinfo = parts.netloc.rsplit("@", 1)
        username = userinfo.split(":", 1)[0]
        masked_userinfo = f"{username}:***" if username else "***"
        return urlunsplit((parts.scheme, f"{masked_userinfo}@{hostinfo}", parts.path, parts.query, parts.fragment))
    except Exception:
        return "<invalid redis url>"


REDIS_URL = _get_env_setting("WX_SERVICE_REDIS_URL", "REDIS_URL") or ""
REDIS_HOST = _get_env_setting("WX_SERVICE_REDIS_HOST", "REDIS_HOST") or ""
REDIS_PORT = _get_env_int(DEFAULT_REDIS_PORT, "WX_SERVICE_REDIS_PORT", "REDIS_PORT")
REDIS_SOCKET_PATH = _get_env_setting("WX_SERVICE_REDIS_SOCKET_PATH", "REDIS_SOCKET_PATH")
if REDIS_SOCKET_PATH is None and not REDIS_URL and not REDIS_HOST:
    REDIS_SOCKET_PATH = DEFAULT_REDIS_SOCKET_PATH
REDIS_SOCKET_PATH = REDIS_SOCKET_PATH or ""

_redis_password_env = _get_env_setting("WX_SERVICE_REDIS_PASSWORD", "REDIS_PASSWORD")
REDIS_PASSWORD = (
    None
    if (_redis_password_env is None and (REDIS_URL or REDIS_HOST))
    else ((DEFAULT_REDIS_PASSWORD or None) if _redis_password_env is None else (_redis_password_env or None))
)
_redis_db_env = _get_env_int_setting("WX_SERVICE_REDIS_DB", "REDIS_DB")
REDIS_DB = DEFAULT_REDIS_DB if _redis_db_env is None else _redis_db_env
REDIS_SOCKET_TIMEOUT_SECONDS = _get_env_float(
    DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS,
    "WX_SERVICE_REDIS_SOCKET_TIMEOUT_SECONDS",
    "REDIS_SOCKET_TIMEOUT_SECONDS",
)
REDIS_HEALTH_CHECK_INTERVAL_SECONDS = _get_env_float(
    DEFAULT_REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
    "WX_SERVICE_REDIS_HEALTH_CHECK_INTERVAL_SECONDS",
    "REDIS_HEALTH_CHECK_INTERVAL_SECONDS",
)
REDIS_MODE = "url" if REDIS_URL else ("tcp" if REDIS_HOST else "unix_socket")

_redis_client: Optional[Redis] = None
_redis_lock: Optional[asyncio.Lock] = None


def pack_payload(payload: dict) -> bytes:
    return msgpack.packb(payload, use_bin_type=True)


def unpack_payload(payload: bytes | bytearray | memoryview | None) -> Optional[dict]:
    if payload is None:
        return None
    return msgpack.unpackb(payload, raw=False)


def get_redis_socket_path() -> str:
    return REDIS_SOCKET_PATH


def should_attempt_redis_connection() -> bool:
    if REDIS_MODE in {"url", "tcp"}:
        return True
    return bool(REDIS_SOCKET_PATH and os.path.exists(REDIS_SOCKET_PATH))


def get_redis_password() -> str | None:
    return REDIS_PASSWORD


def get_redis_runtime_diagnostics() -> dict:
    return {
        "redis_mode": REDIS_MODE,
        "redis_url": _mask_redis_url(REDIS_URL),
        "redis_host": REDIS_HOST,
        "redis_port": REDIS_PORT if REDIS_MODE == "tcp" else None,
        "redis_socket_path": REDIS_SOCKET_PATH,
        "redis_socket_exists": bool(REDIS_SOCKET_PATH and os.path.exists(REDIS_SOCKET_PATH)),
        "redis_db": REDIS_DB,
        "redis_password_configured": REDIS_PASSWORD is not None,
        "redis_socket_timeout_seconds": REDIS_SOCKET_TIMEOUT_SECONDS,
        "redis_health_check_interval_seconds": REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
    }


async def get_redis_client() -> Redis:
    global _redis_client, _redis_lock
    if _redis_client is not None:
        return _redis_client
    if _redis_lock is None:
        _redis_lock = asyncio.Lock()
    async with _redis_lock:
        if _redis_client is None:
            common_kwargs = {
                "decode_responses": False,
                "socket_timeout": REDIS_SOCKET_TIMEOUT_SECONDS,
                "socket_connect_timeout": REDIS_SOCKET_TIMEOUT_SECONDS,
                "health_check_interval": REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
            }
            if REDIS_PASSWORD is not None:
                common_kwargs["password"] = REDIS_PASSWORD
            if REDIS_MODE == "url":
                url_kwargs = dict(common_kwargs)
                if _redis_db_env is not None:
                    url_kwargs["db"] = REDIS_DB
                _redis_client = Redis.from_url(REDIS_URL, **url_kwargs)
            elif REDIS_MODE == "tcp":
                _redis_client = Redis(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    db=REDIS_DB,
                    **common_kwargs,
                )
            else:
                _redis_client = Redis(
                    unix_socket_path=REDIS_SOCKET_PATH,
                    db=REDIS_DB,
                    **common_kwargs,
                )
        return _redis_client


async def close_redis_client() -> None:
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


async def ping_redis() -> bool:
    client = await get_redis_client()
    return bool(await client.ping())


async def redis_get(key: str) -> Optional[bytes]:
    client = await get_redis_client()
    return await client.get(key)


async def redis_set(key: str, value: bytes, ex: Optional[int] = None) -> bool:
    client = await get_redis_client()
    return bool(await client.set(key, value, ex=ex))


async def redis_delete(key: str) -> int:
    client = await get_redis_client()
    return int(await client.delete(key))


async def redis_exists(key: str) -> bool:
    client = await get_redis_client()
    return bool(await client.exists(key))


async def redis_ttl(key: str) -> int:
    client = await get_redis_client()
    return int(await client.ttl(key))
