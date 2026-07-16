from __future__ import annotations

import os
import tempfile
import unittest

from utils.meituan_allowance_task_storage import MeituanAllowanceTaskStorage
from utils.pushplus_service import hash_binding_nonce, normalize_keywords, normalize_merchant_match_text
from utils.pushplus_storage import PushPlusNotificationStorage


class PushPlusNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(delete=False)
        self.db_path = handle.name
        handle.close()
        os.unlink(self.db_path)
        self.storage = PushPlusNotificationStorage(self.db_path)

    def tearDown(self) -> None:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def test_binding_is_one_to_one_and_defaults_are_enabled(self) -> None:
        nonce = "first-binding"
        self.storage.create_bind_session(
            binding_id="b1",
            user_id=1,
            nonce_hash=hash_binding_nonce(nonce),
            qr_image_url="https://example.test/qr",
            expires_at=4102444800,
        )
        result = self.storage.complete_bind_session(
            nonce_hash=hash_binding_nonce(nonce),
            friend_id="friend-1",
            friend_token="token-1",
            nickname="tester",
        )
        self.assertTrue(result["success"])
        binding = self.storage.get_binding(1)
        self.assertEqual(binding["nickname"], "tester")
        self.assertEqual(binding["token_invalid_enabled"], 1)
        self.assertEqual(binding["merchant_match_enabled"], 1)

        self.storage.create_bind_session(
            binding_id="b2",
            user_id=2,
            nonce_hash=hash_binding_nonce("second-binding"),
            qr_image_url="https://example.test/qr2",
            expires_at=4102444800,
        )
        conflict = self.storage.complete_bind_session(
            nonce_hash=hash_binding_nonce("second-binding"),
            friend_id="friend-1",
            friend_token="token-1",
        )
        self.assertFalse(conflict["success"])
        self.assertEqual(conflict["status"], "conflict")

    def test_keyword_normalization_and_limits(self) -> None:
        keywords = normalize_keywords([" K-F C ", "kfc", "麦 当 劳"])
        self.assertEqual(keywords, [("K-F C", "kfc"), ("麦 当 劳", "麦当劳")])
        self.assertEqual(normalize_merchant_match_text("ＫＦＣ（万达店）"), "kfc万达店")
        with self.assertRaises(ValueError):
            normalize_keywords([f"keyword-{index}" for index in range(31)])

    def test_merchant_daily_dedupe_creates_one_summary_job(self) -> None:
        nonce = "merchant-binding"
        self.storage.create_bind_session(
            binding_id="b3",
            user_id=3,
            nonce_hash=hash_binding_nonce(nonce),
            qr_image_url="https://example.test/qr3",
            expires_at=4102444800,
        )
        self.storage.complete_bind_session(
            nonce_hash=hash_binding_nonce(nonce),
            friend_id="friend-3",
            friend_token="token-3",
        )
        merchants = [{"merchant_key": "poi:1", "merchant_name": "肯德基", "matched_keywords": ["肯德基"]}]
        job_id, new_items = self.storage.reserve_and_enqueue_merchant_matches(
            event_key="merchant:task-1",
            user_id=3,
            date_key="2099-01-01",
            address_id="addr-1",
            allowance_type="large",
            merchants=merchants,
            title="测试",
            content_builder=lambda items: items[0]["merchant_name"],
            link_url="https://example.test/web/query",
            expires_at=4102444800,
        )
        self.assertIsNotNone(job_id)
        self.assertEqual(len(new_items), 1)
        second_job_id, second_items = self.storage.reserve_and_enqueue_merchant_matches(
            event_key="merchant:task-2",
            user_id=3,
            date_key="2099-01-01",
            address_id="addr-1",
            allowance_type="large",
            merchants=merchants,
            title="测试",
            content_builder=lambda items: "duplicate",
            link_url="",
            expires_at=4102444800,
        )
        self.assertIsNone(second_job_id)
        self.assertEqual(second_items, [])

    def test_admin_test_attempt_tracks_callback_delivery(self) -> None:
        attempt_id = self.storage.record_send_attempt(job_id=0)
        self.storage.update_send_attempt(
            attempt_id,
            response_code=200,
            accepted=True,
            short_code="admin-test-short-code",
            response_message="请求已受理",
        )
        self.assertTrue(
            self.storage.update_send_attempt_delivery_by_short_code(
                short_code="admin-test-short-code",
                delivery_status=2,
            )
        )
        latest_test = self.storage.get_stats()["latest_admin_test"]
        self.assertEqual(latest_test["short_code"], "admin-test-short-code")
        self.assertEqual(latest_test["delivery_status"], 2)
        self.assertIsNotNone(latest_test["delivered_at"])
        self.assertEqual(self.storage.get_stats()["delivered_today"], 1)


class AllowanceOwnershipSchemaTests(unittest.TestCase):
    def test_allowance_task_persists_web_owner(self) -> None:
        handle = tempfile.NamedTemporaryFile(delete=False)
        db_path = handle.name
        handle.close()
        os.unlink(db_path)
        try:
            storage = MeituanAllowanceTaskStorage(db_path)
            storage.create_task(
                task_id="owned-task",
                status="queued",
                allowance_type="large",
                meituan_user_id="10001",
                address_id="address-1",
                token_masked="abcd***wxyz",
                token_fingerprint="fingerprint",
                input_latitude="39.9",
                input_longitude="116.3",
                normalized_latitude="39900000",
                normalized_longitude="116300000",
                web_user_id=7,
                web_token_id=8,
                task_source="manual",
                address_name="测试地址",
            )
            task = storage.get_task("owned-task")
            self.assertEqual(task["web_user_id"], 7)
            self.assertEqual(task["web_token_id"], 8)
            self.assertEqual(task["notification_status"], "pending")
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(db_path + suffix)
                except FileNotFoundError:
                    pass


if __name__ == "__main__":
    unittest.main()
