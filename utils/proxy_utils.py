"""
代理工具模块
"""
from __future__ import annotations

import asyncio
import gzip
import json
import os
import re
import threading
import time
import urllib.parse
import zlib
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import http_client as requests
from utils.logger import setup_logger
from utils.order_leaderboard_service import get_shared_order_leaderboard_config
from utils.system_settings_store import load_system_settings_store
from utils.timezone_utils import get_timezone

logger = setup_logger(__name__)


PROXY_API_CONFIG = {
    "api_url": (os.getenv("WX_PROXY_API_URL") or os.getenv("PROXY_API_URL") or "").strip()
}
PROXY_API_TIMEOUT_SECONDS = 2
PROXY_FALLBACK_TO_DIRECT = False
PROXY_API_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Accept-Encoding": "identity",
    "User-Agent": "wx-service-proxy-pool/1.0",
}
PROXY_POOL_TARGET_SIZE = 3
PROXY_POOL_MAX_USE_COUNT = 30
PROXY_POOL_MAX_AGE_SECONDS = 60
PROXY_SINGLE_MAX_USE_COUNT = 50
PROXY_SINGLE_MAX_AGE_SECONDS = 120
PROXY_SINGLE_FETCH_BATCH_SIZE = 3
PROXY_RECENT_FAILURE_COOLDOWN_SECONDS = 180
PROXY_POOL_WARMUP_SECONDS = 60
PROXY_POOL_AUTOPREWARM_SECONDS = 30
PROXY_POOL_PREWARM_POLL_SECONDS = 5
DEFAULT_TIMEZONE = "Asia/Shanghai"


class ProxyUnavailableError(RuntimeError):
    """强制代理场景下无法获取代理时抛出的异常。"""


def get_effective_proxy_api_url() -> str:
    try:
        store = load_system_settings_store()
    except Exception:
        store = {}
    runtime_url = str(((store or {}).get("proxy_config") or {}).get("api_url") or "").strip()
    if runtime_url:
        return runtime_url
    return str(PROXY_API_CONFIG.get("api_url") or "").strip()


def _format_exception_message(exc: Exception) -> str:
    message = str(exc).strip()
    if not message:
        return exc.__class__.__name__
    return message


def _is_local_resource_exhausted(exc: Exception) -> bool:
    message = _format_exception_message(exc).lower()
    return isinstance(exc, requests.LocalResourceExhausted) or "too many open files" in message or "emfile" in message


def _build_proxy_api_url(number: int) -> str:
    api_url = get_effective_proxy_api_url()
    parsed = urllib.parse.urlparse(api_url)
    query_params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    normalized_number = str(max(1, int(number)))
    query_params["number"] = normalized_number
    query_params["QTY"] = normalized_number
    if "num" in query_params:
        query_params["num"] = normalized_number
    if "qty" in query_params:
        query_params["qty"] = normalized_number
    if not str(query_params.get("format") or "").strip():
        query_params["format"] = "json"
    query_params.pop("city", None)
    query_params.pop("ISP", None)
    query_params.pop("province", None)
    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query_params)))


def _is_proxy_api_error(proxy_text: str) -> bool:
    return proxy_text in {"206", "210", "406", "215"}


def _translate_proxy_api_error(proxy_text: str) -> str:
    return {
        "206": "IP数量用完",
        "210": "需要添加白名单",
        "406": "提取间隔太快",
        "215": "单次提取数量超过上限",
    }.get(proxy_text, f"未知错误: {proxy_text}")


def _try_gzip_decompress(raw_bytes: bytes) -> bytes:
    try:
        return gzip.decompress(raw_bytes)
    except Exception:
        return b""


def _try_zlib_decompress(raw_bytes: bytes, gzip_wrapper: bool) -> bytes:
    wbits = (16 + zlib.MAX_WBITS) if gzip_wrapper else zlib.MAX_WBITS
    try:
        return zlib.decompress(raw_bytes, wbits)
    except Exception:
        return b""


