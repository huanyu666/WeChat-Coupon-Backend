#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_ROOT"

exec python3 scripts/import_migration_package.py "$@"
