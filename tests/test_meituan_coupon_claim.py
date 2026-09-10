from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.meituan_coupon_claim import (
    MeituanCouponClaimService,
    MeituanCouponClaimStorage,
    aggregate_channel_results,
)


class FakeClient:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def issue(self, token, channel, timeout_seconds):
        self.calls.append((token, channel, timeout_seconds))
        value = self.results[channel]
        if isinstance(value, list):
            return value.pop(0)
        return value


class MeituanCouponClaimTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.storage = MeituanCouponClaimStorage(Path(self.tempdir.name) / "claims.db")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_aggregate_success_and_coupon_summary(self):
        status, result, error_code, error_message = aggregate_channel_results({
            "workbuddy": {"status": "succeeded", "coupons": [{"name": "满20减10"}]},
            "tabbit": {"status": "succeeded", "coupons": [{"name": "满10减3"}]},
        })
        self.assertEqual(status, "succeeded")
        self.assertEqual(result["coupon_count"], 2)
        self.assertEqual(error_code, "")
        self.assertEqual(error_message, "")

    def test_mixed_no_coupon_and_already_received_is_not_no_coupon(self):
        status, _, _, _ = aggregate_channel_results({
            "workbuddy": {"status": "no_coupon", "coupons": []},
            "tabbit": {"status": "already_received", "coupons": []},
        })
        self.assertNotEqual(status, "no_coupon")

    def test_stats_counts_40_20_and_distinct_accounts(self):
        first = self.storage.create_job(
            {"web_user_id": 1, "token_id": 1, "token_name": "a", "source": "test"},
            "token-a", "account-a",
        )
        self.storage.finish(
            first["id"], "succeeded", channels={}, result={"coupons": [
                {"amount_yuan": 20, "threshold_yuan": 40},
                {"amount_yuan": 20, "threshold_yuan": 30},
            ]},
        )
        second = self.storage.create_job(
            {"web_user_id": 2, "token_id": 2, "token_name": "b", "source": "test"},
            "token-b", "account-b",
        )
        self.storage.finish(
            second["id"], "partial_success", channels={}, result={"coupons": [
                {"amount_yuan": 20.0, "threshold_yuan": 40.0},
                {"amount_yuan": 20, "threshold_yuan": 40},
            ]},
        )
        stats = self.storage.stats()
        self.assertEqual(stats["target_40_20_coupon_count"], 3)
        self.assertEqual(stats["target_40_20_account_count"], 2)
        self.assertEqual(stats["target_40_20_task_count"], 2)
        self.assertTrue(stats["target_40_20_latest_at"])

    def test_stats_ignores_invalid_result_and_non_today_jobs(self):
        current = self.storage.create_job(
            {"web_user_id": 1, "token_id": 1, "token_name": "a", "source": "test"},
            "token-a", "account-a",
        )
        self.storage.finish(current["id"], "failed", channels={}, result={"coupons": "bad"})
        old = self.storage.create_job(
            {"web_user_id": 2, "token_id": 2, "token_name": "b", "source": "test"},
            "token-b", "account-b",
        )
        with self.storage._lock, self.storage._connection() as connection:
            connection.execute(
                "UPDATE coupon_claim_jobs SET business_date='2000-01-01', result_json=?, finished_at='2000-01-01 00:00:00' WHERE id=?",
                (json.dumps({"coupons": [{"amount_yuan": 20, "threshold_yuan": 40}]}), old["id"]),
            )
        self.assertEqual(self.storage.stats()["target_40_20_coupon_count"], 0)

    async def test_service_retries_network_failures_but_not_business_errors(self):
        results = {
            "workbuddy": [
                {"channel": "workbuddy", "status": "network_failed", "message": "temporary", "coupons": [], "coupon_count": 0},
                {"channel": "workbuddy", "status": "succeeded", "message": "ok", "coupons": [{"name": "券"}], "coupon_count": 1},
            ],
            "tabbit": {"channel": "tabbit", "status": "no_coupon", "message": "none", "coupons": [], "coupon_count": 0},
        }
        fake = FakeClient(results)
        service = MeituanCouponClaimService(self.storage, fake)
        with patch("utils.meituan_coupon_claim.get_meituan_coupon_claim_config", return_value={
            "enabled": True, "global_concurrency_limit": 2, "channel_timeout_seconds": 5,
            "task_timeout_seconds": 10, "direct_retry_count": 1,
        }):
            job = service.submit({"web_user_id": 1, "token_id": 2, "token_name": "测试", "source": "test"}, "secret-token", "9988")
            for _ in range(100):
                current = self.storage.get_job(job["id"])
                if current and current["status"] in {"succeeded", "partial_success", "no_coupon", "failed"}:
                    break
                await asyncio.sleep(0.01)
        current = self.storage.get_job(job["id"])
        self.assertEqual(current["status"], "partial_success")
        self.assertEqual(len([call for call in fake.calls if call[1] == "workbuddy"]), 2)
        self.assertNotIn("secret-token", str(current))
        await service.stop()

    async def test_same_account_is_idempotently_blocked(self):
        first = self.storage.create_job({"web_user_id": 1, "token_id": 2, "token_name": "a", "source": "test"}, "secret", "same")
        with self.assertRaises(ValueError):
            self.storage.create_job({"web_user_id": 3, "token_id": 4, "token_name": "b", "source": "test"}, "other", "same")
        self.assertEqual(first["status"], "queued")


if __name__ == "__main__":
    unittest.main()
