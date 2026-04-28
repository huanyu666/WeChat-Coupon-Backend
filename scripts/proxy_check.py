from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODE_PORT_KEYS = {
    "dev": "WX_DEV_HTTP_PORT",
    "prod": "WX_HTTP_PORT",
}


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _fetch_json(url: str, timeout: float, use_system_proxy: bool) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    opener = urllib.request.build_opener() if use_system_proxy else urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.getcode() or 0)
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code or 0)
        body = exc.read().decode("utf-8", errors="replace")
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError(f"响应不是 JSON object: {url}")
    return status, payload


def _is_placeholder_or_local(base_url: str) -> bool:
    parsed = urllib.parse.urlparse(base_url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".example.com") or host == "example.com"


def _check_endpoint(label: str, base_url: str, timeout: float, use_system_proxy: bool) -> dict[str, Any]:
    normalized = base_url.rstrip("/")
    health_status, health_payload = _fetch_json(normalized + "/healthz", timeout, use_system_proxy)
    ready_status, ready_payload = _fetch_json(normalized + "/readyz", timeout, use_system_proxy)
    result = {
        "label": label,
        "base_url": normalized,
        "health_status": health_status,
        "health_ok": health_payload.get("ok") is True,
        "ready_status": ready_status,
        "ready_ok": ready_payload.get("ok") is True,
        "ready_shortlink_base_url": str(ready_payload.get("go_shortlink_public_base_url") or ""),
    }
    if health_status != 200 or health_payload.get("ok") is not True:
        raise RuntimeError(f"{label} /healthz failed status={health_status} ok={health_payload.get('ok')}")
    if ready_status != 200 or ready_payload.get("ok") is not True:
        raise RuntimeError(f"{label} /readyz failed status={ready_status} ok={ready_payload.get('ok')}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", choices=["dev", "prod"], default="prod")
    parser.add_argument("base_url", nargs="?", default="")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--port", default="")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("WX_PROXY_CHECK_TIMEOUT_SECONDS", "5")))
    parser.add_argument("--skip-public", action="store_true")
    parser.add_argument("--use-system-proxy", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        env_values = _parse_env(Path(args.env_file).expanduser().resolve())
        port = str(args.port or env_values.get(MODE_PORT_KEYS[args.mode]) or ("18080" if args.mode == "dev" else "8080")).strip()
        if not port.isdigit():
            raise ValueError(f"端口不是数字: {port}")
        local_base_url = f"http://127.0.0.1:{port}"
        public_base_url = str(args.base_url or env_values.get("GO_SHORTLINK_PUBLIC_BASE_URL") or "").strip().rstrip("/")

        results = [_check_endpoint("local", local_base_url, args.timeout, False)]
        if args.skip_public:
            skipped = "skip_public"
        elif not public_base_url:
            raise ValueError("缺少公开访问地址，请传入 base_url 或配置 GO_SHORTLINK_PUBLIC_BASE_URL")
        elif _is_placeholder_or_local(public_base_url):
            raise ValueError(f"公开访问地址仍是本机或占位域名: {public_base_url}")
        else:
            public_result = _check_endpoint("public", public_base_url, args.timeout, args.use_system_proxy)
            expected = public_base_url.rstrip("/")
            actual = public_result["ready_shortlink_base_url"].rstrip("/")
            if actual != expected:
                raise RuntimeError(f"readyz 短链公开地址不匹配 expected={expected} actual={actual}")
            results.append(public_result)
            skipped = ""
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"PROXY_CHECK_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": True, "mode": args.mode, "skipped": skipped, "results": results}, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(
                "PROXY_CHECK_ENDPOINT_OK "
                f"label={result['label']} base_url={result['base_url']} "
                f"health={result['health_status']} ready={result['ready_status']}"
            )
        if skipped:
            print(f"PROXY_CHECK_PUBLIC_SKIPPED {skipped}")
        print("PROXY_CHECK_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
