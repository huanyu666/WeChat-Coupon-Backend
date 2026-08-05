import asyncio
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from utils.order_rankings_v2 import (
    OrderRankingsV2Service,
    OrderRankingsV2Storage,
    parse_source1_ranking_html,
    parse_source2_shop_data,
)


class OrderRankingsV2Tests(TestCase):
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
            }])
            activity = storage.find_activity("麦当劳", "2026-07-29", "12:00")
            assert activity is not None
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
