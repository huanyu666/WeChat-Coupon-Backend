# 聊天记录、开发进度与后续计划

记录日期：2026-04-28  
项目目录：`/www/wwwroot/wx-coupon-dev`  
当前主线：把项目改造成接近 `lizhipay/acg-faka` 那种简单部署体验，但底层继续用 Docker 固定 Python、Redis、meituan-query、socket 等复杂依赖。

## 1. 最终目标

用户希望最终体验接近：

```text
拉代码 / 上传代码
填配置
一键安装
浏览器或固定命令完成部署
后续直接在服务器上开发和测试
```

本项目不适合照搬 `acg-faka` 的 PHP 裸部署形态，因为当前项目依赖：

- Python FastAPI
- Redis
- `meituan-query` 二进制
- Redis Unix socket
- Go internal socket
- 运行时 JSON / DB 数据
- 宝塔 / OpenResty / Nginx 反代

因此确定路线是：

```text
底层用 Docker 固定复杂依赖
上层做成 acg-faka 式的简单安装/更新/检查入口
```

期望最终流程：

```bash
git clone <repo> wx-coupon-dev
cd wx-coupon-dev
./install.sh dev
```

生产环境：

```bash
git clone <repo> wx-coupon-prod
cd wx-coupon-prod
./install.sh prod --port 8080 --shortlink-base-url https://你的正式域名
```

远程开发方式：

```text
Windows VSCode
  -> Remote-SSH 连接服务器
  -> 打开 /www/wwwroot/wx-coupon-dev
  -> 修改代码
  -> Docker dev 容器自动 reload
  -> ./status.sh dev 或 ./check.sh 验证
```

## 2. 当前状态

当前开发环境已部署并跑通：

```text
目录：/www/wwwroot/wx-coupon-dev
端口：18080
app 容器：healthy
redis 容器：healthy
/healthz：OK
/readyz：OK
Redis TCP：OK
Redis Unix socket：OK
meituan-query socket：OK
git status：干净（部署工具和文档已整理为提交）
```

当前可访问：

```text
http://服务器IP:18080
```

当前生产演练环境也已创建并跑通：

```text
目录：/www/wwwroot/wx-coupon-prod
端口：8080
app 容器：healthy
redis 容器：healthy
/healthz：OK
/readyz：OK
Redis TCP：OK
meituan-query socket：OK
GO_SHORTLINK_PUBLIC_BASE_URL：当前暂用 http://154.219.115.75
公网 IP 访问：http://154.219.115.75
```

服务器本机检查：

```bash
./status.sh dev
./status.sh prod
./check.sh
```

## 3. 已完成的关键能力

### Docker 开发/部署基础

- 新增并验证 `Dockerfile`
- 新增并验证 `docker-compose.dev.yml`
- 新增并验证 `docker-compose.yml`
- dev/prod 使用不同 Compose project name：
  - `wx-coupon-dev`
  - `wx-coupon-prod`
- app 和 redis 都有 Docker healthcheck
- app 容器内运行 FastAPI
- redis 容器同时提供 TCP 和 Unix socket
- app 容器和 redis 容器共享 `/run/redis`
- app 容器和 meituan-query 共享 `/run/wx_service`

### Redis / meituan-query 兼容

已确认：

```text
Python 服务可以使用 redis://redis:6379/0
meituan-query 仍硬依赖 /run/redis/redis-server.sock
```

因此 Docker Redis 启动参数固定为：

```text
--port 6379
--unixsocket /run/redis/redis-server.sock
--unixsocketperm 777
```

### 固定命令入口

根目录已新增这些固定入口：

```bash
./install.sh dev
./install.sh prod --port 8080 --shortlink-base-url https://你的正式域名

./update.sh dev
./update.sh prod

./status.sh dev
./doctor.sh dev
./preflight.sh dev
./check.sh
python3 scripts/business_smoke_check.py --base-url http://127.0.0.1:18080
./proxy_check.sh prod https://你的正式域名
./migrate_runtime.sh

./backup.sh
./restore.sh backups/你的备份.tar.gz
./restore.sh backups/你的备份.tar.gz --yes

./configure.sh --mode dev --port 18080 --shortlink-base-url http://localhost:18080
./configure.sh --mode prod --create --port 8080 --shortlink-base-url https://你的正式域名

./wizard.sh --host 127.0.0.1 --port 18081
```

