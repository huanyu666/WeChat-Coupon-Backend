from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _default_base_url() -> str:
    explicit = str(os.getenv("WX_SMOKE_BASE_URL") or "").strip()
    if explicit:
        return explicit

    dev_port = str(os.getenv("WX_DEV_HTTP_PORT") or "").strip()
    if dev_port:
        return f"http://127.0.0.1:{dev_port}"

    http_port = str(os.getenv("WX_HTTP_PORT") or "").strip()
    if http_port:
        return f"http://127.0.0.1:{http_port}"

    return "http://127.0.0.1:18080"


def _env_flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _fetch_json_once(base_url: str, path: str, timeout: float, use_system_proxy: bool) -> tuple[int, dict[str, Any]]:
    normalized_base = str(base_url or "").strip().rstrip("/")
    url = normalized_base + "/" + str(path or "").lstrip("/")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        if use_system_proxy:
            opener = urllib.request.build_opener()
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            status = int(response.getcode() or 0)
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        body = exc.read().decode("utf-8", errors="replace")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        body_excerpt = body.replace("\n", "\\n")[:240]
        raise ValueError(f"{path} 响应不是有效 JSON: status={status} body={body_excerpt!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 响应不是 JSON object")
    return status, payload


def _fetch_json(
    base_url: str,
    path: str,
    timeout: float,
    retries: int,
    use_system_proxy: bool,
) -> tuple[int, dict[str, Any]]:
    last_error: Exception | None = None
    attempts = max(1, retries + 1)
    for attempt in range(1, attempts + 1):
        try:
            return _fetch_json_once(base_url, path, timeout, use_system_proxy)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            time.sleep(min(0.2 * attempt, 1.0))
    assert last_error is not None
    raise last_error


def _print_section(title: str, status: int, payload: dict[str, Any]) -> None:
    print(f"[{title}] http_status={status} ok={payload.get('ok')}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _is_go_degraded_readyz(status: int, payload: dict[str, Any]) -> bool:
    error = str(payload.get("error") or "").strip()
    return status == 503 and error.startswith("go_health_check_failed:")


def _collect_failures(
    *,
    health_status: int,
    health_payload: dict[str, Any],
    ready_status: int,
    ready_payload: dict[str, Any],
    allow_go_degraded: bool,
    allow_redis_unreachable: bool,
    require_go_socket: bool,
    require_redis_socket: bool,
    expect_redis_mode: str,
    expect_shortlink_base_url: str,
) -> list[str]:
    failures: list[str] = []

    if health_status != 200:
        failures.append(f"/healthz 返回非 200: {health_status}")
    if health_payload.get("ok") is not True:
        failures.append("/healthz ok != true")

    ready_go_degraded = _is_go_degraded_readyz(ready_status, ready_payload)
    if ready_status != 200:
        if not (allow_go_degraded and ready_go_degraded):
            failures.append(f"/readyz 返回非 200: {ready_status}")
    if ready_payload.get("ok") is not True:
        if not (allow_go_degraded and ready_go_degraded):
            failures.append("/readyz ok != true")

    if ready_payload.get("redis_reachable") is False and not allow_redis_unreachable:
        failures.append("Redis 不可达")

    if ready_payload.get("go_expected_external") and ready_payload.get("go_reachable") is False:
        if not allow_go_degraded:
            failures.append("Go internal service 不可达")

    if require_go_socket and ready_payload.get("go_socket_exists") is not True:
        failures.append("Go internal socket 不存在")

    if require_redis_socket and ready_payload.get("redis_socket_exists") is not True:
        failures.append("Redis Unix socket 不存在")

    if expect_redis_mode:
        actual_redis_mode = str(ready_payload.get("redis_mode") or "").strip()
        if actual_redis_mode != expect_redis_mode:
            failures.append(f"Redis mode 不符合预期: expected={expect_redis_mode} actual={actual_redis_mode}")

    if expect_shortlink_base_url:
        actual_shortlink_base_url = str(ready_payload.get("go_shortlink_public_base_url") or "").strip()
        if actual_shortlink_base_url.rstrip("/") != expect_shortlink_base_url.rstrip("/"):
            failures.append(
                "短链 public base URL 不符合预期: "
                f"expected={expect_shortlink_base_url} actual={actual_shortlink_base_url}"
            )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=_default_base_url(),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(str(os.getenv("WX_SMOKE_TIMEOUT_SECONDS") or "3").strip()),
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=int(str(os.getenv("WX_SMOKE_RETRIES") or "2").strip()),
    )
    parser.add_argument(
        "--use-system-proxy",
        action="store_true",
        default=_env_flag("WX_SMOKE_USE_SYSTEM_PROXY"),
    )
    parser.add_argument(
        "--allow-go-degraded",
        action="store_true",
        default=_env_flag("WX_SMOKE_ALLOW_GO_DEGRADED"),
    )
    parser.add_argument(
        "--allow-redis-unreachable",
        action="store_true",
        default=_env_flag("WX_SMOKE_ALLOW_REDIS_UNREACHABLE"),
    )
    parser.add_argument(
        "--require-go-socket",
        action="store_true",
        default=_env_flag("WX_SMOKE_REQUIRE_GO_SOCKET"),
    )
    parser.add_argument(
        "--require-redis-socket",
        action="store_true",
        default=_env_flag("WX_SMOKE_REQUIRE_REDIS_SOCKET"),
    )
    parser.add_argument(
        "--expect-redis-mode",
        choices=["url", "tcp", "unix_socket"],
        default=str(os.getenv("WX_SMOKE_EXPECT_REDIS_MODE") or "").strip(),
    )
    parser.add_argument(
        "--expect-shortlink-base-url",
        default=str(os.getenv("WX_SMOKE_EXPECT_SHORTLINK_BASE_URL") or "").strip(),
    )
    args = parser.parse_args()

    try:
        health_status, health_payload = _fetch_json(
            args.base_url,
            "/healthz",
            args.timeout,
            args.retries,
            args.use_system_proxy,
        )
        ready_status, ready_payload = _fetch_json(
            args.base_url,
            "/readyz",
            args.timeout,
            args.retries,
            args.use_system_proxy,
        )
    except Exception as exc:
        print(f"SMOKE_CHECK_FAILED request_error={exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    _print_section("healthz", health_status, health_payload)
    _print_section("readyz", ready_status, ready_payload)

    failures = _collect_failures(
        health_status=health_status,
        health_payload=health_payload,
        ready_status=ready_status,
        ready_payload=ready_payload,
        allow_go_degraded=args.allow_go_degraded,
        allow_redis_unreachable=args.allow_redis_unreachable,
        require_go_socket=args.require_go_socket,
        require_redis_socket=args.require_redis_socket,
        expect_redis_mode=args.expect_redis_mode,
        expect_shortlink_base_url=args.expect_shortlink_base_url,
    )

    if failures:
        print("SMOKE_CHECK_FAILED")
        for item in failures:
            print(f"- {item}")
        return 1

    print("SMOKE_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
