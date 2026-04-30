# wx-coupon 微信公众号优惠券后台

这是一个已经完成 Docker 化收口的微信公众号优惠券服务。项目核心是 FastAPI 后端、Redis、内置 `meituan-query` 短链服务、后台管理页和运行时迁移工具。

最终版部署目标是：

- 代码和运行数据分离，运行数据只放在 `runtime-data/`。
- 后台登录使用 HttpOnly Cookie-only 会话，不依赖前端保存 token。
- 公众号账号级业务配置通过 `/wechat-account-settings` 管理。
- 全局业务配置通过 `/system-settings` 管理。
- 迁服通过迁移包导出、预览、导入闭环完成。
- 常规健康检查通过固定脚本执行，不需要手工拼 Docker 或 Python 命令。

## 目录说明

```text
.
├── main.py                         # FastAPI 入口
├── routes/                         # 后台、微信回调、迁移、系统设置等 API
├── html/                           # 后台页面
├── utils/                          # 运行时路径、迁移、鉴权、业务工具
├── scripts/                        # Docker、smoke、备份恢复、迁服脚本
├── deploy/                         # Nginx/OpenResty 参考模板
├── runtime-data/                   # 本机真实运行数据，不提交 Git
├── logs/                           # 日志，不提交 Git
├── backups/                        # 备份和迁移包，不提交 Git
├── docker-compose.yml              # 生产/普通部署
├── docker-compose.dev.yml          # 开发部署
└── .env.docker.example             # 生产环境变量模板
```

必须保护并且不要提交的内容：

```text
.env
runtime-data/
logs/
backups/
config.toml
*.db
*.db-wal
*.db-shm
mimotion/token_cache/
mimotion/encrypted_tokens.data
```

## 最快生产部署

新服务器装好 Docker、Docker Compose 和 Git 后，只需要：

```bash
mkdir -p /www/wwwroot
cd /www/wwwroot
git clone https://github.com/huanyu666/WeChat-Coupon-Backend.git wx-coupon-prod
cd wx-coupon-prod
git checkout docker版
./deploy.sh
```

脚本会自动：

- 创建 `.env`
- 自动填入端口和访问地址
- 启动 Docker 容器
- 执行健康检查
- 打印后台登录地址

如果自动识别的公网 IP 不对，明确指定访问地址：

```bash
./deploy.sh --public-url http://你的服务器IP:8080
```

如果换端口：

```bash
./deploy.sh --port 8081 --public-url http://你的服务器IP:8081
```

部署完成后打开：

```text
http://你的服务器IP:8080/login
```

全新部署会在网页里创建第一个管理员，不需要命令行创建账号。

## 手动生产部署

### 1. 准备服务器

建议使用一台干净 Linux 服务器，安装：

- Docker
- Docker Compose 插件
- Git
- curl

确认 8080 或你计划使用的端口没有被占用：

```bash
ss -lntp | grep ':8080' || true
docker version
docker compose version
```

### 2. 拉取代码

```bash
mkdir -p /www/wwwroot
cd /www/wwwroot
git clone https://github.com/huanyu666/WeChat-Coupon-Backend.git wx-coupon-prod
cd wx-coupon-prod
git checkout docker版
```

### 3. 创建 `.env`

```bash
cp .env.docker.example .env
```

最小生产配置示例：

```bash
WX_HTTP_PORT=8080
TZ=Asia/Shanghai
WX_SERVICE_ENV=prod
WX_SERVICE_DEPLOYMENT_NAME=wx-coupon-prod
GO_SHORTLINK_PUBLIC_BASE_URL=http://你的服务器IP:8080
WX_SERVICE_REDIS_URL=redis://redis:6379/0
```

如果已经有正式域名，把 `GO_SHORTLINK_PUBLIC_BASE_URL` 改成正式域名，例如：

```bash
GO_SHORTLINK_PUBLIC_BASE_URL=https://coupon.example.com
```

不要把真实 `.env` 提交到 Git。

### 4. 启动容器

```bash
./scripts/docker_prod_up.sh
./status.sh prod
```

`status.sh prod` 正常时会检查：

- Docker 容器运行状态
- `/healthz`
- `/readyz`
- Redis 可用性
- `meituan-query` socket
- 未登录后台受保护 API 返回 401

### 5. 初始化后台和业务配置

打开后台：

```text
http://你的服务器IP:8080/login
```

全新部署且没有导入迁移包时，第一次打开 `/login` 会显示“首次设置管理员”。在浏览器里创建第一个管理员后，初始化入口会自动关闭并进入后台。

如果是从旧环境导入迁移包，后台账号会随 `runtime-data/config.toml` 一起恢复，直接用旧账号登录。

登录后完成两类配置：

- `/wechat-account-settings`：公众号账号级配置，包括 appid、token、AES、欢迎语、默认回复、关键词回复、菜单点击回复、美团小程序配置、授权用户、URL 模式等。
- `/system-settings`：全局业务配置和管理员账号管理，包括链接识别提示词、链接处理配置、排行榜配置、添加管理员、重置管理员密码。

最终版不要求日常手改 TOML/JSON，也不要求用命令行创建管理员。真实配置会写入 `runtime-data/`。

## 开发部署

开发目录示例：