def _parse_proxy_api_json_response(response: requests.Response) -> Dict[str, Any]:
    raw_bytes = bytes(getattr(response, "content", b"") or b"")
    raw_text = raw_bytes.decode("latin1", errors="ignore").strip()
    if _is_proxy_api_error(raw_text):
        raise RuntimeError(f"代理API返回错误: {_translate_proxy_api_error(raw_text)}")

    raw_candidates: List[bytes] = []
    for candidate in (
        raw_bytes,
        _try_gzip_decompress(raw_bytes),
        _try_zlib_decompress(raw_bytes, gzip_wrapper=True),
        _try_zlib_decompress(raw_bytes, gzip_wrapper=False),
    ):
        if candidate and candidate not in raw_candidates:
            raw_candidates.append(candidate)

    candidate_texts: List[str] = []
    for raw_candidate in raw_candidates:
        decoded_error = raw_candidate.decode("latin1", errors="ignore").strip()
        if _is_proxy_api_error(decoded_error):
            raise RuntimeError(f"代理API返回错误: {_translate_proxy_api_error(decoded_error)}")
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
            try:
                decoded = raw_candidate.decode(encoding).strip()
            except Exception:
                continue
            if decoded and decoded not in candidate_texts:
                candidate_texts.append(decoded)

    for decoded in candidate_texts:
        if _is_proxy_api_error(decoded):
            raise RuntimeError(f"代理API返回错误: {_translate_proxy_api_error(decoded)}")
        try:
            payload = json.loads(decoded)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload

    headers = getattr(response, "headers", {}) or {}
    raise RuntimeError(
        "代理API返回非JSON: "
        f"content_type={headers.get('content-type', '')!r} "
        f"content_encoding={headers.get('content-encoding', '')!r} "
        f"raw={raw_bytes[:120]!r}"
    )


def _extract_response_candidate_texts(response: requests.Response) -> List[str]:
    raw_bytes = bytes(getattr(response, "content", b"") or b"")
    raw_candidates: List[bytes] = []
    for candidate in (
        raw_bytes,
        _try_gzip_decompress(raw_bytes),
        _try_zlib_decompress(raw_bytes, gzip_wrapper=True),
        _try_zlib_decompress(raw_bytes, gzip_wrapper=False),
    ):
        if candidate and candidate not in raw_candidates:
            raw_candidates.append(candidate)

    candidate_texts: List[str] = []
    for raw_candidate in raw_candidates:
        decoded_error = raw_candidate.decode("latin1", errors="ignore").strip()
        if _is_proxy_api_error(decoded_error):
            raise RuntimeError(f"代理API返回错误: {_translate_proxy_api_error(decoded_error)}")
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "latin1"):
            try:
                decoded = raw_candidate.decode(encoding).strip()
            except Exception:
                continue
            if decoded and decoded not in candidate_texts:
                candidate_texts.append(decoded)
    return candidate_texts


def _is_valid_proxy_endpoint(value: str) -> bool:
    return bool(re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}", str(value or "").strip()))


def _parse_proxy_candidates_from_json(payload: Dict[str, Any]) -> List[str]:
    status = str(payload.get("status") or "").strip().lower()
    if status != "success":
        logger.error("代理API返回非成功状态: status=%s payload=%s", status or "empty", payload)
        return []
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        logger.error("代理API返回数据为空: payload=%s", payload)
        return []

    results: List[str] = []
    seen = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_ip = str(item.get("IP") or "").strip()
        if not _is_valid_proxy_endpoint(raw_ip):
            continue
        proxy_url = f"http://{raw_ip}"
        if proxy_url in seen:
            continue
        seen.add(proxy_url)
        results.append(proxy_url)
    return results


