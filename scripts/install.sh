#!/usr/bin/env sh
set -eu

MODE="${1:-dev}"
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

case "$MODE" in
  dev)
    ENV_TEMPLATE=".env.dev.example"
    UP_SCRIPT="scripts/docker_dev_up.sh"
    ;;
  prod)
    ENV_TEMPLATE=".env.docker.example"
    UP_SCRIPT="scripts/docker_prod_up.sh"
    ;;
  *)
    echo "INSTALL_FAILED invalid_mode=$MODE expected=dev_or_prod" >&2
    exit 1
    ;;
esac

if [ ! -f "$ENV_TEMPLATE" ]; then
  echo "INSTALL_FAILED missing_env_template=$ENV_TEMPLATE" >&2
  exit 1
fi

mkdir -p runtime-data logs backups

if [ ! -f .env ]; then
  cp "$ENV_TEMPLATE" .env
  echo "INSTALL_ENV_CREATED .env <= $ENV_TEMPLATE"
else
  echo "INSTALL_ENV_EXISTS .env"
fi

chmod +x scripts/*.sh 2>/dev/null || true

./scripts/docker_doctor.sh "$MODE"
"$UP_SCRIPT"

echo "INSTALL_OK mode=$MODE"
