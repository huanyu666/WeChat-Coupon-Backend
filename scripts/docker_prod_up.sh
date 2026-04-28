#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wx-coupon-prod}"

mkdir -p runtime-data logs backups

if [ "${WX_SKIP_PRE_DEPLOY_BACKUP:-0}" != "1" ]; then
  python3 scripts/backup_runtime_data.py
fi

docker compose up -d --build
scripts/docker_prod_smoke.sh
