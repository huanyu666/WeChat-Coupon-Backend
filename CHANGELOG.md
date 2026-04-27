# 修改日志

## 2026-04-27

### 2026-04-27 15:11
- **文件** `README.md`（新增），`utils/redis_async.py`，`docs/SERVER_MIGRATION_AND_DEPLOY.md`
- **变更** 新增根目录 `README.md`，提供完整的 Linux 与 Windows 部署指南；将 `utils/redis_async.py` 中 Redis 连接参数（socket 路径、密码、DB 编号）改为优先读取环境变量（`REDIS_SOCKET_PATH`、`REDIS_PASSWORD`、`REDIS_DB`），保留原值作为默认值；同步更新迁移文档，标记 Redis 环境变量化已完成。
- **原因** 仓库缺少部署文档导致用户无法上手；Redis 配置硬编码不利于多环境部署与安全。
- **影响** 不影响已有部署（默认值与原硬编码一致）；新部署可通过环境变量灵活配置 Redis 连接。

## 2026-04-25
- **初始化机制** 新增 `CHANGELOG.md`，作为项目后续每次代码修改的统一记录位置。
- **记录要求** 后续每次实际修改代码时，同步追加记录修改文件、变更内容、变更原因和影响范围。

### 2026-04-25 16:05
- **文件** `wechat_account_store.py`, `config/config.py`, `config/__init__.py`, `routes/material.py`, `routes/wechat.py`, `html/wechat_account_settings.html`, `html/index.html`, `config.toml`, `.gitignore`, `wechat_accounts.runtime.json`
- **变更** 新增网页可维护的多公众号账号存储与管理 API/页面；运行时改为优先读取网页配置；为现有公众号生成运行时账号文件；首页新增入口；移除 `config.toml` 与默认配置中的公众号敏感字段。
- **原因** 需要把 `app_secret`、`token` 等敏感信息从代码配置中剥离，并支持在网页中维护多个公众号配置。
- **影响** 后续公众号账号信息应通过 `/wechat-account-settings` 页面维护；业务型账号配置仍保留在 `config.toml`；运行时账号文件已加入 `.gitignore`，避免敏感信息误提交。

## 记录模板

### YYYY-MM-DD HH:MM
- **文件** `path/to/file`
- **变更** 简述本次修改内容
- **原因** 简述为什么需要这次修改
- **影响** 简述影响范围、兼容性或注意事项
