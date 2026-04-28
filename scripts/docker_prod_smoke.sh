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
SHORTLINK_BASE_URL="${GO_SHORTLINK_PUBLIC_BASE_URL:-http://localhost:${HTTP_PORT}}"

python3 scripts/migration_smoke_check.py \
  --base-url "http://127.0.0.1:${HTTP_PORT}" \
  --retries "${WX_SMOKE_RETRIES:-30}" \
  --require-go-socket \
  --expect-redis-mode url \
  --expect-shortlink-base-url "$SHORTLINK_BASE_URL"

python3 scripts/business_smoke_check.py \
  --base-url "http://127.0.0.1:${HTTP_PORT}"
