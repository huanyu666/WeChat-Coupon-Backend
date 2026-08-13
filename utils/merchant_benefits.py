from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from utils import http_client
from utils.logger import setup_logger
from utils.meituan_utils import (
    build_meituan_coupon_url,
    build_meituan_official_cashback_url,
    get_default_meituan_coupon_account_id,
)
from utils.path_utils import resolve_runtime_data_path
from utils.system_settings_store import load_system_settings_store, normalize_merchant_benefits_config

logger = setup_logger(__name__)
SERVICE_URL = os.getenv("MERCHANT_BENEFITS_SERVICE_URL", "http://merchant-benefits:18180").rstrip("/")
TIMEZONE = ZoneInfo("Asia/Shanghai")
QUERY_PROTOCOL_VERSION = "cashback-smart-order-v2"


def _safe_reason(value: Any) -> str:
    return str(value or "UNKNOWN").strip()[:160]


def _normalize_name(value: Any) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def extract_shared_merchant_name(value: Any) -> str:
    import re

    text = str(value or "").strip()
    for pattern in (r"「([^」]+)」", r"『([^』]+)』", r"【([^】]+)】"):
        match = re.search(pattern, text)
        if match:
            return str(match.group(1) or "").strip()
    return ""


def _is_https_meituan_url(value: str, path_fragment: str) -> bool:
    try:
        parsed = urlparse(str(value or "").strip())
    except Exception:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower().endswith("offsiteact.meituan.com")
        and path_fragment in (parsed.path or "")
    )