def _parse_proxy_candidates_from_text(proxy_text: str) -> List[str]:
    if _is_proxy_api_error(proxy_text):
        raise RuntimeError(f"代理API返回错误: {_translate_proxy_api_error(proxy_text)}")

    results: List[str] = []
    seen: set[str] = set()
    for line in re.split(r"[\r\n,;|]+", str(proxy_text or "").strip()):
        endpoint = str(line or "").strip()
        if not _is_valid_proxy_endpoint(endpoint):
            continue
        proxy_url = f"http://{endpoint}"
        if proxy_url in seen:
            continue
        seen.add(proxy_url)
        results.append(proxy_url)
    return results


def _parse_slot_datetime(base_dt: datetime, slot_time: str):
    parts = str(slot_time or "").strip().split(":", 1)
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except (TypeError, ValueError):
        return None
    return base_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _resolve_proxy_window(now_ts: Optional[float] = None) -> Tuple[str, Optional[str], Optional[float]]:
    config = get_shared_order_leaderboard_config() or {}
    times = [str(item).strip() for item in list(config.get("times", [])) if str(item).strip()]
    timezone_name = str(config.get("timezone") or DEFAULT_TIMEZONE).strip() or DEFAULT_TIMEZONE
    if not times:
        return "inactive", None, None

    now_ts = time.time() if now_ts is None else float(now_ts)
    timezone = get_timezone(timezone_name)
    now_dt = datetime.fromtimestamp(now_ts, timezone)
    candidates: List[Tuple[float, str, str, Optional[float]]] = []

    for day_offset in (-1, 0, 1):
        base_dt = now_dt + timedelta(days=day_offset)
        for slot_time in times:
            slot_dt = _parse_slot_datetime(base_dt, slot_time)
            if slot_dt is None:
                continue
            warmup_start = slot_dt - timedelta(seconds=PROXY_POOL_WARMUP_SECONDS)
            active_end = slot_dt + timedelta(minutes=30)
            frozen_end = slot_dt + timedelta(minutes=32)
            if warmup_start <= now_dt < slot_dt:
                seconds_to_slot = max(0.0, (slot_dt - now_dt).total_seconds())
                candidates.append((abs((now_dt - slot_dt).total_seconds()), "warmup", slot_time, seconds_to_slot))
            elif slot_dt <= now_dt < active_end:
                candidates.append((abs((now_dt - slot_dt).total_seconds()), "active", slot_time, 0.0))
            elif active_end <= now_dt < frozen_end:
                candidates.append((abs((now_dt - slot_dt).total_seconds()), "frozen", slot_time, 0.0))

    if not candidates:
        return "inactive", None, None
    candidates.sort(key=lambda item: item[0])
    _, phase, slot_time, seconds_to_slot = candidates[0]
    return phase, slot_time, seconds_to_slot


def _resolve_proxy_phase(now_ts: Optional[float] = None) -> Tuple[str, Optional[str]]:
    phase, slot_time, _ = _resolve_proxy_window(now_ts)
    return phase, slot_time


def _extract_proxy_url_from_mapping(proxies: Optional[Dict[str, str]]) -> str:
    if not isinstance(proxies, dict):
        return ""
    return str(proxies.get("http") or proxies.get("https") or "").strip()


