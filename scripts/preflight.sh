#!/usr/bin/env sh
set -eu

MODE="${1:-dev}"
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

case "$MODE" in
  dev)
    COMPOSE_FILE="docker-compose.dev.yml"
    ;;
  prod)
    COMPOSE_FILE="docker-compose.yml"
    ;;
  *)
    echo "PREFLIGHT_FAILED invalid_mode=$MODE expected=dev_or_prod" >&2
    exit 1
    ;;
esac

./doctor.sh "$MODE"
python3 scripts/configure_env.py --mode "$MODE" --check
docker compose -f "$COMPOSE_FILE" config >/dev/null

if [ "$MODE" = "prod" ] && [ ! -f deploy/openresty/docker-http-proxy.conf.example ]; then
  echo "PREFLIGHT_WARN missing_openresty_docker_proxy_template"
fi

echo "PREFLIGHT_OK mode=$MODE"
