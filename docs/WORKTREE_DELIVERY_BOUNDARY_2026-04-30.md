# wx-coupon 工作树交付边界

生成时间：2026-04-30 06:45 UTC
目录：`/www/wwwroot/wx-coupon-dev`

## 可纳入代码交付的变更

- Docker / 环境样例：`.dockerignore`、`.gitignore`、`.env.example`、`.env.dev.example`、`.env.docker.example`、`docker-compose.dev.yml`、`docker-compose.yml`。
- 配置与运行身份：`config/__init__.py`、`config/config.py`、`utils/path_utils.py`、`utils/runtime_identity.py`。
- 迁移闭环：`utils/runtime_migration.py`、`routes/migration.py`、`html/migration.html`、`scripts/export_migration_package.py`、`scripts/import_migration_package.py`、`scripts/migration_drill_check.py`、`export_migration.sh`、`import_migration.sh`。
- 后台与认证：`routes/auth.py`、`utils/auth_utils.py`、`html/dashboard.html`、`html/material_upload.html`、`html/wechat_account_settings.html`、`routes/material.py`、`wechat_account_store.py`。
- 系统级业务配置后台化：`routes/system_settings.py`、`utils/system_settings_store.py`、`html/system_settings.html`、`config/config.py`。
- 安全与稳定性修复：`utils/crypto.py`、`utils/xml_parser.py`、`utils/redis_async.py`、`utils/proxy_utils.py`、`utils/order_rankings_link_crypto.py`。
- 业务配置后台化相关：`routes/wechat.py`、`routes/waimai.py`、`text_processors/*`、`fuwu.py`。
- 脚本与 smoke：`scripts/backup_runtime_data.py`、`scripts/restore_runtime_data.py`、`scripts/business_smoke_check.py`、`scripts/docker_dev_up.sh`、`scripts/docker_prod_up.sh`、`scripts/init_admin_user.py`。
- 文档：`docs/AI_QUICK_HANDOFF_2026-04-29.md`、`docs/CODE_REVIEW_REMEDIATION_2026-04-28.md`、`docs/NEXT_STEP_EXECUTION_2026-04-29.md`、本文件、迁服演练记录。

## 不应纳入代码交付的运行痕迹

- 虚拟环境和缓存：`.venv/`、`__pycache__/`、所有 `*/__pycache__/`。
- 根目录运行数据删除痕迹：`activation_codes*.json`、`p_values.json`、`scenes.json`、`merchant_coupons/merchant_coupons.db`。
- 旧真实配置和日志：根目录 `config.toml`、`server.log`。
- 其他敏感运行文件：`mimotion/*encrypted_tokens.data`。
- 当前策略：只记录这些状态，不恢复、不清理、不提交真实运行数据。

## 最终版收口说明

- 后台鉴权固定为 HttpOnly Cookie-only；登录接口不再返回 token。
- 运行数据固定为 `runtime-data/`；根目录 legacy JSON/DB 仅作为历史痕迹记录，不再作为运行时数据源。
- `xiaoxi.py`、`text_processors/xiaoxi.py` 已退役，不再属于受支持运行入口。

## 推荐提交边界

- 第一组提交：Docker、运行目录、迁移闭环、备份恢复脚本。
- 第二组提交：后台配置页、系统设置页、Cookie-first 登录态、安全修复。
- 第三组提交：文档和交接记录。
- 不在本轮提交中处理历史运行数据删除痕迹，避免误删或误恢复敏感数据。
