from __future__ import annotations

import asyncio
import hashlib
import secrets
import sqlite3
import time
import unicodedata
import uuid
from typing import Any
from urllib.parse import urlencode

from utils.logger import setup_logger
from utils.path_utils import resolve_runtime_data_path
from utils.pushplus_client import PushPlusError, get_pushplus_client
from utils.pushplus_storage import get_pushplus_notification_storage
from utils.system_settings_store import (
    load_system_settings_store,
    normalize_pushplus_config,
    save_system_settings_store,
)


logger = setup_logger(__name__)

PUSHPLUS_QR_TTL_SECONDS = 600
PUSHPLUS_MAX_KEYWORDS = 30
PUSHPLUS_MAX_KEYWORD_LENGTH = 30

_dispatcher_task: asyncio.Task | None = None
_dispatcher_stop_event: asyncio.Event | None = None


def ensure_pushplus_config() -> dict[str, Any]:
    store = load_system_settings_store()
    config = normalize_pushplus_config(store.get("pushplus_config", {}))
    if not config.get("callback_secret"):
        config["callback_secret"] = secrets.token_urlsafe(32)
        store["pushplus_config"] = config
        save_system_settings_store(store)
    return config


def reset_pushplus_runtime_after_config_change() -> None:
    get_pushplus_client().reset_access_key()
    get_pushplus_notification_storage().set_runtime_state(
        "dispatcher",
        {
            "running": _dispatcher_task is not None and not _dispatcher_task.done(),
            "circuit_code": 0,
            "circuit_until": 0,
            "last_error": "",
            "last_config_change_at": int(time.time()),
        },
    )


