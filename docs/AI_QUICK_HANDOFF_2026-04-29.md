# wx-coupon AI 快速交接

最后更新：2026-04-30 06:45 UTC
开发目录：`/www/wwwroot/wx-coupon-dev`
生产目录：`/www/wwwroot/wx-coupon-prod`
详细长文档：`docs/CHAT_PROGRESS_AND_PLAN.md`

## 项目目的

这个项目是一个已经跑通的微信公众号优惠券服务，技术栈是 FastAPI + Redis + Docker + 内置 `meituan-query` 服务。当前主线不是重写业务，而是把现有系统改造成更容易部署、迁移、配置和维护的 Docker 化服务。

目标体验：

- 拉代码或上传代码后，可通过脚本一键安装/更新/检查。
- 公众号、关键词、菜单、美团/点评/商家券等业务尽量在后台配置。
- 换服务器时可一键导出运行数据，在新服务器一键导入。
- dev/prod 环境清晰区分，避免端口和配置混淆。

## 当前状态

- dev：`/www/wwwroot/wx-coupon-dev`，本机 `http://127.0.0.1:18080`，容器 `wx-coupon-dev-app` / Redis healthy。
- prod：`/www/wwwroot/wx-coupon-prod`，公网后台 `http://154.219.115.75/index`，微信回调 `http://154.219.115.75/wechat`，容器 healthy。
- 最近检查：dev/prod 的 `/healthz`、`/readyz` 均为 200；Go 内部 socket 存在；Redis 可达。
- 生产正在使用 IP，不涉及域名和 HTTPS 变更。
- 公众号回调、后台配置、消息回复、美团小程序卡片、短链、商家券、菜单点击、关键词回复等主链路已经跑通过。
- 2026-04-29 08:55 UTC 已完成迁移闭环收尾执行，记录见 `docs/NEXT_STEP_EXECUTION_2026-04-29.md`。
- 2026-04-30 已完成独立目录迁服演练，记录见 `docs/MIGRATION_DRILL_2026-04-30.md`；工作树交付边界见 `docs/WORKTREE_DELIVERY_BOUNDARY_2026-04-30.md`。

## 重要规则

- 不要泄露或提交 `.env`、`runtime-data/`、`logs/`、`backups/`、真实 `config.toml`、真实公众号密钥。
- 当前 Git 工作树不干净，存在历史 `.venv/`、`__pycache__/`、根目录运行 JSON/DB 的删除/取消跟踪痕迹；不要 `git reset --hard`，不要随手清理。
- 生产目录是独立部署目录。dev 改动上线前必须先备份 prod，只同步必要代码，不覆盖 `.env`、`runtime-data/`、`logs/`、`backups/`。
- 用户偏好：普通业务能力尽量后台化，不要要求频繁改代码/TOML/JSON。

## 已完成能力

- Docker dev/prod Compose、安装/更新/状态/诊断/备份脚本基本成型。
- 后台工作台能识别 DEV/PROD，展示后台入口、微信回调、配置来源。
- 公众号配置后台化：账号基础配置、关键词、默认回复、欢迎语、授权用户、文本处理器、小程序 appid、美团链接/小程序/商家券相关配置、菜单 CLICK 回复等。
- 运行数据主目录是 `runtime-data/`，包括配置、公众号运行配置、激活码、场景值、P 值、商家券 DB、站点认证文件。
- 站点认证文件支持 `runtime-data/site-verification/` 和 `site_verify.sh` 管理。
- 数据迁移包已支持导出/导入，默认不包含 `.env`，导入前自动备份当前运行数据。
- 安全审查已修过一批问题：敏感字段不从 API 明文返回、上传校验、登录失败限流、微信 XML 日志脱敏、XML 安全解析、AES Key 校验等。

## 最近 dev 变更

以下是 2026-04-29 最新 dev 工作，迁移核心和主要后台页面已同步到 prod：

