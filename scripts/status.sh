#!/usr/bin/env sh
set -eu

MODE="${1:-dev}"
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

case "$MODE" in
  dev)
    export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wx-coupon-dev}"
    docker compose -f docker-compose.dev.yml ps
    scripts/docker_dev_smoke.sh
    python3 scripts/runtime_config_check.py --mode dev
    ;;
  prod)
    export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-wx-coupon-prod}"
    docker compose ps
    scripts/docker_prod_smoke.sh
    python3 scripts/runtime_config_check.py --mode prod
    ;;
  *)
    echo "STATUS_FAILED invalid_mode=$MODE expected=dev_or_prod" >&2
    exit 1
    ;;
esac
