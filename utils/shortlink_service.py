from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timedelta
from typing import Any
from urllib.parse import urlparse

from utils.logger import setup_logger
from utils.redis_async import redis_delete, redis_get, redis_set, redis_zadd, redis_zrangebyscore, redis_zrem
from utils.timezone_utils import DEFAULT_SHANGHAI_TIMEZONE, get_timezone

logger = setup_logger(__name__)

DEFAULT_SHORTLINK_TTL_SECONDS = 7 * 86400
DEFAULT_SHORTLINK_PUBLIC_BASE_URL = os.getenv("GO_SHORTLINK_PUBLIC_BASE_URL", "").strip().rstrip("/")
DEFAULT_SHORTLINK_CLEANUP_TIME = "00:00"
SHORTLINK_KEY_PREFIX = "wx:shortlink:key:"
SHORTLINK_EXPIRES_ZSET_KEY = "wx:shortlink:expires"
SHORTLINK_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")
SHORTLINK_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
SHORTLINK_CODE_LENGTH = 8
SHORTLINK_MAX_CREATE_ATTEMPTS = 12
URL_TRAILING_PUNCTUATION = "，。；、,.!?！？;)]}>\"'"


@dataclass(frozen=True)
class ShortlinkConfig:
    public_base_url: str
    default_ttl_seconds: int
    cleanup_timezone: str = DEFAULT_SHANGHAI_TIMEZONE
    cleanup_time: str = DEFAULT_SHORTLINK_CLEANUP_TIME


def normalize_public_base_url(value: Any) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        return ""
    if not normalized.startswith(("http://", "https://")):
        raise ValueError("短链公开地址必须以 http:// 或 https:// 开头")
    parsed = urlparse(normalized)
    if not parsed.netloc:
        raise ValueError("短链公开地址缺少域名")
    return normalized


