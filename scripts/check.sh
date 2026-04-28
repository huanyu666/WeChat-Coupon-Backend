#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

python3 -m py_compile \
  scripts/backup_runtime_data.py \
  scripts/configure_env.py \
  scripts/business_smoke_check.py \
  scripts/install_wizard.py \
  scripts/migrate_runtime_data.py \
  scripts/migration_smoke_check.py \
  scripts/proxy_check.py \
  scripts/restore_runtime_data.py \
  scripts/setup_nginx_proxy.py \
  scripts/site_verification_file.py \
  scripts/wechat_callback_check.py \
  main.py \
  run_server.py \
  utils/path_utils.py \
  wechat_account_store.py

sh -n \
  backup.sh \
  configure.sh \
  doctor.sh \
  install.sh \
  migrate_runtime.sh \
  proxy_check.sh \
  restore.sh \
  setup_proxy.sh \
  site_verify.sh \
  wechat_check.sh \
  preflight.sh \
  status.sh \
  update.sh \
  scripts/*.sh

COMPOSE_PROJECT_NAME=wx-coupon-dev docker compose -f docker-compose.dev.yml config >/dev/null
COMPOSE_PROJECT_NAME=wx-coupon-prod docker compose -f docker-compose.yml config >/dev/null

./doctor.sh dev
./status.sh dev

echo "CHECK_OK"
