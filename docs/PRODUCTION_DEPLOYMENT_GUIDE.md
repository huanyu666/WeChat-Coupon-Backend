# 生产部署操作手册

本手册用于把 wx-coupon 部署到新服务器，或从旧服务器迁移到新服务器。最终版的运行边界是：代码在 Git，运行数据在 `runtime-data/`，密钥在 `.env` 和后台运行时配置中。

## 1. 前置条件

服务器需要安装：

```bash
docker version
docker compose version
git --version
curl --version
```

建议目录：

```text
/www/wwwroot/wx-coupon-prod
```

需要保护的目录和文件：

```text
.env
runtime-data/
logs/
backups/
```

不要从开发机或旧服务器直接覆盖这些目录，除非正在执行明确的迁移或恢复动作。

## 2. 全新部署

```bash
mkdir -p /www/wwwroot
cd /www/wwwroot
git clone https://github.com/huanyu666/WeChat-Coupon-Backend.git wx-coupon-prod
cd wx-coupon-prod
git checkout docker版
cp .env.docker.example .env
```

编辑 `.env`，至少确认：

```bash
WX_HTTP_PORT=8080
WX_SERVICE_ENV=prod
WX_SERVICE_DEPLOYMENT_NAME=wx-coupon-prod
GO_SHORTLINK_PUBLIC_BASE_URL=http://你的服务器IP:8080
WX_SERVICE_REDIS_URL=redis://redis:6379/0
```

启动：

```bash
./scripts/docker_prod_up.sh
./status.sh prod
```

登录后台后配置：

```text
/wechat-account-settings
/system-settings
```

账号级配置在 `/wechat-account-settings`，全局业务配置在 `/system-settings`。最终版不要求日常手改 TOML/JSON。

## 3. 从旧服务器迁移

旧服务器：

```bash
cd /www/wwwroot/wx-coupon-prod
./export_migration.sh
```

将 `backups/migration-exports/*.tar.gz` 复制到新服务器。

新服务器：

```bash
cd /www/wwwroot/wx-coupon-prod
python3 scripts/import_migration_package.py inspect /path/to/迁移包.tar.gz
./import_migration.sh /path/to/迁移包.tar.gz
./scripts/docker_prod_up.sh
./status.sh prod
```

导入后必须确认：

```text
runtime-data/config.toml
runtime-data/wechat_accounts.runtime.json
runtime-data/system_settings.runtime.json
runtime-data/merchant_coupons/merchant_coupons.db
runtime-data/site-verification/
```

如果迁移包中没有站点认证文件，`site-verification/` 为空是允许的。

## 4. 验收检查

轻量检查：

```bash
./status.sh prod
```

业务检查：

```bash
python3 scripts/business_smoke_check.py --base-url http://127.0.0.1:8080
```

有后台和公众号凭据时：

```bash
export WX_SMOKE_ADMIN_USERNAME='管理员账号'
export WX_SMOKE_ADMIN_PASSWORD='管理员密码'
export WX_SMOKE_WECHAT_TOKEN='公众号Token'
export WX_SMOKE_WECHAT_ACCOUNT_ID='gh_xxx'
python3 scripts/business_smoke_check.py --base-url http://127.0.0.1:8080
```

验收标准：

```text
/index 200
/material 200
/wechat-account-settings 200
/system-settings 200
/migration 200
未登录 /api/auth/verify 401
未登录 /api/dashboard/overview 401
未登录 /api/migration/status 401
迁移 export 成功
微信签名校验和 subscribe 回复 smoke 成功
```

## 5. 发布更新

生产发布固定顺序：

```bash
./backup.sh
git pull origin docker版
./scripts/docker_prod_up.sh
./status.sh prod
python3 scripts/business_smoke_check.py --base-url http://127.0.0.1:8080
./scripts/docker_prod_logs.sh
```

只同步代码，不覆盖：

```text
.env
runtime-data/
logs/
backups/
```

## 6. 回滚恢复

查看备份：

```bash
ls -lh backups/
```

恢复：

```bash
./restore.sh backups/你的备份.tar.gz
./restore.sh backups/你的备份.tar.gz --yes
./scripts/docker_prod_up.sh
./status.sh prod
```

恢复后重新跑业务 smoke。

## 7. 日志和排错

容器状态：

```bash
docker compose ps
```

日志：

```bash
./scripts/docker_prod_logs.sh
```

健康检查：

```bash
curl -fsS http://127.0.0.1:8080/healthz
curl -fsS http://127.0.0.1:8080/readyz
```

端口冲突时修改 `.env` 的 `WX_HTTP_PORT` 和 `GO_SHORTLINK_PUBLIC_BASE_URL`，然后重建。

## 8. 安全边界

- 不提交 `.env`、`runtime-data/`、`logs/`、`backups/`。
- 不把真实公众号密钥、管理员密码、迁移包发到公开渠道。
- 迁移包可能包含真实配置和数据库，传输后按生产数据保管。
- 如果历史代码曾出现真实密钥，部署完成后应轮换密钥。
