"""
排行榜个人定位链接加密
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from typing import Any, Dict

from Crypto.Cipher import AES


DEFAULT_ORDER_RANKINGS_LINK_SECRET = "wx_service_order_rankings_rank_token_secret_v1"
ORDER_RANKINGS_LINK_SECRET = (
    os.getenv("WX_ORDER_RANKINGS_LINK_SECRET")
    or os.getenv("ORDER_RANKINGS_LINK_SECRET")
    or DEFAULT_ORDER_RANKINGS_LINK_SECRET
)


def _get_secret_bytes() -> bytes:
    return hashlib.sha256(ORDER_RANKINGS_LINK_SECRET.encode("utf-8")).digest()


def _encrypt_payload(payload: Dict[str, Any]) -> str:
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    nonce = secrets.token_bytes(12)
    cipher = AES.new(_get_secret_bytes(), AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    token = nonce + tag + ciphertext
    return base64.urlsafe_b64encode(token).decode("ascii").rstrip("=")


def _decrypt_payload(token: str) -> Dict[str, Any]:
    normalized = str(token or "").strip()
    if not normalized:
        raise ValueError("empty token")

    padded = normalized + "=" * ((4 - len(normalized) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise ValueError("invalid token") from exc
    if len(raw) < 28:
        raise ValueError("invalid token")

    nonce = raw[:12]
    tag = raw[12:28]
    ciphertext = raw[28:]
    cipher = AES.new(_get_secret_bytes(), AES.MODE_GCM, nonce=nonce)
    try:
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except Exception as exc:
        raise ValueError("invalid token") from exc
    try:
        payload = json.loads(plaintext.decode("utf-8"))
    except Exception as exc:
        raise ValueError("invalid token") from exc
    if not isinstance(payload, dict):
        raise ValueError("invalid token")
    return payload


def encrypt_rank_payload(payload: Dict[str, Any]) -> str:
    return _encrypt_payload(payload)


def decrypt_rank_payload(token: str) -> Dict[str, Any]:
    return _decrypt_payload(token)
