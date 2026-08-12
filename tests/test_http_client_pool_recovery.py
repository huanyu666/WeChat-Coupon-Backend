import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from utils import http_client


@pytest_asyncio.fixture(autouse=True)
async def reset_http_client_state():
    await http_client.aclose()
    http_client._RECENT_REQUESTS.clear()
    http_client._CLIENT_RECOVERY_STATS.update({
        "direct_pool_rebuilds": 0,
        "last_direct_pool_rebuild_at": 0.0,
        "last_direct_pool_rebuild_reason": "",
    })
    yield
    await http_client.aclose()


def test_safe_request_target_removes_query_and_credentials():
    target = http_client._safe_request_target("https://user:secret@example.test:8443/path?a=token&b=2")
    assert target == "https://example.test:8443/path"


@pytest.mark.asyncio
async def test_omitted_timeout_uses_configured_default(monkeypatch):
    observed = {}

    class FakeClient:
        async def request(self, *args, **kwargs):
            observed["timeout"] = kwargs["timeout"]
            return SimpleNamespace(status_code=200)

        async def aclose(self):
            return None

    monkeypatch.setattr(http_client, "_get_client", lambda *args: FakeClient())
    await http_client.get("https://example.test/default-timeout")
    assert observed["timeout"].pool == pytest.approx(15.0)


@pytest.mark.asyncio
async def test_rebuild_detaches_only_repeatedly_timing_out_direct_client(monkeypatch):
    key = (None, False, None)
    closed = []

    class FakeClient:
        async def aclose(self):
            closed.append(True)

    client = FakeClient()
    http_client._CLIENTS[key] = client
    http_client._CLIENT_META[key] = {"kind": "direct"}
    monkeypatch.setattr(http_client, "_env_int", lambda name, default, minimum, maximum: 3 if name == "WX_HTTP_CLIENT_POOL_TIMEOUT_REBUILD_THRESHOLD" else default)
    monkeypatch.setattr(http_client, "_env_float", lambda name, default, minimum, maximum: 0.0 if name == "WX_HTTP_CLIENT_RECOVERY_GRACE_SECONDS" else default)

    for _ in range(3):
        await http_client._recover_direct_client_after_pool_timeout(key)

    # The close task itself awaits sleep(0), so yield twice: once to start
    # it and once to resume it after that zero-second grace period.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert key not in http_client._CLIENTS
    assert http_client.get_client_stats()["client_pool_direct_rebuild_count"] == 1
    assert closed == [True]


@pytest.mark.asyncio
async def test_rebuild_does_not_apply_to_proxy_or_unix_socket_client(monkeypatch):
    key = (None, False, None)
    http_client._CLIENTS[key] = SimpleNamespace(aclose=lambda: None)
    http_client._CLIENT_META[key] = {"kind": "direct"}
    proxy_key = ("http://127.0.0.1:8080", False, None)
    uds_key = (None, False, "/tmp/example.sock")
    http_client._CLIENTS[proxy_key] = SimpleNamespace(aclose=lambda: None)
    http_client._CLIENTS[uds_key] = SimpleNamespace(aclose=lambda: None)
    monkeypatch.setattr(http_client, "_env_int", lambda name, default, minimum, maximum: 3 if name == "WX_HTTP_CLIENT_POOL_TIMEOUT_REBUILD_THRESHOLD" else default)

    for _ in range(3):
        await http_client._recover_direct_client_after_pool_timeout(proxy_key)
        await http_client._recover_direct_client_after_pool_timeout(uds_key)

    assert key in http_client._CLIENTS
    assert proxy_key in http_client._CLIENTS
    assert uds_key in http_client._CLIENTS
    assert http_client.get_client_stats()["client_pool_direct_rebuild_count"] == 0