底层脚本位于：

```text
scripts/
```

### .env 配置工具

已新增：

```text
scripts/configure_env.py
configure.sh
```

能力：

- 创建 `.env`
- 修改端口
- 修改短链/公开访问地址
- 校验 dev/prod 配置
- 支持 `--json` 输出，供 Web 安装向导复用
- 修改已有 `.env` 前自动生成 `.env.bak.*`
- `.gitignore` 已忽略 `.env.bak.*`

### 临时 Web 安装向导

已新增：

```text
scripts/install_wizard.py
wizard.sh
```

启动：

```bash
./wizard.sh --host 127.0.0.1 --port 18081
```

它会打印带 token 的 URL。

当前能力：

- 查看 `.env` 配置状态
- 选择 dev/prod
- 设置端口
- 设置短链/公开访问地址
- 保存并校验 `.env`
- 页面按钮执行：
  - doctor
  - status
  - install
  - compose ps
  - docker ps
  - 日志查看（按服务和行数）
  - 安装后备份
  - 恢复 dry-run
  - 管理员账号初始化
  - 公众号基础配置初始化
  - IP/反代访问检查
  - 旧运行时数据迁移预览
  - 无冲突旧运行时数据迁移
  - 站点认证文件添加、列表刷新、删除
- 安装成功后展示访问地址、健康检查和后续操作提示

安全边界：

- 默认监听 `127.0.0.1`
- 必须带 token 访问
- 错误 token 返回 403
- 按钮只允许执行白名单命令
- 不接受任意 shell 输入
- 不挂到主业务服务里
- 用完应关闭该临时进程

### 业务初始化脚本

已新增：

```text
scripts/init_admin_user.py
scripts/init_wechat_account.py
```

能力：

- `scripts/init_admin_user.py`
  - 向 `config.toml` 的 `[admin_users]` 写入或更新管理员账号
  - 密码按 SHA256 哈希存储
  - 写入前自动备份原始配置
- `scripts/init_wechat_account.py`
  - 向 `runtime-data/wechat_accounts.runtime.json` 写入或更新公众号基础配置
  - 支持 `--data-dir`，方便在临时目录和部署目录之间切换
  - 自动备份已有运行时存储
  - 可初始化默认公众号和账号特定配置
- 管理员账号初始化和公众号基础配置初始化均已接入 Web 向导

### 业务 smoke test

已新增：

```text
scripts/business_smoke_check.py
```

能力：

- 默认检查未登录访问 `/api/auth/verify` 和 `/api/dashboard/overview` 必须返回 401
- 设置 `WX_SMOKE_ADMIN_USERNAME` 和 `WX_SMOKE_ADMIN_PASSWORD` 后，自动验证后台登录、token verify、dashboard overview 和 logout
- 设置 `WX_SMOKE_WECHAT_TOKEN` 后，自动验证公众号 URL GET 签名
- 同时设置 `WX_SMOKE_WECHAT_ACCOUNT_ID` 后，自动发送 subscribe 事件并检查 XML 文本回复
- 已接入 `scripts/docker_dev_smoke.sh` 和 `scripts/docker_prod_smoke.sh`

### 反代与运行时迁移辅助

已新增：

```text
proxy_check.sh
scripts/proxy_check.py
setup_proxy.sh
scripts/setup_nginx_proxy.py
site_verify.sh
scripts/site_verification_file.py
migrate_runtime.sh
scripts/migrate_runtime_data.py
```

能力：

- `proxy_check.sh`
  - 检查本机 Docker 端口 `/healthz` 和 `/readyz`
  - 接入真实域名后检查公网域名 `/healthz` 和 `/readyz`
  - 校验 `/readyz` 里暴露的 `GO_SHORTLINK_PUBLIC_BASE_URL` 是否等于真实域名
- `setup_proxy.sh`
  - 生成通用 Nginx/OpenResty/宝塔反代配置
  - 默认 dry-run，显式 `--install --reload` 才写入并重载
  - 支持 `--conf-dir`、`--nginx-bin`、`--nginx-conf` 适配非标准路径
  - 检测同名 `server_name`，避免重复 server block 抢占
