#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

mkdir -p runtime-data logs

docker compose -f docker-compose.dev.yml up -d --build

DEV_PORT="${WX_DEV_HTTP_PORT:-18080}"
SHORTLINK_BASE_URL="${GO_SHORTLINK_PUBLIC_BASE_URL:-http://localhost:${DEV_PORT}}"

python3 scripts/migration_smoke_check.py \
  --base-url "http://127.0.0.1:${DEV_PORT}" \
  --retries 30 \
  --require-go-socket \
  --expect-redis-mode url \
  --expect-shortlink-base-url "$SHORTLINK_BASE_URL"
