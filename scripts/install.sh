#!/usr/bin/env sh
set -eu

MODE="${1:-dev}"
if [ "$#" -gt 0 ]; then
  shift
fi
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

case "$MODE" in
  dev)
    UP_SCRIPT="scripts/docker_dev_up.sh"
    ;;
  prod)
    UP_SCRIPT="scripts/docker_prod_up.sh"
    ;;
  *)
    echo "INSTALL_FAILED invalid_mode=$MODE expected=dev_or_prod" >&2
    exit 1
    ;;
esac

mkdir -p runtime-data logs backups

chmod +x scripts/*.sh 2>/dev/null || true

python3 scripts/configure_env.py --mode "$MODE" --create "$@"
./scripts/docker_doctor.sh "$MODE"
"$UP_SCRIPT"

echo "INSTALL_OK mode=$MODE"
