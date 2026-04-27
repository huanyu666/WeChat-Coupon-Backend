"""通用加密载荷工具。"""
from __future__ import annotations

import base64
import gzip
import json
import os
import uuid
from typing import Any, Dict, Optional

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from utils.redis_async import REDIS_SOCKET_PATH, redis_get, redis_set


AES_KEY = b'merchant_coupon_key_32bytes_1234'
MEMORY_MAP_PREFIX = "MEM:"
REDIS_PAYLOAD_KEY_PREFIX = "wx:payload:map:"
REDIS_PAYLOAD_TTL_SECONDS = 7 * 24 * 60 * 60
INLINE_PAYLOAD_THRESHOLD = 100


def _generate_uuid() -> str:
    if hasattr(uuid, "uuid7"):
        return str(uuid.uuid7())
    return str(uuid.uuid4())


def _serialize_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _encrypt_inline_payload(payload_json: str) -> str:
    compressed_data = gzip.compress(payload_json.encode("utf-8"))
    padded_data = pad(compressed_data, AES.block_size)
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_KEY[:16])
    encrypted = cipher.encrypt(padded_data)
    return base64.urlsafe_b64encode(encrypted).decode("utf-8")


def _decrypt_inline_payload(token: str) -> Dict[str, Any]:
    encrypted = base64.urlsafe_b64decode("".join(str(token or "").split()))
    if len(encrypted) % AES.block_size != 0:
        raise ValueError(f"加密数据长度不正确：{len(encrypted)} 字节")
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_KEY[:16])
    decrypted = cipher.decrypt(encrypted)
    compressed_data = unpad(decrypted, AES.block_size)
    return json.loads(gzip.decompress(compressed_data).decode("utf-8"))


async def aencrypt_payload(payload: Dict[str, Any], logger=None) -> str:
    """异步加密通用载荷。超长内容使用 Redis 映射，短内容使用 gzip + AES。"""
    try:
        payload_json = _serialize_payload(payload)
        if logger:
            logger.info(f"编码前原始载荷: {payload}")
            logger.info(f"编码前JSON字符串长度: {len(payload_json)}")

        if len(payload_json) > INLINE_PAYLOAD_THRESHOLD:
            if os.path.exists(REDIS_SOCKET_PATH):
                try:
                    cache_id = _generate_uuid()
                    redis_key = REDIS_PAYLOAD_KEY_PREFIX + cache_id
                    ok = await redis_set(redis_key, payload_json.encode("utf-8"), ex=REDIS_PAYLOAD_TTL_SECONDS)
                    if ok:
                        result = MEMORY_MAP_PREFIX + cache_id
                        if logger:
                            logger.info(f"载荷使用 Redis 映射: {redis_key}")
                        return result
                    if logger:
                        logger.warning("Redis 映射写入失败，回退内联加密")
                except Exception as exc:
                    if logger:
                        logger.warning(f"Redis 映射不可用，回退内联加密: {exc}")
            elif logger:
                logger.info(f"Redis socket 不存在，超长载荷回退内联加密: {REDIS_SOCKET_PATH}")

        encrypted_str = _encrypt_inline_payload(payload_json)
        if logger:
            logger.info(f"通用载荷编码结果长度: {len(encrypted_str)}")
        return encrypted_str
    except Exception as e:
        raise Exception(f"加密载荷失败: {e}")


async def adecrypt_payload(
    token: str,
    logger=None,
    expired_message: str = "信息已过期请重新生成",
) -> Dict[str, Any]:
    """异步解密通用载荷。"""
    try:
        normalized_token = str(token or "").strip()
        if normalized_token.startswith(MEMORY_MAP_PREFIX):
            cache_id = normalized_token[len(MEMORY_MAP_PREFIX):]
            redis_key = REDIS_PAYLOAD_KEY_PREFIX + cache_id
            payload_bytes = await redis_get(redis_key)
            if payload_bytes is None:
                raise Exception(expired_message)
            payload = json.loads(payload_bytes.decode("utf-8"))
            if logger:
                logger.info(f"通用载荷从 Redis 映射解码成功: {payload}")
            return payload

        payload = _decrypt_inline_payload(normalized_token)
        if logger:
            logger.info(f"通用载荷解码成功: {payload}")
        return payload
    except Exception as e:
        if str(e) == expired_message:
            raise
        if logger:
            logger.error(f"解密通用载荷失败: {e}", exc_info=True)
        raise
