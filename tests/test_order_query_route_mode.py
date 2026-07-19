from utils.system_settings_store import normalize_order_relay_pool_config


def test_order_query_route_mode_defaults_to_third_party_with_fallbacks():
    config = normalize_order_relay_pool_config({})

    assert config["route_mode"] == "third_then_relay_then_local"


def test_order_query_route_mode_accepts_supported_modes():
    assert (
        normalize_order_relay_pool_config({"route_mode": "third_party_only"})["route_mode"]
        == "third_party_only"
    )
    assert normalize_order_relay_pool_config({"route_mode": "local_proxy"})["route_mode"] == "local_proxy"
    assert (
        normalize_order_relay_pool_config({"route_mode": "relay_then_local"})["route_mode"]
        == "relay_then_local"
    )


def test_order_query_route_mode_rejects_unknown_values():
    config = normalize_order_relay_pool_config({"route_mode": "direct"})

    assert config["route_mode"] == "third_then_relay_then_local"
