from unittest import TestCase
from unittest.mock import patch

from routes.go_web_proxy import _inject_ranking_v2_text


class WebRankingSwitchTests(TestCase):
    def _payload(self):
        return {
            "success": True,
            "data": [{
                "poi_name": "麦当劳",
                "accept_time": 1_756_000_000,
            }],
        }

    def test_web_does_not_inject_rank_text_when_shared_switch_is_off(self):
        payload = self._payload()
        with patch("routes.go_web_proxy.is_ranking_rank_text_enabled", return_value=False), patch(
            "routes.go_web_proxy.get_order_rankings_v2_service"
        ) as get_service:
            result = _inject_ranking_v2_text("/web/api/query", payload)
        self.assertNotIn("ranking_v2_rank_text", result["data"][0])
        get_service.assert_not_called()

    def test_web_injects_rank_text_when_shared_switch_is_on(self):
        payload = self._payload()
        with patch("routes.go_web_proxy.is_ranking_rank_text_enabled", return_value=True), patch(
            "routes.go_web_proxy.get_order_rankings_v2_service"
        ) as get_service:
            get_service.return_value.rank_text_for_order.return_value = "并列第 2 名（03s，4 人同秒，合计 10 人）"
            result = _inject_ranking_v2_text("/web/api/query", payload)
        self.assertEqual(
            result["data"][0]["ranking_v2_rank_text"],
            "并列第 2 名（03s，4 人同秒，合计 10 人）",
        )
