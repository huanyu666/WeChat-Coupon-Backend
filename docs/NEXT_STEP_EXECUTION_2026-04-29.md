# wx-coupon 下一步计划执行记录

> 状态提示：这是 2026-04-29 的历史执行记录，不代表当前待办清单。当前状态和后续开发边界请优先看 `docs/CURRENT_STATUS.md`、`docs/VERSION_INVENTORY_2026-05-04.md` 和 `docs/AI_DEVELOPMENT_HANDOFF.md`。

执行时间：2026-04-29 08:45-08:55 UTC
dev：`/www/wwwroot/wx-coupon-dev`
prod：`/www/wwwroot/wx-coupon-prod`

## 本轮完成

- 已确认 dev/prod 健康检查通过，`/healthz`、`/readyz`、业务 smoke 均 OK；未登录受保护 API 返回 401。
- 已将 dev 更新版迁移核心同步到 prod：`utils/runtime_migration.py`、`routes/migration.py`、`html/migration.html`，补齐迁移包列表 `kind`、下载、导入前预览、一键回滚和审计日志能力。
- 已把主要后台页改为 Cookie-first 登录态：`html/dashboard.html`、`html/material_upload.html`、`html/wechat_account_settings.html`、`html/migration.html`。旧 `localStorage` Bearer Token 继续兼容，但不再因为本地 token 为空直接拒绝同源 Cookie 会话。
- 已重建并重启 dev/prod 容器；prod 当前公开入口仍为 `http://154.219.115.75`，未做域名或 HTTPS 改动。

## 迁移演练

- dev 导出迁移包：`backups/migration-exports/wx-coupon-migration-runtime-20260429-084946.tar.gz`，未包含 `.env`。
- dev 迁移包已完成 dry-run inspect，并导入到临时目录 `/tmp/wx-coupon-migration-import.ixdjo5`。
- prod 导出迁移包：`backups/migration-exports/wx-coupon-migration-runtime-20260429-085118.tar.gz`，未包含 `.env`。
- prod 迁移包已完成 dry-run inspect，并导入到临时目录 `/tmp/wx-coupon-prod-migration-import.omardO`。
- 两次临时导入均恢复了核心文件：`config.toml`、`wechat_accounts.runtime.json`、`activation_codes*.json`、`scenes.json`、`p_values.json`、`merchant_coupons/merchant_coupons.db`。

## 生产备份

- 同步迁移核心前运行数据备份：`backups/wx-runtime-backup-20260429-085024.tar.gz`。
- 首次 prod 重建脚本自动备份：`backups/wx-runtime-backup-20260429-085039.tar.gz`。
- 同步后台页面后 prod 重建脚本自动备份：`backups/wx-runtime-backup-20260429-085437.tar.gz`。
- 同步迁移核心前代码备份：`backups/code-migration-core-before-sync-20260429-085035.tar.gz`。
- 同步后台页面前代码备份：`backups/code-admin-pages-before-cookie-sync-20260429-085433.tar.gz`。

## 验证结果

- dev：`./status.sh dev` 通过；`/index`、`/material`、`/wechat-account-settings`、`/migration` 返回 200；未登录 `/api/dashboard/overview` 返回 401。
- prod：`./status.sh prod` 通过；`/index`、`/material`、`/wechat-account-settings`、`/migration` 返回 200；未登录 `/api/dashboard/overview` 返回 401。
- 修改过的内联 JS 已用 `node --check` 校验：dashboard、素材上传、公众号配置、迁移页面均通过。
- Python AST 检查通过：迁移、认证、素材、微信、配置、公众号运行配置相关模块。

## 工作树分组

- 应纳入代码变更的新增迁移文件：`utils/runtime_migration.py`、`routes/migration.py`、`html/migration.html`、`scripts/export_migration_package.py`、`scripts/import_migration_package.py`、`export_migration.sh`、`import_migration.sh`。
- 应纳入代码变更的后台/认证/运行数据相关改动：`routes/auth.py`、`utils/auth_utils.py`、`html/dashboard.html`、`html/material_upload.html`、`html/wechat_account_settings.html`、`config/config.py`、`wechat_account_store.py` 等。
- 历史运行数据/缓存取消跟踪痕迹仍存在，不要恢复或清理：`.venv/`、`__pycache__/`、根目录 `activation_codes*.json`、`config.toml`、`p_values.json`、`scenes.json`、`merchant_coupons/merchant_coupons.db`、`server.log`、`mimotion/*encrypted_tokens.data`。

## 后续事项

- 运行数据当前以 `runtime-data/` 为主；激活码、Scene、P 值、商家券仍保留 root legacy 自动迁移兼容，可在下一轮确认无旧部署依赖后逐步删除兼容分支。
- `html/index.html` 仍是历史混淆版页面并使用 `localStorage` token；当前 `/index` 实际返回新版 `dashboard.html`，后续可归档或替换历史文件。
- 后台登录接口目前仍在 JSON 响应中返回 token 以兼容旧页面；待所有后台页确认 Cookie-first 后，可再收紧为只设置 HttpOnly Cookie。