def validate_cashback_base_url(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if _is_https_meituan_url(normalized, "/web/hoae/order_cashback_activity/"):
        return normalized
    # Keep the historical mini-program page path usable by existing replies.
    # The benefits service will simply skip cashback API lookup for this form.
    if normalized.startswith("pages/") and "?" in normalized:
        return normalized
    raise ValueError("官方返现基础链接必须是美团 HTTPS 返现活动地址，或保留现有 pages/ 小程序路径")


def get_merchant_benefits_config() -> dict[str, Any]:
    store = load_system_settings_store()
    return normalize_merchant_benefits_config(store.get("merchant_benefits_config"))


def _query_links(account_id: str, poi_id_str: str) -> tuple[str, str]:
    coupon_url = build_meituan_coupon_url(account_id, poi_id_str, logger, variant="v8")
    cashback_url = build_meituan_official_cashback_url(account_id, poi_id_str, logger)
    if coupon_url and not _is_https_meituan_url(coupon_url, "/web/hoae/collection_waimai_v8/"):
        coupon_url = ""
    if cashback_url and not _is_https_meituan_url(cashback_url, "/web/hoae/order_cashback_activity/"):
        cashback_url = ""
    return coupon_url, cashback_url


def get_benefits_link_status(account_id: str = "") -> dict[str, Any]:
    normalized_account = str(account_id or "").strip() or get_default_meituan_coupon_account_id(logger)
    if not normalized_account:
        return {
            "account_configured": False,
            "coupon_query_configured": False,
            "cashback_query_configured": False,
        }
    coupon_url, cashback_url = _query_links(normalized_account, "0")
    return {
        "account_configured": True,
        "coupon_query_configured": bool(coupon_url),
        "cashback_query_configured": bool(cashback_url),
    }


class MerchantBenefitsStorage:
    def __init__(self) -> None:
        self.directory = resolve_runtime_data_path("merchant_benefits")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "merchant_benefits.db"
        self._lock = threading.RLock()
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS benefits_cache (
                    cache_key TEXT PRIMARY KEY,
                    requested_poi_id_str TEXT NOT NULL,
                    canonical_poi_id_str TEXT NOT NULL DEFAULT '',
                    merchant_name TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL DEFAULT '',
                    config_fingerprint TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    result_kind TEXT NOT NULL,
                    queried_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_benefits_cache_expiry ON benefits_cache(expires_at);
                CREATE TABLE IF NOT EXISTS query_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_benefits_events_created ON query_events(created_at);
                """
            )

    def get(self, cache_key: str) -> dict[str, Any] | None:
        now = int(time.time())
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT result_json FROM benefits_cache WHERE cache_key = ? AND expires_at > ?",
                (cache_key, now),
            ).fetchone()
        if not row:
            return None
        try:
            result = json.loads(row["result_json"])
        except Exception:
            return None
        return result if isinstance(result, dict) else None

    def put(self, cache_key: str, result: dict[str, Any], *, ttl_seconds: int, account_id: str, config_fingerprint: str) -> None:
        now = int(time.time())
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO benefits_cache (
                    cache_key, requested_poi_id_str, canonical_poi_id_str, merchant_name,
                    account_id, config_fingerprint, result_json, result_kind, queried_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    canonical_poi_id_str=excluded.canonical_poi_id_str,
                    merchant_name=excluded.merchant_name,
                    result_json=excluded.result_json,
                    result_kind=excluded.result_kind,
                    queried_at=excluded.queried_at,
                    expires_at=excluded.expires_at
                """,
                (
                    cache_key,
                    str(result.get("original_poi_id_str") or ""),
                    str(result.get("canonical_poi_id_str") or ""),
                    str(result.get("merchant_name") or ""),
                    account_id,
                    config_fingerprint,
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    str(result.get("status") or "unknown"),
                    now,
                    now + max(1, int(ttl_seconds)),
                ),
            )

    def record_event(self, *, source: str, outcome: str, cache_hit: bool, duration_ms: int, error_code: str = "") -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO query_events(created_at, source, outcome, cache_hit, duration_ms, error_code) VALUES (?, ?, ?, ?, ?, ?)",
                (int(time.time()), str(source or "unknown")[:32], str(outcome or "unknown")[:32], int(cache_hit), max(0, int(duration_ms)), _safe_reason(error_code)),
            )

    def stats(self) -> dict[str, Any]:
        now = int(time.time())
        today = datetime.now(TIMEZONE).replace(hour=0, minute=0, second=0, microsecond=0)
        today_ts = int(today.timestamp())
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT outcome, cache_hit, duration_ms, error_code, created_at FROM query_events WHERE created_at >= ? ORDER BY id",
                (today_ts,),
            ).fetchall()
            cache_count = int(connection.execute("SELECT COUNT(*) FROM benefits_cache WHERE expires_at > ?", (now,)).fetchone()[0])
        total = len(rows)
        cache_hits = sum(int(row["cache_hit"] or 0) for row in rows)
        failures = [row for row in rows if str(row["outcome"] or "") == "unknown"]
        return {
            "today_total": total,
            "today_ok": sum(1 for row in rows if str(row["outcome"] or "") == "ok"),
            "today_no_benefits": sum(1 for row in rows if str(row["outcome"] or "") == "no_benefits"),
            "today_unknown": len(failures),
            "cache_entries": cache_count,
            "cache_hit_rate": round(cache_hits * 100.0 / total, 1) if total else 0.0,
            "average_duration_ms": round(sum(int(row["duration_ms"] or 0) for row in rows) / total) if total else 0,
            "last_error": _safe_reason(failures[-1]["error_code"]) if failures else "",
            "last_error_at": int(failures[-1]["created_at"] or 0) if failures else 0,
        }


