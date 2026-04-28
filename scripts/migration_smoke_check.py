from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _env_flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _fetch_json(base_url: str, path: str, timeout: float) -> tuple[int, dict[str, Any]]:
    normalized_base = str(base_url or "").strip().rstrip("/")
    url = normalized_base + "/" + str(path or "").lstrip("/")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.getcode() or 0)
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        body = exc.read().decode("utf-8", errors="replace")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} 响应不是 JSON object")
    return status, payload


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

    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        default=str(os.getenv("WX_SMOKE_BASE_URL") or "http://127.0.0.1").strip(),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(str(os.getenv("WX_SMOKE_TIMEOUT_SECONDS") or "3").strip()),
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
    args = parser.parse_args()

    try:
        health_status, health_payload = _fetch_json(args.base_url, "/healthz", args.timeout)
        ready_status, ready_payload = _fetch_json(args.base_url, "/readyz", args.timeout)
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
