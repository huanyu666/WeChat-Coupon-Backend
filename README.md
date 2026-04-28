# WeChat Coupon Backend

这是当前项目的 Docker 化入口。目标是把部署和远程开发收敛成固定命令，避免手工拼 Python、Redis、socket、运行目录。

## 开发环境

```bash
cd /www/wwwroot/wx-coupon-dev
./install.sh dev
```

日常检查：

```bash
./status.sh dev
./doctor.sh dev
```

开发访问：

```text
http://服务器IP:18080
```

## 生产环境

建议使用独立目录：

```bash
cd /www/wwwroot/wx-coupon-prod
./install.sh prod --port 8080 --shortlink-base-url https://你的正式域名
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
