#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

SERVICE="${1:-app}"
docker compose -f docker-compose.dev.yml logs -f "$SERVICE"
