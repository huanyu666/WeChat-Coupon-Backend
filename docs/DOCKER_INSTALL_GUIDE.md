# Docker 安装与更新入口

目标是把部署收敛成固定入口，尽量不要手工拼 Docker Compose 命令。

## 最短生产部署

新服务器装好 Docker、Docker Compose 和 Git 后，直接执行：

```bash
mkdir -p /www/wwwroot
cd /www/wwwroot
git clone https://github.com/huanyu666/WeChat-Coupon-Backend.git wx-coupon-prod
cd wx-coupon-prod
git checkout docker版
./deploy.sh
```

如果自动识别的公网 IP 不对，显式指定访问地址：

```bash
./deploy.sh --public-url http://你的服务器IP:8080
```

如果要换端口：

```bash
./deploy.sh --port 8081 --public-url http://你的服务器IP:8081
```

部署完成后打开脚本打印的 `/login` 地址；全新部署会在网页里创建第一个管理员。

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
向导还提供 IP/反代检查、旧运行时数据迁移预览、无冲突旧运行时数据迁移、备份/恢复预览和业务初始化入口。

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

推荐使用上面的 `./deploy.sh`。如果需要手动指定参数，也可以在独立目录执行：

```bash
cd /www/wwwroot/wx-coupon-prod
./install.sh prod --port 8080 --shortlink-base-url https://你的正式域名
```

暂时不用域名时：

```bash
./install.sh prod --port 8080 --shortlink-base-url http://你的服务器IP:8080
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

也可以用脚本生成通用反代配置，适配宝塔 Nginx、普通 Nginx 和 OpenResty：

```bash
./setup_proxy.sh --mode prod --server-name 你的服务器IP --port 8080
./setup_proxy.sh --mode prod --server-name 你的服务器IP --port 8080 --install --reload
```

说明：

- 默认只 dry-run 打印将要写入的配置。
- `--install --reload` 才会写入配置并执行 `nginx -t` / reload。
- `--conf-dir`、`--nginx-bin`、`--nginx-conf` 可用于非标准安装路径。
- 如果同名 `server_name` 已存在，脚本会拒绝生成重复配置；确认要接管已有站点时再加 `--replace-existing --force`。

接入真实域名后验证反代：

```bash
./proxy_check.sh prod https://你的正式域名
```

暂用 IP 时验证：

```bash
./proxy_check.sh prod http://你的服务器IP:8080
```

如果只想确认本机 Docker 端口，不访问公网域名：

```bash
./proxy_check.sh prod --skip-public
```

## 站点认证文件

微信/腾讯申诉或站长认证要求在站点根目录放 `.txt` 文件时，不需要改代码或重建镜像：

```bash
./site_verify.sh add tencentxxxx.txt 认证内容
./site_verify.sh list
curl http://你的服务器IP/tencentxxxx.txt
./site_verify.sh remove tencentxxxx.txt
```

文件实际存放在 `runtime-data/site-verification/`。如果使用 Docker，生产容器会通过 `/data/site-verification/` 读取这些文件。
临时 Web 安装向导里也有站点认证文件的添加、刷新列表和删除入口。

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

## 旧运行时数据迁移

旧版本可能把 `activation_codes*.json`、`scenes.json`、`p_values.json`、`merchant_coupons/` 等数据放在项目根目录。现在统一优先使用 `runtime-data/`。

预览迁移计划：

```bash
./migrate_runtime.sh
```

确认拷贝缺失项：

```bash
./migrate_runtime.sh --yes
```

如果目标已有不同内容，脚本会停止；确认要覆盖时才使用：

```bash
./migrate_runtime.sh --overwrite --yes
```

如果只想先迁移无冲突项，保留冲突项给人工处理：

```bash
./migrate_runtime.sh --skip-conflicts --yes
```

如果迁移后要把根目录旧文件移到 `backups/`，显式加：

```bash
./migrate_runtime.sh --remove-legacy --yes
```

## 业务 smoke test

`./status.sh dev` 和 `./status.sh prod` 会自动执行 `scripts/business_smoke_check.py`。

默认检查：

- 未登录访问 `/api/auth/verify` 必须返回 401
- 未登录访问 `/api/dashboard/overview` 必须返回 401

如果 `.env` 中设置了下面的值，会继续验证真实后台登录、token 校验、dashboard overview 和登出：

```bash
WX_SMOKE_ADMIN_USERNAME=你的管理员账号
WX_SMOKE_ADMIN_PASSWORD=你的管理员明文密码
```

如果 `.env` 中设置了下面的值，会继续验证公众号 URL GET 签名；同时设置账号 ID 时会发送一次 subscribe 事件消息并检查 XML 文本回复：

```bash
WX_SMOKE_WECHAT_TOKEN=你的公众号Token
WX_SMOKE_WECHAT_ACCOUNT_ID=gh_xxx
```

只想单独运行：

```bash
python3 scripts/business_smoke_check.py --base-url http://127.0.0.1:18080
python3 scripts/business_smoke_check.py --base-url http://127.0.0.1:8080
```
