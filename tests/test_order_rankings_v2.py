import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import httpx

from scripts import order_rankings_source1_relay as rankings_relay

from utils.order_rankings_v2 import (
    OrderRankingsV2Service,
    OrderRankingsV2Storage,
    is_ranking_rank_text_enabled,
    normalize_ranking_v2_config,
    parse_source1_ranking_html,
    parse_source2_shop_data,
    serialize_ranking_v2_config,
)
from utils.system_settings_store import normalize_system_settings_store


class OrderRankingsV2Tests(TestCase):
    def test_system_settings_normalization_preserves_proxy_configuration(self):
        store = normalize_system_settings_store({
            "order_rankings_v2_config": {
                "proxy_fallback_enabled": True,
                "proxy_api_url": "https://proxy.example/api?token=secret",
                "proxy_validation_cache_seconds": 120,
                "proxy_retry_count": 3,
                "window_seconds": 1800,
            },
        })
        config = store["order_rankings_v2_config"]
        self.assertTrue(config["proxy_fallback_enabled"])
        self.assertEqual(config["proxy_api_url"], "https://proxy.example/api?token=secret")
        self.assertEqual(config["proxy_validation_cache_seconds"], 120)
        self.assertEqual(config["proxy_retry_count"], 3)
        self.assertEqual(config["window_seconds"], 1800)

        # A later unrelated settings write must not erase the proxy fields.
        store["rank_text_enabled"] = True
        normalized_again = normalize_system_settings_store(store)
        self.assertTrue(normalized_again["order_rankings_v2_config"]["proxy_fallback_enabled"])
        self.assertEqual(normalized_again["order_rankings_v2_config"]["proxy_retry_count"], 3)
        self.assertEqual(normalized_again["order_rankings_v2_config"]["window_seconds"], 1800)

    def test_collection_window_is_bounded_and_serialized(self):
        config = normalize_ranking_v2_config({"window_seconds": 99999})
        self.assertEqual(config["window_seconds"], 3600)
        config = normalize_ranking_v2_config({"window_seconds": 10})
        self.assertEqual(config["window_seconds"], 60)

    def test_proxy_config_is_bounded_and_admin_url_is_masked(self):
        config = normalize_ranking_v2_config({
            "proxy_fallback_enabled": True,
            "proxy_api_url": "https://proxy.example/api?token=secret",
            "proxy_validation_cache_seconds": 9999,
            "proxy_retry_count": 99,
        })
        self.assertTrue(config["proxy_fallback_enabled"])
        self.assertEqual(config["proxy_validation_cache_seconds"], 600)
        self.assertEqual(config["proxy_retry_count"], 5)
        serialized = serialize_ranking_v2_config(config)
        self.assertTrue(serialized["proxy_api_url_configured"])
        self.assertEqual(serialized["proxy_api_url_display"], "https://proxy.example/api?***")

    def test_proxy_lease_reuses_one_ip_and_allows_same_ip_after_invalidation(self):
        lease = rankings_relay.ProxyLease()
        lease._fetch_and_validate = AsyncMock(return_value="http://140.250.177.135:40017")
        options = {
            "enabled": True,
            "api_url": "https://proxy.example/api",
            "validation_cache_seconds": 60,
            "retry_count": 2,
        }

        async def exercise():
            first = await lease.acquire(options, "2026-08-07 17:00", set())
            second = await lease.acquire(options, "2026-08-07 17:00", set())
            await lease.invalidate(first)
            third = await lease.acquire(options, "2026-08-07 17:00", set())
            return first, second, third

        first, second, third = asyncio.run(exercise())
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(lease._fetch_and_validate.await_count, 2)

    def test_proxy_fallback_runs_only_after_direct_transport_failure(self):
        request = httpx.Request("GET", "https://example.test")

        async def operation(proxy_url: str):
            if not proxy_url:
                raise httpx.ConnectError("direct failed", request=request)
            return "proxy-ok"

        with patch.object(rankings_relay.proxy_lease, "acquire", AsyncMock(return_value="http://140.250.177.135:40017")), \
             patch.object(rankings_relay.proxy_lease, "success", AsyncMock()):
            result = asyncio.run(rankings_relay._with_proxy_fallback(operation, "2026-08-07 17:00", {
                "enabled": True,
                "api_url": "https://proxy.example/api",
                "validation_cache_seconds": 60,
                "retry_count": 2,
            }))
        self.assertEqual(result, ("proxy-ok", True, 1))

    def test_forced_proxy_test_skips_direct_request(self):
        calls = []

        async def operation(proxy_url: str):
            calls.append(proxy_url)
            return "proxy-ok"

        with patch.object(rankings_relay.proxy_lease, "acquire", AsyncMock(return_value="http://140.250.177.135:40017")), \
             patch.object(rankings_relay.proxy_lease, "success", AsyncMock()):
            result = asyncio.run(rankings_relay._with_proxy_fallback(operation, "2026-08-07 17:00", {
                "enabled": True,
                "force": True,
                "api_url": "https://proxy.example/api",
                "validation_cache_seconds": 60,
                "retry_count": 2,
            }))
        self.assertEqual(result, ("proxy-ok", True, 1))
        self.assertEqual(calls, ["http://140.250.177.135:40017"])

    def test_main_relay_request_sends_server_side_proxy_options(self):
        service = OrderRankingsV2Service()
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"success": True, "html": "ok"}
        client = AsyncMock()
        client.post.return_value = response
        service._http = AsyncMock(return_value=client)
        with patch("utils.order_rankings_v2.get_ranking_v2_config", return_value={
            "source1_relay_url": "http://relay.example:18081",
            "source1_relay_secret": "",
            "proxy_fallback_enabled": True,
            "proxy_api_url": "https://proxy.example/api",
            "proxy_validation_cache_seconds": 60,
            "proxy_retry_count": 2,
        }):
            asyncio.run(service._source1_relay_request("source2_ranking", {
                "record_date": "2026-08-07", "slot_time": "17:00",
            }))
        body = client.post.await_args.kwargs["json"]
        self.assertEqual(body["proxy_options"]["api_url"], "https://proxy.example/api")
        self.assertTrue(body["proxy_options"]["enabled"])

    def test_source_health_persists_latest_success_and_failure(self):
        with TemporaryDirectory() as directory:
            storage = OrderRankingsV2Storage(str(Path(directory) / "rankings.db"))
            self.assertEqual(storage.get_source_health()["source1"]["status"], "unknown")
            storage.set_source_health("source1", healthy=True, response_ms=120)
            healthy = storage.get_source_health()["source1"]
            self.assertEqual(healthy["status"], "healthy")
            self.assertEqual(healthy["response_ms"], 120)
            self.assertEqual(healthy["last_error"], "")
            self.assertGreater(healthy["last_success_at"], 0)
            storage.set_source_health("source1", healthy=False, response_ms=300, error="HTTPStatusError")
            unhealthy = storage.get_source_health()["source1"]
            self.assertEqual(unhealthy["status"], "unhealthy")
            self.assertEqual(unhealthy["last_error"], "HTTPStatusError")
            self.assertGreater(unhealthy["last_failure_at"], 0)
            self.assertGreater(unhealthy["last_success_at"], 0)

    def test_rank_text_switch_is_shared_by_order_result_consumers(self):
        service = OrderRankingsV2Service()
        service.rank_for_order = lambda *_args: {
            "rank": 2,
            "bucket_second": 3,
            "tie_count": 4,
            "total": 10,
        }
        with patch("utils.order_rankings_v2.get_ranking_v2_config", return_value={"rank_text_enabled": False}):
            self.assertFalse(is_ranking_rank_text_enabled())
            self.assertEqual(service.rank_text_for_order("麦当劳", 1), "")
        with patch("utils.order_rankings_v2.get_ranking_v2_config", return_value={"rank_text_enabled": True}):
            self.assertTrue(is_ranking_rank_text_enabled())
            self.assertEqual(service.rank_text_for_order("麦当劳", 1), "并列第 2 名（03s，4 人同秒，合计 10 人）")

    def test_source_samples_parse(self):
        root = Path(__file__).resolve().parents[1]
        source1 = parse_source1_ranking_html(
            (root / "rankings-analysis" / "source1-rank-response-redacted.html").read_text()
        )
        self.assertEqual(source1["brand"], "麦当劳")
        self.assertEqual(source1["participant_count"], 522)
        self.assertEqual(sum(source1["buckets"].values()), 522)

        slot_start = datetime(2026, 7, 29, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        source2 = parse_source2_shop_data(
            (root / "rankings-analysis" / "source2-page.html").read_text(), slot_start
        )
        # The checked-in source 2 fixture is intentionally a three-row redacted sample.
        self.assertEqual(sum(source2["麦当劳"].values()), 3)

    def test_direct_merge_fills_missing_seconds_and_ties_rank(self):
        with TemporaryDirectory() as directory:
            storage = OrderRankingsV2Storage(str(Path(directory) / "rankings.db"))
            storage.upsert_activities([{
                "merchant_name": "麦当劳",
                "record_date": "2026-07-29",
                "slot_time": "12:00",
                "activity": {"quantity_per_slot": 500, "max_discount": "20.00"},
            }])
            activity = storage.find_activity("麦当劳", "2026-07-29", "12:00")
            assert activity is not None
            self.assertEqual(activity["quantity_per_slot"], 500)
            self.assertEqual(activity["max_discount"], "20.00")
            activity_id = int(activity["id"])
            storage.write_snapshot("source1", activity_id, {1: 2, 3: 1}, {}, 1)
            storage.write_snapshot("source2", activity_id, {1: 4, 2: 3}, {}, 1)
            storage.rebuild_merged_buckets(activity_id)

            payload = storage.get_public_payload(activity_id)
            assert payload is not None
            self.assertEqual(
                [(row["bucket_second"], row["merged_count"], row["cumulative_count"]) for row in payload["buckets"]],
                [(1, 6, 6), (2, 3, 9), (3, 1, 10)],
            )

            service = OrderRankingsV2Service()
            service.storage = storage
            timestamp = int(datetime(2026, 7, 29, 12, 0, 2, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
            rank = service.rank_for_order("麦当劳（测试店）", timestamp)
            assert rank is not None
            self.assertEqual(rank["rank"], 7)
            self.assertEqual(rank["tie_count"], 3)

    def test_source1_relay_is_used_for_ranking_html(self):
        service = OrderRankingsV2Service()
        relay_response = {"success": True, "html": "<html>ranking</html>"}
        with patch("utils.order_rankings_v2.get_ranking_v2_config", return_value={
            "source1_relay_url": "http://relay.example:18081",
            "source1_relay_secret": "",
        }):
            service._source1_relay_request = AsyncMock(return_value=relay_response)
            value = asyncio.run(service._source1_ranking_html("麦当劳", "2026-07-29", "12:00"))
        self.assertEqual(value, "<html>ranking</html>")
        service._source1_relay_request.assert_awaited_once_with("ranking", {
            "merchant_name": "麦当劳", "record_date": "2026-07-29", "slot_time": "12:00",
        })

    def test_source2_reuses_configured_ranking_relay(self):
        service = OrderRankingsV2Service()
        relay_response = {"success": True, "html": "<script>var shopData = {};</script>"}
        with patch("utils.order_rankings_v2.get_ranking_v2_config", return_value={
            "source1_relay_url": "http://relay.example:18081",
            "source1_relay_secret": "",
        }):
            service._source1_relay_request = AsyncMock(return_value=relay_response)
            value = asyncio.run(service._source2_ranking_html("2026-07-29", "12:00"))
        self.assertEqual(value, "<script>var shopData = {};</script>")
        service._source1_relay_request.assert_awaited_once_with("source2_ranking", {
            "record_date": "2026-07-29", "slot_time": "12:00",
        })

    def test_admin_source1_refresh_returns_today_catalog(self):
        with TemporaryDirectory() as directory:
            service = OrderRankingsV2Service()
            service.storage = OrderRankingsV2Storage(str(Path(directory) / "rankings.db"))
            service.ensure_source1_login = AsyncMock()

            async def refresh():
                service.storage.upsert_activities([{
                    "merchant_name": "麦当劳",
                    "record_date": datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat(),
                    "slot_time": "12:00",
                }])
                return 1

            service.refresh_activities = AsyncMock(side_effect=refresh)
            activities = asyncio.run(service.refresh_source1_activities_for_admin())

        self.assertEqual([(item["merchant_name"], item["slot_time"]) for item in activities], [("麦当劳", "12:00")])
        service.ensure_source1_login.assert_awaited_once()
        service.refresh_activities.assert_awaited_once()