class _ProxyRuntimeManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pool_items: List[Dict[str, Any]] = []
        self._pool_current_index: Optional[int] = None
        self._pool_phase = "inactive"
        self._pool_slot_time = ""
        self._pool_refill_task: Optional[asyncio.Task] = None
        self._single_current_proxy = ""
        self._single_selected_at = 0.0
        self._single_use_count = 0
        self._single_needs_rotate = False
        self._recent_failed_proxies: Dict[str, float] = {}

    async def acquire_proxy_url(self) -> Optional[str]:
        if not get_effective_proxy_api_url():
            return None

        phase, slot_time = _resolve_proxy_phase()
        self._clear_pool_if_window_expired(phase)
        if phase in {"warmup", "active", "frozen"}:
            proxy_url = await self._acquire_pool_proxy(phase, slot_time or "")
            if proxy_url:
                return proxy_url
        return await self._acquire_single_proxy()

    async def report_success(self, proxy_url: str) -> None:
        if not proxy_url:
            return
        with self._lock:
            self._recent_failed_proxies.pop(proxy_url, None)
        try:
            from utils.log_event_store import append_proxy_event

            phase, _ = _resolve_proxy_phase()
            append_proxy_event(status="success", proxy_url=proxy_url, phase=phase)
        except Exception:
            pass

    async def report_failure(self, proxy_url: str, error: Optional[Exception] = None) -> None:
        if not proxy_url:
            return
        phase, _ = _resolve_proxy_phase()
        refill_task = None
        valid_remaining = 0
        rotated_to_proxy = ""
        evicted_current_proxy = False
        with self._lock:
            self._remember_recent_failure_unlocked(proxy_url)
            if proxy_url == self._single_current_proxy:
                self._single_current_proxy = ""
                self._single_selected_at = 0.0
                self._single_use_count = 0
                self._single_needs_rotate = False
                logger.warning(
                    "单代理已淘汰并完成切换检查: proxy=%s rotated=yes next_proxy=%s valid_remaining=%d error=%s",
                    proxy_url,
                    "",
                    -1,
                    _format_exception_message(error or Exception("unknown")),
                )

            pool_index = self._find_pool_index_by_proxy_unlocked(proxy_url)
            if pool_index is not None:
                self._pool_items[pool_index]["invalid"] = True
                self._pool_items[pool_index]["selected_at"] = 0.0
                self._pool_items[pool_index]["use_count"] = 0
                if self._pool_current_index == pool_index:
                    evicted_current_proxy = True
                    next_index = self._find_next_valid_pool_index_unlocked(pool_index, allow_same=False)
                    if next_index is not None:
                        self._pool_current_index = next_index
                        self._pool_items[next_index]["selected_at"] = time.time()
                        self._pool_items[next_index]["use_count"] = 0
                        rotated_to_proxy = str(self._pool_items[next_index].get("proxy_url") or "").strip()
                    else:
                        self._pool_current_index = None
                        if phase not in {"warmup", "active"}:
                            self._clear_pool_unlocked()
                valid_remaining = self._count_valid_pool_items_unlocked()
                if phase in {"warmup", "active"} and valid_remaining <= 0:
                    refill_task = self._ensure_pool_refill_task_unlocked(PROXY_POOL_TARGET_SIZE)
        error_message = _format_exception_message(error or Exception("unknown"))
        try:
            from utils.log_event_store import append_proxy_event

            append_proxy_event(
                status="failed",
                proxy_url=proxy_url,
                error=error_message,
                phase=phase,
                valid_remaining=valid_remaining,
            )
        except Exception:
            pass
        if refill_task is not None:
            logger.warning(
                "当前代理失效，池已耗尽，开始补充: proxy=%s rotated=%s next_proxy=%s valid_remaining=%d error=%s",
                proxy_url,
                "yes" if evicted_current_proxy else "no",
                rotated_to_proxy,
                valid_remaining,
                error_message,
            )
        elif evicted_current_proxy or valid_remaining >= 0:
            logger.warning(
                "代理已淘汰并完成切换检查: proxy=%s rotated=%s next_proxy=%s valid_remaining=%d error=%s",
                proxy_url,
                "yes" if evicted_current_proxy else "no",
                rotated_to_proxy,
                valid_remaining,
                error_message,
            )

    async def maybe_proactive_pool_warmup(self) -> None:
        phase, slot_time, seconds_to_slot = _resolve_proxy_window()
        if phase != "warmup" or seconds_to_slot is None or seconds_to_slot > PROXY_POOL_AUTOPREWARM_SECONDS:
            return

        refill_task = None
        with self._lock:
            has_valid_pool = self._count_valid_pool_items_unlocked() > 0
            current_task = self._pool_refill_task
            if has_valid_pool or (current_task is not None and not current_task.done()):
                return
            self._pool_phase = phase
            self._pool_slot_time = slot_time or ""
            refill_task = self._ensure_pool_refill_task_unlocked(PROXY_POOL_TARGET_SIZE)
        if refill_task is not None:
            logger.info(
                "热门时段前预热代理池: slot_time=%s seconds_to_slot=%.1f target_size=%d",
                slot_time or "",
                seconds_to_slot,
                PROXY_POOL_TARGET_SIZE,
            )
            await refill_task

    def get_runtime_state(self) -> Dict[str, Any]:
        phase, slot_time = _resolve_proxy_phase()
        with self._lock:
            return {
                "phase": phase,
                "slot_time": slot_time,
                "pool_phase": self._pool_phase,
                "pool_slot_time": self._pool_slot_time,
                "pool_size": len(self._pool_items),
                "pool_valid_size": self._count_valid_pool_items_unlocked(),
                "pool_current_index": self._pool_current_index,
                "single_current_proxy": self._single_current_proxy,
                "single_selected_at": self._single_selected_at,
                "single_use_count": self._single_use_count,
                "single_needs_rotate": self._single_needs_rotate,
            }

    def _clear_pool_if_window_expired(self, phase: str) -> None:
        if phase != "inactive":
            return
        refill_task = None
        with self._lock:
            if not self._pool_items and self._pool_phase == "inactive":
                return
            previous_phase = self._pool_phase
            previous_slot_time = self._pool_slot_time
            cleared_count = len(self._pool_items)
            refill_task = self._pool_refill_task
            self._clear_pool_unlocked()
            self._pool_refill_task = None
        if refill_task is not None and not refill_task.done():
            refill_task.cancel()
        logger.info(
            "排行榜窗口结束，清空代理池: previous_phase=%s previous_slot_time=%s cleared_count=%d",
            previous_phase,
            previous_slot_time,
            cleared_count,
        )

    async def _acquire_pool_proxy(self, phase: str, slot_time: str) -> Optional[str]:
        while True:
            refill_task = None
            selected_proxy = ""
            with self._lock:
                self._pool_phase = phase
                self._pool_slot_time = slot_time
                if phase == "inactive":
                    self._clear_pool_unlocked()
                    return None
                if phase in {"warmup", "active"} and self._count_valid_pool_items_unlocked() <= 0:
                    refill_task = self._ensure_pool_refill_task_unlocked(PROXY_POOL_TARGET_SIZE)

                current_index = self._resolve_pool_current_index_unlocked(time.time())
                if current_index is not None:
                    item = self._pool_items[current_index]
                    item["use_count"] = int(item.get("use_count") or 0) + 1
                    selected_proxy = str(item.get("proxy_url") or "").strip()

                if not selected_proxy and phase == "frozen":
                    self._clear_pool_unlocked()
                    break

            if selected_proxy:
                return selected_proxy
            if refill_task is None:
                break
            try:
                await refill_task
            except Exception as exc:
                logger.error("代理池补充失败: %s", _format_exception_message(exc))
                break
        return None

    async def _acquire_single_proxy(self) -> Optional[str]:
        while True:
            need_fetch = False
            current_proxy = ""
            with self._lock:
                self._prune_recent_failed_proxies_unlocked()
                if self._single_current_proxy:
                    expired = (time.time() - self._single_selected_at) >= PROXY_SINGLE_MAX_AGE_SECONDS
                    overused = self._single_use_count >= PROXY_SINGLE_MAX_USE_COUNT
                    if self._single_needs_rotate or expired or overused:
                        self._single_current_proxy = ""
                        self._single_selected_at = 0.0
                        self._single_use_count = 0
                        self._single_needs_rotate = False
                        need_fetch = True
                    else:
                        self._single_use_count += 1
                        current_proxy = self._single_current_proxy
                else:
                    need_fetch = True

            if current_proxy:
                return current_proxy
            if not need_fetch:
                return None

            proxy_candidates = await get_proxies_from_api(PROXY_SINGLE_FETCH_BATCH_SIZE)
            if not proxy_candidates:
                return None
            with self._lock:
                proxy_url = self._select_single_proxy_candidate_unlocked(proxy_candidates)
            if not proxy_url:
                proxy_url = proxy_candidates[0]
            with self._lock:
                self._single_current_proxy = proxy_url
                self._single_selected_at = time.time()
                self._single_use_count = 1
                self._single_needs_rotate = False
            return proxy_url

    def _ensure_pool_refill_task_unlocked(self, refill_count: int) -> Optional[asyncio.Task]:
        if refill_count <= 0:
            return None
        task = self._pool_refill_task
        if task is not None and not task.done():
            return task
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._pool_refill_task = loop.create_task(self._refill_pool_async(refill_count))
        return self._pool_refill_task

    async def _refill_pool_async(self, refill_count: int) -> None:
        try:
            proxy_urls = await get_proxies_from_api(refill_count)
            if not proxy_urls:
                return
            fetched_at = time.time()
            with self._lock:
                self._prune_recent_failed_proxies_unlocked()
                if self._pool_phase == "inactive":
                    return
                existing = {str(item.get("proxy_url") or "").strip() for item in self._pool_items}
                for proxy_url in proxy_urls:
                    if proxy_url in existing:
                        continue
                    if self._is_recent_failed_proxy_unlocked(proxy_url):
                        continue
                    existing.add(proxy_url)
                    self._pool_items.append({
                        "proxy_url": proxy_url,
                        "fetched_at": fetched_at,
                        "selected_at": 0.0,
                        "use_count": 0,
                        "invalid": False,
                    })
                if self._pool_current_index is None:
                    self._pool_current_index = self._find_next_valid_pool_index_unlocked(None, allow_same=True)
                    if self._pool_current_index is not None:
                        self._pool_items[self._pool_current_index]["selected_at"] = fetched_at
                        self._pool_items[self._pool_current_index]["use_count"] = 0
            logger.info(
                "代理池补充完成: requested=%d current_size=%d valid_size=%d",
                refill_count,
                len(self._pool_items),
                self.get_runtime_state()["pool_valid_size"],
            )
        finally:
            with self._lock:
                self._pool_refill_task = None

    def _clear_pool_unlocked(self) -> None:
        self._pool_items = []
        self._pool_current_index = None
        self._pool_phase = "inactive"
        self._pool_slot_time = ""

    def _remember_recent_failure_unlocked(self, proxy_url: str) -> None:
        normalized = str(proxy_url or "").strip()
        if not normalized:
            return
        self._recent_failed_proxies[normalized] = time.time() + PROXY_RECENT_FAILURE_COOLDOWN_SECONDS

    def _prune_recent_failed_proxies_unlocked(self) -> None:
        now = time.time()
        expired = [
            proxy_url
            for proxy_url, deadline in self._recent_failed_proxies.items()
            if deadline <= now
        ]
        for proxy_url in expired:
            self._recent_failed_proxies.pop(proxy_url, None)

    def _is_recent_failed_proxy_unlocked(self, proxy_url: str) -> bool:
        normalized = str(proxy_url or "").strip()
        if not normalized:
            return False
        deadline = float(self._recent_failed_proxies.get(normalized) or 0.0)
        return deadline > time.time()

    def _select_single_proxy_candidate_unlocked(self, proxy_candidates: List[str]) -> str:
        self._prune_recent_failed_proxies_unlocked()
        normalized_candidates = [str(item or "").strip() for item in proxy_candidates if str(item or "").strip()]
        current_proxy = str(self._single_current_proxy or "").strip()
        for proxy_url in normalized_candidates:
            if proxy_url == current_proxy:
                continue
            if self._is_recent_failed_proxy_unlocked(proxy_url):
                continue
            return proxy_url
        for proxy_url in normalized_candidates:
            if proxy_url != current_proxy:
                return proxy_url
        return normalized_candidates[0] if normalized_candidates else ""

    def _count_valid_pool_items_unlocked(self) -> int:
        return sum(1 for item in self._pool_items if not bool(item.get("invalid")))

    def _find_pool_index_by_proxy_unlocked(self, proxy_url: str) -> Optional[int]:
        for index, item in enumerate(self._pool_items):
            if str(item.get("proxy_url") or "").strip() == proxy_url:
                return index
        return None

    def _find_next_valid_pool_index_unlocked(self, start_index: Optional[int], allow_same: bool) -> Optional[int]:
        if not self._pool_items:
            return None
        total = len(self._pool_items)
        if start_index is None:
            search_order = range(total)
        else:
            search_order = [((start_index + offset) % total) for offset in range(0 if allow_same else 1, total + (1 if allow_same else 0))]
        for index in search_order:
            if index < 0 or index >= total:
                continue
            item = self._pool_items[index]
            if bool(item.get("invalid")):
                continue
            return index
        return None

    def _resolve_pool_current_index_unlocked(self, now: float) -> Optional[int]:
        current_index = self._pool_current_index
        if current_index is None or current_index >= len(self._pool_items):
            next_index = self._find_next_valid_pool_index_unlocked(None, allow_same=True)
            if next_index is None:
                self._pool_current_index = None
                return None
            self._pool_current_index = next_index
            self._pool_items[next_index]["selected_at"] = now
            self._pool_items[next_index]["use_count"] = 0
            return next_index

        current_item = self._pool_items[current_index]
        if bool(current_item.get("invalid")):
            next_index = self._find_next_valid_pool_index_unlocked(current_index, allow_same=False)
            if next_index is None:
                self._pool_current_index = None
                return None
            self._pool_current_index = next_index
            self._pool_items[next_index]["selected_at"] = now
            self._pool_items[next_index]["use_count"] = 0
            return next_index

        selected_at = float(current_item.get("selected_at") or 0.0)
        use_count = int(current_item.get("use_count") or 0)
        expired = selected_at > 0 and (now - selected_at) >= PROXY_POOL_MAX_AGE_SECONDS
        overused = use_count >= PROXY_POOL_MAX_USE_COUNT
        if not expired and not overused:
            return current_index

        next_index = self._find_next_valid_pool_index_unlocked(current_index, allow_same=False)
        if next_index is None:
            self._pool_current_index = None
            return None
        self._pool_current_index = next_index
        self._pool_items[next_index]["selected_at"] = now
        self._pool_items[next_index]["use_count"] = 0
        return next_index


