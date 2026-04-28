# 远程开发与 Docker 部署记录

记录时间：2026-04-28

## 当前目标

当前项目部署麻烦、维护成本高。暂时不直接重写业务，也不先手工部署旧环境，而是先把部署方式改造成可重复、可回滚、适合远程开发的形态。

最终理想流程：

```text
Windows VSCode
  -> Remote-SSH
宝塔 Linux 服务器
  -> Docker Compose
容器内运行 Python + Redis + meituan-query
```

## 已达成的共识

1. 不建议一开始就换语言重写。
2. 先解决部署复杂、配置混乱、测试困难的问题。
3. 服务器已经安装宝塔面板，并完成 SSH 免密连接。
4. 服务器当前状态已经做了快照和备份，可以作为干净底座。
5. 可以先在当前服务器上折腾跑通流程，全部确认后再回退到干净快照，按脚本重新安装。
6. 之后适合用 VSCode Remote-SSH 直接连服务器开发。
7. 远程开发只能在 dev 目录做，不要直接改 prod 目录。

## 当前推荐目录结构

```text
/www/wwwroot/wx-coupon-dev   # 远程开发目录
/www/wwwroot/wx-coupon-prod  # 正式生产目录
```

开发时只打开：

```text
/www/wwwroot/wx-coupon-dev
```

生产目录只做：

```text
git pull
docker compose up -d --build
```

## 当前仓库已经新增的文件

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

## 当前仓库已经调整的逻辑

Redis 原本主要依赖 Unix socket：

```text
/run/redis/redis-server.sock
```

现在已兼容 Docker 场景下的 TCP/URL 连接：

```env
WX_SERVICE_REDIS_URL=redis://redis:6379/0
```

同时 `meituan-query` 二进制仍会访问旧的 Redis Unix socket：

```text
/run/redis/redis-server.sock
```

所以 `docker-compose.dev.yml` 和 `docker-compose.yml` 里 Redis 会额外创建该 socket，并通过共享卷挂载给 app 容器。

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

本机没有 Docker，所以 Docker Compose 尚未在本地验证。

## Windows 本地下一步

先把当前部署改动提交到 Git。

```powershell
git status
git add .
git commit -m "add docker and remote dev setup"
git push
```

如果还没有远程仓库，先创建一个私有仓库，然后执行：

```powershell
git remote add origin 你的仓库地址
git push -u origin main
```

如果默认分支不是 `main`，用实际分支名替换。

## 宝塔服务器下一步

### 1. 安装 Docker

在宝塔面板里安装 Docker / Docker Compose。

服务器终端检查：

```bash
docker --version
docker compose version
```

如果 `docker compose` 不可用，后续命令改用：

```bash
docker-compose
```

### 2. 创建开发目录

```bash
cd /www/wwwroot
git clone 你的仓库地址 wx-coupon-dev
cd wx-coupon-dev
cp .env.dev.example .env
mkdir -p runtime-data logs
```

先不要放真实生产数据，先跑空环境验证。

### 3. 启动开发容器

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

如果上面命令不可用：

```bash
docker-compose -f docker-compose.dev.yml up -d --build
```

### 4. 检查服务

```bash
docker compose -f docker-compose.dev.yml ps
curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/readyz
```

如果 `/healthz` 返回 JSON，说明基础服务活了。

### 5. 查看日志

```bash
cd /www/wwwroot/wx-coupon-dev
docker compose -f docker-compose.dev.yml logs -f app
```

如果使用旧版命令：

```bash
docker-compose -f docker-compose.dev.yml logs -f app
```

## VSCode Remote-SSH 使用方式

Windows 安装 VSCode 插件：

```text
Remote - SSH
```

连接服务器后打开目录：

```text
/www/wwwroot/wx-coupon-dev
```

之后在 VSCode 保存 `.py` 文件，开发容器里的 uvicorn 会自动 reload。

开发容器使用：

```bash
uvicorn main:app --host 0.0.0.0 --port 80 --reload --reload-dir /app
```

宿主机访问端口：

```text
http://服务器IP:18080
```

## 宝塔反向代理测试域名

等 `18080/healthz` 跑通后，再配置测试域名，例如：

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

## 回退快照前必须确认

回退系统快照之前，确保这些东西已经离开服务器：

```text
代码已 push 到 Git
.env 真实配置已单独保存
runtime-data / JSON / DB 数据已备份
部署命令已经记录
```

否则回退系统时，会把已经摸索出来的成功经验一起删掉。

## 最终重装验证流程

当当前服务器已经跑通后，可以这样验证部署是否真的可重复：

```text
1. 确认代码已 push
2. 确认 .env 和数据已备份
3. 回退到干净快照
4. 安装 Docker
5. git clone 项目
6. cp .env.dev.example .env
7. docker compose -f docker-compose.dev.yml up -d --build
8. curl /healthz 和 /readyz
```

如果这套流程能一次成功，说明部署问题基本被解决。

## 当前最重要的一句话

现在不要急着重构业务，也不要直接改生产。先把：

```text
VSCode Remote-SSH + wx-coupon-dev + docker-compose.dev.yml
```

这条开发链路跑通。
