from __future__ import annotations

import base64
import gzip
import json
import uuid
from typing import Any, Dict, Optional

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from utils.redis_async import redis_get, redis_set, should_attempt_redis_connection


AES_KEY = b"merchant_coupon_key_32bytes_1234"
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
        raise ValueError(f"encrypted payload length is invalid: {len(encrypted)} bytes")
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_KEY[:16])
    decrypted = cipher.decrypt(encrypted)
    compressed_data = unpad(decrypted, AES.block_size)
    return json.loads(gzip.decompress(compressed_data).decode("utf-8"))


async def aencrypt_payload(payload: Dict[str, Any], logger=None) -> str:
    try:
        payload_json = _serialize_payload(payload)
        if logger:
            logger.info(f"payload before encode: {payload}")
            logger.info(f"payload json length before encode: {len(payload_json)}")

        if len(payload_json) > INLINE_PAYLOAD_THRESHOLD:
            if should_attempt_redis_connection():
                try:
                    cache_id = _generate_uuid()
                    redis_key = REDIS_PAYLOAD_KEY_PREFIX + cache_id
                    ok = await redis_set(redis_key, payload_json.encode("utf-8"), ex=REDIS_PAYLOAD_TTL_SECONDS)
                    if ok:
                        result = MEMORY_MAP_PREFIX + cache_id
                        if logger:
                            logger.info(f"payload stored in Redis mapping: {redis_key}")
                        return result
                    if logger:
                        logger.warning("Redis mapping write failed; falling back to inline encryption")
                except Exception as exc:
                    if logger:
                        logger.warning(f"Redis mapping unavailable; falling back to inline encryption: {exc}")
            elif logger:
                logger.info("Redis endpoint unavailable; falling back to inline encryption for long payload")

        encrypted_str = _encrypt_inline_payload(payload_json)
        if logger:
            logger.info(f"encoded payload length: {len(encrypted_str)}")
        return encrypted_str
    except Exception as exc:
        raise Exception(f"encrypt payload failed: {exc}") from exc


async def adecrypt_payload(
    token: str,
    logger=None,
    expired_message: str = "信息已过期请重新生成",
) -> Dict[str, Any]:
    try:
        normalized_token = str(token or "").strip()
        if normalized_token.startswith(MEMORY_MAP_PREFIX):
            cache_id = normalized_token[len(MEMORY_MAP_PREFIX) :]
            redis_key = REDIS_PAYLOAD_KEY_PREFIX + cache_id
            payload_bytes = await redis_get(redis_key)
            if payload_bytes is None:
                raise Exception(expired_message)
            payload = json.loads(payload_bytes.decode("utf-8"))
            if logger:
                logger.info(f"decoded payload from Redis mapping: {payload}")
            return payload

        payload = _decrypt_inline_payload(normalized_token)
        if logger:
            logger.info(f"decoded inline payload: {payload}")
        return payload
    except Exception as exc:
        if str(exc) == expired_message:
            raise
        if logger:
            logger.error(f"decrypt payload failed: {exc}", exc_info=True)
        raise