_proxy_runtime_manager = _ProxyRuntimeManager()
_proxy_pool_prewarm_task: Optional[asyncio.Task] = None


async def _proxy_pool_prewarm_loop() -> None:
    while True:
        try:
            await _proxy_runtime_manager.maybe_proactive_pool_warmup()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("代理池预热检查失败: %s", _format_exception_message(exc))
        await asyncio.sleep(PROXY_POOL_PREWARM_POLL_SECONDS)


def start_proxy_pool_prewarm_task() -> None:
    global _proxy_pool_prewarm_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _proxy_pool_prewarm_task is not None and not _proxy_pool_prewarm_task.done():
        return
    _proxy_pool_prewarm_task = loop.create_task(_proxy_pool_prewarm_loop())


async def stop_proxy_pool_prewarm_task() -> None:
    global _proxy_pool_prewarm_task
    task = _proxy_pool_prewarm_task
    _proxy_pool_prewarm_task = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def get_proxies_from_api(number: int) -> List[str]:
    try:
        api_url = _build_proxy_api_url(number)
        response = await requests.get(
            api_url,
            timeout=PROXY_API_TIMEOUT_SECONDS,
            headers=PROXY_API_HEADERS,
        )
        response.raise_for_status()
        payload = None
        proxies: List[str] = []
        try:
            payload = _parse_proxy_api_json_response(response)
            proxies = _parse_proxy_candidates_from_json(payload)
        except RuntimeError as exc:
            if "代理API返回非JSON" not in str(exc):
                raise
            for text in _extract_response_candidate_texts(response):
                proxies = _parse_proxy_candidates_from_text(text)
                if proxies:
                    break
        if not proxies:
            if payload is not None:
                logger.error("代理JSON内容无有效代理: payload=%s", payload)
            else:
                logger.error("代理文本内容无有效代理: api_url=%s", api_url)
            return []

        logger.info(
            "从API批量获取代理成功: count=%d number=%s left_time=%s requested=%d",
            len(proxies),
            payload.get("number") if isinstance(payload, dict) else None,
            payload.get("left_time") if isinstance(payload, dict) else None,
            number,
        )
        return proxies
    except requests.LocalResourceExhausted as exc:
        logger.error("从代理API获取代理失败: local_resource_exhausted error=%s", _format_exception_message(exc))
        raise
    except Exception as exc:
        logger.error("从代理API获取代理失败: %s", _format_exception_message(exc))
        return []


