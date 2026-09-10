# sk.waimaiyouhui.top

美团多账户领券与商家券查询测试站点。包含管理后台、二维码授权、一次授权后并行领取 WorkBuddy 与 Tabbit 优惠券、独立授权会话、8 个二维码备用池、商家券查询和活动链接拼接。

## 运行环境

- Ubuntu 24.04 或其他常见 Linux 发行版
- Node.js 18 或更高版本
- Nginx（生产环境反向代理）
- systemd（生产环境守护进程）

## 安装

```bash
cd /www/wwwroot/skill
cp .env.example .env
npm ci --omit=dev
cd coupon-runtime
npm ci --omit=dev
cd ..
```

服务器没有 npm 时，可解压随包附带的 `node_modules-qrcode.tar.gz`：

```bash
tar -xzf node_modules-qrcode.tar.gz
```

然后编辑 `.env`，至少替换：

- `ADMIN_PASSWORD`：管理后台密码。
- `SESSION_SECRET`：不少于 32 个字符的随机值。
- `PUBLIC_API_KEY`：WorkBuddy 调用公开接口时使用的 Bearer Token；不填写则不校验。

不要把真实 `.env`、授权会话目录或账号 Token 提交或再次打包。

## 启动与验证

```bash
npm run check
npm test
npm start
```

生产环境配置参考：

- `deploy/skill-admin.service`
- `deploy/nginx-http.conf`
- `deploy/nginx-https.conf`

服务默认监听 `127.0.0.1:3188`，健康检查地址为 `/health`。

## 主要接口

- `POST /api/admin/login`：管理后台登录。
- `POST /api/claim/start`：取用独立授权二维码并在后台补充备用池。
- `POST /api/claim/poll`：查询授权和领券状态。
- `POST /api/claim/cancel`：取消授权会话。
- `GET /api/public/config`：读取公开功能开关。
- `POST /api/merchant/query`：查询商家券、返现并生成商家券活动链接。

## 不包含的运行数据

源码包主动排除了 `.env`、`data/claim-sessions`、日志、临时授权缓存、测试授权目录和 npm 下载缓存。压缩包内没有真实管理密码、登录 Token 或用户授权信息。