- `site_verify.sh`
  - 管理 `runtime-data/site-verification/` 下的临时站点认证 txt 文件
  - 根路径 `/{filename}.txt` 会从运行时目录读取，无需改代码或重建镜像
  - 支持 add / list / remove，适合微信/腾讯申诉后及时删除认证文件
- `migrate_runtime.sh`
  - 默认 dry-run，只展示根目录旧运行时数据迁移计划
  - 支持把旧根目录 `activation_codes*.json`、`scenes.json`、`p_values.json`、`merchant_coupons/` 迁到 `runtime-data/`
  - 目标已有不同内容时默认停止，必须显式 `--overwrite`
  - 支持 `--skip-conflicts` 先迁移无冲突项
  - 需要移走旧根目录文件时必须显式 `--remove-legacy`

### 备份与恢复

已新增：

```text
scripts/backup_runtime_data.py
scripts/restore_runtime_data.py
backup.sh
restore.sh
```

能力：

- 优先备份 `runtime-data/`
- 兼容旧根目录运行时数据
- 恢复前默认 dry-run
- 加 `--yes` 才真正恢复
- 覆盖旧数据前自动备份到 `backups/pre-restore-*`

### 部署前检查

已新增：

```text
preflight.sh
scripts/preflight.sh
```

用途：

```bash
./preflight.sh dev
./preflight.sh prod
```

检查内容：

- `.env` 配置
- Docker / Docker Compose
- Compose config
- 目录
- 端口格式
- 生产模式短链域名是否误用 localhost
- OpenResty 反代模板是否存在

### 宝塔 / OpenResty 反代模板

已新增：

```text
deploy/openresty/docker-http-proxy.conf.example
```

默认代理：

```text
http://127.0.0.1:8080
```

需要和生产 `.env` 中的：

```text
WX_HTTP_PORT=8080
```

保持一致。

## 4. 最近关键提交

```text
40f5cf5 add deployment operations tooling
2712e8d add deployment preflight check
e1a8d4f enhance install wizard operations
525d1b9 add temporary web install wizard
747c35d add one command project check
fe79547 document installer quickstart
5b7aa59 add env configuration helper
bc6766c add root maintenance wrappers
050f1c8 add root install update status wrappers
5b4c2cf add installer style docker entrypoints
a454961 add production docker helper scripts
bfb0efe add runtime data restore workflow
20701e8 add docker dev helper commands
458d3d3 add remote dev startup script
20f71a4 reuse runtime path config in backup script
2649948 centralize runtime path config
9d5a8e7 fix docker remote dev runtime
```

## 5. 当前常用命令

开发安装：

```bash
./install.sh dev
```

开发状态：

```bash
./status.sh dev
```

完整检查：

```bash
./check.sh
```

配置：

```bash
./configure.sh --mode dev --port 18080 --shortlink-base-url http://localhost:18080
```

临时安装向导：

```bash
./wizard.sh --host 127.0.0.1 --port 18081
```

管理员初始化：

```bash
python3 scripts/init_admin_user.py --username admin --password '你的安全密码'
```

公众号初始化：

```bash
python3 scripts/init_wechat_account.py --data-dir runtime-data --account-id gh_xxx --appid wx_xxx --name '公众号名称'
```

备份：

```bash
./backup.sh
```

恢复预览：

```bash
./restore.sh backups/你的备份.tar.gz
```

确认恢复：

```bash
./restore.sh backups/你的备份.tar.gz --yes
```

生产安装示例：

```bash
./install.sh prod --port 8080 --shortlink-base-url https://你的正式域名
```

生产部署前检查：

```bash
./preflight.sh prod
```

## 6. 当前完成度评估

按最终目标估算，目前约完成：

```text
85% - 90%
```

已完成：

