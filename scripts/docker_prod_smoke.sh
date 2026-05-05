#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wx-coupon-prod}"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

HTTP_PORT="${WX_HTTP_PORT:-8080}"
SHORTLINK_BASE_URL="$(python3 - <<'PY'
import json
from pathlib import Path

root = Path.cwd()
store_path = root / "runtime-data" / "system_settings.runtime.json"
base_url = ""
if store_path.exists():
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
        base_url = str((payload.get("shortlink_config") or {}).get("public_base_url") or "").strip().rstrip("/")
    except Exception:
        base_url = ""
if not base_url:
    from os import getenv
    env_value = str(getenv("GO_SHORTLINK_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    base_url = env_value or f"http://localhost:{getenv('WX_HTTP_PORT', '8080')}"
print(base_url)
PY
)"

python3 scripts/migration_smoke_check.py \
  --base-url "http://127.0.0.1:${HTTP_PORT}" \
  --retries "${WX_SMOKE_RETRIES:-30}" \
  --require-go-socket \
  --expect-redis-mode url \
  --expect-shortlink-base-url "$SHORTLINK_BASE_URL"

python3 scripts/business_smoke_check.py \
  --base-url "http://127.0.0.1:${HTTP_PORT}"

SHORTLINK_SMOKE_PAYLOAD="$(docker compose exec -T -e WX_SMOKE_PUBLIC_BASE_URL="http://127.0.0.1:${HTTP_PORT}" app python - <<'PY'
import asyncio
import json
import os

from utils.shortlink_service import create_shortlink_async


async def main():
    payload = await create_shortlink_async(
        "https://example.com/wx-coupon-smoke",
        ttl_seconds=60,
        public_base_url=os.environ["WX_SMOKE_PUBLIC_BASE_URL"],
        log_source="docker_prod_smoke",
    )
    print(json.dumps({"short_key": payload["short_key"], "path": payload["path"], "target_url": payload["target_url"]}))


asyncio.run(main())
PY
)"
SHORTLINK_SMOKE_JSON="$(printf '%s\n' "$SHORTLINK_SMOKE_PAYLOAD" | tail -n 1)"
SHORTLINK_PATH="$(printf '%s' "$SHORTLINK_SMOKE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["path"])')"
SHORTLINK_KEY="$(printf '%s' "$SHORTLINK_SMOKE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["short_key"])')"
SHORTLINK_TARGET="$(printf '%s' "$SHORTLINK_SMOKE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["target_url"])')"
SHORTLINK_RESULT="$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "http://127.0.0.1:${HTTP_PORT}${SHORTLINK_PATH}")"
SHORTLINK_STATUS="${SHORTLINK_RESULT%% *}"
SHORTLINK_LOCATION="${SHORTLINK_RESULT#* }"
docker compose exec -T app python - "$SHORTLINK_KEY" <<'PY' >/dev/null 2>&1 || true
import asyncio
import sys

from utils.shortlink_service import delete_shortlink_async


asyncio.run(delete_shortlink_async(sys.argv[1]))
PY
if [ "$SHORTLINK_STATUS" != "302" ] || [ "$SHORTLINK_LOCATION" != "$SHORTLINK_TARGET" ]; then
  echo "BUSINESS_SMOKE_SHORTLINK_REDIRECT_FAILED status=${SHORTLINK_STATUS} location=${SHORTLINK_LOCATION} expected=${SHORTLINK_TARGET}" >&2
  exit 1
fi
echo "BUSINESS_SMOKE_SHORTLINK_REDIRECT_OK short_key=${SHORTLINK_KEY}"
