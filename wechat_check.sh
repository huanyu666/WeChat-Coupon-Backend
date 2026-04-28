#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_ROOT"

if python3 - <<'PY' >/dev/null 2>&1
from Crypto.Cipher import AES
PY
then
  exec python3 scripts/wechat_callback_check.py "$@"
fi

run_with_image() {
  image_name="$1"
  shift

  if ! docker image inspect "$image_name" >/dev/null 2>&1; then
    return 1
  fi

  echo "WECHAT_CALLBACK_CHECK_USING_DOCKER image=$image_name" >&2
  exec docker run --rm --network host \
    -v "$PROJECT_ROOT:/app" \
    -w /app \
    "$image_name" \
    python3 scripts/wechat_callback_check.py "$@"
}

if [ "${WX_WECHAT_CHECK_NO_DOCKER:-0}" != "1" ] && command -v docker >/dev/null 2>&1; then
  if [ -n "${WX_WECHAT_CHECK_DOCKER_IMAGE:-}" ]; then
    run_with_image "$WX_WECHAT_CHECK_DOCKER_IMAGE" "$@" || true
  fi
  run_with_image wx-coupon-backend:dev "$@" || true
  run_with_image wx-coupon-backend:local "$@" || true
fi

echo "WECHAT_CALLBACK_CHECK_DEPENDENCY_MISSING pycryptodome; run inside Docker or install requirements.txt" >&2
exit 1
