#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wx-coupon-dev}"

mkdir -p runtime-data logs
if [ -f config.toml ] && [ ! -f runtime-data/config.toml ]; then
  cp config.toml runtime-data/config.toml
  chmod 600 runtime-data/config.toml 2>/dev/null || true
  echo "DOCKER_DEV_UP_MIGRATED_CONFIG runtime-data/config.toml"
fi

docker compose -f docker-compose.dev.yml up -d --build

scripts/docker_dev_smoke.sh
