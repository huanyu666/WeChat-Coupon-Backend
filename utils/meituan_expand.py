from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from Crypto.Cipher import AES

from utils.logger import setup_logger
from utils.path_utils import resolve_runtime_data_path
from utils.system_settings_store import load_system_settings_store, normalize_meituan_expand_config


logger = setup_logger(__name__)
TIMEZONE = ZoneInfo("Asia/Shanghai")
HOST = "https://market.waimai.meituan.com"
ALLOWED_ACCOUNT_HOSTS = {"i.meituan.com", "meituan.com", "www.meituan.com"}
UA_MT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN"
)
UA_WM = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.75(0x18004b46) NetType/WIFI Language/zh_CN "
    "miniProgram/wx2c348cf579062e56"
)
CHANNELS = (
    {
        "name": "mt_mp",
        "ctype": "mt_mp",
        "wm_ctype": "",
        "app_id": "wxde8ac0a21135c07d",
        "page_source": "610",
        "wm_appversion": "10.28.01",
        "gd_ctype": "mt_mp",
        "gd_entry": "",
        "user_agent": UA_MT,
        "send_cookie": True,
    },
    {
        "name": "wm_wxapp",
        "ctype": "wm_wxapp",
        "wm_ctype": "wxapp",
        "app_id": "wx2c348cf579062e56",
        "page_source": "103",
        "wm_appversion": "10.30.01",
        "gd_ctype": "wm_wxapp",
        "gd_entry": "wxTab",
        "user_agent": UA_WM,
        "send_cookie": False,
    },
)
MEITUAN_EXPAND_COORDINATE_PRESETS = (
    {"id": "xingcheng", "label": "葫芦岛市兴城市 · 兴城高中", "latitude": 40.60353704, "longitude": 120.75256222, "note": "大额券 38-24 / 28-18"},
    {"id": "wangcheng", "label": "长沙市望城区", "latitude": 28.3665, "longitude": 112.827, "note": "能卡出 20-13/12 左右，有效期 1 个月"},
    {"id": "yuzhou", "label": "许昌市禹州市 · 颍顺路", "latitude": 34.13397825, "longitude": 113.52310728, "note": "能卡出 20-13/12 左右，有效期 1 个月"},
    {"id": "haifeng", "label": "汕尾市海丰县", "latitude": 22.973452, "longitude": 115.329502, "note": "能卡出 20-13/12 左右，有效期 1 个月"},
    {"id": "laiyang", "label": "烟台市莱阳市", "latitude": 36.98374057, "longitude": 120.71443814, "note": "大额券 38-24 / 28-18"},
    {"id": "gongzhuling", "label": "长春市公主岭市 · 陶家屯镇", "latitude": 43.65051533, "longitude": 124.99881644, "note": "大额券 38-24 / 28-18"},
    {"id": "lixin", "label": "安徽利辛县 · 王市镇", "latitude": 33.18045085, "longitude": 116.08392476, "note": "大额券 38-24 / 28-18"},
    {"id": "gongan", "label": "湖北荆州市 · 公安县书香门邸", "latitude": 30.29208563, "longitude": 112.24722599, "note": "大额券 38-24 / 28-18"},
    {"id": "huazhou", "label": "化州市中医院", "latitude": 21.68955664, "longitude": 110.66757371, "note": "大额券 38-22 / 28-16"},
    {"id": "langxi", "label": "安徽郎溪 · 城南乡", "latitude": 31.12789172, "longitude": 119.18333029, "note": "大额券 38-22 / 28-16"},
    {"id": "hongze", "label": "淮安市洪泽区 · 夕阳红", "latitude": 33.30129529, "longitude": 118.87068404, "note": "大额券 38-20；APP进店可卡 23-13"},
    {"id": "guangshan", "label": "信阳市光山县", "latitude": 32.016551, "longitude": 114.925905, "note": "APP进店可卡 23-13；小程序膨胀 20-10"},
    {"id": "dafang", "label": "贵州大方县 · 羊场镇", "latitude": 27.07702602, "longitude": 105.68190626, "note": "易出 20-10 / 22-12"},
    {"id": "yuanyang", "label": "云南省元阳县", "latitude": 23.22563285, "longitude": 102.84367407, "note": "易出 20-10 / 22-12"},
    {"id": "guangfeng", "label": "江西上饶 · 广丰区", "latitude": 28.38501181, "longitude": 118.25451831, "note": "易出 20-10 / 27-14"},
    {"id": "hezhou", "label": "贺州市八步区", "latitude": 24.4102975, "longitude": 111.57308363, "note": "27-14 及大额券；部分号 20-11"},
    {"id": "jining_huadi", "label": "济宁市 · 华地公元壹品", "latitude": 35.4098, "longitude": 116.6262, "note": "40-25 / 42-27 膨胀放水地点"},
    {"id": "zhucheng_gov", "label": "诸城市政府", "latitude": 36.009274, "longitude": 119.412991, "note": "40-25 / 42-27 膨胀放水地点"},
    {"id": "jianyang_xuhai", "label": "简阳市 · 旭海时代广场", "latitude": 30.411062, "longitude": 104.547218, "note": "23-13 / 23-12 膨胀放水地点"},
    {"id": "shangcheng_gov", "label": "商城县政府", "latitude": 31.806247, "longitude": 115.413076, "note": "23-13 / 23-12 膨胀放水地点"},
    {"id": "huazhou_no2_middle", "label": "化州市 · 第二中学", "latitude": 21.675473, "longitude": 110.652147, "note": "40-24 / 38-23 膨胀放水地点"},
    {"id": "gongan_gov", "label": "荆州市公安县 · 人民政府", "latitude": 30.064562, "longitude": 112.236335, "note": "40-24 / 38-23 膨胀放水地点"},
    {"id": "gongzhuling_gov", "label": "公主岭市 · 人民政府", "latitude": 43.5013, "longitude": 124.8002, "note": "40-24 / 38-23 膨胀放水地点"},
)
LARGE_TARGET_MIN_AMOUNT_YUAN = 16
LARGE_TARGET_MIN_THRESHOLD_YUAN = 28


class MeituanExpandError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "failed",
        retryable: bool = False,
        retry_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_count = max(0, int(retry_count))


def get_meituan_expand_config() -> dict[str, Any]:
    return normalize_meituan_expand_config(load_system_settings_store().get("meituan_expand_config"))


def get_meituan_expand_coordinate_presets() -> list[dict[str, Any]]:
    return [dict(item) for item in MEITUAN_EXPAND_COORDINATE_PRESETS]


def mask_value(value: Any, left: int = 3, right: int = 2) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= left + right:
        return "*" * len(text)
    return f"{text[:left]}***{text[-right:]}"


def parse_mttouch_url(value: Any) -> dict[str, str]:
    raw = str(value or "").strip()
    if not raw or len(raw) > 8192:
        raise ValueError("美团账号链接格式不正确")
    try:
        parsed = urlparse(raw)
    except Exception as exc:
        raise ValueError("美团账号链接格式不正确") from exc
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in ALLOWED_ACCOUNT_HOSTS:
        raise ValueError("只允许使用美团 HTTPS 账号链接")
    if (parsed.path or "").rstrip("/") != "/mttouch/page/account":
        raise ValueError("链接必须是 mttouch/page/account 账号链接")
    query = parse_qs(parsed.query)
    token = str((query.get("token") or [""])[0]).strip()
    user_id = str((query.get("userId") or query.get("userid") or [""])[0]).strip()
    if not token or not user_id:
        raise ValueError("链接缺少 token 或 userId")
    if len(token) > 4096 or len(user_id) > 128 or re.search(r"[\x00-\x1f\x7f]", token + user_id):
        raise ValueError("链接中的 token 或 userId 不合法")
    account_url = "https://i.meituan.com/mttouch/page/account?" + urllib.parse.urlencode(
        {"userId": user_id, "token": token}
    )
    return {"token": token, "meituan_user_id": user_id, "account_url": account_url}


