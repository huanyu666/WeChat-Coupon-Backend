# AI / 开发者接手说明

本文件给后续 AI 或开发者快速接手使用。当前项目已经收口到最终版基线：Docker 部署、Cookie-only 后台鉴权、runtime-only 数据、迁移闭环、系统设置后台化。

## 当前基线

- 分支：`docker版`
- 生产部署建议目录：`/www/wwwroot/wx-coupon-prod`
- 开发目录：`/www/wwwroot/wx-coupon-dev`
- 后台登录：HttpOnly Cookie-only
- 首次管理员：无管理员时 `/login` 显示浏览器初始化页，成功后入口自动关闭
- 运行数据：`runtime-data/`
- 账号级配置：`/wechat-account-settings`
- 全局业务配置：`/system-settings`
- 迁移接口：`/api/migration/*`
- 迁移命令：`./export_migration.sh`、`./import_migration.sh`

已退役入口：

```text
xiaoxi.py
text_processors/xiaoxi.py
```

不要重新把它们作为正式入口引入。

## 禁止提交

任何时候都不要提交：

```text
.env
runtime-data/
logs/
backups/
config.toml
*.db
*.db-wal
*.db-shm
mimotion/token_cache/
mimotion/encrypted_tokens.data
```

不要使用 `git add .`。必须按文件白名单 staging。

## 常用验证

Python 语法：

```bash
python3 -m py_compile $(git diff --name-only -- '*.py') $(git ls-files --others --exclude-standard -- '*.py')
```

开发健康检查：

```bash
./status.sh dev
```

生产健康检查：

```bash
./status.sh prod
```

迁服演练：

```bash
python3 scripts/migration_drill_check.py --startup-check
```

业务 smoke：

```bash
python3 scripts/business_smoke_check.py --base-url http://127.0.0.1:18080
```

## Git 操作纪律

提交前检查：

```bash
git status --short
git diff --check
git diff --cached --name-status
```

发布前固定顺序：

```text
backup.sh -> 同步代码 -> docker_prod_up.sh -> status.sh prod -> business smoke -> 观察日志
```

如果远程地址包含 token，先改回普通 HTTPS：

```bash
git remote set-url origin https://github.com/huanyu666/WeChat-Coupon-Backend.git
```

## 修改边界

- 业务配置优先进入后台页面，不新增手改 TOML/JSON 的日常流程。
- 新运行数据必须落在 `runtime-data/`，不要回到仓库根目录。
- 后台受保护接口只依赖 Cookie 会话，不恢复 Bearer token 登录。
- 首次管理员设置只允许在 `admin_users` 为空时创建第一个账号，不要改成长期开放注册入口。
- 迁移包必须覆盖新增的运行时配置文件。
- Smoke 脚本读取凭据只能来自环境变量或本机 `.env`，不能写入仓库。
