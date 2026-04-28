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

docker version >/dev/null
docker compose version >/dev/null
docker compose -f "$COMPOSE_FILE" config >/dev/null

echo "DOCKER_DOCTOR_OK"
