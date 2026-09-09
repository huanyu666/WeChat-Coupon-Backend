from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from routes import meituan_expand as expand_routes

from utils.meituan_expand import (
    MeituanExpandClient,
    MeituanExpandError,
    MeituanExpandService,
    MeituanExpandStorage,
    _amount_to_yuan,
    _extract_pre,
    _raise_for_http_status,
    _select_coupon,
    get_meituan_expand_coordinate_presets,
    is_large_meituan_expand_target,
    normalize_credential,
    parse_mttouch_url,
)
from utils.system_settings_store import normalize_meituan_expand_config, normalize_system_settings_store


TEST_CONFIG = {
    "enabled": True,
    "default_latitude": 40.60353704,
    "default_longitude": 120.75256222,
    "global_concurrency_limit": 4,
    "account_concurrency_limit": 1,
    "direct_retry_count": 3,
    "task_timeout_seconds": 90,
    "proxy_enabled": False,
    "fallback_to_proxy": False,
    "proxy_api_url": "",
    "proxy_max_switches": 3,
    "proxy_timeout_seconds": 15,
}


class FakeResponse:
    def __init__(self, status_code: int, *, text: str = "", headers=None, payload=None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeExpandClient:
    def __init__(self) -> None:
        self.precheck_calls = 0
        self.execute_calls = 0

    def precheck(self, credential, latitude, longitude, task_timeout):
        self.precheck_calls += 1
        public = {
            "coupon": {
                "coupon_view_id": "view-1",
                "coupon_config_id": "coupon-1",
                "coupon_name": "测试神券",
                "coupon_amount": 10,
                "coupon_threshold": 20,
                "coupon_count": 1,
            },
            "targets": [
                {
                    "target_coupon_config_id": "target-1",
                    "target_coupon_amount": 18,
                    "target_coupon_threshold": 20,
                    "target_asset_type": 1,
                    "channel": "wm_wxapp",
                    "channel_context_index": 0,
                    "channel_target_index": 0,
                    "coupon_name": "测试神券",
                    "coupon_count": 1,
                    "status": "available",
                    "failure_reason": "",
                    "request_duration_ms": 12,
                }
            ],
            "default_target_index": 0,
            "duration_ms": 20,
        }
        private = {
            "channels": [
                {
                    "channel": "wm_wxapp",
                    "inflate_token": "inflate-secret",
                    "biz_group": "101",
                    "targets": [
                        {
                            "target_coupon_config_id": "target-1",
                            "target_coupon_amount": 18,
                            "target_coupon_threshold": 20,
                            "target_asset_type": 1,
                        }
                    ],
                    "pre_body": {"token": credential["token"]},
                    "pre_query": {},
                    "headers": {},
                }
            ]
        }
        return public, private

    def execute(self, execution_context, public_target, task_timeout):
        self.execute_calls += 1
        return {
            "ok": True,
            "channel": "wm_wxapp",
            "inflated_coupons": [
                {"coupon_name": "测试神券", "coupon_amount": 18, "coupon_threshold": 20}
            ],
            "duration_ms": 10,
            "direct": True,
        }


async def wait_for_status(storage: MeituanExpandStorage, job_id: str, statuses: set[str]) -> dict:
    for _ in range(200):
        job = storage.get_job(job_id, include_secrets=False) or {}
        if job.get("status") in statuses:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"job did not reach {statuses}: {storage.get_job(job_id)}")


async def wait_for_batch(storage: MeituanExpandStorage, batch_id: str, statuses: set[str]) -> dict:
    for _ in range(200):
        batch = storage.batch_snapshot(batch_id) or {}
        if batch.get("status") in statuses:
            return batch
        await asyncio.sleep(0.01)
    raise AssertionError(f"batch did not reach {statuses}: {storage.batch_snapshot(batch_id)}")


