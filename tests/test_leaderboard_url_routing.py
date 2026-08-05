from unittest import TestCase
from unittest.mock import patch

from utils.order_leaderboard_service import get_global_leaderboard_url


class LeaderboardUrlRoutingTests(TestCase):
    def test_v2_public_switch_overrides_only_new_global_destination(self):
        with patch("utils.order_leaderboard_service.get_global_leaderboard_config", return_value={
            "enabled": True,
            "leaderboard_url": "https://98vx.cn/key/legacy",
        }), patch("utils.order_leaderboard_service.resolve_auto_leaderboard_base_url", return_value="https://98vx.cn"), patch(
            "utils.order_rankings_v2.get_ranking_v2_config", return_value={"public_enabled": True}
        ):
            self.assertEqual(get_global_leaderboard_url(), "https://98vx.cn/order-rankings-v2")

    def test_v2_public_switch_off_preserves_legacy_global_destination(self):
        with patch("utils.order_leaderboard_service.get_global_leaderboard_config", return_value={
            "enabled": True,
            "leaderboard_url": "https://98vx.cn/key/legacy",
        }), patch("utils.order_rankings_v2.get_ranking_v2_config", return_value={"public_enabled": False}):
            self.assertEqual(get_global_leaderboard_url(), "https://98vx.cn/key/legacy")

    def test_global_switch_still_has_priority(self):
        with patch("utils.order_leaderboard_service.get_global_leaderboard_config", return_value={
            "enabled": False,
            "leaderboard_url": "https://98vx.cn/key/legacy",
        }), patch("utils.order_rankings_v2.get_ranking_v2_config", return_value={"public_enabled": True}):
            self.assertEqual(get_global_leaderboard_url(), "")
