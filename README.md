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
也可以在向导里执行 IP/反代检查、旧数据迁移预览、备份/恢复预览和首次业务初始化。

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
./status.sh prod
```

暂时不用域名时，可以直接使用服务器公网 IP：

```bash
./configure.sh --mode prod --port 8080 --shortlink-base-url http://你的服务器IP:8080
./status.sh prod
```

宝塔/OpenResty 反代模板见：

```text
deploy/openresty/docker-http-proxy.conf.example
```

也可以生成通用 Nginx/OpenResty/宝塔配置：

```bash
./setup_proxy.sh --mode prod --server-name 你的服务器IP --port 8080
./setup_proxy.sh --mode prod --server-name 你的服务器IP --port 8080 --install --reload
```

默认第一条只预览；确认后再加 `--install --reload` 写入并重载 Nginx。

接入真实域名后验证：

```bash
./proxy_check.sh prod https://你的正式域名
```

暂用 IP 时验证：

```bash
./proxy_check.sh prod http://你的服务器IP:8080
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
./migrate_runtime.sh
```

运行数据放在 `runtime-data/`，日志放在 `logs/`，备份放在 `backups/`。
`./migrate_runtime.sh` 默认只预览根目录旧运行时数据迁移计划；确认后才加 `--yes`。

## 站点认证文件

微信/腾讯申诉或站长认证需要临时放根路径 `.txt` 文件时：

```bash
./site_verify.sh add tencentxxxx.txt 认证内容
./site_verify.sh list
./site_verify.sh remove tencentxxxx.txt
```

文件会写入 `runtime-data/site-verification/`，访问地址是 `http://你的访问地址/tencentxxxx.txt`。
同样功能也已接入临时 Web 安装向导，适合申诉时临时添加、验证后立即删除。

## Smoke Test

`./status.sh dev` 和 `./status.sh prod` 会检查 `/healthz`、`/readyz`、Redis、meituan-query socket 和后台鉴权保护。

如果需要覆盖真实后台登录和公众号回调，在 `.env` 中临时填写：

```bash
WX_SMOKE_ADMIN_USERNAME=你的管理员账号
WX_SMOKE_ADMIN_PASSWORD=你的管理员明文密码
WX_SMOKE_WECHAT_TOKEN=你的公众号Token
WX_SMOKE_WECHAT_ACCOUNT_ID=gh_xxx
```

更多说明见 [docs/DOCKER_INSTALL_GUIDE.md](docs/DOCKER_INSTALL_GUIDE.md)。
