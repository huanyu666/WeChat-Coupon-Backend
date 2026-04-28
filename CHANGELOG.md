# 修改日志

## 2026-04-25
- **初始化机制** 新增 `CHANGELOG.md`，作为项目后续每次代码修改的统一记录位置。
- **记录要求** 后续每次实际修改代码时，同步追加记录修改文件、变更内容、变更原因和影响范围。

### 2026-04-25 16:05
- **文件** `wechat_account_store.py`, `config/config.py`, `config/__init__.py`, `routes/material.py`, `routes/wechat.py`, `html/wechat_account_settings.html`, `html/index.html`, `config.toml`, `.gitignore`, `wechat_accounts.runtime.json`
- **变更** 新增网页可维护的多公众号账号存储与管理 API/页面；运行时改为优先读取网页配置；为现有公众号生成运行时账号文件；首页新增入口；移除 `config.toml` 与默认配置中的公众号敏感字段。
- **原因** 需要把 `app_secret`、`token` 等敏感信息从代码配置中剥离，并支持在网页中维护多个公众号配置。
- **影响** 后续公众号账号信息应通过 `/wechat-account-settings` 页面维护；业务型账号配置仍保留在 `config.toml`；运行时账号文件已加入 `.gitignore`，避免敏感信息误提交。

### 2026-04-27 20:39
- **文件** `utils/redis_async.py`, `utils/encrypted_payload_utils.py`, `utils/go_local_api.py`, `main.py`, `scripts/migration_smoke_check.py`, `scripts/backup_runtime_data.py`, `.env.example`, `deploy/systemd/wx-service.service.example`, `deploy/openresty/wx-coupon.conf.example`, `.gitignore`, `docs/SERVER_MIGRATION_AND_DEPLOY.md`
- **变更** 将 Redis 连接参数外置到环境变量；为 `/healthz` 和 `/readyz` 增加 Redis/Go 运行时诊断；新增迁服 smoke check 脚本；新增运行时数据备份脚本；补充 `.env.example`、systemd 服务模板和 OpenResty 反代模板；同步更新迁服文档并忽略 `backups/`。
- **原因** 当前阶段以迁服优先、验证优先为主，需要把新服务器部署、健康检查和运行时数据备份链路尽快补齐，降低迁服时的配置遗漏和排查成本。
- **影响** 新服务器可直接复用模板配置环境变量、systemd 和 OpenResty；可通过脚本快速做健康检查和运行时数据备份；现有业务逻辑未改动，但部署与运维流程更可落地。

## 记录模板

### YYYY-MM-DD HH:MM
- **文件** `path/to/file`
- **变更** 简述本次修改内容
- **原因** 简述为什么需要这次修改
- **影响** 简述影响范围、兼容性或注意事项
