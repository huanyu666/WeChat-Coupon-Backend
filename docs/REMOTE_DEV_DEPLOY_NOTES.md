# 远程开发与 Docker 部署交接记录

记录日期：2026-04-28  
项目：WeChat Coupon Backend  
本地路径：`E:\Code\WeChat Coupon Backend`  
服务器开发路径：`/www/wwwroot/wx-coupon-dev`  
服务器 hostname：`ser472724381636`  
SSH Host：`154.219.115.75`

## 1. 背景

用户觉得当前项目部署极其麻烦、可维护性差，最初考虑像 `lizhipay/acg-faka` 一样换语言或重写，目标是让部署极快。

讨论后确定：当前最痛的不是 Python 语言本身，而是部署、配置、运行时数据、Redis、OpenResty/Nginx、`meituan-query` 二进制服务等边界太散。

因此路线从“先重写”调整为：

```text
先把部署和远程开发链路固定住
再小步重构业务模块
最后再决定是否换语言或拆服务
```

用户已完成：

- 宝塔面板安装
- SSH 免密连接
- 服务器快照和备份
- VSCode/SSH 开发准备

用户希望后续通过：

```text
Windows VSCode
  -> Remote-SSH
宝塔 Linux 服务器
  -> Docker Compose
容器内运行 Python + Redis + meituan-query
```

## 2. 当前策略

不建议直接在生产目录改代码。

推荐目录：

```text
/www/wwwroot/wx-coupon-dev   # 远程开发目录
/www/wwwroot/wx-coupon-prod  # 正式生产目录
```

开发只打开：

```text
/www/wwwroot/wx-coupon-dev
```

生产目录只做：

```bash
git pull
docker compose up -d --build
```

## 3. 已新增的部署文件

当前仓库中已新增：

```text
Dockerfile
docker-compose.yml
docker-compose.dev.yml
.dockerignore
.env.docker.example
.env.dev.example
deploy/docker/README.md
deploy/docker/vscode-remote-dev.md
docs/REMOTE_DEV_DEPLOY_NOTES.md
```

用途：

- `docker-compose.dev.yml`：VSCode Remote-SSH 开发环境，代码挂载，`uvicorn --reload` 自动重载。
- `docker-compose.yml`：偏生产/普通部署。
- `.env.dev.example`：开发环境变量模板。
- `.env.docker.example`：普通 Docker 部署环境变量模板。
- `deploy/docker/vscode-remote-dev.md`：远程开发说明。
- 本文件：完整交接记录。

## 4. 已调整的代码逻辑

### Redis 连接兼容 Docker

原项目主要依赖 Unix socket：

```text
/run/redis/redis-server.sock
```

Python 侧现在已兼容 Docker 场景下的 TCP/URL：

```env
WX_SERVICE_REDIS_URL=redis://redis:6379/0
```

相关文件：

```text
utils/redis_async.py
utils/encrypted_payload_utils.py
.env.example
```

语法检查已通过：

```powershell
python -m py_compile utils\redis_async.py utils\encrypted_payload_utils.py
```

### meituan-query 仍需要 Redis Unix socket

实际在服务器启动时发现：

```text
美团订单查询服务启动失败，进程已退出 (退出码: 1)
```

查看日志后定位到：

```text
初始化 Redis 失败: dial unix /run/redis/redis-server.sock: connect: no such file or directory
```

结论：

```text
Python 服务可以走 redis://redis:6379/0
但 meituan-query 二进制仍硬依赖 /run/redis/redis-server.sock
```

因此 `docker-compose.dev.yml` 和 `docker-compose.yml` 已改为：

- Redis 继续开放 TCP `6379`
- Redis 同时创建 `/run/redis/redis-server.sock`
- app 和 redis 共享 `/run/redis`
- Redis healthcheck 同时检查 TCP 和 Unix socket
- smoke check 默认绕过系统代理，避免服务器设置了 `HTTP_PROXY` 时本机健康检查误走代理

关键配置形态：

```yaml
redis:
  image: redis:7-alpine
  entrypoint:
    - /bin/sh
    - -c
    - |
      mkdir -p /run/redis
      chmod 777 /run/redis
      exec redis-server --appendonly yes --port 6379 --unixsocket /run/redis/redis-server.sock --unixsocketperm 777
  healthcheck:
    test: ["CMD-SHELL", "redis-cli ping && redis-cli -s /run/redis/redis-server.sock ping"]
```

app 侧挂载：

```yaml
volumes:
  - redis-dev-runtime:/run/redis
```

## 5. 服务器当前验证状态

已通过本地 SSH 直接连接服务器：

```bash
ssh 154.219.115.75
```

确认目标服务器：

```text
hostname = ser472724381636
project = /www/wwwroot/wx-coupon-dev
```

重启开发容器后状态正常：

```bash
cd /www/wwwroot/wx-coupon-dev
docker compose -f docker-compose.dev.yml ps
```

期望状态：

```text
wx-coupon-dev-redis-1   Up ... healthy
wx-coupon-dev-app-1     Up ...
```

已验证：

```bash
curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/readyz
python3 scripts/migration_smoke_check.py --require-go-socket --expect-redis-mode url --expect-shortlink-base-url http://localhost:18080
```

关键返回项已正常：

```json
"ok": true
"redis_reachable": true
"go_socket_exists": true
```

Redis socket 已验证：

```bash
docker compose -f docker-compose.dev.yml exec redis sh -lc "ls -lah /run/redis && redis-cli -s /run/redis/redis-server.sock ping"
```

已看到：

```text
redis-server.sock
PONG
```

app 日志最后已无：

```text
美团订单查询服务启动失败，进程已退出
```

说明：