```bash
cd /www/wwwroot/wx-coupon-dev
cp .env.dev.example .env
./scripts/docker_dev_up.sh
./status.sh dev
```

默认开发访问地址：

```text
http://127.0.0.1:18080
```

常用命令：

```bash
./status.sh dev
./scripts/docker_dev_logs.sh
./scripts/docker_dev_restart.sh
./scripts/docker_dev_down.sh
```

## 从旧服务器迁移

旧服务器导出迁移包：

```bash
cd /www/wwwroot/wx-coupon-prod
./export_migration.sh
```

导出的包默认在：

```text
backups/migration-exports/
```

把迁移包传到新服务器后，在新部署目录执行：

```bash
cd /www/wwwroot/wx-coupon-prod
./import_migration.sh /path/to/wx-coupon-migration-runtime-YYYYMMDD-HHMMSS.tar.gz
./status.sh prod
```

迁移包会覆盖或恢复这些核心运行数据：

- `runtime-data/config.toml`
- `runtime-data/wechat_accounts.runtime.json`
- `runtime-data/system_settings.runtime.json`
- 激活码、Scene、P 值等 JSON 数据
- `runtime-data/merchant_coupons/merchant_coupons.db`
- `runtime-data/site-verification/`

独立演练命令：

```bash
python3 scripts/migration_drill_check.py --startup-check
```

## Smoke 检查

轻量检查：

```bash
./status.sh prod
```

业务 smoke：

```bash
python3 scripts/business_smoke_check.py --base-url http://127.0.0.1:8080
```

如果需要覆盖后台登录、公众号读取保存和微信回调，把凭据只放在本机环境变量或 `.env` 中：

```bash
WX_SMOKE_ADMIN_USERNAME=你的管理员账号
WX_SMOKE_ADMIN_PASSWORD=你的管理员密码
WX_SMOKE_WECHAT_TOKEN=你的公众号Token
WX_SMOKE_WECHAT_ACCOUNT_ID=gh_xxx
```

不要把这些值写进 Git。

## 备份、恢复、更新

生产更新前先备份：

```bash
./backup.sh
```

更新代码并重建：

```bash
git pull origin docker版
./scripts/docker_prod_up.sh
./status.sh prod
```

恢复备份：

```bash
./restore.sh backups/你的备份.tar.gz
./restore.sh backups/你的备份.tar.gz --yes
./scripts/docker_prod_up.sh
./status.sh prod
```

发布顺序固定为：

```text
backup.sh -> 同步代码 -> 重建容器 -> status.sh prod -> business smoke -> 观察日志
```

## 站点认证文件

微信或腾讯站点认证需要临时放根路径 `.txt` 文件时，不要直接写仓库根目录，使用：

```bash
./site_verify.sh add tencentxxxx.txt 认证内容
./site_verify.sh list
./site_verify.sh remove tencentxxxx.txt
```

文件会写入：

```text
runtime-data/site-verification/
```

访问地址：

```text
http://你的服务器IP:8080/tencentxxxx.txt
```

## 常见问题

### 后台打不开

先看容器和健康检查：

```bash
docker compose ps
./status.sh prod
./scripts/docker_prod_logs.sh
```

### 首次部署没有账号

打开：

```text
http://你的服务器IP:8080/login
```

如果系统里没有管理员，会自动显示首次设置页面。创建第一个管理员后，入口会关闭，后续只显示普通登录页。

### 重置管理员密码

登录后台后打开：

```text
http://你的服务器IP:8080/system-settings
```

在“管理员账号”区域输入用户名和新密码即可添加管理员或重置已有管理员密码。这个功能要求已经登录后台；不会开放未登录远程重置。

### 端口冲突

修改 `.env`：

```bash
WX_HTTP_PORT=新的端口
GO_SHORTLINK_PUBLIC_BASE_URL=http://你的服务器IP:新的端口
```

然后重建：

```bash
./scripts/docker_prod_up.sh
./status.sh prod
```

### 迁移后配置不存在

先 inspect 迁移包，再导入：

```bash
python3 scripts/import_migration_package.py inspect backups/migration-exports/你的迁移包.tar.gz
./import_migration.sh backups/migration-exports/你的迁移包.tar.gz
```

确认 `runtime-data/` 下存在核心文件。

### 公众号回调失败

检查后台 `/wechat-account-settings` 中默认账号的 token、appid、EncodingAESKey。微信服务器配置的 URL 通常是：

```text
http://你的服务器IP:8080/wechat
```

然后执行：

```bash
./wechat_check.sh prod
```

### Git 状态出现运行数据

不要 `git add .`。先看具体文件：

```bash
git status --short
```

运行数据、日志、备份、真实配置和数据库都不应该提交。

## 安全注意事项

- 真实 `.env`、公众号密钥、管理员密码、token cache、SQLite 数据库不进 Git。
- 后台鉴权为 HttpOnly Cookie-only，不支持把登录 token 存到前端。
- 生产发布前必须先 `./backup.sh`。
- 迁移包可能包含真实业务数据，只在可信服务器间传输和保存。
- 如果历史仓库曾暴露过密钥，应在微信公众平台和相关服务中轮换密钥。

更多生产细节见 [docs/PRODUCTION_DEPLOYMENT_GUIDE.md](docs/PRODUCTION_DEPLOYMENT_GUIDE.md)。