async def get_proxy_from_api(number: int = 1) -> Optional[str]:
    proxies = await get_proxies_from_api(number)
    if not proxies:
        return None
    return proxies[0]


async def test_proxy_api_async(number: int = 1) -> Dict[str, Any]:
    effective_api_url = get_effective_proxy_api_url()
    if not effective_api_url:
        raise ValueError("代理 API 地址未配置")

    requested = max(1, int(number))
    api_url = _build_proxy_api_url(requested)
    response = await requests.get(
        api_url,
        timeout=PROXY_API_TIMEOUT_SECONDS,
        headers=PROXY_API_HEADERS,
    )
    response.raise_for_status()
    payload = None
    response_format = "json"
    try:
        payload = _parse_proxy_api_json_response(response)
        proxies = _parse_proxy_candidates_from_json(payload)
    except RuntimeError as exc:
        if "代理API返回非JSON" not in str(exc):
            raise
        response_format = "text"
        proxies = []
        for text in _extract_response_candidate_texts(response):
            proxies = _parse_proxy_candidates_from_text(text)
            if proxies:
                break
    if not proxies:
        raise ValueError("代理接口返回成功，但没有可用代理")

    return {
        "message": "代理测试成功",
        "effective_api_url": effective_api_url,
        "request_url": api_url,
        "response_format": response_format,
        "proxy_count": len(proxies),
        "sample_proxy": proxies[0],
        "upstream_status": payload.get("status") if isinstance(payload, dict) else None,
        "upstream_number": payload.get("number") if isinstance(payload, dict) else None,
        "upstream_left_time": payload.get("left_time") if isinstance(payload, dict) else None,
    }


