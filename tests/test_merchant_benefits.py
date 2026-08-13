from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.merchant_benefits import (
    MerchantBenefitsService,
    MerchantBenefitsStorage,
    extract_shared_merchant_name,
    format_benefits_for_wechat,
    format_benefits_pending_for_wechat,
    validate_cashback_base_url,
)
from utils.system_settings_store import normalize_merchant_benefits_config, normalize_system_settings_store


class MerchantBenefitsConfigTests(unittest.TestCase):
    def test_defaults_are_disabled_and_bounded(self):
        config = normalize_merchant_benefits_config({})
        self.assertFalse(config["enabled"])
        self.assertEqual(config["positive_cache_seconds"], 300)
        self.assertEqual(config["negative_cache_seconds"], 120)

        bounded = normalize_merchant_benefits_config(
            {"latitude": 1000, "longitude": -1000, "positive_cache_seconds": 1}
        )
        self.assertEqual(bounded["latitude"], 90.0)
        self.assertEqual(bounded["longitude"], -180.0)
        self.assertEqual(bounded["positive_cache_seconds"], 30)

    def test_system_store_preserves_config(self):
        normalized = normalize_system_settings_store(
            {"merchant_benefits_config": {"enabled": True, "latitude": 30, "longitude": 110}}
        )
        self.assertTrue(normalized["merchant_benefits_config"]["enabled"])
        self.assertEqual(normalized["merchant_benefits_config"]["latitude"], 30.0)

    def test_cashback_url_requires_official_https_page(self):
        valid = "https://offsiteact.meituan.com/web/hoae/order_cashback_activity/index.html?activityId=17"
        self.assertEqual(validate_cashback_base_url(valid), valid)
        with self.assertRaises(ValueError):
            validate_cashback_base_url("http://offsiteact.meituan.com/web/hoae/order_cashback_activity/index.html")
        with self.assertRaises(ValueError):
            validate_cashback_base_url("https://example.com/web/hoae/order_cashback_activity/index.html")
        legacy = "pages/index/index?poi_id_str=old"
        self.assertEqual(validate_cashback_base_url(legacy), legacy)

    def test_shared_card_title_extracts_only_explicit_merchant_name(self):
        title = "我最近很喜欢美团外卖的「袁记云饺(理工大学两江校区店)」，分享给你看看"
        self.assertEqual(extract_shared_merchant_name(title), "袁记云饺(理工大学两江校区店)")
        self.assertEqual(extract_shared_merchant_name("美团外卖"), "")


class MerchantBenefitsStorageTests(unittest.TestCase):
    def test_cache_round_trip_and_expiry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("utils.merchant_benefits.resolve_runtime_data_path", return_value=Path(temp_dir)):
                storage = MerchantBenefitsStorage()
            result = {
                "status": "ok",
                "original_poi_id_str": "old-poi",
                "canonical_poi_id_str": "new-poi",
                "merchant_name": "测试店",
                "coupon": {"status": "has_coupon", "amount_yuan": 3, "threshold_yuan": 12},
                "cashback": {"status": "no_cashback"},
            }
            storage.put("key", result, ttl_seconds=60, account_id="gh_x", config_fingerprint="fp")
            self.assertEqual(storage.get("key")["canonical_poi_id_str"], "new-poi")

    def test_wechat_formatter_only_includes_confirmed_benefits(self):
        lines = format_benefits_for_wechat(
            {
                "status": "ok",
                "coupon": {"status": "has_coupon", "amount_yuan": 3, "threshold_yuan": 12},
                "cashback": {
                    "status": "has_cashback",
                    "total_max_yuan": 24,
                    "order_ratio": 20,
                    "order_max_yuan": 22.5,
                    "review_ratio": 5,
                    "review_max_yuan": 1.5,
                    "valid_inventory": 3,
                    "sign_status": "CAN_SIGN",
                },
                "cashback_url": "https://offsiteact.meituan.com/cashback",
            }
        )
        self.assertIn("商家券：3元，满12元可用", lines)
        self.assertIn("返现：最高24元，剩余3份", lines)
        self.assertIn("下单：20%，最高22.5元；评价：5%，最高1.5元", lines)
        self.assertTrue(any("参加官方返现" in item for item in lines))
        self.assertEqual(format_benefits_for_wechat({"status": "unknown"}), [])

    def test_wechat_formatter_reports_exhausted_cashback_inventory(self):
        lines = format_benefits_for_wechat(
            {
                "status": "ok",
                "coupon": {"status": "no_coupon"},
                "cashback": {
                    "status": "has_cashback",
                    "total_max_yuan": 16,
                    "order_ratio": 30,
                    "order_max_yuan": 15,
                    "review_ratio": 5,
                    "review_max_yuan": 1,
                    "valid_inventory": 0,
                    "sign_status": "NO_INVENTORY",
                },
            }
        )
        self.assertIn("返现：最高16元，名额已抢完", lines)
        self.assertIn("下单：30%，最高15元；评价：5%，最高1元", lines)

    def test_wechat_formatter_reports_confirmed_absence_and_partial_unknown(self):
        lines = format_benefits_for_wechat(
            {
                "status": "ok",
                "coupon": {"status": "no_coupon"},
                "cashback": {"status": "unknown"},
            }
        )
        self.assertIn("商家券：暂无商家券", lines)
        self.assertIn("返现：暂时无法确认", lines)
        self.assertEqual(
            MerchantBenefitsService._outcome(
                {
                    "status": "ok",
                    "coupon": {"status": "no_coupon"},
                    "cashback": {"status": "unknown"},
                }
            ),
            "unknown",
        )

    def test_wechat_pending_message_does_not_claim_no_benefits(self):
        self.assertEqual(
            format_benefits_pending_for_wechat(),
            ["商家券和返现金额获取中，请3秒后重新发送查询"],
        )


if __name__ == "__main__":
    unittest.main()
