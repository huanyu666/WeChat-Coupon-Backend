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
git status：干净
```

当前可访问：

```text
http://服务器IP:18080
```

服务器本机检查：

```bash
./status.sh dev
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

安全边界：

- 默认监听 `127.0.0.1`
- 必须带 token 访问
- 错误 token 返回 403
- 按钮只允许执行白名单命令
- 不接受任意 shell 输入
- 不挂到主业务服务里
- 用完应关闭该临时进程

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
65% - 70%
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
- app / redis healthcheck
- Redis socket 和 meituan-query socket 跑通
- OpenResty Docker HTTP 反代模板

仍未完成：

- `/www/wwwroot/wx-coupon-prod` 生产目录真实部署验证
- 宝塔站点反代真实配置验证
- Web 向导还没有日志分页/更完整 UI
- Web 向导还没有管理员账号初始化
- Web 向导还没有公众号配置初始化
- 业务级 smoke test 还没有覆盖后台登录和核心业务链路
- 运行时数据旧根目录兼容还没有完全收干净
- 还没有发布远程可 pull 的正式 Docker 镜像

## 7. 后续开发计划

### 阶段 A：生产部署实战

目标：把 dev 中已验证的安装器流程，在 prod 目录真实跑一遍。

计划：

```bash
cd /www/wwwroot
git clone <repo> wx-coupon-prod
cd wx-coupon-prod
./configure.sh --mode prod --create --port 8080 --shortlink-base-url https://你的正式域名
./preflight.sh prod
./install.sh prod --port 8080 --shortlink-base-url https://你的正式域名
./status.sh prod
```

验收：

- prod app healthy
- prod redis healthy
- `/healthz` OK
- `/readyz` OK
- Redis socket OK
- meituan-query socket OK

### 阶段 B：宝塔 / OpenResty 接入

目标：让域名反代成为固定流程。

计划：

- 使用 `deploy/openresty/docker-http-proxy.conf.example`
- 在宝塔站点中代理到 `127.0.0.1:8080`
- 验证域名访问 `/healthz`
- 验证域名访问 `/readyz`
- 根据实际域名更新 `GO_SHORTLINK_PUBLIC_BASE_URL`

### 阶段 C：完善 Web 安装向导

目标：更接近网页安装器体验。

计划：

- 增加 docker ps / compose ps 展示
- 增加日志查看按钮
- 增加备份按钮
- 增加恢复 dry-run 上传或路径输入
- 增加生产域名和端口检查
- 增加安装完成页

注意：

```text
Web 向导仍应保持临时进程 + token + localhost 监听，
不要直接挂进主业务服务暴露到公网。
```

### 阶段 D：业务初始化

目标：让首次部署不仅能启动服务，还能配置必要业务信息。

计划：

- 管理员账号初始化
- 公众号基础配置初始化
- 关键运行时文件初始化
- 后台登录 smoke test
- 公众号主流程 smoke test

### 阶段 E：继续收拢运行时数据

目标：减少旧根目录数据兼容分支。

计划：

- 确认所有 JSON / DB 都优先使用 `runtime-data/`
- 对旧根目录数据做一次迁移脚本
- 迁移完成后逐步减少历史兼容路径

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
1. 在 /www/wwwroot/wx-coupon-prod 做一次真实生产部署验证
2. 接入宝塔/OpenResty 反代
3. 完善 Web 安装向导的日志/状态/备份按钮
```

做完这三件，整体体感预计可以到：

```text
75% - 80%
```

也就是用户想要的“像 acg-faka 一样简单部署”的核心体验基本成型。
