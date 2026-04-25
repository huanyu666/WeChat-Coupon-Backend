"""订单查询 token Redis 存取工具。"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from .redis_async import pack_payload, redis_delete, redis_get, redis_set, unpack_payload


ORDER_QUERY_TOKEN_KEY_PREFIX = "mt_order_token:"
ORDER_QUERY_TOKEN_TTL_SECONDS = 7 * 24 * 60 * 60


def build_order_query_token_key(from_user_id: str) -> str:
    return f"{ORDER_QUERY_TOKEN_KEY_PREFIX}{str(from_user_id or '').strip()}"


async def get_order_query_token_record(from_user_id: str) -> Optional[Dict[str, Any]]:
    key = build_order_query_token_key(from_user_id)
    raw = await redis_get(key)
    record = unpack_payload(raw)
    if not isinstance(record, dict):
        return None
    return record


async def set_order_query_token_record(
    *,
    from_user_id: str,
    to_user_name: str,
    account_name: str,
    token: str,
    meituan_user_id: str,
    source_url: str = "",
) -> bool:
    record = {
        "from_user_id": str(from_user_id or "").strip(),
        "to_user_name": str(to_user_name or "").strip(),
        "account_name": str(account_name or "").strip(),
        "token": str(token or "").strip(),
        "meituan_user_id": str(meituan_user_id or "").strip(),
        "source_url": str(source_url or "").strip(),
        "updated_at": int(time.time()),
    }
    return await redis_set(
        build_order_query_token_key(from_user_id),
        pack_payload(record),
        ex=ORDER_QUERY_TOKEN_TTL_SECONDS,
    )


async def delete_order_query_token_record(from_user_id: str) -> int:
    return await redis_delete(build_order_query_token_key(from_user_id))
