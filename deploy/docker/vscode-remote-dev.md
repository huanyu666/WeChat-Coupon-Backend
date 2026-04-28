# VSCode Remote-SSH 开发

推荐结构：

```text
/www/wwwroot/wx-coupon-dev   # 远程开发目录
/www/wwwroot/wx-coupon-prod  # 正式生产目录
```

不要直接在生产目录里改代码。

## 1. 服务器准备

宝塔里安装 Docker，然后在终端执行：

```bash
cd /www/wwwroot
git clone 你的仓库地址 wx-coupon-dev
cd wx-coupon-dev
cp .env.dev.example .env
mkdir -p runtime-data logs
```

如果没有 Git 仓库，也可以先用宝塔文件上传项目压缩包，解压到 `wx-coupon-dev`。

## 2. 启动开发容器

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

检查：

```bash
curl http://127.0.0.1:18080/healthz
curl http://127.0.0.1:18080/readyz
```

## 3. VSCode 连接

Windows 上安装 VSCode 插件：

```text
Remote - SSH
```

连接服务器后打开目录：

```text
/www/wwwroot/wx-coupon-dev
```

之后你在 VSCode 保存 `.py` 文件，容器里的 uvicorn 会自动 reload。

## 4. 常用命令

看日志：

```bash
docker compose -f docker-compose.dev.yml logs -f app
```

重启开发环境：

```bash
docker compose -f docker-compose.dev.yml restart app
```

依赖变更后重建：

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

停止：

```bash
docker compose -f docker-compose.dev.yml down
```

## 5. 上生产

开发确认没问题后，在本地或服务器提交：

```bash
git add .
git commit -m "your change"
git push
```

生产目录只做拉取和重建：

```bash
cd /www/wwwroot/wx-coupon-prod
git pull
docker compose up -d --build
```