def mask_secret(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return text[:2] + "***" + text[-2:]
    return text[:4] + "***" + text[-4:]


def hash_binding_nonce(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def normalize_merchant_match_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(
        char
        for char in text
        if not char.isspace() and not unicodedata.category(char).startswith(("P", "Z"))
    )


def normalize_keywords(raw_keywords: Any) -> list[tuple[str, str]]:
    values = raw_keywords if isinstance(raw_keywords, list) else []
    normalized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw_value in values:
        keyword = str(raw_value or "").strip()
        if not keyword:
            continue
        if len(keyword) > PUSHPLUS_MAX_KEYWORD_LENGTH:
            raise ValueError(f"每个商家关键词最多 {PUSHPLUS_MAX_KEYWORD_LENGTH} 个字符")
        match_value = normalize_merchant_match_text(keyword)
        if not match_value or match_value in seen:
            continue
        seen.add(match_value)
        normalized.append((keyword, match_value))
    if len(normalized) > PUSHPLUS_MAX_KEYWORDS:
        raise ValueError(f"最多设置 {PUSHPLUS_MAX_KEYWORDS} 个商家关键词")
    return normalized


def build_callback_url(config: dict[str, Any] | None = None) -> str:
    normalized = normalize_pushplus_config(config or ensure_pushplus_config())
    public_base_url = str(normalized.get("public_base_url") or "").strip().rstrip("/")
    callback_secret = str(normalized.get("callback_secret") or "").strip()
    if not public_base_url or not callback_secret:
        return ""
    return f"{public_base_url}/api/pushplus/callback/{callback_secret}"


def serialize_pushplus_admin_settings(config: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = normalize_pushplus_config(config or ensure_pushplus_config())
    return {
        "enabled": bool(normalized.get("enabled")),
        "platform_token_masked": mask_secret(normalized.get("platform_token")),
        "secret_key_masked": mask_secret(normalized.get("secret_key")),
        "has_platform_token": bool(normalized.get("platform_token")),
        "has_secret_key": bool(normalized.get("secret_key")),
        "app_id": str(normalized.get("app_id") or ""),
        "public_base_url": str(normalized.get("public_base_url") or ""),
        "account_tier": str(normalized.get("account_tier") or "standard"),
        "callback_url": build_callback_url(normalized),
        "configured": bool(
            normalized.get("platform_token")
            and normalized.get("secret_key")
            and normalized.get("public_base_url")
        ),
    }


def serialize_pushplus_user_settings(user_id: int) -> dict[str, Any]:
    config = normalize_pushplus_config(load_system_settings_store().get("pushplus_config", {}))
    settings = get_pushplus_notification_storage().get_user_settings(int(user_id))
    binding = settings.get("binding") if isinstance(settings.get("binding"), dict) else None
    return {
        "global_enabled": bool(config.get("enabled")),
        "service_configured": bool(config.get("platform_token") and config.get("secret_key")),
        "bound": binding is not None,
        "binding": {
            "nickname": str((binding or {}).get("nickname") or "微信用户"),
            "friend_id_masked": mask_secret((binding or {}).get("friend_id")),
            "bound_at": int((binding or {}).get("bound_at") or 0),
        } if binding else None,
        "token_invalid_enabled": bool(int((binding or {}).get("token_invalid_enabled") or 0)) if binding else True,
        "merchant_match_enabled": bool(int((binding or {}).get("merchant_match_enabled") or 0)) if binding else True,
        "keywords": [str(item.get("keyword") or "") for item in settings.get("keywords") or []],
        "keyword_limit": PUSHPLUS_MAX_KEYWORDS,
        "keyword_length_limit": PUSHPLUS_MAX_KEYWORD_LENGTH,
    }


def _is_pushplus_enabled() -> bool:
    config = normalize_pushplus_config(load_system_settings_store().get("pushplus_config", {}))
    return bool(config.get("enabled") and config.get("platform_token") and config.get("secret_key"))


def mark_web_token_inactive(token_id: int, *, reason: str = "美团登录状态已失效") -> bool:
    normalized_token_id = int(token_id or 0)
    if normalized_token_id <= 0:
        return False
    db_path = resolve_runtime_data_path("meituan_query.db")
    if not db_path.exists():
        return False
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, user_id, name, meituan_user_id, is_active
            FROM tokens WHERE id = ? LIMIT 1
            """,
            (normalized_token_id,),
        ).fetchone()
        if row is None or not bool(int(row["is_active"] or 0)):
            conn.rollback()
            return False
        cursor = conn.execute(
            """
            UPDATE tokens
            SET is_active = 0, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND is_active = 1
            """,
            (normalized_token_id,),
        )
        conn.commit()
        changed = cursor.rowcount > 0
        token_record = dict(row)
    finally:
        conn.close()
    if changed:
        try:
            enqueue_token_invalid_notification(token_record, reason=reason)
        except Exception as exc:
            logger.warning("创建 Token 失效推送任务失败: token_id=%s error=%s", normalized_token_id, exc, exc_info=True)
    return changed


def enqueue_token_invalid_notification(token_record: dict[str, Any], *, reason: str) -> int | None:
    if not _is_pushplus_enabled():
        return None
    config = normalize_pushplus_config(load_system_settings_store().get("pushplus_config", {}))
    base_url = str(config.get("public_base_url") or "").strip().rstrip("/")
    link_url = f"{base_url}/web/query" if base_url else ""
    user_id = int(token_record.get("user_id") or 0)
    token_id = int(token_record.get("id") or 0)
    storage = get_pushplus_notification_storage()
    binding = storage.get_binding(user_id)
    if not binding or not bool(int(binding.get("token_invalid_enabled") or 0)):
        return None
    token_name = str(token_record.get("name") or f"Token {token_id}").strip()
    meituan_user_id = str(token_record.get("meituan_user_id") or "").strip()
    event_reference = uuid.uuid4().hex[:12]
    content = (
        f"## 美团登录状态已失效\n\n"
        f"- 账号：{token_name}\n"
        f"- UserID：{meituan_user_id or '未记录'}\n"
        f"- 原因：{reason or '美团明确返回登录失效'}\n\n"
        f"请重新登录美团并在平台更新 Token。"
        + (f"\n\n[打开平台]({link_url})" if link_url else "")
        + "\n\n"
        + f"事件编号：`{event_reference}`"
    )
    return storage.enqueue_job(
        event_key=f"token_invalid:{token_id}:{event_reference}",
        user_id=user_id,
        event_type="token_invalid",
        title=f"{token_name} 的美团 Token 已失效",
        content=content,
        link_url=link_url,
        expires_at=None,
    )


def _address_display_name(resolved_address: dict[str, Any], fallback: str = "") -> str:
    parts = [
        str(resolved_address.get("card_name") or "").strip(),
        str(resolved_address.get("display_text") or resolved_address.get("address") or "").strip(),
        str(resolved_address.get("house_number") or "").strip(),
    ]
    return " ".join(part for part in parts if part) or str(fallback or "未命名地址")


def _merchant_identifier(merchant: dict[str, Any]) -> str:
    poi_id = str(merchant.get("poi_id") or merchant.get("wm_poi_id_str") or "").strip()
    if poi_id:
        return f"poi:{poi_id}"
    return f"name:{normalize_merchant_match_text(merchant.get('poi_name'))}"


def process_allowance_task_notification(task_id: str) -> dict[str, Any]:
    from utils.meituan_allowance_task_storage import get_meituan_allowance_task_storage

    task_storage = get_meituan_allowance_task_storage()
    task = task_storage.get_task(str(task_id or ""))
    if not task:
        return {"processed": False, "reason": "task_not_found"}
    if str(task.get("status") or "") != "succeeded":
        return {"processed": False, "reason": "task_not_succeeded"}
    user_id = int(task.get("web_user_id") or 0)
    if user_id <= 0:
        task_storage.update_task_notification(task_id, status="not_applicable")
        return {"processed": True, "reason": "legacy_task"}
    if not _is_pushplus_enabled():
        task_storage.update_task_notification(task_id, status="skipped_disabled")
        return {"processed": True, "reason": "pushplus_disabled"}

    notification_storage = get_pushplus_notification_storage()
    settings = notification_storage.get_user_settings(user_id)
    binding = settings.get("binding") if isinstance(settings.get("binding"), dict) else None
    if not binding or not bool(int(binding.get("merchant_match_enabled") or 0)):
        task_storage.update_task_notification(task_id, status="skipped_unbound")
        return {"processed": True, "reason": "unbound_or_disabled"}
    keywords = [
        (str(item.get("keyword") or ""), str(item.get("normalized_keyword") or ""))
        for item in settings.get("keywords") or []
        if str(item.get("normalized_keyword") or "")
    ]
    if not keywords:
        task_storage.update_task_notification(task_id, status="no_keywords")
        return {"processed": True, "reason": "no_keywords"}

    meituan_user_id = str(task.get("meituan_user_id") or "")
    address_id = str(task.get("address_id") or "")
    allowance_type = str(task.get("allowance_type") or "large")
    aggregate = task_storage.get_daily_aggregate(
        meituan_user_id=meituan_user_id,
        address_id=address_id,
        allowance_type=allowance_type,
    )
    if not aggregate:
        task_storage.update_task_notification(task_id, status="no_aggregate")
        return {"processed": True, "reason": "no_aggregate"}

    matched: list[dict[str, Any]] = []
    for merchant in aggregate.get("merchants") or []:
        merchant_name = str(merchant.get("poi_name") or "").strip()
        normalized_name = normalize_merchant_match_text(merchant_name)
        if not normalized_name:
            continue
        matched_keywords = [display for display, normalized in keywords if normalized in normalized_name]
        if not matched_keywords:
            continue
        matched.append(
            {
                "merchant_key": _merchant_identifier(merchant),
                "merchant_name": merchant_name,
                "matched_keywords": matched_keywords,
            }
        )
    if not matched:
        task_storage.update_task_notification(task_id, status="no_match")
        return {"processed": True, "reason": "no_match"}

    resolved_address = aggregate.get("resolved_address") if isinstance(aggregate.get("resolved_address"), dict) else {}
    address_name = _address_display_name(resolved_address, str(task.get("address_name") or ""))
    type_label = "小额津贴（免单）" if allowance_type == "small_free_order" else "大额津贴"
    config = ensure_pushplus_config()
    base_url = str(config.get("public_base_url") or "").rstrip("/")
    query_string = urlencode(
        {
            "tab": "allowance",
            "allowance_type": allowance_type,
            "token_id": str(task.get("web_token_id") or ""),
            "address_id": address_id,
        }
    )
    link_url = f"{base_url}/web/query?{query_string}" if base_url else ""
    event_reference = uuid.uuid4().hex[:12]

    def build_content(new_merchants: list[dict[str, Any]]) -> str:
        merchant_lines = [
            f"- {item['merchant_name']}（命中：{'、'.join(item['matched_keywords'])}）"
            for item in new_merchants[:80]
        ]
        if len(new_merchants) > 80:
            merchant_lines.append(f"- 另有 {len(new_merchants) - 80} 家，请打开平台查看")
        link_line = f"\n\n[打开当天津贴列表]({link_url})" if link_url else ""
        return (
            f"## 找到你关注的津贴商家\n\n"
            f"- 类型：{type_label}\n"
            f"- 地址：{address_name}\n"
            f"- 本次新增命中：{len(new_merchants)} 家\n\n"
            + "\n".join(merchant_lines)
            + link_line
            + f"\n\n事件编号：`{event_reference}`"
        )

    job_id, new_merchants = notification_storage.reserve_and_enqueue_merchant_matches(
        event_key=f"merchant_match:{task_id}",
        user_id=user_id,
        date_key=str(aggregate.get("date_key") or notification_storage.date_key()),
        address_id=address_id,
        allowance_type=allowance_type,
        merchants=matched,
        title=f"{type_label}发现关注商家",
        content_builder=build_content,
        link_url=link_url,
        expires_at=notification_storage.next_midnight_timestamp(),
    )
    task_storage.update_task_notification(
        task_id,
        status="queued" if job_id else "deduplicated",
    )
    return {
        "processed": True,
        "reason": "queued" if job_id else "deduplicated",
        "job_id": job_id,
        "merchant_count": len(new_merchants),
    }


async def recover_allowance_notifications() -> None:
    try:
        from utils.meituan_allowance_task_storage import get_meituan_allowance_task_storage

        storage = get_meituan_allowance_task_storage()
        tasks = await asyncio.to_thread(storage.list_pending_notification_tasks, 100)
        for task in tasks:
            try:
                await asyncio.to_thread(process_allowance_task_notification, str(task.get("task_id") or ""))
            except Exception as exc:
                logger.warning("恢复津贴通知处理失败: task_id=%s error=%s", task.get("task_id"), exc, exc_info=True)
    except Exception as exc:
        logger.warning("恢复津贴通知任务失败: %s", exc, exc_info=True)


def _dispatcher_runtime(**updates: Any) -> None:
    storage = get_pushplus_notification_storage()
    current = storage.get_runtime_state("dispatcher")
    current.pop("updated_at", None)
    if updates.get("running") is True:
        current.pop("stopped_at", None)
    current.update(updates)
    storage.set_runtime_state("dispatcher", current)


def _circuit_wait_seconds(runtime: dict[str, Any]) -> int:
    code = int(runtime.get("circuit_code") or 0)
    if not code:
        return 0
    until = int(runtime.get("circuit_until") or 0)
    if until <= 0:
        return 60
    return max(0, until - int(time.time()))


async def _reconcile_accepted_jobs() -> None:
    storage = get_pushplus_notification_storage()
    client = get_pushplus_client()
    for job in storage.list_stale_accepted_jobs(older_than_seconds=120, limit=3):
        try:
            result = await client.get_send_result(str(job.get("short_code") or ""))
            status = int(result.get("status") if result.get("status") is not None else -1)
            if status == 2:
                storage.mark_job_delivered(int(job["id"]), response_message="PushPlus 查询确认已送达")
            elif status == 3:
                storage.mark_job_failed(
                    int(job["id"]),
                    error_message=str(result.get("error_message") or "PushPlus 投递失败"),
                )
            else:
                storage.touch_job(int(job["id"]))
        except PushPlusError as exc:
            storage.touch_job(int(job["id"]))
            logger.warning("查询 PushPlus 投递结果失败: job_id=%s code=%s error=%s", job.get("id"), exc.code, exc)

    for attempt in storage.list_stale_admin_test_attempts(older_than_seconds=120, limit=3):
        try:
            result = await client.get_send_result(str(attempt.get("short_code") or ""))
            storage.update_send_attempt_delivery_by_short_code(
                short_code=str(attempt.get("short_code") or ""),
                delivery_status=int(result.get("status") if result.get("status") is not None else -1),
                error_message=str(result.get("error_message") or ""),
            )
        except PushPlusError as exc:
            logger.warning("查询 PushPlus 后台自测投递结果失败: attempt_id=%s code=%s error=%s", attempt.get("id"), exc.code, exc)


async def _dispatch_job(job: dict[str, Any]) -> None:
    storage = get_pushplus_notification_storage()
    client = get_pushplus_client()
    job_id = int(job.get("id") or 0)
    recipient = storage.get_job_recipient(int(job.get("user_id") or 0))
    if not recipient:
        storage.mark_job_failed(job_id, error_message="用户未绑定 PushPlus 或已解绑")
        return

    config = normalize_pushplus_config(load_system_settings_store().get("pushplus_config", {}))
    tier = str(config.get("account_tier") or "standard")
    rate = storage.get_rate_limit_state()
    daily_limit = 2000 if tier == "member" else 200
    window_count = int(rate.get("ten_second_count") or 0) if tier == "member" else int(rate.get("minute_count") or 0)
    window_limit = 5
    if int(rate.get("daily_count") or 0) >= daily_limit:
        next_at = int(rate.get("daily_reset_at") or storage.next_midnight_timestamp()) + 5
        if str(job.get("event_type") or "") == "merchant_match":
            storage.mark_job_failed(job_id, error_message="PushPlus 今日额度已用尽，商家通知已过期")
        else:
            storage.defer_job(job_id, next_attempt_at=next_at, error_message="PushPlus 今日额度已用尽")
        return
    if window_count >= window_limit:
        reset_key = "ten_second_reset_at" if tier == "member" else "minute_reset_at"
        storage.defer_job(
            job_id,
            next_attempt_at=int(rate.get(reset_key) or (time.time() + 12)) + 1,
            error_message="等待 PushPlus 频率窗口",
        )
        return

    attempt_count = storage.begin_job_attempt(job_id)
    attempt_id = storage.record_send_attempt(job_id=job_id)
    try:
        result = await client.send_message(
            title=str(job.get("title") or "平台通知"),
            content=str(job.get("content") or ""),
            friend_token=str(recipient.get("friend_token") or ""),
        )
        storage.update_send_attempt(
            attempt_id,
            response_code=int(result.get("code") or 0),
            accepted=True,
            short_code=str(result.get("short_code") or ""),
            response_message=str(result.get("message") or ""),
        )
        storage.mark_job_accepted(
            job_id,
            short_code=str(result.get("short_code") or ""),
            response_code=int(result.get("code") or 200),
            response_message=str(result.get("message") or "请求已受理"),
        )
        _dispatcher_runtime(
            last_success_at=int(time.time()),
            last_error="",
            circuit_code=0,
            circuit_until=0,
        )
    except PushPlusError as exc:
        storage.update_send_attempt(attempt_id, response_code=exc.code, accepted=False)
        if exc.code in {900, 903, 905}:
            circuit_until = storage.next_midnight_timestamp() if exc.code == 900 else 0
            _dispatcher_runtime(
                circuit_code=exc.code,
                circuit_until=circuit_until,
                last_error=str(exc),
                last_failure_at=int(time.time()),
            )
            if exc.code == 900 and str(job.get("event_type") or "") != "merchant_match":
                storage.defer_job(
                    job_id,
                    next_attempt_at=circuit_until + 5,
                    error_message=str(exc),
                )
            else:
                storage.mark_job_failed(job_id, error_message=str(exc), response_code=exc.code)
        elif exc.retryable and attempt_count < int(job.get("max_attempts") or 3):
            storage.defer_job(
                job_id,
                next_attempt_at=int(time.time()) + min(300, 15 * (2 ** max(0, attempt_count - 1))),
                error_message=str(exc),
            )
        else:
            storage.mark_job_failed(job_id, error_message=str(exc), response_code=exc.code)
        _dispatcher_runtime(last_error=str(exc), last_failure_at=int(time.time()))
    except Exception as exc:
        storage.update_send_attempt(attempt_id, response_code=0, accepted=False)
        if attempt_count < int(job.get("max_attempts") or 3):
            storage.defer_job(
                job_id,
                next_attempt_at=int(time.time()) + min(300, 15 * (2 ** max(0, attempt_count - 1))),
                error_message=str(exc),
            )
        else:
            storage.mark_job_failed(job_id, error_message=str(exc))
        _dispatcher_runtime(last_error=str(exc), last_failure_at=int(time.time()))


async def _dispatcher_loop() -> None:
    assert _dispatcher_stop_event is not None
    storage = get_pushplus_notification_storage()
    storage.recover_stuck_jobs()
    await recover_allowance_notifications()
    _dispatcher_runtime(running=True, started_at=int(time.time()))
    last_reconcile_at = 0.0
    try:
        while not _dispatcher_stop_event.is_set():
            try:
                storage.cleanup_expired()
                config = normalize_pushplus_config(load_system_settings_store().get("pushplus_config", {}))
                if not config.get("enabled"):
                    await asyncio.sleep(2)
                    continue
                runtime = storage.get_runtime_state("dispatcher")
                circuit_wait = _circuit_wait_seconds(runtime)
                if int(runtime.get("circuit_code") or 0) and circuit_wait > 0:
                    await asyncio.sleep(min(30, max(2, circuit_wait)))
                    continue
                if int(runtime.get("circuit_code") or 0) == 900 and circuit_wait == 0:
                    _dispatcher_runtime(circuit_code=0, circuit_until=0, last_error="")

                job = storage.claim_next_job()
                if job:
                    await _dispatch_job(job)
                    await asyncio.sleep(0.2)
                    continue

                if time.time() - last_reconcile_at >= 60:
                    last_reconcile_at = time.time()
                    await _reconcile_accepted_jobs()
                try:
                    await asyncio.wait_for(_dispatcher_stop_event.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("PushPlus 推送调度异常: %s", exc, exc_info=True)
                _dispatcher_runtime(last_error=str(exc), last_failure_at=int(time.time()))
                await asyncio.sleep(5)
    finally:
        _dispatcher_runtime(running=False, stopped_at=int(time.time()))


def start_pushplus_dispatcher() -> None:
    global _dispatcher_task, _dispatcher_stop_event
    ensure_pushplus_config()
    get_pushplus_notification_storage()
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return
    _dispatcher_stop_event = asyncio.Event()
    _dispatcher_task = asyncio.create_task(_dispatcher_loop())
    logger.info("PushPlus 推送调度器已启动")


async def stop_pushplus_dispatcher() -> None:
    global _dispatcher_task, _dispatcher_stop_event
    if _dispatcher_stop_event is not None:
        _dispatcher_stop_event.set()
    if _dispatcher_task is not None:
        try:
            await _dispatcher_task
        except asyncio.CancelledError:
            pass
    _dispatcher_task = None
    _dispatcher_stop_event = None
