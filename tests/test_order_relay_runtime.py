import importlib.util
import sys
from pathlib import Path

import httpx
import pytest


RELAY_PATH = Path(__file__).resolve().parents[1] / "scripts" / "meituan_order_relay.py"
SPEC = importlib.util.spec_from_file_location("meituan_order_relay_test", RELAY_PATH)
assert SPEC and SPEC.loader
relay = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = relay
SPEC.loader.exec_module(relay)


def test_order_relay_options_are_bounded():
    options = relay._relay_options({
        "proxy_mode": "pool",
        "proxy_retry_count": 99,
        "queue_wait_seconds": 99,
    })
    assert options == {
        "use_pool": True,
        "proxy_mode": "pool",
        "proxy_retry_count": 3,
        "queue_wait_seconds": 10.0,
        "proxy_api_url": relay.PROXY_API_URL,
        "proxy_fallback_enabled": bool(relay.PROXY_API_URL),
        "third_party_timeout_seconds": relay.UPSTREAM_TIMEOUT_SECONDS,
        "third_party_direct_timeout_seconds": min(5.0, relay.UPSTREAM_TIMEOUT_SECONDS),
    }


@pytest.mark.asyncio
async def test_direct_mode_fetches_a_fresh_proxy_each_time(monkeypatch):
    pool = relay.ProxyPool()
    calls = []

    async def fake_fetch(count, api_url):
        calls.append((count, api_url))
        return [relay.ProxyItem(url=f"http://127.0.0.1:{8000 + len(calls)}", selected_at=0)]

    monkeypatch.setattr(pool, "_fetch", fake_fetch)

    assert await pool.acquire(use_pool=False, api_url="http://proxy.test") == "http://127.0.0.1:8001"
    assert await pool.acquire(use_pool=False, api_url="http://proxy.test") == "http://127.0.0.1:8002"
    assert calls == [(1, "http://proxy.test"), (1, "http://proxy.test")]


@pytest.mark.asyncio
async def test_pool_mode_keeps_cached_proxy(monkeypatch):
    pool = relay.ProxyPool()
    calls = []

    async def fake_fetch(count, api_url):
        calls.append((count, api_url))
        return [relay.ProxyItem(url="http://127.0.0.1:8001", selected_at=relay.time.time())]

    monkeypatch.setattr(pool, "_fetch", fake_fetch)

    assert await pool.acquire(use_pool=True, api_url="http://proxy.test") == "http://127.0.0.1:8001"
    assert await pool.acquire(use_pool=True, api_url="http://proxy.test") == "http://127.0.0.1:8001"
    assert calls == [(relay.PROXY_POOL_SIZE, "http://proxy.test")]


def test_third_party_endpoint_rejects_non_https_and_accepts_https():
    payload = relay.OrderRelayRequest(
        operation="third_party_order",
        params={"third_party_url": "https://mt.liliabc.fun/api/acceptOrders4"},
    )

    assert relay._third_party_endpoint(payload) == "https://mt.liliabc.fun/api/acceptOrders4"

    with pytest.raises(Exception):
        relay._third_party_endpoint(relay.OrderRelayRequest(
            operation="third_party_order",
            params={"third_party_url": "http://127.0.0.1/test"},
        ))


@pytest.mark.asyncio
async def test_third_party_uses_proxy_after_direct_failure(monkeypatch):
    pool = relay.ProxyPool()
    calls = []

    async def fake_request(endpoint, body, headers, timeout_seconds, proxy_url=None):
        calls.append(proxy_url or "direct")
        if proxy_url is None:
            raise httpx.ConnectTimeout("blocked")
        request = httpx.Request("POST", endpoint)
        return httpx.Response(200, json={"code": 200, "data": []}, request=request), {"code": 200, "data": []}

    async def fake_acquire(*, use_pool, api_url):
        assert use_pool is False
        assert api_url == "http://proxy.test/api"
        return "http://127.0.0.1:8001"

    monkeypatch.setattr(relay, "_request_third_party_direct", fake_request)
    monkeypatch.setattr(pool, "acquire", fake_acquire)
    monkeypatch.setattr(relay, "proxy_pool", pool)
    result = await relay._relay_third_party_order(
        relay.OrderRelayRequest(
            operation="third_party_order",
            params={"third_party_url": "https://mt.liliabc.fun/api/acceptOrders4"},
            json_body={"token": "test"},
        ),
        relay._relay_options({
            "proxy_api_url": "http://proxy.test/api",
            "proxy_fallback_enabled": True,
            "proxy_retry_count": 0,
            "third_party_timeout_seconds": 10,
            "third_party_direct_timeout_seconds": 2,
        }),
    )

    assert result["success"] is True
    assert result["route"] == "proxy"
    assert calls == ["direct", "http://127.0.0.1:8001"]
