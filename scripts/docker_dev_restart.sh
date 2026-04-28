#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

docker compose -f docker-compose.dev.yml restart app
scripts/docker_dev_smoke.sh