def normalize_default_ttl_seconds(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_SHORTLINK_TTL_SECONDS
    try:
        ttl_seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("短链默认有效期必须是正整数秒") from exc
    if ttl_seconds <= 0:
        raise ValueError("短链默认有效期必须大于 0")
    return ttl_seconds


def normalize_shortlink_config(raw_value: Any) -> dict[str, Any]:
    raw_config = raw_value if isinstance(raw_value, dict) else {}
    public_base_url = normalize_public_base_url(
        raw_config.get("public_base_url", DEFAULT_SHORTLINK_PUBLIC_BASE_URL)
    )
    default_ttl_seconds = normalize_default_ttl_seconds(
        raw_config.get("default_ttl_seconds", DEFAULT_SHORTLINK_TTL_SECONDS)
    )
    cleanup_timezone = str(raw_config.get("cleanup_timezone") or DEFAULT_SHANGHAI_TIMEZONE).strip()
    if cleanup_timezone != DEFAULT_SHANGHAI_TIMEZONE:
        cleanup_timezone = DEFAULT_SHANGHAI_TIMEZONE
    cleanup_time = str(raw_config.get("cleanup_time") or DEFAULT_SHORTLINK_CLEANUP_TIME).strip()
    if cleanup_time != DEFAULT_SHORTLINK_CLEANUP_TIME:
        cleanup_time = DEFAULT_SHORTLINK_CLEANUP_TIME
    return {
        "public_base_url": public_base_url,
        "default_ttl_seconds": default_ttl_seconds,
        "cleanup_timezone": cleanup_timezone,
        "cleanup_time": cleanup_time,
    }


def get_shortlink_config() -> ShortlinkConfig:
    try:
        from utils.system_settings_store import load_system_settings_store

        raw_config = load_system_settings_store().get("shortlink_config", {})
    except Exception:
        raw_config = {}
    normalized = normalize_shortlink_config(raw_config)
    return ShortlinkConfig(**normalized)


def get_shortlink_runtime_diagnostics() -> dict[str, Any]:
    config = get_shortlink_config()
    return {
        "shortlink_public_base_url": config.public_base_url,
        "shortlink_default_ttl_seconds": config.default_ttl_seconds,
        "shortlink_cleanup_timezone": config.cleanup_timezone,
        "shortlink_cleanup_time": config.cleanup_time,
    }


def _shortlink_key(short_key: str) -> str:
    return f"{SHORTLINK_KEY_PREFIX}{short_key}"


def _normalize_target_url(url: str, *, allow_bare_url: bool = False) -> str:
    normalized = str(url or "").strip()
    if allow_bare_url and normalized and "://" not in normalized:
        normalized = f"https://{normalized}"
    if not normalized.startswith(("http://", "https://")):
        raise ValueError("URL 必须以 http:// 或 https:// 开头")
    parsed = urlparse(normalized)
    if not parsed.netloc or "." not in parsed.netloc:
        raise ValueError("URL 域名无效")
    return normalized


def _generate_short_key() -> str:
    return "".join(secrets.choice(SHORTLINK_CODE_ALPHABET) for _ in range(SHORTLINK_CODE_LENGTH))


def _build_public_url(short_key: str, public_base_url: str | None = None) -> str:
    base_url = normalize_public_base_url(public_base_url if public_base_url is not None else get_shortlink_config().public_base_url)
    if not base_url:
        raise ValueError("短链公开地址未配置")
    return f"{base_url}/key/{short_key}"


def build_public_shortlink_url(path_or_code: str, *, base_url: str = "") -> str:
    raw_value = str(path_or_code or "").strip()
    short_key = raw_value
    if raw_value.startswith("/key/"):
        short_key = raw_value[len("/key/") :].strip()
    elif raw_value.startswith("key/"):
        short_key = raw_value[len("key/") :].strip()
    if not SHORTLINK_CODE_RE.fullmatch(short_key):
        raise ValueError("短链 code 无效")
    return _build_public_url(short_key, public_base_url=base_url or None)


def _parse_public_shortlink_host(public_base_url: str) -> str:
    try:
        return str(urlparse(public_base_url).netloc or "").lower()
    except Exception:
        return ""


def _is_existing_public_shortlink(url: str, public_base_url: str) -> bool:
    parsed_url = urlparse(url)
    public_host = _parse_public_shortlink_host(public_base_url)
    if not public_host or str(parsed_url.netloc or "").lower() != public_host:
        return False
    return bool(re.fullmatch(r"/key/[A-Za-z0-9_-]{4,64}", parsed_url.path or ""))


async def create_shortlink_async(
    url: str,
    ttl_seconds: int | None = None,
    *,
    allow_bare_url: bool = False,
    public_base_url: str = "",
    original_url: str = "",
    log_source: str = "",
) -> dict[str, Any]:
    target_url = _normalize_target_url(url, allow_bare_url=allow_bare_url)
    config = get_shortlink_config()
    effective_ttl_seconds = config.default_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
    if effective_ttl_seconds < 0:
        raise ValueError("短链有效期不能小于 0")

    now = int(time.time())
    expires_at = 0 if effective_ttl_seconds == 0 else now + effective_ttl_seconds
    payload = {
        "url": target_url,
        "created_at": now,
        "expires_at": expires_at,
        "ttl_seconds": effective_ttl_seconds,
    }
    payload_bytes = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    for _ in range(SHORTLINK_MAX_CREATE_ATTEMPTS):
        short_key = _generate_short_key()
        if not await redis_set(_shortlink_key(short_key), payload_bytes, nx=True):
            continue
        try:
            if expires_at > 0:
                await redis_zadd(SHORTLINK_EXPIRES_ZSET_KEY, {short_key: float(expires_at)})
        except Exception:
            await redis_delete(_shortlink_key(short_key))
            raise
        public_url = _build_public_url(short_key, public_base_url=public_base_url or config.public_base_url)
        logger.warning(
            "短链创建成功: short_key=%s original_url_len=%s target_url_len=%s ttl_seconds=%s expires_at=%s public_base_url=%s source=%s",
            short_key,
            len(str(original_url or target_url)),
            len(target_url),
            effective_ttl_seconds,
            expires_at,
            public_base_url or config.public_base_url,
            log_source or "direct",
        )
        return {
            "success": True,
            "short_key": short_key,
            "path": f"/key/{short_key}",
            "url": public_url,
            "target_url": target_url,
            "expires_at": expires_at,
            "ttl_seconds": effective_ttl_seconds,
        }

    raise RuntimeError("短链 code 冲突过多，请稍后重试")


async def delete_shortlink_async(short_key: str) -> bool:
    normalized_key = str(short_key or "").strip()
    if not SHORTLINK_CODE_RE.fullmatch(normalized_key):
        return False
    deleted = await redis_delete(_shortlink_key(normalized_key))
    await redis_zrem(SHORTLINK_EXPIRES_ZSET_KEY, normalized_key)
    return bool(deleted)


async def resolve_shortlink_target_async(short_key: str) -> str:
    normalized_key = str(short_key or "").strip()
    if not SHORTLINK_CODE_RE.fullmatch(normalized_key):
        raise KeyError("短链不存在")

    raw_payload = await redis_get(_shortlink_key(normalized_key))
    if not raw_payload:
        raise KeyError("短链不存在")

    try:
        payload = json.loads(raw_payload.decode("utf-8"))
    except Exception as exc:
        await delete_shortlink_async(normalized_key)
        raise KeyError("短链数据无效") from exc

    expires_at = int(payload.get("expires_at") or 0)
    if expires_at > 0 and expires_at <= int(time.time()):
        await delete_shortlink_async(normalized_key)
        raise KeyError("短链已过期")

    target_url = str(payload.get("url") or "").strip()
    try:
        _normalize_target_url(target_url)
    except ValueError as exc:
        await delete_shortlink_async(normalized_key)
        raise KeyError("短链目标无效") from exc
    return target_url


def _split_url_trailing_punctuation(candidate: str) -> tuple[str, str]:
    url = str(candidate or "")
    suffix = ""
    while url and url[-1] in URL_TRAILING_PUNCTUATION:
        suffix = url[-1] + suffix
        url = url[:-1]
    return url, suffix


HTTP_URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)
BARE_URL_RE = re.compile(
    r"(?<![A-Za-z0-9@._:/-])((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s<>'\"]*)?)"
)


