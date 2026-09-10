from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from utils.logger import setup_logger
from utils.path_utils import resolve_runtime_data_path
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_meituan_coupon_claim_config,
)


logger = setup_logger(__name__)
TIMEZONE = ZoneInfo("Asia/Shanghai")
ISSUE_URLS = {
    "workbuddy": "https://media.meituan.com/fulishemini/couponActivity/sendCouponWork",
    "tabbit": "https://media.meituan.com/fulishemini/couponActivity/sendCouponTabbit",
}
AI_SCENES = {
    "workbuddy": "a0d4da77f918ab204d86c911fcdd0ce1",
    "tabbit": "5a38dc8b7f17f76b9644b78abb41f0bc",
}
ACTIVE_STATUSES = {"queued", "running"}
LOCKING_STATUSES = {"succeeded", "partial_success", "already_received"}
TERMINAL_STATUSES = {
    "succeeded", "partial_success", "already_received", "no_coupon",
    "token_invalid", "rate_limited", "network_failed", "timeout",
    "cancelled", "failed",
}


def _now_text() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _business_date() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d")


def _safe_json(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        return fallback
    return parsed


def _is_40_20_coupon(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        amount = float(value.get("amount_yuan") or 0)
        threshold = float(value.get("threshold_yuan") or 0)
    except (TypeError, ValueError):
        return False
    return abs(amount - 20) < 0.001 and abs(threshold - 40) < 0.001


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _mask_user_id(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= 5:
        return "*" * len(text)
    return f"{text[:3]}***{text[-2:]}"


def get_meituan_coupon_claim_config() -> dict[str, Any]:
    store = load_system_settings_store()
    return normalize_meituan_coupon_claim_config(store.get("meituan_coupon_claim_config"))


def _amount_yuan(value: Any) -> float | int:
    try:
        cents = int(value or 0)
    except (TypeError, ValueError):
        cents = 0
    result = cents / 100
    return int(result) if result.is_integer() else round(result, 2)


def _format_date(value: Any) -> str:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    if timestamp < 10_000_000_000:
        timestamp *= 1000
    try:
        return datetime.fromtimestamp(timestamp / 1000, TIMEZONE).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def normalize_coupon(raw: Any) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    amount = _amount_yuan(item.get("couponValue"))
    threshold = _amount_yuan(item.get("priceLimit"))
    start = _format_date(item.get("couponStartTime"))
    end = _format_date(item.get("couponEndTime"))
    period = f"{start} 至 {end}" if start and end else start or end
    return {
        "name": str(item.get("couponName") or "美团优惠券")[:120],
        "tab_name": str(item.get("tabName") or "")[:80],
        "amount_yuan": amount,
        "threshold_yuan": threshold,
        "valid_period": period[:40],
    }


class MeituanCouponClaimClient:
    def __init__(self) -> None:
        self._worker: _CouponClaimNodeWorker | None = None
        self._worker_lock = asyncio.Lock()

    @property
    def worker_alive(self) -> bool:
        return bool(self._worker is not None and not self._worker.closed)

    async def issue(self, token: str, channel: str, timeout_seconds: int) -> dict[str, Any]:
        if channel not in ISSUE_URLS:
            raise ValueError("未知领券渠道")
        started = time.monotonic()
        try:
            worker = await self._get_worker()
            return await worker.request(token, channel, timeout_seconds)
        except asyncio.TimeoutError:
            return self._error(channel, "timeout", "请求超时，请稍后重试", started)
        except _CouponClaimWorkerError:
            return self._error(channel, "network_failed", "领券服务暂时不可用，请稍后重试", started)
        except Exception:
            return self._error(channel, "failed", "领券请求失败", started)

    async def _get_worker(self) -> "_CouponClaimNodeWorker":
        async with self._worker_lock:
            if self._worker is None or self._worker.closed:
                self._worker = _CouponClaimNodeWorker()
                await self._worker.start()
            return self._worker

    async def close(self) -> None:
        async with self._worker_lock:
            if self._worker is not None:
                await self._worker.close()
                self._worker = None

    @staticmethod
    def _error(
        channel: str, status: str, message: str, started: float, *, http_status: int = 0
    ) -> dict[str, Any]:
        return {
            "channel": channel, "status": status, "http_status": http_status,
            "business_code": None, "message": message, "coupon_count": 0,
            "coupons": [], "duration_ms": int((time.monotonic() - started) * 1000),
        }


class _CouponClaimWorkerError(RuntimeError):
    pass


class _CouponClaimNodeWorker:
    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[Any] | None = None
        self._write_lock = asyncio.Lock()
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.closed = True
        self._script = Path(__file__).resolve().parents[1] / "get_coupon" / "sk.waimaiyouhui.top-source" / "coupon-runtime" / "claim_worker.js"

    async def start(self) -> None:
        if not self._script.exists():
            raise _CouponClaimWorkerError("领券签名 worker 不存在")
        self._process = await asyncio.create_subprocess_exec(
            "node", str(self._script), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        self.closed = False
        self._reader_task = asyncio.create_task(self._read_loop(), name="meituan-coupon-claim-worker-reader")

    async def _read_loop(self) -> None:
        process = self._process
        try:
            while process is not None and process.stdout is not None:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line.decode("utf-8"))
                except Exception:
                    continue
                request_id = str(payload.get("id") or "")
                future = self._pending.pop(request_id, None)
                if future is not None and not future.done():
                    future.set_result(payload)
        finally:
            self.closed = True
            error = _CouponClaimWorkerError("领券签名 worker 已退出")
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()

    async def request(self, token: str, channel: str, timeout_seconds: int) -> dict[str, Any]:
        if self.closed or self._process is None or self._process.stdin is None:
            raise _CouponClaimWorkerError("领券签名 worker 未启动")
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        request = json.dumps({
            "id": request_id, "channel": channel, "token": token,
            "timeout_seconds": int(timeout_seconds),
        }, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            async with self._write_lock:
                self._process.stdin.write(request.encode("utf-8"))
                await self._process.stdin.drain()
            return await asyncio.wait_for(future, timeout=float(timeout_seconds) + 5.0)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(self.cancel(request_id))
            except Exception:
                pass
            self._pending.pop(request_id, None)
            raise
        except Exception:
            self._pending.pop(request_id, None)
            raise

    async def cancel(self, request_id: str) -> None:
        if self.closed or self._process is None or self._process.stdin is None:
            return
        message = json.dumps({"id": request_id, "cancel": True}, separators=(",", ":")) + "\n"
        async with self._write_lock:
            self._process.stdin.write(message.encode("utf-8"))
            await self._process.stdin.drain()

    async def close(self) -> None:
        self.closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        self._reader_task = None
        self._process = None


class MeituanCouponClaimStorage:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else resolve_runtime_data_path("meituan_coupon_claims.db")
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_schema(self) -> None:
        now = _now_text()
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS coupon_claim_jobs (
                    id TEXT PRIMARY KEY,
                    web_user_id INTEGER NOT NULL,
                    token_id INTEGER NOT NULL,
                    token_name TEXT NOT NULL DEFAULT '',
                    meituan_user_id_hash TEXT NOT NULL,
                    meituan_user_id_masked TEXT NOT NULL DEFAULT '',
                    token_fingerprint TEXT NOT NULL,
                    business_date TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'web',
                    status TEXT NOT NULL,
                    channels_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_coupon_claim_user ON coupon_claim_jobs(web_user_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_coupon_claim_account_date ON coupon_claim_jobs(meituan_user_id_hash, business_date, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_coupon_claim_status ON coupon_claim_jobs(status);
                """
            )
            connection.execute(
                """
                UPDATE coupon_claim_jobs
                SET status='failed', error_code='service_restarted',
                    error_message='服务重启，运行中任务已中断', finished_at=?, updated_at=?
                WHERE status IN ('queued','running')
                """,
                (now, now),
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["channels"] = _safe_json(result.pop("channels_json", "{}"), {})
        result["result"] = _safe_json(result.pop("result_json", "{}"), {})
        result["cancel_requested"] = bool(result.get("cancel_requested"))
        result.pop("meituan_user_id_hash", None)
        result.pop("token_fingerprint", None)
        return result

    def create_job(self, payload: dict[str, Any], token: str, meituan_user_id: str) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = _now_text()
        account_hash = _fingerprint(meituan_user_id)
        date = _business_date()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            blocked = connection.execute(
                """
                SELECT id, status FROM coupon_claim_jobs
                WHERE meituan_user_id_hash=? AND business_date=?
                  AND status IN ('queued','running','succeeded','partial_success','already_received')
                ORDER BY created_at DESC LIMIT 1
                """,
                (account_hash, date),
            ).fetchone()
            if blocked is not None:
                raise ValueError("这个美团账号今天已有领券任务或已完成领取")
            connection.execute(
                """
                INSERT INTO coupon_claim_jobs (
                    id, web_user_id, token_id, token_name, meituan_user_id_hash,
                    meituan_user_id_masked, token_fingerprint, business_date, source,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    job_id, int(payload.get("web_user_id") or 0), int(payload.get("token_id") or 0),
                    str(payload.get("token_name") or "")[:100], account_hash,
                    _mask_user_id(meituan_user_id), _fingerprint(token), date,
                    str(payload.get("source") or "web")[:20], now, now,
                ),
            )
        return self.get_job(job_id) or {}

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM coupon_claim_jobs WHERE id=?", (str(job_id),)).fetchone()
        return self._row(row)

    def latest_for_user(self, web_user_id: int, token_id: int = 0) -> dict[str, Any] | None:
        query = "SELECT * FROM coupon_claim_jobs WHERE web_user_id=? AND business_date=?"
        params: list[Any] = [int(web_user_id), _business_date()]
        if int(token_id or 0) > 0:
            query += " AND token_id=?"
            params.append(int(token_id))
        query += " ORDER BY created_at DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, params).fetchone()
        return self._row(row)

    def list_jobs(self, *, limit: int = 100, status: str = "") -> list[dict[str, Any]]:
        query = "SELECT * FROM coupon_claim_jobs"
        params: list[Any] = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row(row) or {} for row in rows]

    def mark_running(self, job_id: str) -> bool:
        now = _now_text()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE coupon_claim_jobs SET status='running', started_at=?, updated_at=? WHERE id=? AND status='queued' AND cancel_requested=0",
                (now, now, job_id),
            )
        return cursor.rowcount == 1

    def set_retry_count(self, job_id: str, retry_count: int) -> None:
        now = _now_text()
        with self._lock, self._connection() as connection:
            connection.execute(
                "UPDATE coupon_claim_jobs SET retry_count=?, updated_at=? WHERE id=?",
                (max(0, int(retry_count)), now, job_id),
            )

    def finish(
        self, job_id: str, status: str, *, channels: dict[str, Any], result: dict[str, Any],
        error_code: str = "", error_message: str = "", duration_ms: int = 0,
    ) -> dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise ValueError("非法任务状态")
        now = _now_text()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE coupon_claim_jobs SET status=?, channels_json=?, result_json=?,
                    error_code=?, error_message=?, duration_ms=?, finished_at=?, updated_at=?
                WHERE id=?
                """,
                (
                    status, json.dumps(channels, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")),
                    str(error_code or "")[:80], str(error_message or "")[:200],
                    max(0, int(duration_ms)), now, now, job_id,
                ),
            )
        return self.get_job(job_id) or {}

    def request_cancel(self, job_id: str) -> bool:
        now = _now_text()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "UPDATE coupon_claim_jobs SET cancel_requested=1, updated_at=? WHERE id=? AND status IN ('queued','running')",
                (now, job_id),
            )
        return cursor.rowcount == 1

    def stats(self) -> dict[str, Any]:
        today = _business_date()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM coupon_claim_jobs WHERE business_date=? GROUP BY status",
                (today,),
            ).fetchall()
            avg = connection.execute(
                "SELECT AVG(duration_ms) FROM coupon_claim_jobs WHERE business_date=? AND finished_at!=''",
                (today,),
            ).fetchone()
            channel_rows = connection.execute(
                "SELECT channels_json FROM coupon_claim_jobs WHERE business_date=? AND finished_at!=''",
                (today,),
            ).fetchall()
            target_rows = connection.execute(
                """
                SELECT meituan_user_id_hash, finished_at, result_json
                FROM coupon_claim_jobs
                WHERE business_date=? AND finished_at!=''
                """,
                (today,),
            ).fetchall()
            latest_error = connection.execute(
                "SELECT error_message, updated_at FROM coupon_claim_jobs WHERE error_message!='' ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        channel_stats = {name: {"success": 0, "total": 0} for name in ISSUE_URLS}
        for row in channel_rows:
            channels = _safe_json(row["channels_json"], {})
            for name in ISSUE_URLS:
                item = channels.get(name) if isinstance(channels, dict) else None
                if not isinstance(item, dict):
                    continue
                channel_stats[name]["total"] += 1
                if item.get("status") == "succeeded":
                    channel_stats[name]["success"] += 1
        target_40_20_coupon_count = 0
        target_40_20_account_hashes: set[str] = set()
        target_40_20_task_count = 0
        target_40_20_latest_at = ""
        for row in target_rows:
            result = _safe_json(row["result_json"], {})
            coupons = result.get("coupons") if isinstance(result, dict) else None
            matched_count = sum(1 for coupon in (coupons or []) if _is_40_20_coupon(coupon))
            if matched_count <= 0:
                continue
            target_40_20_coupon_count += matched_count
            target_40_20_task_count += 1
            account_hash = str(row["meituan_user_id_hash"] or "")
            if account_hash:
                target_40_20_account_hashes.add(account_hash)
            finished_at = str(row["finished_at"] or "")
            if finished_at > target_40_20_latest_at:
                target_40_20_latest_at = finished_at
        return {
            "business_date": today, "total": sum(counts.values()), "counts": counts,
            "running": counts.get("running", 0), "queued": counts.get("queued", 0),
            "average_duration_ms": int((avg[0] if avg else 0) or 0),
            "channels": channel_stats,
            "target_40_20_coupon_count": target_40_20_coupon_count,
            "target_40_20_account_count": len(target_40_20_account_hashes),
            "target_40_20_task_count": target_40_20_task_count,
            "target_40_20_latest_at": target_40_20_latest_at,
            "latest_error": {"message": str(latest_error[0] or "")[:160], "at": str(latest_error[1] or "")} if latest_error else None,
        }


def aggregate_channel_results(channels: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any], str, str]:
    items = [channels.get(name) or {} for name in ISSUE_URLS]
    statuses = [str(item.get("status") or "failed") for item in items]
    succeeded = sum(status == "succeeded" for status in statuses)
    coupons = []
    for name in ISSUE_URLS:
        for coupon in (channels.get(name) or {}).get("coupons") or []:
            coupons.append({**coupon, "source": name})
    result = {
        "coupon_count": len(coupons), "coupons": coupons[:200],
        "channel_summary": {name: str((channels.get(name) or {}).get("status") or "failed") for name in ISSUE_URLS},
    }
    if succeeded == len(ISSUE_URLS):
        return "succeeded", result, "", ""
    if succeeded > 0:
        return "partial_success", result, "partial_success", "部分渠道领取成功"
    if all(status == "already_received" for status in statuses):
        return "already_received", result, "already_received", "这个账号今天已经领取过"
    if all(status == "no_coupon" for status in statuses):
        return "no_coupon", result, "no_coupon", "当前没有可领取神券"
    if set(statuses).issubset({"no_coupon", "already_received"}) and "already_received" in statuses:
        return "already_received", result, "already_received", "这个账号今天已经领取过或当前渠道无可用券"
    for priority in ("token_invalid", "rate_limited", "timeout", "network_failed"):
        if priority in statuses:
            message = next((str(item.get("message") or "") for item in items if item.get("status") == priority), priority)
            return priority, result, priority, message
    return "failed", result, "failed", next((str(item.get("message") or "") for item in items if item.get("message")), "领券失败")


class MeituanCouponClaimService:
    def __init__(
        self, storage: MeituanCouponClaimStorage | None = None, client: MeituanCouponClaimClient | None = None
    ) -> None:
        self.storage = storage or MeituanCouponClaimStorage()
        self.client = client or MeituanCouponClaimClient()
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_limit = 0
        self._active = 0

    def _get_semaphore(self, limit: int) -> asyncio.Semaphore:
        if self._semaphore is None or (self._active == 0 and self._semaphore_limit != limit):
            self._semaphore = asyncio.Semaphore(limit)
            self._semaphore_limit = limit
        return self._semaphore

    def submit(self, payload: dict[str, Any], token: str, meituan_user_id: str) -> dict[str, Any]:
        job = self.storage.create_job(payload, token, meituan_user_id)
        task = asyncio.create_task(self._run(job["id"], token), name=f"coupon-claim-{job['id'][:8]}")
        self._tasks[job["id"]] = task
        task.add_done_callback(lambda _task, job_id=job["id"]: self._tasks.pop(job_id, None))
        return job

    async def _run(self, job_id: str, token: str) -> None:
        config = get_meituan_coupon_claim_config()
        semaphore = self._get_semaphore(int(config["global_concurrency_limit"]))
        started = time.monotonic()
        acquired = False
        try:
            async with semaphore:
                acquired = True
                self._active += 1
                if not self.storage.mark_running(job_id):
                    return
                async def issue_all() -> dict[str, dict[str, Any]]:
                    async def issue_with_retry(channel: str) -> dict[str, Any]:
                        retry_limit = int(config.get("direct_retry_count") or 0)
                        last: dict[str, Any] = {}
                        for attempt in range(retry_limit + 1):
                            last = await self.client.issue(token, channel, int(config["channel_timeout_seconds"]))
                            if last.get("status") not in {"timeout", "network_failed"} or attempt >= retry_limit:
                                return last
                            self.storage.set_retry_count(job_id, attempt + 1)
                            await asyncio.sleep(min(1.0, 0.2 * (attempt + 1)))
                        return last

                    values = await asyncio.gather(*(issue_with_retry(channel) for channel in ISSUE_URLS))
                    return {str(item.get("channel")): item for item in values}
                try:
                    channels = await asyncio.wait_for(issue_all(), timeout=float(config["task_timeout_seconds"]))
                except asyncio.TimeoutError:
                    channels = {name: {"channel": name, "status": "timeout", "message": "任务总超时", "coupon_count": 0, "coupons": []} for name in ISSUE_URLS}
                status, result, error_code, error_message = aggregate_channel_results(channels)
                self.storage.finish(
                    job_id, status, channels=channels, result=result, error_code=error_code,
                    error_message=error_message, duration_ms=int((time.monotonic() - started) * 1000),
                )
                logger.info(
                    "美团40-20领券完成: job=%s status=%s workbuddy=%s tabbit=%s duration_ms=%s",
                    job_id[:10], status, (channels.get("workbuddy") or {}).get("status"),
                    (channels.get("tabbit") or {}).get("status"), int((time.monotonic() - started) * 1000),
                )
        except asyncio.CancelledError:
            self.storage.finish(
                job_id, "cancelled", channels={}, result={}, error_code="cancelled",
                error_message="任务已取消", duration_ms=int((time.monotonic() - started) * 1000),
            )
            raise
        except Exception:
            logger.exception("美团40-20领券任务异常: job=%s", job_id[:10])
            self.storage.finish(
                job_id, "failed", channels={}, result={}, error_code="failed",
                error_message="领券任务执行失败", duration_ms=int((time.monotonic() - started) * 1000),
            )
        finally:
            if acquired and self._active > 0:
                self._active -= 1

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        job = self.storage.get_job(job_id)
        if not job or job.get("status") not in ACTIVE_STATUSES:
            return job
        if self.storage.request_cancel(job_id):
            # Finish in storage before cancelling the asyncio task as a queued
            # task may not have entered _run yet and therefore cannot handle
            # CancelledError itself.
            self.storage.finish(
                job_id, "cancelled", channels={}, result={}, error_code="cancelled",
                error_message="任务已取消",
            )
            task = self._tasks.get(job_id)
            if task is not None:
                task.cancel()
        return self.storage.get_job(job_id)

    def runtime(self) -> dict[str, Any]:
        config = get_meituan_coupon_claim_config()
        queued_tasks = sum(
            1 for task in self.storage.list_jobs(limit=500)
            if str(task.get("status") or "") == "queued"
        )
        return {
            "active_tasks": self._active, "queued_tasks": queued_tasks,
            "tracked_tasks": len(self._tasks),
            "global_limit": int(config["global_concurrency_limit"]),
            "request_mode": "direct", "channels": list(ISSUE_URLS),
            "worker_alive": bool(getattr(self.client, "worker_alive", False)),
        }

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        close = getattr(self.client, "close", None)
        if close is not None:
            await close()


_service: MeituanCouponClaimService | None = None
_service_lock = threading.Lock()


def get_meituan_coupon_claim_service() -> MeituanCouponClaimService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = MeituanCouponClaimService()
    return _service
