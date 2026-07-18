import importlib.util
import sys
from pathlib import Path

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
    }


@pytest.mark.asyncio
async def test_direct_mode_fetches_a_fresh_proxy_each_time(monkeypatch):
    pool = relay.ProxyPool()
    calls = []

    async def fake_fetch(count):
        calls.append(count)
        return [relay.ProxyItem(url=f"http://127.0.0.1:{8000 + len(calls)}", selected_at=0)]

    monkeypatch.setattr(pool, "_fetch", fake_fetch)

    assert await pool.acquire(use_pool=False) == "http://127.0.0.1:8001"
    assert await pool.acquire(use_pool=False) == "http://127.0.0.1:8002"
    assert calls == [1, 1]


@pytest.mark.asyncio
async def test_pool_mode_keeps_cached_proxy(monkeypatch):
    pool = relay.ProxyPool()
    calls = []

    async def fake_fetch(count):
        calls.append(count)
        return [relay.ProxyItem(url="http://127.0.0.1:8001", selected_at=relay.time.time())]

    monkeypatch.setattr(pool, "_fetch", fake_fetch)

    assert await pool.acquire(use_pool=True) == "http://127.0.0.1:8001"
    assert await pool.acquire(use_pool=True) == "http://127.0.0.1:8001"
    assert calls == [relay.PROXY_POOL_SIZE]
