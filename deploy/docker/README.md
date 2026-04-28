# Docker 快速部署

这一步不是业务重写，而是先把当前项目改造成可重复部署。

更完整的安装和更新入口见：

```text
docs/DOCKER_INSTALL_GUIDE.md
```

## 1. 准备环境变量

```bash
cp .env.docker.example .env
```

如果端口或短链域名不同，改 `.env`：

```bash
WX_HTTP_PORT=8080
GO_SHORTLINK_PUBLIC_BASE_URL=https://你的域名
```

## 2. 放入运行数据

把旧项目的运行数据放到 `runtime-data/`：

- `wechat_accounts.runtime.json`
- `activation_codes*.json`
- `scenes.json`
- `p_values.json`
- `merchant_coupons/`
- `*.db`

只是先验证服务能不能启动的话，`runtime-data/` 可以先空着。

## 3. 启动

推荐直接执行：

```bash
./install.sh prod
```

它会创建基础目录、检查环境、启动普通 Docker Compose，并执行 smoke check。

也可以手动执行：

```bash
docker compose up -d --build
```

检查：

```bash
curl http://127.0.0.1:${WX_HTTP_PORT:-8080}/healthz
curl http://127.0.0.1:${WX_HTTP_PORT:-8080}/readyz
```

或者进容器跑项目自带检查：

```bash
docker compose exec app python scripts/migration_smoke_check.py --base-url http://127.0.0.1
```

在宿主机上也可以直接检查映射端口：

```bash
python3 scripts/migration_smoke_check.py --base-url http://127.0.0.1:${WX_HTTP_PORT:-8080} --expect-redis-mode url
```

生产常用命令：

```bash
./scripts/docker_doctor.sh prod
./scripts/docker_prod_smoke.sh
./scripts/docker_prod_restart.sh
./scripts/docker_prod_logs.sh app
./scripts/docker_prod_logs.sh redis
./scripts/docker_prod_down.sh
```

## 4. 运行数据备份与恢复

备份：

```bash
python3 scripts/backup_runtime_data.py
```

脚本会优先备份宿主机 `runtime-data/`，同时兼容旧项目根目录里的历史数据。

恢复前先预览：

```bash
python3 scripts/restore_runtime_data.py backups/你的备份文件.tar.gz
```

确认无误后恢复：

```bash
python3 scripts/restore_runtime_data.py backups/你的备份文件.tar.gz --yes
```

恢复默认写入 `runtime-data/`；覆盖已有文件前，会先保存一份到 `backups/pre-restore-*`。

## 说明

- 应用容器内部监听 `80`，宿主机端口由 `WX_HTTP_PORT` 控制。
- Redis 通过 `WX_SERVICE_REDIS_URL=redis://redis:6379/0` 连接。
- Redis 同时会在共享卷里生成 `/run/redis/redis-server.sock`，给 `meituan-query` 使用。
- `WX_SERVICE_AUTO_START_GO=true` 时，Python 服务会自动拉起 `meituan-query`。
- `scripts/migration_smoke_check.py` 默认绕过系统代理，避免服务器上 `HTTP_PROXY` 影响本机健康检查。
- 日志在 `logs/`，运行数据在 `runtime-data/`。