- 新增/完善迁移闭环：
  - `utils/runtime_migration.py`
  - `routes/migration.py`
  - `html/migration.html`
  - `scripts/backup_runtime_data.py`
  - `scripts/restore_runtime_data.py`
- `/migration` 页面改为导入前必须先 inspect 预览迁移包，再确认导入。
- 最近迁移包列表增加 `kind`，支持下载；`pre-import` 自动备份支持一键回滚。
- `backup.sh` / `restore.sh` 保留入口，但底层改为复用新的迁移核心。
- 后台登录固定为 HttpOnly SameSite Cookie-only，会话不再依赖 Bearer Token。
- 迁移、回滚、公众号配置保存/删除/设默认增加审计日志，不记录敏感字段。
- 本地非 Docker 默认运行数据路径更偏向 `runtime-data/`，减少根目录运行数据继续作为主源。

已验证：

- AST 语法检查通过，避免写入 `__pycache__`。
- `./status.sh dev` 通过。
- `/migration` 页面 200。
- 未登录访问迁移 API 返回 401。
- Cookie 登录态可访问 `/api/migration/status`。
- 迁移包导出、inspect、临时目录导入、旧 `backup.sh`/`restore.sh` 预览均通过。
- prod 已同步 dev 版迁移核心与 cookie-first 后台页面，已重建并通过 `./status.sh prod`。
- 当前最终版基线不再依赖 `WX_DISABLE_LEGACY_RUNTIME_FALLBACK`、`WX_AUTH_RETURN_TOKEN` 这类兼容开关；后台鉴权固定为 Cookie-only，运行数据固定为 `runtime-data/`。

## 常用命令

dev：

```bash
cd /www/wwwroot/wx-coupon-dev
./status.sh dev
./check.sh
./export_migration.sh
./import_migration.sh backups/migration-exports/xxx.tar.gz
```

prod：

```bash
cd /www/wwwroot/wx-coupon-prod
./status.sh prod
./backup.sh
curl -i http://127.0.0.1:8080/healthz
curl -i http://127.0.0.1:8080/readyz
```

生产同步原则：

```bash
cd /www/wwwroot/wx-coupon-prod
./backup.sh
# 只同步必要代码文件，排除 .env runtime-data logs backups
scripts/docker_prod_up.sh
./status.sh prod
```

## 接下来计划

优先级 1：整理并上线当前 dev 迁移闭环改动

- 先整理 Git 工作树，确认本轮要纳入的文件。
- 在 prod 执行 `./backup.sh`。
- 同步必要代码到 prod，不覆盖运行数据。
- 重建/重启 prod 容器，跑 `./status.sh prod`。
- 验证 `/migration` 页面 200，未登录迁移 API 401。

优先级 2：做一次完整迁服演练

- 在新目录或新服务器安装服务。
- 从旧环境导出迁移包。
- 新环境导入迁移包。
- 验证后台、公众号配置、微信回调 smoke、商家券 DB、站点认证文件。

优先级 3：继续收口运行数据

- 扫描仍读写根目录 JSON/DB 的兼容分支。
- 确认哪些是核心迁移数据，哪些只是缓存。
- 逐步减少 legacy path 兼容，清理前必须先备份并确认 Git 策略。

优先级 4：继续后台化配置和安全增强

- 扫描 TOML、JSON、环境变量、Python 常量中仍需手改的业务开关。
- 能按公众号配置的放入 `/wechat-account-settings`。
- 全局配置再考虑新增系统设置页。
- 旧页面已停止依赖 `localStorage` Bearer Token，后台统一转向 HttpOnly Cookie。
- 补更多后台操作审计和自动化 smoke。

## 关键文件入口

- 应用入口：`main.py`
- 认证与工作台：`routes/auth.py`
- 公众号回调：`routes/wechat.py`
- 后台配置/素材：`routes/material.py`
- 迁移后台：`routes/migration.py`
- 运行数据迁移核心：`utils/runtime_migration.py`
- 路径工具：`utils/path_utils.py`
- 公众号运行配置存储：`wechat_account_store.py`
- 迁移页面：`html/migration.html`
