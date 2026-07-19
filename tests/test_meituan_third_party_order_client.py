from utils.meituan_third_party_order_client import _build_provider_token, _normalize_item
from utils.system_settings_store import normalize_order_relay_pool_config


def test_third_party_order_item_normalizes_shanghai_times():
    item = _normalize_item({
        "id": "3402192342295420700",
        "shopName": "测试商家",
        "createTime": "2026-07-01 11:00:06",
        "payTime": "2026-07-01 11:00:17",
        "acceptTime": "2026-07-01 11:00:20",
    })

    assert item["orderId"] == "3402192342295420700"
    assert item["poi_name"] == "测试商家"
    assert item["createTime"] == 1782874806
    assert item["payTime"] == 1782874817
    assert item["acceptTime"] == 1782874820


def test_third_party_url_must_remain_https():
    config = normalize_order_relay_pool_config({"third_party_url": "http://127.0.0.1:8080/test"})

    assert config["third_party_url"] == "https://mt.liliabc.fun/api/acceptOrders4"


def test_third_party_receives_full_meituan_link_when_user_id_is_known():
    result = _build_provider_token("raw-token", "4260381148")

    assert "userId=4260381148" in result
    assert "token=raw-token" in result
