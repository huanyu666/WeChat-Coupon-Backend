#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wx-coupon-dev}"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

DEV_PORT="${WX_DEV_HTTP_PORT:-18080}"
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
    base_url = env_value or f"http://localhost:{getenv('WX_DEV_HTTP_PORT', '18080')}"
print(base_url)
PY
)"

python3 scripts/migration_smoke_check.py \
  --base-url "http://127.0.0.1:${DEV_PORT}" \
  --retries "${WX_SMOKE_RETRIES:-30}" \
  --require-go-socket \
  --expect-redis-mode url \
  --expect-shortlink-base-url "$SHORTLINK_BASE_URL"

python3 scripts/business_smoke_check.py \
  --base-url "http://127.0.0.1:${DEV_PORT}"

python3 scripts/shortlink_redirect_smoke.py \
  --compose-file docker-compose.dev.yml \
  --base-url "http://127.0.0.1:${DEV_PORT}" \
  --source docker_dev_smoke
