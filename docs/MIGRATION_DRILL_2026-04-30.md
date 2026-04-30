# wx-coupon 完整迁服演练记录

执行时间：2026-04-30 06:43 UTC
演练目录：`/www/wwwroot/wx-coupon-migration-drill-20260430-064352`
Compose project：`wx-coupon-drill`
端口：`127.0.0.1:18082`

## 输入

- 代码来源：`/www/wwwroot/wx-coupon-dev`，复制时排除 `.git/`、`.env`、`.venv/`、`runtime-data/`、`logs/`、`backups/`、缓存目录。
- 迁移包来源：prod 导出包 `backups/migration-exports/wx-coupon-migration-runtime-20260430-064352.tar.gz`。
- 迁移包不包含 `.env`。

## 步骤

- 在独立目录复制代码。
- 执行 `./import_migration.sh <prod-migration-package> --yes` 导入运行数据。
- 使用独立环境变量启动：`COMPOSE_PROJECT_NAME=wx-coupon-drill`、`WX_HTTP_PORT=18082`、`WX_SERVICE_DEPLOYMENT_NAME=wx-coupon-drill`。
- 启动后运行脚本自带 smoke，并额外验证页面、运行数据文件、商家券 DB、微信回调签名。

## 验证结果

- `healthz` / `readyz` 均返回 OK。
- `/index`、`/material`、`/wechat-account-settings`、`/migration` 均返回 200。
- 未登录 `/api/migration/status` 返回 401，符合权限预期。
- `runtime-data/` 已恢复核心文件：`config.toml`、`wechat_accounts.runtime.json`、`activation_codes*.json`、`scenes.json`、`p_values.json`、`merchant_coupons/merchant_coupons.db`。
- 商家券 SQLite 表 `merchant_coupons` 存在。
- 微信回调签名校验通过，订阅事件 smoke 返回文本 XML。

## 结论

- 当前迁移包可以支撑“新目录安装 -> 导入运行数据 -> 启动服务 -> 基础业务 smoke”的迁服流程。
- 演练未覆盖真实公网反代、域名、HTTPS、微信平台真实回调切换；这些属于后续生产迁服窗口事项。
