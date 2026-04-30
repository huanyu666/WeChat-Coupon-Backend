#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wx-coupon-prod}"

mkdir -p runtime-data logs backups
if [ -f config.toml ] && [ ! -f runtime-data/config.toml ]; then
  cp config.toml runtime-data/config.toml
  chmod 600 runtime-data/config.toml 2>/dev/null || true
  echo "DOCKER_PROD_UP_MIGRATED_CONFIG runtime-data/config.toml"
fi

if [ "${WX_SKIP_PRE_DEPLOY_BACKUP:-0}" != "1" ]; then
  python3 scripts/backup_runtime_data.py
fi

docker compose up -d --build
scripts/docker_prod_smoke.sh
