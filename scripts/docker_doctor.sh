#!/usr/bin/env sh
set -eu

MODE="${1:-dev}"
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

case "$MODE" in
  dev)
    COMPOSE_FILE="docker-compose.dev.yml"
    DEFAULT_PROJECT_NAME="wx-coupon-dev"
    ;;
  prod)
    COMPOSE_FILE="docker-compose.yml"
    DEFAULT_PROJECT_NAME="wx-coupon-prod"
    ;;
  *)
    echo "DOCKER_DOCTOR_FAILED invalid_mode=$MODE expected=dev_or_prod" >&2
    exit 1
    ;;
esac

export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$DEFAULT_PROJECT_NAME}"

if [ -f .env ]; then
  set -a
  . ./.env
  set +a
fi

echo "DOCKER_DOCTOR_MODE $MODE"
echo "PROJECT_ROOT $PROJECT_ROOT"
echo "COMPOSE_PROJECT_NAME $COMPOSE_PROJECT_NAME"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "DOCKER_DOCTOR_FAILED missing_compose_file=$COMPOSE_FILE" >&2
  exit 1
fi

if [ ! -f .env ]; then
  echo "DOCKER_DOCTOR_WARN missing_.env copy .env.dev.example or .env.docker.example"
fi

mkdir -p runtime-data logs backups

if [ ! -x meituan-query ]; then
  echo "DOCKER_DOCTOR_WARN meituan-query_not_executable"
fi

case "$MODE" in
  dev)
    CHECK_PORT="${WX_DEV_HTTP_PORT:-18080}"
    ;;
  prod)
    CHECK_PORT="${WX_HTTP_PORT:-8080}"
    if [ "${GO_SHORTLINK_PUBLIC_BASE_URL:-}" = "" ] || echo "${GO_SHORTLINK_PUBLIC_BASE_URL:-}" | grep -q "localhost"; then
      echo "DOCKER_DOCTOR_WARN prod_shortlink_base_url_is_localhost"
    fi
    ;;
esac

case "$CHECK_PORT" in
  ''|*[!0-9]*)
    echo "DOCKER_DOCTOR_FAILED invalid_http_port=$CHECK_PORT" >&2
    exit 1
    ;;
esac

if [ "${WX_SERVICE_REDIS_URL:-redis://redis:6379/0}" = "" ]; then
  echo "DOCKER_DOCTOR_WARN empty_WX_SERVICE_REDIS_URL"
fi

docker version >/dev/null
docker compose version >/dev/null
docker compose -f "$COMPOSE_FILE" config >/dev/null

echo "DOCKER_DOCTOR_OK"
