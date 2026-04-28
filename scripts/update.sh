#!/usr/bin/env sh
set -eu

MODE="${1:-dev}"
SKIP_GIT="${WX_UPDATE_SKIP_GIT:-0}"

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
    echo "UPDATE_FAILED invalid_mode=$MODE expected=dev_or_prod" >&2
    exit 1
    ;;
esac

mkdir -p runtime-data logs backups
python3 scripts/backup_runtime_data.py

if [ "$SKIP_GIT" != "1" ]; then
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if git remote >/dev/null 2>&1 && [ -n "$(git remote)" ]; then
      git pull --ff-only
    else
      echo "UPDATE_WARN no_git_remote skip_git_pull"
    fi
  else
    echo "UPDATE_WARN not_a_git_worktree skip_git_pull"
  fi
else
  echo "UPDATE_SKIP_GIT"
fi

./scripts/docker_doctor.sh "$MODE"
export WX_SKIP_PRE_DEPLOY_BACKUP=1
"$UP_SCRIPT"

echo "UPDATE_OK mode=$MODE"
