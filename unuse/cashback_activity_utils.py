"""返现活动加密载荷工具。"""
import base64


_CASHBACK_XOR_KEY = b"cbk"
_CASHBACK_COMPACT_PREFIX = "c"


def _xor_bytes(raw: bytes) -> bytes:
    return bytes(b ^ _CASHBACK_XOR_KEY[i % len(_CASHBACK_XOR_KEY)] for i, b in enumerate(raw))


def encrypt_cashback_activity_data(poi_value: str, logger=None) -> str:
    normalized_poi = str(poi_value or "").strip()
    if not normalized_poi:
        raise Exception("返现活动参数无效")
    obfuscated = _xor_bytes(normalized_poi.encode("utf-8"))
    checksum = sum(normalized_poi.encode("utf-8")) % 256
    token_body = base64.urlsafe_b64encode(bytes([checksum]) + obfuscated).decode("utf-8").rstrip("=")
    return _CASHBACK_COMPACT_PREFIX + token_body


def decrypt_cashback_activity_data(token: str, logger=None) -> dict:
    try:
        normalized_token = str(token or "").strip()
        if not normalized_token.startswith(_CASHBACK_COMPACT_PREFIX):
            raise Exception("返现活动参数无效")
        encoded = normalized_token[len(_CASHBACK_COMPACT_PREFIX):]
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(encoded + padding)
        if len(raw) < 2:
            raise Exception("返现活动参数无效")
        expected_checksum = raw[0]
        poi_bytes = _xor_bytes(raw[1:])
        if (sum(poi_bytes) % 256) != expected_checksum:
            raise Exception("返现活动参数无效")
        poi_value = poi_bytes.decode("utf-8").strip()
        if not poi_value:
            raise Exception("返现活动参数无效")
        return {"poi_value": poi_value}
    except Exception as e:
        if str(e) == "返现活动参数无效":
            raise
        if logger:
            logger.error(f"解密返现活动数据失败: {e}", exc_info=True)
        raise Exception("返现活动参数无效")