async def report_proxy_success_async(proxy_url: str) -> None:
    await _proxy_runtime_manager.report_success(proxy_url)


async def report_proxy_failure_async(proxy_url: str, error: Optional[Exception] = None) -> None:
    await _proxy_runtime_manager.report_failure(proxy_url, error=error)


def get_proxy_runtime_state() -> Dict[str, Any]:
    state = _proxy_runtime_manager.get_runtime_state()
    state["effective_api_url"] = get_effective_proxy_api_url()
    return state


async def get_proxy_config_async() -> Dict[str, str]:
    if not get_effective_proxy_api_url():
        return {}

    try:
        proxy_url = await _proxy_runtime_manager.acquire_proxy_url()
    except requests.LocalResourceExhausted:
        logger.warning("无法获取代理地址: local_resource_exhausted")
        raise
    if not proxy_url:
        logger.warning("无法获取代理地址")
        if PROXY_FALLBACK_TO_DIRECT:
            logger.info("回退到不使用代理的直接连接")
            return {}
        return {}

    return {
        "http": proxy_url,
        "https": proxy_url,
    }


async def require_proxy_config_async() -> Dict[str, str]:
    proxies = await get_proxy_config_async()
    if proxies:
        return proxies
    raise ProxyUnavailableError("无法获取代理地址，请求已取消")


async def get_proxy_config() -> Dict[str, str]:
    return await get_proxy_config_async()