- Docker dev/prod 底座
- 一键安装脚本
- 更新脚本
- 状态检查脚本
- 总检查脚本
- 备份/恢复脚本
- `.env` 配置工具
- 临时 Web 安装向导
- Web 安装向导中的 `docker ps / compose ps` 展示
- 安装完成页
- 日志查看服务/行数选择
- 安装后备份与恢复 dry-run
- Web 安装向导中的 IP/反代检查和运行时迁移入口
- Web 安装向导中的站点认证文件增删查入口
- 管理员账号初始化
- 公众号基础配置初始化脚本与 Web 向导入口
- 生产演练目录 `/www/wwwroot/wx-coupon-prod` 基础部署验证
- 基础业务 smoke test 默认接入 dev/prod status
- 运行时数据迁移 dry-run 工具
- 真实域名/OpenResty 反代验证工具
- 通用 Nginx/OpenResty/宝塔反代配置生成工具
- 站点根路径临时认证文件运行时管理工具和 Web 向导入口
- app / redis healthcheck
- Redis socket 和 meituan-query socket 跑通
- OpenResty Docker HTTP 反代模板

仍未完成：

- 业务级 smoke test 还需要填真实管理员密码和公众号 Token 后跑 full 模式
- 运行时数据旧根目录文件还未执行 `--remove-legacy` 清理；prod 迁移预演发现 `merchant_coupons` 目录存在冲突，需先人工确认
- 还没有发布远程可 pull 的正式 Docker 镜像

## 7. 后续开发计划

### 阶段 A：生产部署实战

结果：已完成基础生产容器验证。

已执行：

```bash
cd wx-coupon-prod
./configure.sh --mode prod --create --port 8080 --shortlink-base-url https://wx-coupon.example.com
./preflight.sh prod
./install.sh prod --port 8080 --shortlink-base-url https://wx-coupon.example.com
./status.sh prod
```

验收：

- prod app healthy：OK
- prod redis healthy：OK
- `/healthz` OK
- `/readyz` OK
- Redis TCP：OK
- meituan-query socket：OK
- 基础业务 smoke：OK

注意：

```text
当前生产演练目录是从 dev 当前工作树同步出来的，用于覆盖未提交安装器改动。
当前暂时不用域名，prod 已通过宝塔/Nginx 反代改为公网 IP 访问：http://154.219.115.75。
后续接入宝塔/OpenResty 和真实域名时，再把 GO_SHORTLINK_PUBLIC_BASE_URL 改成正式 HTTPS 域名。
```

### 阶段 B：宝塔 / OpenResty 接入

目标：让域名反代成为固定流程。

结果：IP 方式已完成，真实域名接入等待后续需要。

已完成：

- 宝塔/Nginx 已把 `http://154.219.115.75` 反代到 `127.0.0.1:8080`
- `./proxy_check.sh prod http://154.219.115.75` 验证通过
- `http://154.219.115.75/wechat` 明文和安全模式 GET 验证通过
- 站点认证文件在 `http://154.219.115.75/{filename}.txt` 和 `http://154.219.115.75:8080/{filename}.txt` 均验证通过

计划：

- 后续有域名后，使用 `setup_proxy.sh` 或 `deploy/openresty/docker-http-proxy.conf.example` 生成域名反代配置
- 验证域名访问 `/healthz` 和 `/readyz`
- 根据实际域名更新 `GO_SHORTLINK_PUBLIC_BASE_URL`
- 执行 `./proxy_check.sh prod https://你的正式域名`

### 阶段 C：完善 Web 安装向导

结果：已完成。

已落地：

- 增加 docker ps / compose ps 展示
- 增加日志查看按钮和服务/行数选择
- 增加备份按钮
- 增加恢复 dry-run 路径输入
- 增加生产域名和端口检查
- 增加安装完成页
- 增加站点认证文件添加、列表刷新、删除入口

注意：

```text
Web 向导仍应保持临时进程 + token + localhost 监听，
不要直接挂进主业务服务暴露到公网。
```

### 阶段 D：业务初始化

目标：让首次部署不仅能启动服务，还能配置必要业务信息。

已完成：