def _find_link_matches(text: str, *, include_bare_urls: bool) -> list[tuple[int, int, str, bool]]:
    matches: list[tuple[int, int, str, bool]] = []
    occupied: list[tuple[int, int]] = []

    for match in HTTP_URL_RE.finditer(text):
        raw_url, _ = _split_url_trailing_punctuation(match.group(0))
        if not raw_url:
            continue
        start = match.start()
        end = start + len(raw_url)
        matches.append((start, end, raw_url, False))
        occupied.append((start, end))

    if include_bare_urls:
        for match in BARE_URL_RE.finditer(text):
            start, raw_end = match.span(1)
            if any(start < end and raw_end > existing_start for existing_start, end in occupied):
                continue
            raw_url, _ = _split_url_trailing_punctuation(match.group(1))
            if not raw_url:
                continue
            end = start + len(raw_url)
            matches.append((start, end, raw_url, True))

    matches.sort(key=lambda item: item[0])
    return matches


async def transform_shortlinks_in_text_async(
    text: str,
    *,
    ttl_seconds: int | None = None,
    max_success_count: int = 100,
    include_bare_urls: bool = False,
    public_base_url: str = "",
) -> dict[str, Any]:
    source_text = str(text or "")
    config = get_shortlink_config()
    effective_base_url = normalize_public_base_url(public_base_url or config.public_base_url)
    effective_ttl_seconds = config.default_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
    if not effective_base_url:
        raise ValueError("短链公开地址未配置")

    link_matches = _find_link_matches(source_text, include_bare_urls=include_bare_urls)
    matched_count = len(link_matches)
    if matched_count <= 0 or max_success_count <= 0:
        return {
            "text": source_text,
            "matched_count": matched_count,
            "success_count": 0,
            "failed_count": 0,
            "skipped_count": 0,
            "reused_count": 0,
            "existing_count": 0,
            "results": [],
        }

    output_parts: list[str] = []
    results: list[dict[str, Any]] = []
    created_url_cache: dict[str, dict[str, Any]] = {}
    cursor = 0
    success_count = 0
    failed_count = 0
    skipped_count = 0
    reused_count = 0
    existing_count = 0

    for start, end, raw_url, is_bare_url in link_matches:
        output_parts.append(source_text[cursor:start])
        replacement = raw_url
        if success_count < max_success_count:
            try:
                target_url = _normalize_target_url(raw_url, allow_bare_url=is_bare_url)
                if _is_existing_public_shortlink(target_url, effective_base_url):
                    replacement = target_url
                    existing_count += 1
                elif target_url in created_url_cache:
                    replacement = str(created_url_cache[target_url]["url"])
                    reused_count += 1
                else:
                    shortlink = await create_shortlink_async(
                        target_url,
                        ttl_seconds=ttl_seconds,
                        public_base_url=effective_base_url,
                        original_url=raw_url,
                        log_source="text_transform",
                    )
                    created_url_cache[target_url] = shortlink
                    replacement = str(shortlink["url"])
                    success_count += 1
                    results.append({
                        "original_url": raw_url,
                        "target_url": target_url,
                        "short_url": replacement,
                        "short_key": shortlink["short_key"],
                    })
            except Exception as exc:
                failed_count += 1
                logger.warning("短链替换失败 url=%s error=%s", raw_url, exc)
        else:
            skipped_count += 1
        output_parts.append(replacement)
        cursor = end

    output_parts.append(source_text[cursor:])
    transformed = {
        "text": "".join(output_parts),
        "matched_count": matched_count,
        "success_count": success_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "reused_count": reused_count,
        "existing_count": existing_count,
        "results": results,
    }
    logger.warning(
        "短链批量转换完成: matched=%s success=%s failed=%s skipped=%s reused=%s existing=%s ttl_seconds=%s include_bare_urls=%s public_base_url=%s",
        matched_count,
        success_count,
        failed_count,
        skipped_count,
        reused_count,
        existing_count,
        effective_ttl_seconds,
        include_bare_urls,
        effective_base_url,
    )
    return transformed


