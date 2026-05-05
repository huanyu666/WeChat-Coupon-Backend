from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_command(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _compose_command(compose_file: str) -> list[str]:
    command = ["docker", "compose"]
    if compose_file:
        command.extend(["-f", compose_file])
    return command


def _container_python(compose_file: str, script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    command = _compose_command(compose_file)
    command.extend(["exec", "-T"])
    for key, value in (env or {}).items():
        command.extend(["-e", f"{key}={value}"])
    command.extend(["app", "python", "-"])
    command.extend(args)
    return _run_command(command, input_text=script)


def _last_json_line(output: str) -> dict[str, str]:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return {str(key): str(value) for key, value in payload.items()}
    raise ValueError("container output did not contain a JSON payload")


def _create_shortlink(compose_file: str, public_base_url: str, source: str) -> dict[str, str]:
    script = r'''
import asyncio
import json
import os

from utils.shortlink_service import create_shortlink_async


async def main():
    payload = await create_shortlink_async(
        "https://example.com/wx-coupon-smoke",
        ttl_seconds=60,
        public_base_url=os.environ["WX_SMOKE_PUBLIC_BASE_URL"],
        log_source=os.environ.get("WX_SMOKE_LOG_SOURCE", "shortlink_redirect_smoke"),
    )
    print(json.dumps({
        "short_key": payload["short_key"],
        "path": payload["path"],
        "target_url": payload["target_url"],
    }))


asyncio.run(main())
'''
    result = _container_python(
        compose_file,
        script,
        env={
            "WX_SMOKE_PUBLIC_BASE_URL": public_base_url.rstrip("/"),
            "WX_SMOKE_LOG_SOURCE": source,
        },
    )
    if result.returncode != 0:
        raise RuntimeError(f"shortlink create failed: {result.stderr.strip() or result.stdout.strip()}")
    return _last_json_line(result.stdout)


def _delete_shortlink(compose_file: str, short_key: str) -> None:
    script = r'''
import asyncio
import sys

from utils.shortlink_service import delete_shortlink_async


asyncio.run(delete_shortlink_async(sys.argv[1]))
'''
    _container_python(compose_file, script, short_key)


def _check_redirect(base_url: str, path: str, expected_target: str, timeout: float) -> None:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirectHandler(), urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"Accept": "text/plain, */*"}, method="GET")
    try:
        opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        location = str(exc.headers.get("Location") or "").strip()
    else:
        raise AssertionError("shortlink request followed or missed redirect")

    if status != 302 or location != expected_target:
        raise AssertionError(
            f"shortlink redirect mismatch status={status} location={location!r} expected={expected_target!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a temporary shortlink in the app container and verify local 302 redirect.")
    parser.add_argument("--base-url", required=True, help="Local app base URL, for example http://127.0.0.1:18080")
    parser.add_argument("--compose-file", default="", help="Optional docker compose file, for example docker-compose.dev.yml")
    parser.add_argument("--source", default="shortlink_redirect_smoke", help="Log source for the temporary shortlink.")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    created: dict[str, str] | None = None
    try:
        created = _create_shortlink(args.compose_file, args.base_url, args.source)
        short_key = str(created.get("short_key") or "").strip()
        path = str(created.get("path") or "").strip()
        target_url = str(created.get("target_url") or "").strip()
        if not short_key or not path or not target_url:
            raise ValueError(f"shortlink create returned incomplete payload: {created}")
        _check_redirect(args.base_url, path, target_url, args.timeout)
    except Exception as exc:
        print(f"BUSINESS_SMOKE_SHORTLINK_REDIRECT_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if created and created.get("short_key"):
            _delete_shortlink(args.compose_file, created["short_key"])

    print(f"BUSINESS_SMOKE_SHORTLINK_REDIRECT_OK short_key={created['short_key']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
