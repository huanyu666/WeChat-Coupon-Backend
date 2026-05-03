from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.shortlink_service import normalize_default_ttl_seconds, normalize_public_base_url
from utils.system_settings_store import ensure_system_settings_store, save_system_settings_store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-base-url", default="")
    parser.add_argument("--ttl-seconds", default="")
    parser.add_argument("--cleanup-timezone", default="Asia/Shanghai")
    parser.add_argument("--cleanup-time", default="00:00")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        public_base_url = normalize_public_base_url(args.public_base_url)
        ttl_seconds = normalize_default_ttl_seconds(args.ttl_seconds)
        store = ensure_system_settings_store()
        shortlink_config = dict(store.get("shortlink_config") or {})
        shortlink_config.update(
            {
                "public_base_url": public_base_url,
                "default_ttl_seconds": ttl_seconds,
                "cleanup_timezone": args.cleanup_timezone,
                "cleanup_time": args.cleanup_time,
            }
        )
        store["shortlink_config"] = shortlink_config
        saved = save_system_settings_store(store)
        payload = {
            "ok": True,
            "shortlink_config": saved.get("shortlink_config", {}),
        }
    except Exception as exc:
        payload = {
            "ok": False,
            "error": exc.__class__.__name__,
            "message": str(exc),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"CONFIGURE_SHORTLINK_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        config = payload["shortlink_config"]
        print(
            "CONFIGURE_SHORTLINK_OK "
            f"public_base_url={config.get('public_base_url', '')} "
            f"ttl_seconds={config.get('default_ttl_seconds', '')} "
            f"cleanup_timezone={config.get('cleanup_timezone', '')} "
            f"cleanup_time={config.get('cleanup_time', '')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
