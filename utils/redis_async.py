"""Redis 通用工具。"""
from __future__ import annotations

import asyncio
import os
from typing import Optional

import msgpack
from redis.asyncio import Redis


REDIS_SOCKET_PATH = os.getenv("REDIS_SOCKET_PATH", "/run/redis/redis-server.sock")
# 生产环境请务必通过 REDIS_PASSWORD 环境变量覆盖此默认值，不要在源代码中保留生产密码。
REDIS_PASSWORD: Optional[str] = os.getenv(
    "REDIS_PASSWORD",
    "OvgWJ1dCDo3NrlV4Q/VUWa5D2fjJmlW225bgbtx2NPVB+ckc",
) or None
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_SOCKET_TIMEOUT_SECONDS = 1.5
REDIS_HEALTH_CHECK_INTERVAL_SECONDS = 30

_redis_client: Optional[Redis] = None
_redis_lock: Optional[asyncio.Lock] = None


def pack_payload(payload: dict) -> bytes:
    return msgpack.packb(payload, use_bin_type=True)


def unpack_payload(payload: bytes | bytearray | memoryview | None) -> Optional[dict]:
    if payload is None:
        return None
    return msgpack.unpackb(payload, raw=False)


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
