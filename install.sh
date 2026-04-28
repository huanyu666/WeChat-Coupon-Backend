#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_ROOT"

if [ "$#" -eq 0 ]; then
  set -- dev
fi

exec scripts/install.sh "$@"