async def cleanup_expired_shortlinks_async(now_ts: int | None = None, *, batch_size: int = 500) -> int:
    now = int(now_ts or time.time())
    total_deleted = 0
    while True:
        members = await redis_zrangebyscore(
            SHORTLINK_EXPIRES_ZSET_KEY,
            0,
            float(now),
            start=0,
            num=batch_size,
        )
        if not members:
            break
        short_keys = [
            member.decode("utf-8") if isinstance(member, (bytes, bytearray)) else str(member)
            for member in members
        ]
        for short_key in short_keys:
            await redis_delete(_shortlink_key(short_key))
        await redis_zrem(SHORTLINK_EXPIRES_ZSET_KEY, *short_keys)
        total_deleted += len(short_keys)
        if len(short_keys) < batch_size:
            break
    return total_deleted


_cleanup_task: asyncio.Task | None = None


def _seconds_until_next_cleanup(config: ShortlinkConfig) -> float:
    tz = get_timezone(config.cleanup_timezone)
    now = datetime.now(tz)
    cleanup_at = datetime.combine(now.date(), datetime_time(0, 0), tzinfo=tz)
    if cleanup_at <= now:
        cleanup_at += timedelta(days=1)
    return max(1.0, (cleanup_at - now).total_seconds())


async def _cleanup_worker_async() -> None:
    while True:
        try:
            config = get_shortlink_config()
            await asyncio.sleep(_seconds_until_next_cleanup(config))
            started_at = time.time()
            deleted_count = await cleanup_expired_shortlinks_async()
            logger.info(
                "短链过期清理完成: deleted=%s elapsed=%.2fs timezone=%s",
                deleted_count,
                time.time() - started_at,
                config.cleanup_timezone,
            )
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning("短链过期清理失败: %s", exc, exc_info=True)
            await asyncio.sleep(300)


def start_shortlink_cleanup_task() -> None:
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        _cleanup_task = asyncio.create_task(_cleanup_worker_async())
        logger.info("短链过期清理任务已启动")


async def stop_shortlink_cleanup_task() -> None:
    global _cleanup_task
    task = _cleanup_task
    _cleanup_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