def build_account_url(token: str, meituan_user_id: str) -> str:
    return "https://i.meituan.com/mttouch/page/account?" + urllib.parse.urlencode(
        {"userId": str(meituan_user_id or "").strip(), "token": str(token or "").strip()}
    )


def normalize_credential(token_value: Any, meituan_user_id: Any = "") -> dict[str, str]:
    raw_token = str(token_value or "").strip()
    stored_user_id = str(meituan_user_id or "").strip()
    if raw_token.startswith(("http://", "https://")):
        credential = parse_mttouch_url(raw_token)
        if stored_user_id and credential["meituan_user_id"] != stored_user_id:
            raise ValueError("Token 链接中的 userId 与系统记录不一致")
        return credential
    if not raw_token or not stored_user_id:
        raise ValueError("Token 或 userId 为空")
    if len(raw_token) > 4096 or len(stored_user_id) > 128 or re.search(r"[\x00-\x1f\x7f]", raw_token + stored_user_id):
        raise ValueError("Token 或 userId 不合法")
    return {
        "token": raw_token,
        "meituan_user_id": stored_user_id,
        "account_url": build_account_url(raw_token, stored_user_id),
    }


def token_fingerprint(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _cipher_key() -> bytes:
    configured = os.getenv("WX_MEITUAN_EXPAND_ENCRYPTION_KEY", "").strip()
    if configured:
        return hashlib.sha256(configured.encode("utf-8")).digest()

    key_path = resolve_runtime_data_path("meituan_expand.key")
    try:
        encoded = key_path.read_text(encoding="ascii").strip()
        key = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if len(key) == 32:
            return key
    except FileNotFoundError:
        pass
    except Exception as exc:
        raise RuntimeError("神券膨胀运行时加密密钥损坏") from exc

    key = secrets.token_bytes(32)
    encoded = base64.urlsafe_b64encode(key).decode("ascii")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(key_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _cipher_key()
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(encoded)
    return key


def _encrypt_json(payload: dict[str, Any]) -> str:
    nonce = os.urandom(12)
    cipher = AES.new(_cipher_key(), AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    return base64.urlsafe_b64encode(nonce + tag + ciphertext).decode("ascii")


def _decrypt_json(value: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(str(value or "").encode("ascii"))
    if len(raw) < 29:
        raise ValueError("加密任务上下文无效")
    nonce, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
    cipher = AES.new(_cipher_key(), AES.MODE_GCM, nonce=nonce)
    decoded = cipher.decrypt_and_verify(ciphertext, tag)
    payload = json.loads(decoded.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("任务上下文结构无效")
    return payload


def _safe_json(value: Any, fallback: Any) -> Any:
    try:
        parsed = json.loads(str(value or ""))
    except Exception:
        return fallback
    return parsed


def _now_text() -> str:
    return datetime.now(TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


class MeituanExpandStorage:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = resolve_runtime_data_path("meituan_expand.db") if path is None else os.fspath(path)
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
                CREATE TABLE IF NOT EXISTS expand_jobs (
                    id TEXT PRIMARY KEY,
                    batch_id TEXT NOT NULL DEFAULT '',
                    actor_id INTEGER NOT NULL DEFAULT 0,
                    actor_username TEXT NOT NULL DEFAULT '',
                    target_user_id INTEGER,
                    target_username TEXT NOT NULL DEFAULT '',
                    token_source TEXT NOT NULL,
                    token_id INTEGER,
                    meituan_user_id_masked TEXT NOT NULL DEFAULT '',
                    token_fingerprint TEXT NOT NULL,
                    credential_ciphertext TEXT NOT NULL,
                    context_ciphertext TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'pre_only',
                    status TEXT NOT NULL,
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    coordinate_label TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL DEFAULT '',
                    selected_target_index INTEGER,
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    pre_result_json TEXT NOT NULL DEFAULT '{}',
                    selected_target_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_expand_jobs_created ON expand_jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_expand_jobs_status ON expand_jobs(status);
                CREATE INDEX IF NOT EXISTS idx_expand_jobs_token ON expand_jobs(token_fingerprint, created_at DESC);
                CREATE TABLE IF NOT EXISTS expand_batches (
                    id TEXT PRIMARY KEY,
                    actor_id INTEGER NOT NULL DEFAULT 0,
                    actor_username TEXT NOT NULL DEFAULT '',
                    planned_combinations INTEGER NOT NULL DEFAULT 0,
                    account_count INTEGER NOT NULL DEFAULT 0,
                    coordinate_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'queued',
                    error_message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_expand_batches_created ON expand_batches(created_at DESC);
                CREATE TABLE IF NOT EXISTS expand_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    actor_id INTEGER NOT NULL DEFAULT 0,
                    actor_username TEXT NOT NULL DEFAULT '',
                    target_user_id INTEGER,
                    target_username TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_expand_audit_job ON expand_audit_logs(job_id, id);
                """
            )
            connection.execute(
                """
                UPDATE expand_jobs
                SET status='failed', error_code='service_restarted',
                    error_message='服务重启，运行中任务已中断', finished_at=?, updated_at=?
                WHERE status IN ('queued','pre_running','execute_queued','execute_running')
                """,
                (_now_text(), _now_text()),
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(expand_jobs)").fetchall()}
            if "batch_id" not in columns:
                connection.execute("ALTER TABLE expand_jobs ADD COLUMN batch_id TEXT NOT NULL DEFAULT ''")
            if "idempotency_key" not in columns:
                connection.execute("ALTER TABLE expand_jobs ADD COLUMN idempotency_key TEXT NOT NULL DEFAULT ''")
            if "coordinate_label" not in columns:
                connection.execute("ALTER TABLE expand_jobs ADD COLUMN coordinate_label TEXT NOT NULL DEFAULT ''")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_expand_jobs_batch ON expand_jobs(batch_id, created_at)")
            connection.execute(
                """
                UPDATE expand_batches
                SET status='failed', error_message='服务重启，批量调度已中断', finished_at=?, updated_at=?
                WHERE status IN ('queued','running')
                """,
                (_now_text(), _now_text()),
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_expand_jobs_success_idempotency
                ON expand_jobs(idempotency_key)
                WHERE status='succeeded' AND idempotency_key != ''
                """
            )

    def create_job(self, payload: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
        now = _now_text()
        job_id = uuid.uuid4().hex
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO expand_jobs (
                    id, batch_id, actor_id, actor_username, target_user_id, target_username,
                    token_source, token_id, meituan_user_id_masked, token_fingerprint,
                    credential_ciphertext, mode, status, latitude, longitude, coordinate_label,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pre_only', 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    str(payload.get("batch_id") or ""),
                    int(payload.get("actor_id") or 0),
                    str(payload.get("actor_username") or "")[:80],
                    int(payload.get("target_user_id") or 0) or None,
                    str(payload.get("target_username") or "")[:80],
                    str(payload.get("token_source") or "saved_token"),
                    int(payload.get("token_id") or 0) or None,
                    mask_value(credential.get("meituan_user_id")),
                    token_fingerprint(str(credential.get("token") or "")),
                    _encrypt_json(credential),
                    float(payload.get("latitude")),
                    float(payload.get("longitude")),
                    str(payload.get("coordinate_label") or "")[:120],
                    now,
                    now,
                ),
            )
        self.audit(job_id, payload, "create_precheck", "queued", {})
        return self.get_job(job_id, include_secrets=False) or {}

    def create_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        batch_id = uuid.uuid4().hex
        now = _now_text()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO expand_batches (
                    id, actor_id, actor_username, planned_combinations, account_count,
                    coordinate_count, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    batch_id,
                    int(payload.get("actor_id") or 0),
                    str(payload.get("actor_username") or "")[:80],
                    int(payload.get("planned_combinations") or 0),
                    int(payload.get("account_count") or 0),
                    int(payload.get("coordinate_count") or 0),
                    now,
                    now,
                ),
            )
        return self.get_batch(batch_id) or {}

    def update_batch(self, batch_id: str, **fields: Any) -> None:
        allowed = {"status", "error_message", "started_at", "finished_at", "updated_at"}
        normalized = {key: value for key, value in fields.items() if key in allowed}
        normalized["updated_at"] = _now_text()
        if not normalized:
            return
        assignments = ", ".join(f"{key}=?" for key in normalized)
        with self._lock, self._connection() as connection:
            connection.execute(
                f"UPDATE expand_batches SET {assignments} WHERE id=?",
                tuple(normalized.values()) + (str(batch_id),),
            )

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM expand_batches WHERE id=?", (str(batch_id),)).fetchone()
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "actor_id": int(row["actor_id"] or 0),
            "actor_username": str(row["actor_username"] or ""),
            "planned_combinations": int(row["planned_combinations"] or 0),
            "account_count": int(row["account_count"] or 0),
            "coordinate_count": int(row["coordinate_count"] or 0),
            "status": str(row["status"] or ""),
            "error_message": str(row["error_message"] or ""),
            "created_at": str(row["created_at"] or ""),
            "started_at": str(row["started_at"] or ""),
            "finished_at": str(row["finished_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def latest_batch_id_for_actor(self, actor_id: int) -> str:
        """Return the caller's latest batch so a page refresh can restore it."""
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT id FROM expand_batches WHERE actor_id=? ORDER BY created_at DESC, id DESC LIMIT 1",
                (int(actor_id),),
            ).fetchone()
        return str(row["id"] or "") if row else ""

    def list_batch_jobs(self, batch_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM expand_jobs WHERE batch_id=? ORDER BY created_at, id", (str(batch_id),)
            ).fetchall()
        return [self._serialize_batch_job(row) for row in rows]

    def batch_snapshot(self, batch_id: str) -> dict[str, Any] | None:
        batch = self.get_batch(batch_id)
        if not batch:
            return None
        jobs = self.list_batch_jobs(batch_id)
        terminal = {"waiting_confirmation", "succeeded", "no_coupon", "business_error", "token_invalid", "gateway_blocked", "network_failed", "timeout", "cancelled", "idempotent_blocked", "failed"}
        batch["created_jobs"] = len(jobs)
        batch["completed_jobs"] = sum(str(job["status"]) in terminal for job in jobs)
        batch["running_jobs"] = sum(str(job["status"]) in {"queued", "pre_running", "execute_queued", "execute_running"} for job in jobs)
        batch["no_coupon_accounts"] = len({str(job["token_id"]) for job in jobs if str(job["status"]) == "no_coupon"})
        # Before the scheduler completes, combinations without a row are still
        # waiting to be checked. Only a successful finished batch may have
        # deliberately skipped them after an account had no usable coupon.
        batch["skipped_combinations"] = (
            max(0, int(batch["planned_combinations"]) - len(jobs))
            if str(batch.get("status") or "") == "succeeded"
            else 0
        )
        batch["large_hits"] = sum(bool((job.get("pre_result") or {}).get("has_large_target")) for job in jobs)
        batch["jobs"] = jobs
        return batch

    def has_active_jobs_for_token(self, token_id: int) -> bool:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                """SELECT 1 FROM expand_jobs
                   WHERE token_id=? AND status IN ('queued','pre_running','execute_queued','execute_running')
                   LIMIT 1""",
                (int(token_id),),
            ).fetchone()
        return row is not None

    def _serialize_batch_job(self, row: sqlite3.Row) -> dict[str, Any]:
        """Return only fields needed by the batch dashboard.

        Full precheck details remain available from the individual job endpoint;
        keeping them out of every batch poll prevents large batches from making
        the admin page and API response unnecessarily heavy.
        """
        job = self._serialize(row)
        pre = job.get("pre_result") or {}
        targets = []
        for target in pre.get("targets") or []:
            targets.append({
                "target_coupon_config_id": target.get("target_coupon_config_id"),
                "target_coupon_amount_yuan": target.get("target_coupon_amount_yuan"),
                "target_coupon_threshold_yuan": target.get("target_coupon_threshold_yuan"),
                "is_large_amount": bool(target.get("is_large_amount")),
                "channel": target.get("channel"),
                "coupon_name": target.get("coupon_name"),
                "status": target.get("status"),
                "failure_reason": target.get("failure_reason"),
            })
        job["pre_result"] = {
            "has_large_target": bool(pre.get("has_large_target")),
            "max_target_amount_yuan": pre.get("max_target_amount_yuan", 0),
            "target_count": len(targets),
            "targets": targets,
            "duration_ms": pre.get("duration_ms", job.get("duration_ms", 0)),
        }
        return job

    def update(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "mode", "status", "channel", "selected_target_index", "idempotency_key", "pre_result_json",
            "selected_target_json", "result_json", "context_ciphertext", "error_code",
            "error_message", "retry_count", "duration_ms", "cancel_requested",
            "started_at", "finished_at", "updated_at",
        }
        normalized = {key: value for key, value in fields.items() if key in allowed}
        normalized["updated_at"] = _now_text()
        if not normalized:
            return
        assignments = ", ".join(f"{key}=?" for key in normalized)
        with self._lock, self._connection() as connection:
            connection.execute(
                f"UPDATE expand_jobs SET {assignments} WHERE id=?",
                tuple(normalized.values()) + (str(job_id),),
            )

    def transition(self, job_id: str, expected_status: str, **fields: Any) -> bool:
        allowed = {
            "mode", "status", "channel", "selected_target_index", "idempotency_key", "pre_result_json",
            "selected_target_json", "result_json", "context_ciphertext", "error_code",
            "error_message", "retry_count", "duration_ms", "cancel_requested",
            "started_at", "finished_at", "updated_at",
        }
        normalized = {key: value for key, value in fields.items() if key in allowed}
        normalized["updated_at"] = _now_text()
        assignments = ", ".join(f"{key}=?" for key in normalized)
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                f"UPDATE expand_jobs SET {assignments} WHERE id=? AND status=?",
                tuple(normalized.values()) + (str(job_id), str(expected_status)),
            )
            return cursor.rowcount == 1

    def get_job(self, job_id: str, *, include_secrets: bool = False) -> dict[str, Any] | None:
        with self._lock, self._connection() as connection:
            row = connection.execute("SELECT * FROM expand_jobs WHERE id=?", (str(job_id),)).fetchone()
        if not row:
            return None
        result = self._serialize(row)
        if include_secrets:
            result["token_fingerprint"] = str(row["token_fingerprint"] or "")
            result["credential"] = _decrypt_json(str(row["credential_ciphertext"] or ""))
            result["execution_context"] = (
                _decrypt_json(str(row["context_ciphertext"] or "")) if row["context_ciphertext"] else {}
            )
        return result

    def _serialize(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "batch_id": str(row["batch_id"] or ""),
            "actor_id": int(row["actor_id"] or 0),
            "actor_username": str(row["actor_username"] or ""),
            "target_user_id": int(row["target_user_id"] or 0),
            "target_username": str(row["target_username"] or ""),
            "token_source": str(row["token_source"] or ""),
            "token_id": int(row["token_id"] or 0),
            "meituan_user_id_masked": str(row["meituan_user_id_masked"] or ""),
            "mode": str(row["mode"] or ""),
            "status": str(row["status"] or ""),
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "coordinate_label": str(row["coordinate_label"] or ""),
            "channel": str(row["channel"] or ""),
            "selected_target_index": row["selected_target_index"],
            "pre_result": _safe_json(row["pre_result_json"], {}),
            "selected_target": _safe_json(row["selected_target_json"], {}),
            "result": _safe_json(row["result_json"], {}),
            "error_code": str(row["error_code"] or ""),
            "error_message": str(row["error_message"] or ""),
            "retry_count": int(row["retry_count"] or 0),
            "duration_ms": int(row["duration_ms"] or 0),
            "cancel_requested": bool(row["cancel_requested"]),
            "created_at": str(row["created_at"] or ""),
            "started_at": str(row["started_at"] or ""),
            "finished_at": str(row["finished_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def list_jobs(
        self,
        *,
        status: str = "",
        user_id: int = 0,
        actor_id: int = 0,
        created_from: str = "",
        created_to: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if user_id > 0:
            clauses.append("target_user_id=?")
            params.append(user_id)
        if actor_id > 0:
            clauses.append("actor_id=?")
            params.append(actor_id)
        if created_from:
            clauses.append("created_at>=?")
            params.append(created_from)
        if created_to:
            clauses.append("created_at<=?")
            params.append(created_to)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(min(max(int(limit), 1), 300))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM expand_jobs{where} ORDER BY created_at DESC LIMIT ?", params
            ).fetchall()
        return [self._serialize(row) for row in rows]

    def audit(self, job_id: str, actor: dict[str, Any], action: str, status: str, detail: dict[str, Any]) -> None:
        safe_detail = {
            key: value for key, value in (detail or {}).items()
            if key not in {"token", "cookie", "account_url", "credential", "execution_context"}
        }
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO expand_audit_logs (
                    job_id, actor_id, actor_username, target_user_id, target_username,
                    action, status, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(job_id), int(actor.get("actor_id") or actor.get("id") or 0),
                    str(actor.get("actor_username") or actor.get("username") or "")[:80],
                    int(actor.get("target_user_id") or 0) or None,
                    str(actor.get("target_username") or "")[:80], str(action), str(status),
                    json.dumps(safe_detail, ensure_ascii=False, separators=(",", ":")), _now_text(),
                ),
            )

    def audits(self, job_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM expand_audit_logs WHERE job_id=? ORDER BY id", (str(job_id),)
            ).fetchall()
        return [
            {
                "id": int(row["id"]), "job_id": str(row["job_id"]),
                "actor_id": int(row["actor_id"] or 0), "actor_username": str(row["actor_username"] or ""),
                "target_user_id": int(row["target_user_id"] or 0), "target_username": str(row["target_username"] or ""),
                "action": str(row["action"]), "status": str(row["status"]),
                "detail": _safe_json(row["detail_json"], {}), "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def successful_idempotency_exists(self, idempotency_key: str, exclude_job_id: str = "") -> bool:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM expand_jobs WHERE status='succeeded' AND idempotency_key=? AND id<>? LIMIT 1",
                (str(idempotency_key), str(exclude_job_id)),
            ).fetchone()
        return row is not None

    def claim_execute(self, job_id: str, target_index: int, target: dict[str, Any], idempotency_key: str) -> bool:
        now = _now_text()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE expand_jobs
                SET mode='execute', status='execute_queued', selected_target_index=?,
                    idempotency_key=?, selected_target_json=?, error_code='', error_message='',
                    finished_at='', updated_at=?
                WHERE id=? AND status='waiting_confirmation'
                  AND NOT EXISTS (
                      SELECT 1 FROM expand_jobs active
                      WHERE active.idempotency_key=?
                        AND active.status IN ('execute_queued','execute_running','succeeded')
                  )
                """,
                (
                    int(target_index), str(idempotency_key),
                    json.dumps(target, ensure_ascii=False, separators=(",", ":")), now, str(job_id), str(idempotency_key),
                ),
            )
            return cursor.rowcount == 1

    def cancel_if_safe(self, job_id: str) -> bool:
        now = _now_text()
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE expand_jobs
                SET status='cancelled', cancel_requested=1, finished_at=?, updated_at=?
                WHERE id=? AND status IN (
                    'queued', 'pre_running', 'waiting_confirmation', 'execute_queued',
                    'no_coupon', 'business_error', 'token_invalid', 'gateway_blocked',
                    'network_failed', 'timeout', 'idempotent_blocked', 'failed'
                )
                """,
                (now, now, str(job_id)),
            )
            return cursor.rowcount == 1

    def stats(self) -> dict[str, Any]:
        today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT status, mode, duration_ms, error_message, updated_at, pre_result_json FROM expand_jobs WHERE created_at LIKE ? ORDER BY created_at",
                (today + "%",),
            ).fetchall()
        durations = [int(row["duration_ms"] or 0) for row in rows if int(row["duration_ms"] or 0) > 0]
        statuses = [str(row["status"] or "") for row in rows]
        success_count = statuses.count("succeeded")
        execute_completed = sum(
            1 for row in rows
            if str(row["mode"] or "") == "execute"
            and str(row["status"] or "") not in {"execute_queued", "execute_running"}
        )
        failures = [row for row in rows if str(row["error_message"] or "")]
        return {
            "today_total": len(rows),
            "pre_success": sum(bool(_safe_json(row["pre_result_json"], {}).get("targets")) for row in rows),
            "execute_success": success_count,
            "no_coupon": statuses.count("no_coupon"),
            "token_invalid": statuses.count("token_invalid"),
            "gateway_blocked": statuses.count("gateway_blocked"),
            "network_failed": statuses.count("network_failed") + statuses.count("timeout"),
            "running": sum(status in {"pre_running", "execute_running"} for status in statuses),
            "queued": sum(status in {"queued", "execute_queued"} for status in statuses),
            "average_duration_ms": round(sum(durations) / len(durations)) if durations else 0,
            "direct_success_rate": round(success_count * 100.0 / execute_completed, 1) if execute_completed else 0.0,
            "last_error": str(failures[-1]["error_message"] or "")[:160] if failures else "",
            "last_error_at": str(failures[-1]["updated_at"] or "") if failures else "",
        }

    def today_execute_overview(self, limit: int = 200) -> dict[str, Any]:
        """Return a compact, credential-free summary of today's real executions."""
        today = datetime.now(TIMEZONE).strftime("%Y-%m-%d")
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, actor_username, target_user_id, target_username,
                       meituan_user_id_masked, mode, status, channel,
                       result_json, error_code, error_message, retry_count,
                       duration_ms, created_at, started_at, finished_at
                FROM expand_jobs
                WHERE mode='execute' AND created_at LIKE ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (today + "%", min(max(int(limit), 1), 300)),
            ).fetchall()

        items: list[dict[str, Any]] = []
        success_count = 0
        coupon_count = 0
        total_amount = 0.0
        active_statuses = {"execute_queued", "execute_running"}
        active_count = 0
        failure_count = 0
        for row in rows:
            status = str(row["status"] or "")
            result = _safe_json(row["result_json"], {})
            coupons: list[dict[str, Any]] = []
            for coupon in result.get("inflated_coupons") or []:
                if not isinstance(coupon, dict):
                    continue
                amount = coupon.get("coupon_amount_yuan", coupon.get("coupon_amount", 0))
                threshold = coupon.get("coupon_threshold_yuan", coupon.get("coupon_threshold", 0))
                try:
                    amount_number = float(amount or 0)
                except (TypeError, ValueError):
                    amount_number = 0.0
                try:
                    threshold_number = float(threshold or 0)
                except (TypeError, ValueError):
                    threshold_number = 0.0
                coupons.append({
                    "coupon_name": str(coupon.get("coupon_name") or "神券")[:80],
                    "coupon_amount_yuan": int(amount_number) if amount_number.is_integer() else amount_number,
                    "coupon_threshold_yuan": int(threshold_number) if threshold_number.is_integer() else threshold_number,
                })
            if status == "succeeded":
                success_count += 1
                coupon_count += len(coupons)
                total_amount += sum(float(coupon["coupon_amount_yuan"] or 0) for coupon in coupons)
            elif status in active_statuses:
                active_count += 1
            else:
                failure_count += 1
            items.append({
                "job_id": str(row["id"] or ""),
                "status": status,
                "actor_username": str(row["actor_username"] or ""),
                "target_user_id": int(row["target_user_id"] or 0),
                "target_username": str(row["target_username"] or ""),
                "meituan_user_id_masked": str(row["meituan_user_id_masked"] or ""),
                "channel": str(row["channel"] or result.get("channel") or ""),
                "coupons": coupons,
                "error_code": str(row["error_code"] or ""),
                "error_message": str(row["error_message"] or "")[:160],
                "retry_count": int(row["retry_count"] or 0),
                "duration_ms": int(row["duration_ms"] or result.get("duration_ms") or 0),
                "created_at": str(row["created_at"] or ""),
                "started_at": str(row["started_at"] or ""),
                "finished_at": str(row["finished_at"] or ""),
            })
        return {
            "date": today,
            "total": len(rows),
            "success_count": success_count,
            "coupon_count": coupon_count,
            "total_amount_yuan": int(total_amount) if total_amount.is_integer() else round(total_amount, 2),
            "active_count": active_count,
            "failure_count": failure_count,
            "items": items,
        }


def _encode_form(body: dict[str, Any]) -> str:
    return urllib.parse.urlencode(
        {
            key: value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            for key, value in body.items()
        }
    )


def _response_json(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise MeituanExpandError("上游返回非 JSON 数据", code="invalid_response") from exc
    if not isinstance(payload, dict):
        raise MeituanExpandError("上游响应结构异常", code="invalid_response")
    return payload


def _response_text(response: Any) -> str:
    try:
        return str(getattr(response, "text", "") or "")
    except Exception:
        return ""


def _response_headers(response: Any) -> dict[str, str]:
    try:
        return {str(key).lower(): str(value) for key, value in dict(getattr(response, "headers", {}) or {}).items()}
    except Exception:
        return {}


def _amount_to_yuan(value: Any) -> int | float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0
    if amount >= 100:
        amount /= 100.0
    rounded = round(amount, 2)
    return int(rounded) if rounded.is_integer() else rounded


def is_large_meituan_expand_target(amount_yuan: Any, threshold_yuan: Any) -> bool:
    try:
        return (
            float(amount_yuan) >= LARGE_TARGET_MIN_AMOUNT_YUAN
            and float(threshold_yuan) >= LARGE_TARGET_MIN_THRESHOLD_YUAN
        )
    except (TypeError, ValueError):
        return False


def _transport_error(message: str, exc: Exception) -> MeituanExpandError:
    name = exc.__class__.__name__.lower()
    code = "timeout" if "timeout" in name else "network_failed"
    return MeituanExpandError(message, code=code, retryable=True)


def _raise_for_http_status(response: Any, stage: str, *, half_success: bool = False) -> None:
    status = int(getattr(response, "status_code", 0) or 0)
    if status == 403:
        text = _response_text(response).strip()
        headers = _response_headers(response)
        content_type = headers.get("content-type", "").lower()
        is_json = "application/json" in content_type or text.startswith("{")
        is_gateway = (
            not is_json
            and (
                "text/html" in content_type
                or "403 forbidden" in text.lower()
                or text.lower().startswith(("<html", "<!doctype html"))
                or bool(headers.get("x-forbid-reason"))
            )
        )
        if is_gateway:
            message = f"{stage}被美团网关限制"
            if half_success:
                message = "预查询已成功，但实际膨胀被美团网关限制；已停止重试，请勿重复提交"
            raise MeituanExpandError(message, code="gateway_blocked")
        if is_json:
            try:
                payload = response.json()
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                raise _business_error(payload, f"{stage}被拒绝")
        raise MeituanExpandError(f"{stage}请求被拒绝", code="business_error")
    if status in {429, 502, 503, 504}:
        raise MeituanExpandError(f"{stage}暂时不可用", code="network_failed", retryable=True)
    if status >= 500 or status <= 0:
        raise MeituanExpandError(f"{stage}请求失败", code="network_failed", retryable=True)


def _business_error(payload: dict[str, Any], default: str) -> MeituanExpandError:
    message = str(payload.get("msg") or payload.get("message") or default)[:160]
    lowered = message.lower()
    if any(word in lowered for word in ("token", "login", "登录", "失效", "过期", "未登录")):
        return MeituanExpandError(message, code="token_invalid")
    return MeituanExpandError(message, code="business_error")


def _select_coupon(asset: dict[str, Any]) -> dict[str, Any] | None:
    for group in ((asset.get("data") or {}).get("userMagicalCouponGroups") or []):
        if group.get("assetType") != 3 or group.get("inflated") or not group.get("userMmcInfos"):
            continue
        info = group["userMmcInfos"][0]
        raw_amount = group.get("couponAmount")
        raw_threshold = group.get("orderAmountLimit")
        return {
            "coupon_view_id": info.get("couponViewId"),
            "coupon_config_id": str(info.get("couponConfigIdStr") or info.get("couponConfigId") or ""),
            "exchange_type": str(group.get("exchangeType") or "11"),
            "coupon_name": group.get("couponName"),
            "coupon_amount": raw_amount,
            "coupon_threshold": raw_threshold,
            "coupon_amount_yuan": _amount_to_yuan(raw_amount),
            "coupon_threshold_yuan": _amount_to_yuan(raw_threshold),
            "coupon_count": len(group.get("userMmcInfos") or []),
        }
    return None


def _extract_pre(payload: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    data = payload.get("data") or {}
    inflate_token = str(data.get("inflateToken") or "")
    biz_group = "101"
    targets: list[dict[str, Any]] = []
    for group in data.get("targetCouponGroups") or []:
        biz_group = str(group.get("couponBizGroupId") or biz_group)
        for target in group.get("targetCoupons") or []:
            raw_amount = int(target.get("targetCouponRealAmount") or target.get("targetCouponAmount") or 0)
            raw_threshold = int(target.get("targetCouponRealAmountLimit") or target.get("targetCouponAmountLimit") or 0)
            targets.append(
                {
                    "target_coupon_config_id": str(target.get("targetCouponConfigId") or ""),
                    "target_coupon_amount": raw_amount,
                    "target_coupon_threshold": raw_threshold,
                    "target_coupon_amount_yuan": _amount_to_yuan(raw_amount),
                    "target_coupon_threshold_yuan": _amount_to_yuan(raw_threshold),
                    "target_asset_type": int(target.get("assetType") or 1),
                }
            )
    return inflate_token, biz_group, targets


class MeituanExpandClient:
    @staticmethod
    def _session() -> Any:
        from curl_cffi import requests as curl_requests

        return curl_requests.Session(impersonate="safari17_2_ios")

    @staticmethod
    def _remaining_timeout(deadline: float, maximum: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0.25:
            raise MeituanExpandError("任务超过总超时", code="timeout")
        return max(0.25, min(float(maximum), remaining))

    def precheck(self, credential: dict[str, Any], latitude: float, longitude: float, task_timeout: float = 90.0) -> tuple[dict[str, Any], dict[str, Any]]:
        started = time.monotonic()
        deadline = started + max(5.0, float(task_timeout))
        token = str(credential["token"])
        meituan_user_id = str(credential["meituan_user_id"])
        account_url = str(credential["account_url"])
        wm_lat = str(int(round(float(latitude) * 1_000_000)))
        wm_lng = str(int(round(float(longitude) * 1_000_000)))
        session = self._session()
        cookie = f"token={token}; mt_c_token={token}; isid={token}; oops={token}; userId={meituan_user_id}; u={meituan_user_id}"
        steps: list[dict[str, Any]] = []
        try:
            warm = session.get(account_url, headers={"User-Agent": UA_MT, "Cookie": cookie}, timeout=self._remaining_timeout(deadline, 15), allow_redirects=True)
            steps.append({"name": "warm", "http": int(warm.status_code)})
            _raise_for_http_status(warm, "账号页面")
            for key, value in session.cookies.get_dict().items():
                if value and f"{key}=" not in cookie:
                    cookie += f"; {key}={value}"
        except Exception as exc:
            if isinstance(exc, MeituanExpandError):
                raise
            raise _transport_error("账号页面连接失败", exc) from exc

        asset_body = {
            "el_biz": "waimai", "gundam_id": "3oMg3O", "tenant": "gundam", "gdEntry": "wxTab",
            "pageSource": "103", "ctype": "wm_wxapp", "wm_ctype": "wxapp", "isMini": "1",
            "webview_source": "native", "wm_latitude": wm_lat, "wm_longitude": wm_lng,
            "wm_actual_latitude": wm_lat, "wm_actual_longitude": wm_lng, "wm_appversion": "10.30.01",
            "app_id": "wx2c348cf579062e56", "userid": meituan_user_id, "token": token,
            "wm_logintoken": token, "welfareCenterPageSource": "wm",
        }
        base_query = {
            "gdBs": "0000", "pageVersion": "1783565608018", "__gd_activid": "552713",
            "__gd_pageid": "560699", "__gd_pagev": "1783565608018", "__gd_appv": "",
            "__gd_ctype": "wm_wxapp", "yodaReady": "h5", "csecplatform": "4", "csecversion": "4.2.4",
        }
        try:
            response = session.post(
                HOST + "/vp/magical/welfare/asset_module_v2?" + urllib.parse.urlencode(base_query),
                data=_encode_form(asset_body),
                headers={"User-Agent": UA_WM, "Content-Type": "application/x-www-form-urlencoded", "Origin": HOST, "Referer": HOST + "/", "X-Requested-With": "XMLHttpRequest"},
                timeout=self._remaining_timeout(deadline, 25),
            )
        except Exception as exc:
            raise _transport_error("券资产接口连接失败", exc) from exc
        _raise_for_http_status(response, "券资产接口")
        asset = _response_json(response)
        steps.append({"name": "asset", "http": int(response.status_code), "code": asset.get("code"), "msg": str(asset.get("msg") or "")[:80]})
        if int(response.status_code) != 200 or asset.get("code") not in (0, "0"):
            raise _business_error(asset, "读取券资产失败")
        selected = _select_coupon(asset)
        data = asset.get("data") or {}
        region_id = str(data.get("regionId") or "")
        region_version = str(data.get("regionVersion") or "")
        if not selected or not region_id or not region_version:
            raise MeituanExpandError("当前没有可膨胀神券", code="no_coupon")

        public_targets: list[dict[str, Any]] = []
        private_channels: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []
        for channel in CHANNELS:
            pre_body = {
                "el_biz": "waimai", "el_page": "gundam.loader", "gundam_id": "3oMg3O", "tenant": "gundam",
                "pageSource": channel["page_source"], "ctype": channel["ctype"], "isMini": "1", "webview_source": "native",
                "wm_latitude": wm_lat, "wm_longitude": wm_lng, "wm_actual_latitude": wm_lat, "wm_actual_longitude": wm_lng,
                "wm_appversion": channel["wm_appversion"], "app_id": channel["app_id"], "userid": meituan_user_id,
                "token": token, "wm_logintoken": token, "welfareCenterPageSource": "wm", "request_page_source": "54",
                "planToken": "temple_id_93", "exchange_type": selected["exchange_type"],
                "coupon_view_id": selected["coupon_view_id"], "coupon_config_id": selected["coupon_config_id"], "assetType": "3",
            }
            if channel["wm_ctype"]:
                pre_body["wm_ctype"] = channel["wm_ctype"]
            if channel["gd_entry"]:
                pre_body["gdEntry"] = channel["gd_entry"]
            pre_query = dict(base_query)
            pre_query.update({"region_id": region_id, "region_version": region_version, "__gd_ctype": channel["gd_ctype"]})
            headers = {
                "User-Agent": channel["user_agent"], "Content-Type": "application/x-www-form-urlencoded",
                "Origin": HOST, "Referer": HOST + "/", "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            }
            if channel["send_cookie"]:
                headers["Cookie"] = cookie
                headers["dj-token"] = token
            channel_started = time.monotonic()
            try:
                pre_response = session.post(
                    HOST + "/vp/magical/exchange/pre_exchange_for_magical_coupon?" + urllib.parse.urlencode(pre_query),
                    data=_encode_form(pre_body), headers=headers, timeout=self._remaining_timeout(deadline, 25),
                )
                _raise_for_http_status(pre_response, f"{channel['name']} 预查询接口")
                pre_payload = _response_json(pre_response)
            except MeituanExpandError as exc:
                failures.append({
                    "channel": channel["name"],
                    "code": exc.code,
                    "retryable": bool(exc.retryable),
                })
                continue
            except Exception as exc:
                transport = _transport_error("膨胀预查询连接失败", exc)
                failures.append({"channel": channel["name"], "code": transport.code, "retryable": True})
                continue
            inflate_token, biz_group, targets = _extract_pre(pre_payload)
            steps.append({
                "name": f"pre_{channel['name']}", "http": int(pre_response.status_code),
                "code": pre_payload.get("code"), "msg": str(pre_payload.get("msg") or "")[:80],
                "target_count": len(targets),
                "duration_ms": int((time.monotonic() - channel_started) * 1000),
            })
            if int(pre_response.status_code) != 200 or pre_payload.get("code") not in (0, "0") or not inflate_token or not targets:
                failures.append({"channel": channel["name"], "code": "business_error", "message": str(pre_payload.get("msg") or "")[:80]})
                continue
            channel_index = len(private_channels)
            private_channels.append({
                "channel": channel["name"], "inflate_token": inflate_token, "biz_group": biz_group,
                "targets": targets, "pre_body": pre_body, "pre_query": pre_query, "headers": headers,
            })
            for target_index, target in enumerate(targets):
                target_amount_yuan = _amount_to_yuan(target.get("target_coupon_amount"))
                target_threshold_yuan = _amount_to_yuan(target.get("target_coupon_threshold"))
                is_large_amount = is_large_meituan_expand_target(target_amount_yuan, target_threshold_yuan)
                public_targets.append({
                    **target,
                    "channel": channel["name"], "channel_context_index": channel_index,
                    "channel_target_index": target_index,
                    "coupon_name": selected["coupon_name"], "coupon_amount": selected["coupon_amount"],
                    "coupon_threshold": selected["coupon_threshold"], "coupon_count": selected["coupon_count"],
                    "is_large_amount": is_large_amount,
                    "target_size": "large" if is_large_amount else "standard",
                    "status": "available", "failure_reason": "",
                    "request_duration_ms": int((time.monotonic() - channel_started) * 1000),
                })
        if not public_targets:
            if failures and all(item.get("code") == "gateway_blocked" for item in failures):
                raise MeituanExpandError("账号当前被美团网关限制", code="gateway_blocked")
            if any(item.get("retryable") for item in failures):
                code = "timeout" if any(item.get("code") == "timeout" for item in failures) else "network_failed"
                raise MeituanExpandError("膨胀预查询网络失败", code=code, retryable=True)
            raise MeituanExpandError("未获取到可膨胀目标", code="business_error")
        default_index = max(range(len(public_targets)), key=lambda index: int(public_targets[index].get("target_coupon_amount") or 0))
        duration_ms = int((time.monotonic() - started) * 1000)
        large_targets = [target for target in public_targets if target.get("is_large_amount")]
        max_target_amount_yuan = max(
            (float(target.get("target_coupon_amount_yuan") or 0) for target in public_targets),
            default=0.0,
        )
        public = {
            "coupon": selected, "targets": public_targets, "default_target_index": default_index,
            "has_large_target": bool(large_targets), "large_target_count": len(large_targets),
            "max_target_amount_yuan": int(max_target_amount_yuan) if max_target_amount_yuan.is_integer() else max_target_amount_yuan,
            "failures": failures, "steps": steps, "duration_ms": duration_ms, "direct": True,
        }
        private = {"channels": private_channels, "cookie": cookie, "created_at": int(time.time())}
        return public, private

    def execute(self, execution_context: dict[str, Any], public_target: dict[str, Any], task_timeout: float = 90.0) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + max(5.0, float(task_timeout))
        channel_index = int(public_target.get("channel_context_index") or 0)
        target_index = int(public_target.get("channel_target_index") or 0)
        channels = execution_context.get("channels") or []
        if channel_index < 0 or channel_index >= len(channels):
            raise MeituanExpandError("预查询执行上下文已失效", code="failed")
        channel = channels[channel_index]
        targets = channel.get("targets") or []
        if target_index < 0 or target_index >= len(targets):
            raise MeituanExpandError("目标券上下文已失效", code="failed")
        target = targets[target_index]
        do_body = dict(channel.get("pre_body") or {})
        do_body.update(
            {
                "inflateToken": str(channel.get("inflate_token") or ""),
                "targetCouponInfosStr": json.dumps(
                    [{
                        "targetCouponConfigId": target["target_coupon_config_id"],
                        "targetCouponAmount": target["target_coupon_amount"],
                        "targetCouponAmountLimit": target["target_coupon_threshold"],
                        "targetAssetType": target["target_asset_type"],
                    }],
                    ensure_ascii=False, separators=(",", ":"),
                ),
                "couponBizGroupId": str(channel.get("biz_group") or "101"),
            }
        )
        session = self._session()
        try:
            response = session.post(
                HOST + "/vp/magical/exchange/do_exchange_for_magical_coupon?" + urllib.parse.urlencode(channel.get("pre_query") or {}),
                data=_encode_form(do_body), headers=channel.get("headers") or {}, timeout=self._remaining_timeout(deadline, 25),
            )
        except Exception as exc:
            raise _transport_error("实际膨胀网络连接失败", exc) from exc
        _raise_for_http_status(response, "实际膨胀接口", half_success=True)
        payload = _response_json(response)
        if int(response.status_code) != 200 or payload.get("code") not in (0, "0"):
            raise _business_error(payload, "实际膨胀失败")
        data = payload.get("data") or {}
        items = data.get("couponMultipleList") or data.get("inflateAndGiftAssetList") or []
        coupons = [
            {
                "coupon_name": item.get("couponName"), "coupon_amount": item.get("couponAmount"),
                "coupon_threshold": item.get("orderAmountLimit"),
                "coupon_amount_yuan": _amount_to_yuan(item.get("couponAmount")),
                "coupon_threshold_yuan": _amount_to_yuan(item.get("orderAmountLimit")),
            }
            for item in items if isinstance(item, dict)
        ]
        if not coupons:
            raise MeituanExpandError("接口返回成功但未解析到膨胀券", code="invalid_response")
        return {
            "ok": True, "channel": str(channel.get("channel") or ""), "inflated_coupons": coupons,
            "http": int(response.status_code), "code": payload.get("code"),
            "message": str(payload.get("msg") or "成功")[:80],
            "duration_ms": int((time.monotonic() - started) * 1000), "direct": True,
        }


class MeituanExpandService:
    def __init__(self, storage: MeituanExpandStorage | None = None, client: MeituanExpandClient | None = None) -> None:
        self.storage = storage or MeituanExpandStorage()
        self.client = client or MeituanExpandClient()
        self._global_semaphore: asyncio.Semaphore | None = None
        self._global_limit = 0
        self._account_locks: dict[str, asyncio.Lock] = {}
        self._account_locks_guard = asyncio.Lock()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._batch_tasks: dict[str, asyncio.Task[None]] = {}
        self._tasks_guard = threading.RLock()

    def _semaphore(self, limit: int) -> asyncio.Semaphore:
        if self._global_semaphore is None or self._global_limit != limit:
            self._global_semaphore = asyncio.Semaphore(limit)
            self._global_limit = limit
        return self._global_semaphore

    async def _account_lock(self, meituan_user_id: str) -> asyncio.Lock:
        async with self._account_locks_guard:
            return self._account_locks.setdefault(meituan_user_id, asyncio.Lock())

    def submit_precheck(self, payload: dict[str, Any], credential: dict[str, Any]) -> dict[str, Any]:
        job = self.storage.create_job(payload, credential)
        task = self._schedule_precheck(job["id"])
        return job

    def _schedule_precheck(self, job_id: str) -> asyncio.Task[None]:
        task = asyncio.create_task(self._run_precheck(job_id))
        with self._tasks_guard:
            self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._forget(job_id))
        return task

    def _forget(self, job_id: str) -> None:
        with self._tasks_guard:
            self._tasks.pop(job_id, None)

    def submit_batch_precheck(
        self,
        actor: dict[str, Any],
        accounts: list[tuple[dict[str, Any], dict[str, Any]]],
        coordinates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        batch = self.storage.create_batch(
            {
                "actor_id": actor.get("actor_id"),
                "actor_username": actor.get("actor_username"),
                "planned_combinations": len(accounts) * len(coordinates),
                "account_count": len(accounts),
                "coordinate_count": len(coordinates),
            }
        )
        batch_id = str(batch["id"])
        task = asyncio.create_task(self._run_batch_precheck(batch_id, actor, accounts, coordinates))
        with self._tasks_guard:
            self._batch_tasks[batch_id] = task
        task.add_done_callback(lambda _: self._forget_batch(batch_id))
        return self.storage.batch_snapshot(batch_id) or batch

    def _forget_batch(self, batch_id: str) -> None:
        with self._tasks_guard:
            self._batch_tasks.pop(batch_id, None)

    async def _wait_for_precheck(self, job_id: str) -> dict[str, Any]:
        terminal = {
            "waiting_confirmation", "succeeded", "no_coupon", "business_error", "token_invalid",
            "gateway_blocked", "network_failed", "timeout", "cancelled", "idempotent_blocked", "failed",
        }
        while True:
            job = self.storage.get_job(job_id, include_secrets=False) or {}
            if str(job.get("status") or "") in terminal:
                return job
            await asyncio.sleep(0.2)

    async def _run_batch_account(
        self,
        batch_id: str,
        actor: dict[str, Any],
        credential: dict[str, Any],
        owner: dict[str, Any],
        coordinates: list[dict[str, Any]],
    ) -> None:
        for coordinate in coordinates:
            payload = {
                "batch_id": batch_id,
                "actor_id": int(actor.get("actor_id") or 0),
                "actor_username": str(actor.get("actor_username") or ""),
                **owner,
                "token_source": "saved_token",
                "token_id": owner.get("token_id"),
                "latitude": float(coordinate["latitude"]),
                "longitude": float(coordinate["longitude"]),
                "coordinate_label": str(coordinate.get("label") or "预置坐标"),
            }
            job = self.submit_precheck(payload, credential)
            final = await self._wait_for_precheck(str(job["id"]))
            if str(final.get("status") or "") == "no_coupon":
                # 无券是账号级结果，后续地址不会再产生有效膨胀候选，直接跳过。
                break

    async def _run_batch_precheck(
        self,
        batch_id: str,
        actor: dict[str, Any],
        accounts: list[tuple[dict[str, Any], dict[str, Any]]],
        coordinates: list[dict[str, Any]],
    ) -> None:
        batch = self.storage.get_batch(batch_id)
        if not batch:
            return
        self.storage.update_batch(batch_id, status="running", started_at=_now_text(), error_message="")
        try:
            await asyncio.gather(*(
                self._run_batch_account(batch_id, actor, credential, owner, coordinates)
                for credential, owner in accounts
            ))
            self.storage.update_batch(batch_id, status="succeeded", finished_at=_now_text())
        except asyncio.CancelledError:
            self.storage.update_batch(batch_id, status="cancelled", finished_at=_now_text(), error_message="批量任务已取消")
            raise
        except Exception:
            logger.exception("神券膨胀批量调度异常 batch=%s", batch_id[:12])
            self.storage.update_batch(batch_id, status="failed", finished_at=_now_text(), error_message="批量调度内部错误")

    async def _run_precheck(self, job_id: str) -> None:
        job = self.storage.get_job(job_id, include_secrets=True)
        if not job:
            return
        config = get_meituan_expand_config()
        semaphore = self._semaphore(int(config["global_concurrency_limit"]))
        account_lock = await self._account_lock(str(job["credential"]["meituan_user_id"]))
        started = time.monotonic()
        try:
            async with semaphore, account_lock:
                if not self.storage.transition(job_id, "queued", status="pre_running", started_at=_now_text(), error_code="", error_message=""):
                    return
                self.storage.audit(job_id, job, "precheck_started", "pre_running", {})
                public, private, retries = await self._with_retries(
                    self.client.precheck, job["credential"], job["latitude"], job["longitude"],
                    retry_count=int(config["direct_retry_count"]),
                    total_timeout_seconds=float(config["task_timeout_seconds"]),
                )
            self.storage.update(
                job_id, status="waiting_confirmation", channel=str((public.get("targets") or [{}])[0].get("channel") or ""),
                pre_result_json=json.dumps(public, ensure_ascii=False, separators=(",", ":")),
                context_ciphertext=_encrypt_json(private), retry_count=retries,
                duration_ms=int((time.monotonic() - started) * 1000), finished_at=_now_text(),
            )
            self.storage.audit(job_id, job, "precheck_succeeded", "waiting_confirmation", {"target_count": len(public.get("targets") or [])})
        except MeituanExpandError as exc:
            status = exc.code if exc.code in {"no_coupon", "business_error", "token_invalid", "gateway_blocked", "network_failed", "timeout"} else "failed"
            self.storage.update(
                job_id, status=status, error_code=exc.code, error_message=str(exc)[:160],
                retry_count=exc.retry_count,
                duration_ms=int((time.monotonic() - started) * 1000), finished_at=_now_text(),
            )
            self.storage.audit(job_id, job, "precheck_failed", status, {"error_code": exc.code, "message": str(exc)[:160]})
        except Exception as exc:
            logger.exception("神券膨胀预查询异常 job=%s error=%s", job_id[:12], exc)
            self.storage.update(job_id, status="failed", error_code="failed", error_message="预查询内部错误", finished_at=_now_text())

    def submit_execute(self, job_id: str, target_index: int, actor: dict[str, Any]) -> dict[str, Any]:
        if not get_meituan_expand_config()["enabled"]:
            raise ValueError("神券膨胀功能尚未启用")
        job = self.storage.get_job(job_id, include_secrets=True)
        if not job:
            raise KeyError(job_id)
        if job["status"] != "waiting_confirmation":
            raise ValueError("任务当前不能执行实际膨胀")
        targets = (job.get("pre_result") or {}).get("targets") or []
        if target_index < 0 or target_index >= len(targets):
            raise ValueError("目标券编号无效")
        target = dict(targets[target_index])
        idempotency_key = hashlib.sha256(
            "|".join(
                [
                    str((job.get("credential") or {}).get("meituan_user_id") or ""),
                    str((job.get("pre_result") or {}).get("coupon", {}).get("coupon_view_id") or ""),
                    str((job.get("pre_result") or {}).get("coupon", {}).get("coupon_config_id") or ""),
                    str(target.get("target_coupon_config_id") or ""),
                ]
            ).encode("utf-8")
        ).hexdigest()
        target["idempotency_key"] = idempotency_key
        if self.storage.successful_idempotency_exists(idempotency_key, exclude_job_id=job_id):
            self.storage.update(job_id, status="idempotent_blocked", error_code="idempotent_blocked", error_message="该目标券已经成功膨胀")
            self.storage.audit(job_id, actor, "execute_blocked", "idempotent_blocked", {"reason": "already_succeeded"})
            raise ValueError("该目标券已经成功膨胀，已阻止重复操作")
        stored_target = dict(target)
        stored_target.pop("idempotency_key", None)
        if not self.storage.claim_execute(job_id, target_index, stored_target, idempotency_key):
            raise ValueError("任务已被其他操作占用或不再等待确认")
        audit_actor = {
            **actor,
            "target_user_id": job.get("target_user_id"),
            "target_username": job.get("target_username"),
        }
        self.storage.audit(job_id, audit_actor, "execute_confirmed", "execute_queued", {"target_index": target_index})
        task = asyncio.create_task(self._run_execute(job_id))
        with self._tasks_guard:
            self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._forget(job_id))
        return self.storage.get_job(job_id, include_secrets=False) or {}

    async def _run_execute(self, job_id: str) -> None:
        job = self.storage.get_job(job_id, include_secrets=True)
        if not job:
            return
        config = get_meituan_expand_config()
        semaphore = self._semaphore(int(config["global_concurrency_limit"]))
        account_lock = await self._account_lock(str(job["credential"]["meituan_user_id"]))
        started = time.monotonic()
        try:
            async with semaphore, account_lock:
                if not self.storage.transition(job_id, "execute_queued", status="execute_running", started_at=_now_text()):
                    return
                self.storage.audit(job_id, job, "execute_started", "execute_running", {})
                result, retries = await self._with_retries(
                    self.client.execute, job["execution_context"], job["selected_target"],
                    retry_count=int(config["direct_retry_count"]),
                    total_timeout_seconds=float(config["task_timeout_seconds"]),
                )
            self.storage.update(
                job_id, status="succeeded", channel=str(result.get("channel") or ""),
                result_json=json.dumps(result, ensure_ascii=False, separators=(",", ":")), retry_count=retries,
                duration_ms=int((time.monotonic() - started) * 1000), finished_at=_now_text(),
            )
            self.storage.audit(job_id, job, "execute_succeeded", "succeeded", {"coupon_count": len(result.get("inflated_coupons") or [])})
        except MeituanExpandError as exc:
            status = exc.code if exc.code in {"business_error", "token_invalid", "gateway_blocked", "network_failed", "timeout"} else "failed"
            self.storage.update(
                job_id, status=status, error_code=exc.code, error_message=str(exc)[:160],
                retry_count=exc.retry_count,
                duration_ms=int((time.monotonic() - started) * 1000), finished_at=_now_text(),
            )
            self.storage.audit(job_id, job, "execute_failed", status, {"error_code": exc.code, "message": str(exc)[:160]})
        except Exception as exc:
            logger.exception("神券实际膨胀异常 job=%s error=%s", job_id[:12], exc)
            self.storage.update(job_id, status="failed", error_code="failed", error_message="实际膨胀内部错误", finished_at=_now_text())

    async def _with_retries(
        self,
        function: Any,
        *args: Any,
        retry_count: int,
        total_timeout_seconds: float,
    ) -> tuple[Any, ...]:
        retries = 0
        deadline = time.monotonic() + max(5.0, float(total_timeout_seconds))
        maximum_attempts = 1 + max(0, int(retry_count))
        for attempt in range(maximum_attempts):
            remaining = deadline - time.monotonic()
            if remaining <= 0.25:
                raise MeituanExpandError("任务超过总超时", code="timeout")
            try:
                result = await asyncio.to_thread(function, *args, remaining)
                if isinstance(result, tuple):
                    return (*result, retries)
                return result, retries
            except MeituanExpandError as exc:
                if not exc.retryable or attempt >= maximum_attempts - 1:
                    exc.retry_count = retries
                    raise
                retries += 1
                sleep_seconds = min(1.5 * (attempt + 1), 4.0, max(0.0, deadline - time.monotonic()))
                if sleep_seconds <= 0:
                    raise MeituanExpandError(
                        "任务超过总超时",
                        code="timeout",
                        retry_count=retries,
                    ) from exc
                await asyncio.sleep(sleep_seconds)
        raise MeituanExpandError("请求重试耗尽", code="network_failed")

    def cancel(self, job_id: str, actor: dict[str, Any]) -> dict[str, Any]:
        job = self.storage.get_job(job_id, include_secrets=False)
        if not job:
            raise KeyError(job_id)
        if job["status"] == "execute_running":
            raise ValueError("实际膨胀请求已经发出，不能安全取消")
        if job["status"] in {"succeeded", "cancelled"}:
            raise ValueError("任务已经结束")
        if not self.storage.cancel_if_safe(job_id):
            latest = self.storage.get_job(job_id, include_secrets=False) or {}
            if latest.get("status") == "execute_running":
                raise ValueError("实际膨胀请求已经发出，不能安全取消")
            raise ValueError("任务当前不能取消")
        with self._tasks_guard:
            task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        audit_actor = {
            **actor,
            "target_user_id": job.get("target_user_id"),
            "target_username": job.get("target_username"),
        }
        self.storage.audit(job_id, audit_actor, "cancelled", "cancelled", {})
        return self.storage.get_job(job_id, include_secrets=False) or {}

    def runtime(self) -> dict[str, Any]:
        with self._tasks_guard:
            active = sum(1 for task in self._tasks.values() if not task.done())
        with self._tasks_guard:
            active_batches = sum(1 for task in self._batch_tasks.values() if not task.done())
        return {"active_tasks": active, "active_batches": active_batches, "global_limit": int(get_meituan_expand_config()["global_concurrency_limit"]), "proxy_enabled": False}


_service: MeituanExpandService | None = None
_service_lock = threading.Lock()


def get_meituan_expand_service() -> MeituanExpandService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = MeituanExpandService()
    return _service