class MeituanExpandParsingTests(unittest.TestCase):
    def test_mttouch_url_is_strict_and_normalized(self):
        parsed = parse_mttouch_url(
            "https://i.meituan.com/mttouch/page/account?userId=123456&token=abc_DEF-123"
        )
        self.assertEqual(parsed["token"], "abc_DEF-123")
        self.assertEqual(parsed["meituan_user_id"], "123456")
        self.assertNotIn("example.com", parsed["account_url"])

        for invalid in (
            "http://i.meituan.com/mttouch/page/account?userId=1&token=x",
            "https://example.com/mttouch/page/account?userId=1&token=x",
            "https://i.meituan.com/mttouch/page/account/other?userId=1&token=x",
            "https://i.meituan.com/mttouch/page/account?userId=1",
        ):
            with self.assertRaises(ValueError):
                parse_mttouch_url(invalid)

    def test_saved_token_accepts_raw_or_full_link(self):
        raw = normalize_credential("raw-token", "123")
        linked = normalize_credential(
            "https://i.meituan.com/mttouch/page/account?userId=123&token=raw-token",
            "123",
        )
        self.assertEqual(raw, linked)
        with self.assertRaises(ValueError):
            normalize_credential(
                "https://i.meituan.com/mttouch/page/account?userId=456&token=raw-token",
                "123",
            )

    def test_config_is_disabled_and_proxy_is_forced_off(self):
        config = normalize_meituan_expand_config(
            {"enabled": True, "proxy_enabled": True, "fallback_to_proxy": True, "global_concurrency_limit": 99}
        )
        self.assertTrue(config["enabled"])
        self.assertFalse(config["proxy_enabled"])
        self.assertFalse(config["fallback_to_proxy"])
        self.assertEqual(config["global_concurrency_limit"], 16)
        store = normalize_system_settings_store({})
        self.assertFalse(store["meituan_expand_config"]["enabled"])

    def test_user_expansion_settings_have_separate_switch_and_purchase_url(self):
        config = normalize_meituan_expand_config({
            "enabled": True,
            "user_enabled": True,
            "purchase_url": "https://example.com/buy",
        })
        self.assertTrue(config["enabled"])
        self.assertTrue(config["user_enabled"])
        self.assertEqual(config["purchase_url"], "https://example.com/buy")
        defaults = normalize_meituan_expand_config({})
        self.assertFalse(defaults["user_enabled"])
        self.assertEqual(defaults["purchase_url"], "")

    def test_original_and_new_coordinate_presets_are_available_without_duplicates(self):
        presets = get_meituan_expand_coordinate_presets()
        self.assertEqual(len(presets), 23)
        self.assertEqual(presets[0]["id"], "xingcheng")
        self.assertEqual(presets[0]["latitude"], 40.60353704)
        self.assertEqual(presets[-1]["id"], "gongzhuling_gov")
        self.assertEqual(len({item["id"] for item in presets}), 23)
        self.assertEqual(sum("兴城高中" in item["label"] for item in presets), 1)
        self.assertEqual(sum("望城区" in item["label"] for item in presets), 1)
        self.assertEqual(sum("海丰" in item["label"] for item in presets), 1)

    def test_large_target_rule_matches_known_coupon_shapes(self):
        self.assertTrue(is_large_meituan_expand_target(16, 28))
        self.assertTrue(is_large_meituan_expand_target(25, 40))
        self.assertFalse(is_large_meituan_expand_target(13, 20))
        self.assertFalse(is_large_meituan_expand_target(14, 27))

    def test_retryable_http_status_is_classified_before_json(self):
        with self.assertRaises(MeituanExpandError) as raised:
            _raise_for_http_status(FakeResponse(503), "测试接口")
        self.assertEqual(raised.exception.code, "network_failed")
        self.assertTrue(raised.exception.retryable)

    def test_amount_display_is_normalized_without_changing_request_units(self):
        self.assertEqual(_amount_to_yuan(1800), 18)
        self.assertEqual(_amount_to_yuan(1850), 18.5)
        self.assertEqual(_amount_to_yuan(20), 20)
        _, _, targets = _extract_pre({
            "data": {
                "inflateToken": "inflate",
                "targetCouponGroups": [{
                    "couponBizGroupId": "101",
                    "targetCoupons": [{
                        "targetCouponConfigId": "target",
                        "targetCouponRealAmount": 1800,
                        "targetCouponRealAmountLimit": 2000,
                    }],
                }],
            }
        })
        self.assertEqual(targets[0]["target_coupon_amount"], 1800)
        self.assertEqual(targets[0]["target_coupon_amount_yuan"], 18)
        self.assertEqual(targets[0]["target_coupon_threshold_yuan"], 20)
        coupon = _select_coupon({
            "data": {
                "userMagicalCouponGroups": [{
                    "assetType": 3,
                    "inflated": False,
                    "couponName": "测试神券",
                    "couponAmount": 500,
                    "orderAmountLimit": 2000,
                    "userMmcInfos": [{"couponViewId": "view", "couponConfigIdStr": "coupon"}],
                }]
            }
        })
        self.assertEqual(coupon["coupon_amount"], 500)
        self.assertEqual(coupon["coupon_amount_yuan"], 5)
        self.assertEqual(coupon["coupon_threshold_yuan"], 20)

    def test_html_403_is_gateway_lock_and_json_403_is_business_error(self):
        html_response = FakeResponse(
            403,
            text="<html><title>403 Forbidden</title></html>",
            headers={"content-type": "text/html", "x-forbid-reason": "risk"},
        )
        with self.assertRaises(MeituanExpandError) as raised:
            _raise_for_http_status(html_response, "实际膨胀接口", half_success=True)
        self.assertEqual(raised.exception.code, "gateway_blocked")
        self.assertIn("预查询已成功", str(raised.exception))
        self.assertFalse(raised.exception.retryable)

        json_response = FakeResponse(
            403,
            text='{"code":401,"msg":"token已过期"}',
            headers={"content-type": "application/json"},
            payload={"code": 401, "msg": "token已过期"},
        )
        with self.assertRaises(MeituanExpandError) as raised:
            _raise_for_http_status(json_response, "实际膨胀接口", half_success=True)
        self.assertEqual(raised.exception.code, "token_invalid")
        self.assertFalse(raised.exception.retryable)


class MeituanExpandServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.key_patch = patch.dict(os.environ, {"WX_MEITUAN_EXPAND_ENCRYPTION_KEY": "test-only-key"})
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        self.storage = MeituanExpandStorage(Path(self.temp_dir.name) / "expand.db")
        self.client = FakeExpandClient()
        self.service = MeituanExpandService(storage=self.storage, client=self.client)
        self.config_patch = patch("utils.meituan_expand.get_meituan_expand_config", return_value=dict(TEST_CONFIG))
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.credential = normalize_credential("sensitive-token-value", "123456789")
        self.actor = {
            "actor_id": 1,
            "actor_username": "admin",
            "target_user_id": 2,
            "target_username": "target",
            "token_source": "saved_token",
            "token_id": 99,
            "latitude": 40.60353704,
            "longitude": 120.75256222,
        }

    async def test_precheck_encrypts_credentials_and_execute_is_idempotent(self):
        first = self.service.submit_precheck(self.actor, self.credential)
        first = await wait_for_status(self.storage, first["id"], {"waiting_confirmation"})
        self.assertEqual(self.client.precheck_calls, 1)
        self.assertEqual(self.client.execute_calls, 0)
        self.assertNotIn("sensitive-token-value", str(first))
        database_bytes = b"".join(
            path.read_bytes()
            for path in Path(self.temp_dir.name).glob("expand.db*")
            if path.is_file()
        )
        self.assertNotIn(b"sensitive-token-value", database_bytes)

        self.service.submit_execute(first["id"], 0, {"actor_id": 1, "actor_username": "admin"})
        first = await wait_for_status(self.storage, first["id"], {"succeeded"})
        self.assertEqual(self.client.execute_calls, 1)
        self.assertEqual(first["result"]["inflated_coupons"][0]["coupon_amount"], 18)

        second = self.service.submit_precheck(self.actor, self.credential)
        second = await wait_for_status(self.storage, second["id"], {"waiting_confirmation"})
        with self.assertRaisesRegex(ValueError, "已经成功膨胀"):
            self.service.submit_execute(second["id"], 0, {"actor_id": 1, "actor_username": "admin"})
        blocked = self.storage.get_job(second["id"], include_secrets=False)
        self.assertEqual(blocked["status"], "idempotent_blocked")

    async def test_network_retry_count_means_three_retries_after_first_attempt(self):
        calls = 0

        def flaky(task_timeout):
            nonlocal calls
            calls += 1
            if calls < 4:
                raise MeituanExpandError("temporary", code="network_failed", retryable=True)
            return "ok"

        with patch("utils.meituan_expand.asyncio.sleep", return_value=None):
            result, retries = await self.service._with_retries(
                flaky,
                retry_count=3,
                total_timeout_seconds=30,
            )
        self.assertEqual(result, "ok")
        self.assertEqual(retries, 3)
        self.assertEqual(calls, 4)

    async def test_failed_retries_are_exposed_on_the_error(self):
        calls = 0

        def always_fails(task_timeout):
            nonlocal calls
            calls += 1
            raise MeituanExpandError("temporary", code="network_failed", retryable=True)

        with patch("utils.meituan_expand.asyncio.sleep", return_value=None):
            with self.assertRaises(MeituanExpandError) as raised:
                await self.service._with_retries(
                    always_fails,
                    retry_count=3,
                    total_timeout_seconds=30,
                )
        self.assertEqual(raised.exception.retry_count, 3)
        self.assertEqual(calls, 4)

    async def test_cancelled_execute_queue_cannot_start(self):
        job = self.storage.create_job(self.actor, self.credential)
        self.storage.transition(job["id"], "queued", status="waiting_confirmation")
        cancelled = self.service.cancel(job["id"], {"actor_id": 1, "actor_username": "admin"})
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(self.storage.transition(job["id"], "execute_queued", status="execute_running"))

    async def test_batch_skips_remaining_addresses_after_no_coupon_account(self):
        class BatchClient(FakeExpandClient):
            def precheck(self, credential, latitude, longitude, task_timeout):
                if credential["meituan_user_id"] == "no-coupon-account":
                    self.precheck_calls += 1
                    raise MeituanExpandError("当前账号没有可用神券", code="no_coupon")
                return super().precheck(credential, latitude, longitude, task_timeout)

        self.service.client = BatchClient()
        no_coupon_credential = normalize_credential("no-coupon-token", "no-coupon-account")
        valid_credential = normalize_credential("valid-token", "valid-account")
        owner = lambda token_id, username: {
            "target_user_id": token_id,
            "target_username": username,
            "token_source": "saved_token",
            "token_id": token_id,
        }
        coordinates = [
            {"id": "one", "label": "地址一", "latitude": 1.0, "longitude": 2.0},
            {"id": "two", "label": "地址二", "latitude": 3.0, "longitude": 4.0},
            {"id": "three", "label": "地址三", "latitude": 5.0, "longitude": 6.0},
        ]
        batch = self.service.submit_batch_precheck(
            {"actor_id": 1, "actor_username": "admin"},
            [
                (no_coupon_credential, owner(11, "无券账号")),
                (valid_credential, owner(12, "有效账号")),
            ],
            coordinates,
        )
        final = await wait_for_batch(self.storage, batch["id"], {"succeeded"})
        jobs = self.storage.list_batch_jobs(batch["id"])
        no_coupon_jobs = [job for job in jobs if job["token_id"] == 11]
        valid_jobs = [job for job in jobs if job["token_id"] == 12]
        self.assertEqual(len(no_coupon_jobs), 1)
        self.assertEqual(no_coupon_jobs[0]["status"], "no_coupon")
        self.assertEqual(len(valid_jobs), 3)
        self.assertEqual(final["planned_combinations"], 6)
        self.assertEqual(final["created_jobs"], 4)
        self.assertEqual(final["skipped_combinations"], 2)
        self.assertEqual(final["no_coupon_accounts"], 1)
        self.assertEqual(self.service.client.execute_calls, 0)


class MeituanExpandRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_auth_failure_is_returned_without_route_data(self):
        with patch.object(
            expand_routes,
            "_get_current_web_admin_user",
            AsyncMock(side_effect=PermissionError("Web 登录已过期，请重新登录")),
        ):
            response = await expand_routes.get_settings(object())
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 401)
        self.assertFalse(payload["success"])
        self.assertTrue(payload["login_expired"])

        with patch.object(
            expand_routes,
            "_get_current_web_admin_user",
            AsyncMock(return_value={"id": 2, "username": "user", "is_admin": False}),
        ):
            response = await expand_routes.get_settings(object())
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(payload["success"])

    async def test_settings_are_disabled_by_default_and_proxy_url_is_masked(self):
        service = type(
            "FakeService",
            (),
            {
                "runtime": lambda self: {"active_tasks": 0, "global_limit": 4, "proxy_enabled": False},
                "storage": type("FakeStorage", (), {"stats": lambda self: {"today_total": 0}})(),
            },
        )()
        config = dict(TEST_CONFIG, enabled=False, proxy_api_url="https://proxy.example/api?secret=value")
        with (
            patch.object(expand_routes, "_require_admin", AsyncMock(return_value={"id": 1, "username": "admin"})),
            patch.object(expand_routes, "get_meituan_expand_config", return_value=config),
            patch.object(expand_routes, "get_meituan_expand_service", return_value=service),
        ):
            response = await expand_routes.get_settings(object())
        payload = json.loads(response.body)
        self.assertFalse(payload["settings"]["enabled"])
        self.assertEqual(len(payload["coordinate_presets"]), 23)
        self.assertNotIn("proxy_api_url", payload["settings"])
        self.assertTrue(payload["settings"]["proxy_api_configured"])
        self.assertNotIn("secret=value", json.dumps(payload, ensure_ascii=False))

    async def test_token_list_is_masked_and_never_returns_token(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "meituan_query.db"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY, username TEXT, status TEXT, is_admin INTEGER
                    );
                    CREATE TABLE tokens (
                        id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, token TEXT,
                        meituan_user_id TEXT, is_active INTEGER, updated_at TEXT
                    );
                    INSERT INTO users VALUES (1, 'target', 'approved', 0);
                    INSERT INTO tokens VALUES (
                        9, 1, '常用账号', 'full-sensitive-token', '123456789', 1, '2026-08-13 10:00:00'
                    );
                    """
                )
            with (
                patch.object(expand_routes, "_require_admin", AsyncMock(return_value={"id": 1, "username": "admin"})),
                patch.object(expand_routes, "resolve_runtime_data_path", return_value=database),
            ):
                response = await expand_routes.list_tokens(object(), user_id=1)
        payload = json.loads(response.body)
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(payload["tokens"]), 1)
        self.assertNotIn("full-sensitive-token", encoded)
        self.assertNotIn("123456789", encoded)
        self.assertEqual(payload["tokens"][0]["meituan_user_id"], "123***89")
        self.assertEqual(len(payload["tokens"][0]["token_fingerprint"]), 10)

    async def test_user_list_only_contains_accounts_with_valid_tokens_and_keeps_admin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "meituan_query.db"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY, username TEXT, status TEXT, is_admin INTEGER
                    );
                    CREATE TABLE tokens (
                        id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, token TEXT,
                        meituan_user_id TEXT, is_active INTEGER, updated_at TEXT
                    );
                    INSERT INTO users VALUES (1, 'admin', 'approved', 1);
                    INSERT INTO users VALUES (2, 'valid-user', 'approved', 0);
                    INSERT INTO users VALUES (3, 'no-token-user', 'approved', 0);
                    INSERT INTO tokens VALUES (11, 1, '管理员账号', 'admin-token', 'mt-admin', 1, '2026-08-14 10:00:00');
                    INSERT INTO tokens VALUES (12, 2, '普通账号', 'user-token', 'mt-user', 1, '2026-08-14 10:00:00');
                    INSERT INTO tokens VALUES (13, 3, '失效账号', 'old-token', 'mt-old', 0, '2026-08-14 10:00:00');
                    """
                )
            with (
                patch.object(expand_routes, "_require_admin", AsyncMock(return_value={"id": 1, "username": "admin"})),
                patch.object(expand_routes, "resolve_runtime_data_path", return_value=database),
            ):
                response = await expand_routes.list_users(object())
        payload = json.loads(response.body)
        self.assertEqual([item["username"] for item in payload["users"]], ["admin", "valid-user"])
        self.assertTrue(payload["users"][0]["is_admin"])
        self.assertEqual(payload["users"][0]["valid_token_count"], 1)

    async def test_batch_precheck_creates_persistent_scheduler(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "meituan_query.db"
            with sqlite3.connect(database) as connection:
                connection.executescript(
                    """
                    CREATE TABLE users (
                        id INTEGER PRIMARY KEY, username TEXT, status TEXT, is_admin INTEGER
                    );
                    CREATE TABLE tokens (
                        id INTEGER PRIMARY KEY, user_id INTEGER, name TEXT, token TEXT,
                        meituan_user_id TEXT, is_active INTEGER, updated_at TEXT
                    );
                    INSERT INTO users VALUES (1, 'admin', 'approved', 1);
                    INSERT INTO users VALUES (2, 'second', 'approved', 0);
                    INSERT INTO tokens VALUES (11, 1, 'A', 'token-a', 'mt-a', 1, '2026-08-14 10:00:00');
                    INSERT INTO tokens VALUES (12, 2, 'B', 'token-b', 'mt-b', 1, '2026-08-14 10:00:00');
                    """
                )

            class BatchService:
                def submit_batch_precheck(self, actor, accounts, coordinates):
                    return {
                        "id": "batch-1",
                        "status": "queued",
                        "planned_combinations": len(accounts) * len(coordinates),
                        "account_count": len(accounts),
                        "coordinate_count": len(coordinates),
                        "jobs": [],
                    }

            request = expand_routes.ExpandBatchPrecheckRequest(
                token_ids=[11, 12],
                coordinate_preset_ids=["jining_huadi", "jianyang_xuhai"],
            )
            with (
                patch.object(expand_routes, "_require_admin", AsyncMock(return_value={"id": 1, "username": "admin"})),
                patch.object(expand_routes, "resolve_runtime_data_path", return_value=database),
                patch.object(expand_routes, "get_meituan_expand_config", return_value=dict(TEST_CONFIG)),
                patch.object(expand_routes, "get_meituan_expand_service", return_value=BatchService()),
            ):
                response = await expand_routes.precheck_batch(object(), request)
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(payload["account_count"], 2)
        self.assertEqual(payload["coordinate_count"], 2)
        self.assertEqual(payload["combination_count"], 4)
        self.assertEqual(payload["batch"]["id"], "batch-1")
        self.assertEqual(payload["jobs"], [])

    async def test_precheck_rejects_ambiguous_credentials(self):
        request = expand_routes.ExpandPrecheckRequest(
            token_id=9,
            mttouch_url="https://i.meituan.com/mttouch/page/account?userId=1&token=x",
        )
        with (
            patch.object(expand_routes, "_require_admin", AsyncMock(return_value={"id": 1, "username": "admin"})),
            patch.object(expand_routes, "get_meituan_expand_config", return_value=dict(TEST_CONFIG)),
        ):
            response = await expand_routes.precheck(object(), request)
        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 400)
        self.assertIn("只能选择一个", payload["error"])

    async def test_user_settings_require_login_and_do_not_expose_admin_settings(self):
        with patch.object(
            expand_routes,
            "_current_user_or_error",
            AsyncMock(return_value=(None, expand_routes._error("需要登录", 401, login_expired=True))),
        ):
            response = await expand_routes.get_user_expand_settings(object())
        self.assertEqual(response.status_code, 401)
        self.assertTrue(json.loads(response.body)["login_expired"])

        with (
            patch.object(expand_routes, "_current_user_or_error", AsyncMock(return_value=({"id": 7}, None))),
            patch.object(
                expand_routes,
                "get_meituan_expand_config",
                return_value={"enabled": True, "user_enabled": True, "purchase_url": "https://example.com/buy"},
            ),
        ):
            response = await expand_routes.get_user_expand_settings(object())
        payload = json.loads(response.body)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["purchase_url"], "https://example.com/buy")
        self.assertNotIn("default_latitude", payload)
        self.assertNotIn("proxy_api_url", payload)


if __name__ == "__main__":
    unittest.main()
