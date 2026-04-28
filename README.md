# WeChat Coupon Backend

这是当前项目的 Docker 化入口。目标是把部署和远程开发收敛成固定命令，避免手工拼 Python、Redis、socket、运行目录。

## 开发环境

```bash
cd /www/wwwroot/wx-coupon-dev
./install.sh dev
```

日常检查：

```bash
./preflight.sh dev
./status.sh dev
./doctor.sh dev
./check.sh
```

临时浏览器安装向导：

```bash
./wizard.sh --host 127.0.0.1 --port 18081
```

通过 SSH 端口转发访问它，配置完成后关闭进程。
向导支持保存 `.env`，也可以直接运行 doctor、status 和 install。

开发访问：

```text
http://服务器IP:18080
```

## 生产环境

建议使用独立目录：

```bash
cd /www/wwwroot/wx-coupon-prod
./install.sh prod --port 8080 --shortlink-base-url https://你的正式域名
./preflight.sh prod
```

宝塔/OpenResty 反代模板见：

```text
deploy/openresty/docker-http-proxy.conf.example
```

## 更新

```bash
./update.sh dev
./update.sh prod
```

## 数据

```bash
./backup.sh
./restore.sh backups/你的备份.tar.gz
./restore.sh backups/你的备份.tar.gz --yes
```

运行数据放在 `runtime-data/`，日志放在 `logs/`，备份放在 `backups/`。

更多说明见 [docs/DOCKER_INSTALL_GUIDE.md](docs/DOCKER_INSTALL_GUIDE.md)。
