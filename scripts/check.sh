#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

python3 -m py_compile \
  scripts/backup_runtime_data.py \
  scripts/configure_env.py \
  scripts/install_wizard.py \
  scripts/migration_smoke_check.py \
  scripts/restore_runtime_data.py \
  main.py \
  run_server.py \
  utils/path_utils.py \
  wechat_account_store.py

sh -n \
  backup.sh \
  configure.sh \
  doctor.sh \
  install.sh \
  restore.sh \
  status.sh \
  update.sh \
  scripts/*.sh

COMPOSE_PROJECT_NAME=wx-coupon-dev docker compose -f docker-compose.dev.yml config >/dev/null
COMPOSE_PROJECT_NAME=wx-coupon-prod docker compose -f docker-compose.yml config >/dev/null

./doctor.sh dev
./status.sh dev

echo "CHECK_OK"