class MerchantBenefitsService:
    def __init__(self) -> None:
        self.storage = MerchantBenefitsStorage()
        self._inflight: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._inflight_lock = asyncio.Lock()

    @staticmethod
    def _fingerprint(config: dict[str, Any], account_id: str, coupon_url: str, cashback_url: str) -> str:
        payload = json.dumps(
            {
                "query_protocol_version": QUERY_PROTOCOL_VERSION,
                "account_id": account_id,
                "latitude": config["latitude"],
                "longitude": config["longitude"],
                "coupon_url": coupon_url,
                "cashback_url": cashback_url,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def query(
        self,
        *,
        poi_id_str: str,
        merchant_name: str,
        account_id: str = "",
        source: str = "web",
        force_refresh: bool = False,
        allow_unknown_merchant: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        config = get_merchant_benefits_config()
        if not config["enabled"] and source != "admin_test":
            return {"success": True, "status": "disabled", "coupon": {"status": "unknown"}, "cashback": {"status": "unknown"}}

        normalized_poi = str(poi_id_str or "").strip()
        normalized_name = str(merchant_name or "").strip()
        normalized_account = str(account_id or "").strip() or get_default_meituan_coupon_account_id(logger)
        if not normalized_poi or not normalized_account or (not normalized_name and not allow_unknown_merchant):
            raise ValueError("缺少商家 POI、名称或公众号配置")
        coupon_url, cashback_url = _query_links(normalized_account, normalized_poi)
        if not coupon_url:
            raise ValueError("当前公众号未配置有效的商家券基础链接")
        fingerprint = self._fingerprint(config, normalized_account, coupon_url, cashback_url)
        cache_key = hashlib.sha256(f"{normalized_poi}|{_normalize_name(normalized_name)}|{fingerprint}".encode()).hexdigest()
        if not force_refresh:
            cached = self.storage.get(cache_key)
            if cached:
                cached = dict(cached)
                cached["cache_hit"] = True
                self.storage.record_event(source=source, outcome=self._outcome(cached), cache_hit=True, duration_ms=int((time.monotonic() - started) * 1000))
                return cached

        async with self._inflight_lock:
            task = self._inflight.get(cache_key)
            if task is None:
                task = asyncio.create_task(
                    self._query_uncached(
                        cache_key=cache_key,
                        poi_id_str=normalized_poi,
                        merchant_name=normalized_name,
                        account_id=normalized_account,
                        coupon_url=coupon_url,
                        cashback_url=cashback_url,
                        config=config,
                        fingerprint=fingerprint,
                    )
                )
                self._inflight[cache_key] = task
        try:
            result = await asyncio.shield(task)
            self.storage.record_event(source=source, outcome=self._outcome(result), cache_hit=False, duration_ms=int((time.monotonic() - started) * 1000))
            return result
        except Exception as exc:
            self.storage.record_event(source=source, outcome="unknown", cache_hit=False, duration_ms=int((time.monotonic() - started) * 1000), error_code=_safe_reason(exc))
            raise
        finally:
            if task.done():
                async with self._inflight_lock:
                    if self._inflight.get(cache_key) is task:
                        self._inflight.pop(cache_key, None)

    async def _query_uncached(self, **kwargs: Any) -> dict[str, Any]:
        response = await http_client.post(
            f"{SERVICE_URL}/v1/merchant-benefits/query",
            json={
                "requested_poi_id_str": kwargs["poi_id_str"],
                "merchant_name": kwargs["merchant_name"],
                "coupon_page_url": kwargs["coupon_url"],
                "cashback_base_url": kwargs["cashback_url"],
                "latitude": kwargs["config"]["latitude"],
                "longitude": kwargs["config"]["longitude"],
            },
            timeout=17.0,
            stateless_cookies=True,
        )
        try:
            payload = response.json()
        except Exception as exc:
            raise RuntimeError("权益服务响应格式异常") from exc
        if response.status_code != 200 or not isinstance(payload, dict) or not payload.get("success"):
            raise RuntimeError(_safe_reason(payload.get("error") if isinstance(payload, dict) else f"HTTP_{response.status_code}"))
        result = dict(payload)
        result["cache_hit"] = False
        result["account_id"] = kwargs["account_id"]
        positive = any(
            str((result.get(section) or {}).get("status") or "").startswith("has_")
            for section in ("coupon", "cashback")
        )
        has_unknown_section = any(
            str((result.get(section) or {}).get("status") or "") == "unknown"
            for section in ("coupon", "cashback")
        )
        if result.get("status") == "ok":
            transient_unknown = str((result.get("cashback") or {}).get("reason") or "") in {
                "FIRST_POI_MISMATCH",
                "CASHBACK_CANONICAL_POI_MISMATCH",
            }
            if transient_unknown:
                # The cashback endpoint returns a dynamic ranked list. A target
                # missing from one response is not proof of no cashback and must
                # not become a reusable negative cache entry.
                ttl = 0
            elif has_unknown_section:
                ttl = min(30, kwargs["config"]["negative_cache_seconds"])
            else:
                ttl = kwargs["config"]["positive_cache_seconds"] if positive else kwargs["config"]["negative_cache_seconds"]
            if ttl > 0:
                self.storage.put(kwargs["cache_key"], result, ttl_seconds=ttl, account_id=kwargs["account_id"], config_fingerprint=kwargs["fingerprint"])
        return result

    @staticmethod
    def _outcome(result: dict[str, Any]) -> str:
        if str(result.get("status") or "") != "ok":
            return "unknown"
        if any(
            str((result.get(key) or {}).get("status") or "") == "unknown"
            for key in ("coupon", "cashback")
        ):
            return "unknown"
        has_benefits = any(str((result.get(key) or {}).get("status") or "").startswith("has_") for key in ("coupon", "cashback"))
        return "ok" if has_benefits else "no_benefits"

    async def health(self) -> dict[str, Any]:
        try:
            response = await http_client.get(f"{SERVICE_URL}/healthz", timeout=3.0, stateless_cookies=True)
            payload = response.json()
            return payload if isinstance(payload, dict) else {"ok": False, "error": "invalid_response"}
        except Exception as exc:
            return {"ok": False, "error": _safe_reason(exc)}


_service: MerchantBenefitsService | None = None


def get_merchant_benefits_service() -> MerchantBenefitsService:
    global _service
    if _service is None:
        _service = MerchantBenefitsService()
    return _service


def format_benefits_for_wechat(result: dict[str, Any]) -> list[str]:
    if str(result.get("status") or "") != "ok":
        return []
    lines: list[str] = []
    coupon = result.get("coupon") if isinstance(result.get("coupon"), dict) else {}
    cashback = result.get("cashback") if isinstance(result.get("cashback"), dict) else {}
    if coupon.get("status") == "has_coupon":
        lines.append(f"商家券：{coupon.get('amount_yuan', 0):g}元，满{coupon.get('threshold_yuan', 0):g}元可用")
    elif coupon.get("status") == "no_coupon":
        lines.append("商家券：暂无商家券")
    else:
        lines.append("商家券：暂时无法确认")
    if cashback.get("status") == "has_cashback":
        inventory_text = ""
        if str(cashback.get("sign_status") or "") == "NO_INVENTORY":
            inventory_text = "，名额已抢完"
        else:
            try:
                inventory = float(cashback.get("valid_inventory"))
                inventory_text = f"，剩余{inventory:g}份"
            except (TypeError, ValueError):
                pass
        lines.append(f"返现：最高{float(cashback.get('total_max_yuan') or 0):g}元{inventory_text}")
        lines.append(
            f"下单：{float(cashback.get('order_ratio') or 0):g}%，"
            f"最高{float(cashback.get('order_max_yuan') or 0):g}元；"
            f"评价：{float(cashback.get('review_ratio') or 0):g}%，"
            f"最高{float(cashback.get('review_max_yuan') or 0):g}元"
        )
        cashback_url = str(result.get("cashback_url") or "").strip()
        if cashback_url:
            lines.append(f'<a href="{cashback_url}">参加官方返现</a>')
    elif cashback.get("status") == "no_cashback":
        lines.append("返现：暂无返现活动")
    elif cashback.get("status") == "not_configured":
        lines.append("返现：当前公众号未配置返现查询")
    else:
        lines.append("返现：暂时无法确认")
    return lines


async def aquery_benefits_for_wechat(
    *,
    poi_id_str: str,
    merchant_name: str = "",
    account_id: str,
    source: str,
    timeout_seconds: float = 12.0,
) -> dict[str, Any] | None:
    try:
        return await asyncio.wait_for(
            get_merchant_benefits_service().query(
                poi_id_str=poi_id_str,
                merchant_name=merchant_name,
                account_id=account_id,
                source=source,
                allow_unknown_merchant=not bool(str(merchant_name or "").strip()),
            ),
            timeout=max(0.5, float(timeout_seconds)),
        )
    except Exception as exc:
        logger.info(
            "公众号商家权益查询未附加，不影响原回复: source=%s error=%s",
            str(source or "wechat")[:40],
            _safe_reason(exc),
        )
        return None
