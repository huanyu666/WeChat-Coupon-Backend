# Docker 安装与更新入口

目标是把部署收敛成固定入口，尽量不要手工拼 Docker Compose 命令。

## 首次开发环境安装

```bash
cd /www/wwwroot/wx-coupon-dev
./install.sh dev
```

安装脚本会：

- 从 `.env.dev.example` 创建 `.env`（如果 `.env` 不存在）
- 创建 `runtime-data/`、`logs/`、`backups/`
- 执行 `docker_doctor`
- 启动开发容器
- 自动执行 smoke check

## 配置 .env

首次安装会自动创建 `.env`。之后可以用固定入口修改配置：

```bash
./configure.sh --mode dev --port 18080 --shortlink-base-url http://localhost:18080
./configure.sh --mode prod --port 8080 --shortlink-base-url https://你的正式域名
```

只校验不写入：

```bash
./configure.sh --mode dev --check
./configure.sh --mode prod --check
```

修改已有 `.env` 时会自动保存 `.env.bak.*`。

也可以启动临时浏览器向导：

```bash
./wizard.sh --host 127.0.0.1 --port 18081
```

它会在终端打印带 token 的访问地址。建议通过 SSH 端口转发访问，配置完成后关闭进程。
向导可以保存 `.env`，也可以运行 doctor、status 和 install。

## 日常开发

```bash
./doctor.sh dev
./preflight.sh dev
./status.sh dev
./check.sh
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
./install.sh prod --port 8080 --shortlink-base-url https://你的正式域名
```

安装前可以先生成或修改 `.env`：

```bash
./configure.sh --mode prod --create --port 8080 --shortlink-base-url https://你的正式域名
./preflight.sh prod
```

宝塔/OpenResty 反代模板：

```text
deploy/openresty/docker-http-proxy.conf.example
```

默认代理到 `http://127.0.0.1:8080`，需要和 `.env` 里的 `WX_HTTP_PORT` 保持一致。

## 更新

开发环境：

```bash
./update.sh dev
```

生产环境：

```bash
./update.sh prod
```

更新脚本会先备份运行数据，再尝试 `git pull --ff-only`，最后重建并执行 smoke check。

如果只想验证当前目录，不拉 Git：

```bash
WX_UPDATE_SKIP_GIT=1 ./update.sh dev
```

## 数据备份与恢复

```bash
./backup.sh
./restore.sh backups/你的备份.tar.gz
./restore.sh backups/你的备份.tar.gz --yes
```

恢复默认写入 `runtime-data/`，覆盖旧文件前会自动保存到 `backups/pre-restore-*`。