```text
FastAPI + Redis TCP + Redis Unix socket + meituan-query socket
```

这条开发链路已经跑通。

## 6. 当前 Git 状态注意事项

本地和服务器都有未提交改动。

本地曾看到：

```text
 M .gitignore
 M docker-compose.dev.yml
 M docker-compose.yml
M  meituan-query
```

服务器曾看到：

```text
 M .gitignore
 M docker-compose.dev.yml
 M docker-compose.yml
 M meituan-query
```

原因：

- `docker-compose.dev.yml` / `docker-compose.yml`：Redis socket 修复。
- `.gitignore`：加入 `data/`，避免运行时目录误提交。
- `meituan-query`：Linux 下需要可执行权限，已执行 `git update-index --chmod=+x meituan-query`。

下一步建议提交：

```bash
cd /www/wwwroot/wx-coupon-dev
git add .gitignore docker-compose.dev.yml docker-compose.yml meituan-query docs/REMOTE_DEV_DEPLOY_NOTES.md
git commit -m "fix docker remote dev runtime"
git push
```

如果在 Windows 本地提交，也执行类似命令。

## 7. Windows 本地到服务器同步方式

曾经因为服务器没有拿到最新 compose 改动，`git pull` 显示：

```text
Already up to date.
```

但服务器文件实际还是旧版。

后来通过本机 SSH 配置找到正确服务器：

```text
Host 154.219.115.75
HostName 154.219.115.75
User root
IdentityFile C:/Users/huanyu/.ssh/id_ed25519_new
```

并使用 `scp` 直接同步：

```powershell
scp docker-compose.dev.yml 154.219.115.75:/www/wwwroot/wx-coupon-dev/docker-compose.dev.yml
scp docker-compose.yml 154.219.115.75:/www/wwwroot/wx-coupon-dev/docker-compose.yml
scp .gitignore 154.219.115.75:/www/wwwroot/wx-coupon-dev/.gitignore
```

后续不建议长期依赖 `scp`，最好走 Git：

```text
本地 commit + push
服务器 git pull
```

## 8. 常用命令

进入开发目录：

```bash
cd /www/wwwroot/wx-coupon-dev
```

启动开发环境：

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

停止：

```bash
docker compose -f docker-compose.dev.yml down
```

重启 app：

```bash
docker compose -f docker-compose.dev.yml restart app
```

查看 app 日志：

```bash
docker compose -f docker-compose.dev.yml logs -f app
```

查看 redis 日志：

```bash
docker compose -f docker-compose.dev.yml logs --no-color redis
```

检查服务：

```bash
curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/readyz
```

检查 Redis socket：

```bash
docker compose -f docker-compose.dev.yml exec redis sh -lc "redis-cli -s /run/redis/redis-server.sock ping"
```

检查 meituan-query socket 是否存在：

```bash
docker compose -f docker-compose.dev.yml exec app sh -lc "ls -lah /run/wx_service"
```

## 9. VSCode Remote-SSH 使用方式

Windows 安装 VSCode 插件：

```text
Remote - SSH
```

连接服务器后打开目录：

```text
/www/wwwroot/wx-coupon-dev
```

开发容器使用：

```bash
uvicorn main:app --host 0.0.0.0 --port 80 --reload --reload-dir /app
```

因此保存 `.py` 文件后，容器内会自动 reload。

开发访问地址：

```text
http://服务器IP:18080
```

本机服务器内访问：

```text
http://127.0.0.1:18080
```

## 10. 宝塔反向代理测试域名

等 `18080/healthz` 跑通后，可以配置测试域名：

```text
dev.xxx.com
```

宝塔面板：

```text
网站 -> 添加站点 -> dev.xxx.com
反向代理 -> 添加反向代理
```

目标 URL：

```text
http://127.0.0.1:18080
```

先不要把生产域名直接指向 dev。

## 11. 回退快照前必须确认

用户计划：在当前服务器上折腾跑通后，回退到宝塔 + SSH 的干净快照，再按文档重新安装。

回退快照之前必须确认：

```text
代码已 push 到 Git
.env 真实配置已单独保存
runtime-data / JSON / DB 数据已备份
部署命令已经记录
当前容器配置已提交
```

否则回退会把已经摸索出的成功配置一起删掉。

## 12. 最终重装验证流程

当前服务器跑通后，回退快照并验证：

```text
1. 确认代码已 push
2. 确认 .env 和数据已备份
3. 回退到干净快照
4. 安装 Docker
5. git clone 项目
6. cp .env.dev.example .env
7. mkdir -p runtime-data logs
8. docker compose -f docker-compose.dev.yml up -d --build
9. curl /healthz 和 /readyz
10. 检查 Redis socket PONG
```

如果这套流程能一次成功，说明部署问题基本被解决。

## 13. 下一步建议

当前不建议马上大重写业务。

下一步顺序：

```text
1. 先提交当前 Docker/Redis/meituan-query 修复
2. VSCode Remote-SSH 打开 /www/wwwroot/wx-coupon-dev
3. 配置宝塔测试域名反代到 127.0.0.1:18080
4. 补最小 smoke check 和常用功能验证清单
5. 再开始按模块重构
```

模块重构推荐顺序：

```text
1. 部署、配置、数据目录
2. Redis / meituan-query / 外部服务边界
3. routes
4. text_processors
5. 后台页面和 API
6. 必要时再拆独立服务或换语言
```

## 14. 当前最重要的一句话

现在已经不是“能不能部署”的阶段了。

当前阶段目标是：

```text
把已经跑通的 Docker + VSCode Remote-SSH 开发链路提交到 Git，
然后开始小步重构，每一步都用 /healthz、/readyz 和功能手测验证。
```
