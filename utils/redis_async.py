"""Redis 通用工具。"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import msgpack
from redis.asyncio import Redis


DEFAULT_REDIS_SOCKET_PATH = "/run/redis/redis-server.sock"
DEFAULT_REDIS_PASSWORD = "OvgWJ1dCDo3NrlV4Q/VUWa5D2fjJmlW225bgbtx2NPVB+ckc"
DEFAULT_REDIS_DB = 0
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


def _get_env_float(default: float, *names: str) -> float:
    value = _get_env_setting(*names)
    if not value:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


REDIS_SOCKET_PATH = _get_env_setting("WX_SERVICE_REDIS_SOCKET_PATH", "REDIS_SOCKET_PATH") or DEFAULT_REDIS_SOCKET_PATH
_redis_password_env = _get_env_setting("WX_SERVICE_REDIS_PASSWORD", "REDIS_PASSWORD")
REDIS_PASSWORD = DEFAULT_REDIS_PASSWORD if _redis_password_env is None else (_redis_password_env or None)
REDIS_DB = _get_env_int(DEFAULT_REDIS_DB, "WX_SERVICE_REDIS_DB", "REDIS_DB")
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


def get_redis_password() -> str | None:
    return REDIS_PASSWORD


def get_redis_runtime_diagnostics() -> dict:
    return {
        "redis_socket_path": REDIS_SOCKET_PATH,
        "redis_socket_exists": os.path.exists(REDIS_SOCKET_PATH),
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
            _redis_client = Redis(
                unix_socket_path=REDIS_SOCKET_PATH,
                password=REDIS_PASSWORD,
                db=REDIS_DB,
                decode_responses=False,
                socket_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
                socket_connect_timeout=REDIS_SOCKET_TIMEOUT_SECONDS,
                health_check_interval=REDIS_HEALTH_CHECK_INTERVAL_SECONDS,
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
