#!/usr/bin/env sh
set -eu

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$PROJECT_ROOT"

PORT="${WX_HTTP_PORT:-8080}"
PUBLIC_URL="${GO_SHORTLINK_PUBLIC_BASE_URL:-}"
SHORTLINK_TTL_SECONDS="${SHORTLINK_DEFAULT_TTL_SECONDS:-604800}"
SKIP_DOCTOR=0

usage() {
  cat <<'EOF'
Usage:
  ./deploy.sh [--port 8080] [--public-url http://你的服务器IP:8080] [--shortlink-ttl-seconds 604800] [--skip-doctor]

Examples:
  ./deploy.sh
  ./deploy.sh --public-url http://154.219.115.75:8080
  ./deploy.sh --port 8081 --public-url http://154.219.115.75:8081
  ./deploy.sh --public-url https://98vx.cn --shortlink-ttl-seconds 604800
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --port)
      PORT="${2:-}"
      shift 2
      ;;
    --public-url|--base-url|--shortlink-base-url)
      PUBLIC_URL="${2:-}"
      shift 2
      ;;
    --shortlink-ttl-seconds)
      SHORTLINK_TTL_SECONDS="${2:-}"
      shift 2
      ;;
    --skip-doctor)
      SKIP_DOCTOR=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "DEPLOY_FAILED unknown_argument=$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

case "$PORT" in
  ''|*[!0-9]*)
    echo "DEPLOY_FAILED invalid_port=$PORT" >&2
    exit 1
    ;;
esac

case "$SHORTLINK_TTL_SECONDS" in
  ''|*[!0-9]*)
    echo "DEPLOY_FAILED invalid_shortlink_ttl_seconds=$SHORTLINK_TTL_SECONDS" >&2
    exit 1
    ;;
esac

detect_public_ip() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 3 https://api.ipify.org 2>/dev/null || true
    return
  fi
  hostname -I 2>/dev/null | awk '{print $1}'
}

if [ -z "$PUBLIC_URL" ]; then
  DETECTED_IP="$(detect_public_ip | tr -d '\r\n' || true)"
  if [ -n "$DETECTED_IP" ]; then
    PUBLIC_URL="http://${DETECTED_IP}:${PORT}"
  else
    PUBLIC_URL="http://127.0.0.1:${PORT}"
  fi
fi

case "$PUBLIC_URL" in
  http://*|https://*) ;;
  *)
    PUBLIC_URL="http://${PUBLIC_URL}"
    ;;
esac

echo "DEPLOY_START port=$PORT public_url=$PUBLIC_URL"

mkdir -p runtime-data logs backups
chmod +x scripts/*.sh ./*.sh 2>/dev/null || true

python3 scripts/configure_env.py \
  --mode prod \
  --create \
  --port "$PORT" \
  --shortlink-base-url "$PUBLIC_URL"

python3 scripts/configure_shortlink_settings.py \
  --public-base-url "$PUBLIC_URL" \
  --ttl-seconds "$SHORTLINK_TTL_SECONDS"

if [ "$SKIP_DOCTOR" -ne 1 ]; then
  ./scripts/docker_doctor.sh prod
fi

./scripts/docker_prod_up.sh
./status.sh prod

cat <<EOF
DEPLOY_OK

后台地址:
  ${PUBLIC_URL%/}/login

短链默认配置:
  域名: ${PUBLIC_URL%/}
  路径: /key/{code}
  TTL: ${SHORTLINK_TTL_SECONDS} 秒
  清理: Asia/Shanghai 每天 00:00

下一步:
  1. 浏览器打开上面的地址。
  2. 如果是全新部署，页面会让你创建第一个管理员。
  3. 登录后进入 /wechat-account-settings 配置业务账号。
  4. 如需改短链域名或 TTL，可在 /system-settings 调整，或重新执行 scripts/configure_shortlink_settings.py。

重要目录:
  runtime-data/ 真实运行数据
  logs/         日志
  backups/      备份和迁移包
EOF
