# Docker 安装与更新入口

目标是把部署收敛成固定入口，尽量不要手工拼 Docker Compose 命令。

## 首次开发环境安装

```bash
cd /www/wwwroot/wx-coupon-dev
./scripts/install.sh dev
```

安装脚本会：

- 从 `.env.dev.example` 创建 `.env`（如果 `.env` 不存在）
- 创建 `runtime-data/`、`logs/`、`backups/`
- 执行 `docker_doctor`
- 启动开发容器
- 自动执行 smoke check

## 日常开发

```bash
./scripts/status.sh dev
./scripts/docker_dev_restart.sh
./scripts/docker_dev_logs.sh app
./scripts/docker_dev_logs.sh redis
```

开发访问：

```text
http://服务器IP:18080
```

## 生产安装

建议在独立目录执行，例如：

```bash
cd /www/wwwroot/wx-coupon-prod
./scripts/install.sh prod
```

安装前请先检查并修改 `.env`：

```bash
WX_HTTP_PORT=8080
GO_SHORTLINK_PUBLIC_BASE_URL=https://你的正式域名
```

## 更新

开发环境：

```bash
./scripts/update.sh dev
```

生产环境：

```bash
./scripts/update.sh prod
```

更新脚本会先备份运行数据，再尝试 `git pull --ff-only`，最后重建并执行 smoke check。

如果只想验证当前目录，不拉 Git：

```bash
WX_UPDATE_SKIP_GIT=1 ./scripts/update.sh dev
```

## 数据备份与恢复

```bash
python3 scripts/backup_runtime_data.py
python3 scripts/restore_runtime_data.py backups/你的备份.tar.gz
python3 scripts/restore_runtime_data.py backups/你的备份.tar.gz --yes
```

恢复默认写入 `runtime-data/`，覆盖旧文件前会自动保存到 `backups/pre-restore-*`。