- 管理员账号初始化（`scripts/init_admin_user.py` + Web 向导入口）
- 公众号基础配置初始化（`scripts/init_wechat_account.py` + Web 向导入口）
- 基础业务 smoke test（认证保护检查已默认接入 dev/prod status）
- 后台登录 smoke test 和公众号回调 smoke test 的脚本能力已完成，填入真实凭据/Token 后自动启用
- 新增微信回调专项诊断入口：`./wechat_check.sh URL --token TOKEN --encoding-aes-key AES_KEY --appid APPID`
  - 宿主机缺 `pycryptodome` 时会自动使用 Docker 镜像环境执行安全模式 AES 检查
  - 已确认 `http://154.219.115.75:8080/wechat` 明文和安全模式 GET 验证通过
  - 已确认截图中的 `http://150.40.180.94/wechat` 明文和安全模式 GET 验证通过
  - 已在宝塔/Nginx 增加 IP 反代配置：`/www/server/panel/vhost/nginx/wx-coupon-ip.conf`
  - 已确认 `http://154.219.115.75/wechat` 明文和安全模式 GET 验证通过
  - 生产 `GO_SHORTLINK_PUBLIC_BASE_URL` 已从 `http://154.219.115.75:8080` 改为 `http://154.219.115.75`
- 新增通用反代配置生成入口：`./setup_proxy.sh --mode prod --server-name 你的服务器IP --port 8080`
- 新增站点认证文件 Web 向导入口，并已在生产演练目录同步验证

计划：

- 关键运行时文件初始化
- 用真实管理员密码跑一次后台登录 full smoke
- 用真实公众号 Token 和账号 ID 跑一次公众号回调 full smoke
- 微信后台 `verify token fail,200302` 表示微信已访问到 URL 但响应校验不通过，常见于填错路径或回调响应不正确；当前 `http://154.219.115.75/wechat` 已验证通过
- 微信后台 `invalid args,200002` 更符合平台参数校验或风控拦截；用户已对 `http://154.219.115.75:8080/index` 发起申诉，当前等待微信处理

### 阶段 E：继续收拢运行时数据

目标：减少旧根目录数据兼容分支。

已完成：

- 新增 `migrate_runtime.sh` / `scripts/migrate_runtime_data.py`
- dev dry-run 显示根目录历史数据和 `runtime-data/` 内容一致，未执行清理
- prod 已执行正式备份：`/www/wwwroot/wx-coupon-prod/backups/wx-runtime-backup-20260428-130812.tar.gz`
- prod dry-run 显示 `activation_codes*.json`、`p_values.json`、`scenes.json` 可复制到 `runtime-data/`
- prod dry-run 显示 `merchant_coupons` 与目标目录内容不同，暂不迁移、不覆盖、不删除

计划：

- 确认是否保留根目录示例数据
- 对比 prod 的 `merchant_coupons` 根目录和 `runtime-data/merchant_coupons`，确认哪一份是业务真实数据
- 如不再保留，执行 `./migrate_runtime.sh --remove-legacy --yes` 前先确认这些 tracked 文件的处理策略
- 清理完成后逐步减少历史兼容路径

### 阶段 F：可选远程 Docker 镜像

目标：进一步减少服务器构建时间。

当前是服务器本地 build：

```text
docker build -> wx-coupon-backend:dev/local
```

未来可选：

```text
CI 构建镜像
push 到 registry
服务器 docker pull
docker compose up -d
```

这一步不是当前最急，因为目前更重要的是把安装、配置、数据和生产实战跑稳。

## 8. 重要注意事项

- 当前 dev 环境已跑通，不要随意清空 Docker volume。
- `.env`、`runtime-data/`、`logs/`、`backups/` 都不应提交。
- `.env.bak.*` 已加入 `.gitignore`。
- Web 安装向导是临时工具，用完关闭。
- 生产部署必须使用独立目录 `/www/wwwroot/wx-coupon-prod`。
- dev 域名不要直接替代生产域名。
- 回退服务器快照前必须确认：
  - 代码已 push
  - `.env` 已备份
  - `runtime-data/` 已备份
  - `backups/` 已保存
  - 当前部署命令已记录

## 9. 下一步建议

下一步最值得做：

```text
1. 先按 IP 访问继续业务配置和测试：http://154.219.115.75
2. 等微信 IP 风控申诉通过后，再在微信后台尝试保存：http://154.219.115.75/wechat
3. 填真实管理员密码和公众号 Token，跑完整后台登录/公众号回调 smoke test
4. 确认 prod `merchant_coupons` 冲突后，再决定是否执行运行时数据迁移
```

做完这三件，整体体感预计可以到：

```text
90% - 95%
```

也就是用户想要的“像 acg-faka 一样简单部署”的核心体验基本成型。
