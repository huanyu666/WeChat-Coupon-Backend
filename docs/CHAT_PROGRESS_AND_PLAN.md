# wx-coupon 项目交接文档

> 状态提示：这是 2026-04-29 的历史交接记录，保留用于追溯当时迁服和后台化过程。当前状态请优先看 `docs/CURRENT_STATUS.md`、`docs/VERSION_INVENTORY_2026-05-04.md` 和 `docs/AI_DEVELOPMENT_HANDOFF.md`。

最后更新：2026-04-29
开发目录：`/www/wwwroot/wx-coupon-dev`
生产目录：`/www/wwwroot/wx-coupon-prod`
当前主线：把现有微信公众号优惠券项目改造成“像 acg-faka 一样容易部署、迁移、配置和维护”的 Docker 化服务。

## 1. 给下一轮 AI 的快速结论

这是一个已经在服务器上跑通的 FastAPI + Redis + meituan-query 项目，不是从零开始。继续开发前先读本文件，优先使用现有脚本和后台页面，不要重新发明部署流程。

当前状态：

- dev 环境：`/www/wwwroot/wx-coupon-dev`，本机端口 `127.0.0.1:18080`，公网不暴露。
- prod 环境：`/www/wwwroot/wx-coupon-prod`，公网后台 `http://154.219.115.75/index`，微信回调 `http://154.219.115.75/wechat`。
- 当前暂时不用域名，使用 IP 地址。
- 微信审核/校验已通过，用户已把一个公众号 URL 切到新系统并测试消息回复，消息记录进入 `http://154.219.115.75/index` 面板。
- 后台已支持公众号业务配置、折叠配置区、站点认证文件、运行环境识别、数据迁移包导出/导入。
- 生产最新健康检查通过：`./status.sh prod` OK。
- 生产迁移功能已部署：`http://154.219.115.75/migration`。

重要规则：

- 不要提交或泄露 `.env`、`runtime-data/`、`logs/`、`backups/`、真实 `config.toml`、真实公众号密钥。
- 不要还原用户或其他 AI 已做的工作；当前 Git 工作树有大量历史运行文件/虚拟环境/缓存取消跟踪痕迹，未确认前不要清理。
- 生产目录是独立部署目录，改 dev 后如需上线，要谨慎同步必要代码到 prod，先备份，再重建容器，再跑 `./status.sh prod`。
- 用户倾向让系统尽量在后台面板配置，不要让普通业务能力必须改代码或改文件才能启用。

## 2. 项目目标

用户的最终体验目标：

```text
拉代码或上传代码
填必要配置
一键安装
后台面板配置公众号和业务功能
换服务器时一键导出所有数据，新服务器一键导入
后续可直接在服务器上开发和测试
```

技术路线已经确定：

```text
底层：Docker 固定 Python、Redis、meituan-query、socket 等复杂依赖
上层：提供 install/update/status/check/configure/wizard/migration 等简单入口
业务：尽量后台化配置，减少改代码、改 TOML、改 JSON 的需求
```

本项目不适合完全照搬 PHP 裸部署，因为依赖包括：

- Python FastAPI
- Redis TCP
- Redis Unix socket
- Go/meituan-query 内部 socket
- 微信公众号消息回调
- 运行时 JSON / SQLite 数据
- 宝塔 / Nginx / OpenResty 反代
- 美团/点评/商家券/小程序解析等业务处理器

## 3. 当前环境

### dev 环境

```text
目录：/www/wwwroot/wx-coupon-dev
Compose project：wx-coupon-dev
访问：仅本机 http://127.0.0.1:18080
容器：app healthy，redis healthy
配置来源：runtime-data/config.toml，容器内为 /data/config.toml
用途：开发和验证，不作为公网入口
```

常用检查：

```bash
cd /www/wwwroot/wx-coupon-dev
./status.sh dev
./check.sh
```

### prod 环境

```text
目录：/www/wwwroot/wx-coupon-prod
Compose project：wx-coupon-prod
公网后台：http://154.219.115.75/index
微信回调：http://154.219.115.75/wechat
Docker 端口：0.0.0.0:8080 -> app:80
宝塔/Nginx：把 http://154.219.115.75 反代到 127.0.0.1:8080
配置来源：runtime-data/config.toml，容器内为 /data/config.toml
```

常用检查：

```bash
cd /www/wwwroot/wx-coupon-prod
./status.sh prod
curl -i http://127.0.0.1:8080/healthz
curl -i http://127.0.0.1:8080/readyz
curl -i http://154.219.115.75/wechat
```

不要再把 `http://154.219.115.75:18080/index` 当作公网后台。`18080` 是 dev 本机端口，现在已限制为 `127.0.0.1`。

## 4. 当前运行数据存储

当前有效运行数据以 `runtime-data/` 为主：

```text
runtime-data/config.toml
runtime-data/wechat_accounts.runtime.json
runtime-data/merchant_coupons/merchant_coupons.db
runtime-data/activation_codes.json
runtime-data/activation_codes_link.json
runtime-data/activation_codes_meituan_order.json
runtime-data/scenes.json
runtime-data/p_values.json
runtime-data/site-verification/
```

容器内挂载：

```text
host runtime-data/ -> container /data
host logs/        -> container /logs
```

说明：

- 主配置、管理员账号、公众号运行配置、业务开关、激活码、场景值、P 值、商家券数据库都应进入迁移包。
- Redis 主要用于临时缓存、短期 token/payload 等，不作为当前核心迁移数据。
- `logs/` 和 `backups/` 不进入业务迁移包。
- prod 旧根目录的 `activation_codes*.json`、`p_values.json`、`scenes.json` 已补复制到 `runtime-data/`。
- prod 旧根目录的 `merchant_coupons/` 已按用户确认删除；真实商家券库是 `runtime-data/merchant_coupons/merchant_coupons.db`。

## 5. 已完成能力

### Docker 与部署脚本

已完成：

- `Dockerfile`
- `docker-compose.dev.yml`
- `docker-compose.yml`
- `install.sh`
- `update.sh`
- `status.sh`
- `doctor.sh`
- `preflight.sh`
- `check.sh`
- `configure.sh`
- `backup.sh`
- `restore.sh`

核心命令：

```bash
./install.sh dev
./install.sh prod --port 8080 --shortlink-base-url http://154.219.115.75
./update.sh dev
./update.sh prod
./status.sh dev
./status.sh prod
./check.sh
```

Redis 同时提供：

```text
redis://redis:6379/0
/run/redis/redis-server.sock
```

meituan-query 内部 socket：

```text
/run/wx_service/meituan-query-internal.sock
```

### 配置工具和安装向导

已完成：

- `scripts/configure_env.py`
- `configure.sh`
- `scripts/install_wizard.py`
- `wizard.sh`

Web 安装向导是临时工具，默认监听本机并使用 token，不要挂到主业务服务公网暴露。

启动示例：

```bash
./wizard.sh --host 127.0.0.1 --port 18081
```

### 初始化和诊断脚本

已完成：

- `scripts/init_admin_user.py`
- `scripts/init_wechat_account.py`
- `scripts/business_smoke_check.py`
- `wechat_check.sh`
- `proxy_check.sh`
- `setup_proxy.sh`
- `site_verify.sh`
- `migrate_runtime.sh`

常用：

```bash
python3 scripts/init_admin_user.py --username admin --password '你的安全密码'
python3 scripts/init_wechat_account.py --data-dir runtime-data --account-id gh_xxx --appid wx_xxx --name '公众号名称'
./wechat_check.sh http://154.219.115.75/wechat --token TOKEN --encoding-aes-key AES_KEY --appid APPID
./proxy_check.sh prod http://154.219.115.75
./site_verify.sh add MP_verify_xxx.txt '文件内容'
```

### 后台工作台

入口：

```text
dev：http://127.0.0.1:18080/index
prod：http://154.219.115.75/index
```

已完成：

- 登录后台。
- 工作台展示 DEV/PROD 环境、后台入口、微信回调 URL、配置来源。
- 避免 `8080` 和 `18080` 两个后台混淆。
- 工作台快捷入口包括公众号配置、数据迁移、素材上传、订单查询、重载配置等。

### 公众号配置后台化

入口：

```text
/wechat-account-settings
```

已完成：

- 公众号账号基础配置后台维护。
- 关键词回复后台维护。
- 默认回复、欢迎语、授权用户、URL 模式后台维护。
- 启用文本处理器后台维护。
- 启用小程序 appid 后台维护。
- 美团小程序卡片回复配置后台维护。
- 美团小程序链接解析回复配置后台维护。
- 美团/点评链接识别配置后台维护。
- 商家券查看回复配置后台维护。
- 商家券列表自定义提示语后台维护。
- 菜单 CLICK 事件回复后台维护。
- 保存后热加载运行时配置。
- 配置页增加折叠/展开能力，降低长页面滚动负担。

已经修过的重要问题：

- 新增公众号无法配置小程序解析，需要改代码的问题已处理。
- `gh_bdfb8233fa30` 出现“小程序解析打开保存后仍提示该公众号暂未配置美团优惠功能”的问题已修复，原因是空运行时 `meituan_base_url` 覆盖了旧有效配置。
- 部分处理器静态持有旧配置，保存后仍读 TOML 的问题已修复。
- 折叠功能最初被 `.field { display:flex }` 覆盖，后改为 `.collapsible-hidden { display:none!important; }`。

用户已测试通过：

- 美团小程序卡片。
- 美团短链。
- 保存商家券 / 我的商家券。
- 菜单点击回复。
- 关键词回复。
- 微信公众号消息回复。

### 微信回调和风控处理

相关历史：

- 原系统位置：`http://150.40.180.94/wechat`。
- 新系统生产回调：`http://154.219.115.75/wechat`。
- 微信后台保存 URL 时曾出现 `invalid args,200002`，后来判断主要是 IP/URL 风控，不是回调校验代码问题。
- 用户对 `http://154.219.115.75:8080/index` 做过申诉。
- 申诉后微信审核通过，用户已切换一个公众号 URL 并测试消息回复成功。

已验证：

- `http://154.219.115.75/wechat` 明文 GET 校验通过。
- AES 安全模式 GET 校验通过。
- subscribe POST 文本回复 smoke 通过。

### 站点认证文件

已完成：

- `runtime-data/site-verification/` 运行时目录管理站点认证 txt。
- 根路径 `/{filename}.txt` 可直接读取认证文件，不用改代码、不用重建镜像。
- 支持 `site_verify.sh add/list/remove`。
- Web 安装向导也接入了站点认证文件添加、列表、删除。

### 数据迁移包

入口：

```text
prod：http://154.219.115.75/migration
dev：http://127.0.0.1:18080/migration
```

已完成：

- 新增 `utils/runtime_migration.py`。
- 新增 `routes/migration.py`。
- 新增 `html/migration.html`。
- 新增 `scripts/export_migration_package.py`。
- 新增 `scripts/import_migration_package.py`。
- 新增 `export_migration.sh`。
- 新增 `import_migration.sh`。

后台接口：

```text
GET  /api/migration/status
GET  /api/migration/export
POST /api/migration/import
POST /api/migration/inspect
```

能力：

- 后台一键导出迁移包。
- 后台一键导入迁移包。
- 导入前自动备份当前运行数据。
- 迁移包包含 `runtime-data` 下的核心业务数据。
- 导出可选包含 `.env`，但默认不包含，避免跨服务器误覆盖部署环境。
- 命令行导出兼容旧根目录运行文件，会放到 `legacy_project_root/`；导入时 `runtime_data/` 优先。
- SQLite 数据库导出使用在线备份方式，降低运行中复制数据库风险。
- 导入校验 tar 路径和条目类型，拒绝不安全路径、软链、特殊文件。
- 自动跳过迁移自动备份目录，避免递归打包。

命令行：

```bash
./export_migration.sh
./export_migration.sh --include-env
./import_migration.sh backups/migration-exports/xxx.tar.gz
./import_migration.sh backups/migration-exports/xxx.tar.gz --yes
```

验证：

- dev 宿主机导出、导入预览、临时目录导入通过。
- dev 容器内认证覆盖测试 `/api/migration/status`、`/api/migration/export`、`/api/migration/import` 通过。
- prod `/migration` 返回 200。
- prod `/api/migration/status` 未登录返回 401，权限正常。
- prod 容器内认证覆盖测试导出通过。
- prod `./status.sh prod` 通过。

最近生产备份：

```text
/www/wwwroot/wx-coupon-prod/backups/wx-runtime-backup-20260429-075903.tar.gz
/www/wwwroot/wx-coupon-prod/backups/wx-runtime-backup-20260429-080211.tar.gz
```

## 6. 安全和代码审查已修复问题

记录文件：

```text
docs/CODE_REVIEW_REMEDIATION_2026-04-28.md
```

已完成：

- 移除/替换硬编码代理 API、Redis 默认密码、旧脚本 AppSecret/Token 等敏感信息。
- 管理端公众号敏感字段改为写入型，不再从 API 返回明文。
- 素材上传增加类型、扩展名、大小校验。
- 上传结果渲染增加 HTML 转义。
- 登录接口增加失败限流。
- 微信回调日志去除原始 XML / 解密 XML / 回复 XML 预览。
- XML 解析优先使用 `defusedxml`。
- 微信 AES Key 增加格式校验。
- 加密随机前缀改为随机字节。
- `/youxi` 缺失模板改为 404。
- `config.toml` 不再复制进 Docker 镜像，容器优先读 `/data/config.toml`。
- dev/prod 启动脚本在首次需要时把旧根目录 `config.toml` 迁移到 `runtime-data/config.toml`。
- 后台工作台、`/healthz`、`/readyz` 增加运行环境识别。
- dev 端口限制到 `127.0.0.1:18080`。

仍需处理：

- 历史上提交或暴露过的真实密钥仍建议轮换。
- 后台登录态还在使用 `localStorage` Bearer Token，后续建议迁移到 HttpOnly SameSite Cookie。
- 需要补后台操作审计日志。

## 7. 当前 Git / 文件状态提醒

当前工作树不是干净状态。常见现象：

- `.venv/`、`__pycache__/`、根目录运行 JSON/DB、旧 `config.toml` 等有大量删除/取消跟踪痕迹。
- 这些大多是前面安全审查时将运行时文件、虚拟环境和缓存从 Git 索引移除导致。
- 不要随手 `git reset --hard`。
- 不要随手 `git checkout -- .`。
- 不要删除 `.env`、`runtime-data/`、`logs/`、`backups/`。
- 如果要提交，先单独确认要纳入的代码文件，不要混入真实运行数据。

新增迁移功能相关文件当前应保留：

```text
utils/runtime_migration.py
routes/migration.py
html/migration.html
scripts/export_migration_package.py
scripts/import_migration_package.py
export_migration.sh
import_migration.sh
```

## 8. 生产部署操作原则

上线前：

```bash
cd /www/wwwroot/wx-coupon-prod
./backup.sh
```

同步代码时只同步必要文件，不要覆盖：

```text
.env
runtime-data/
logs/
backups/
真实 config.toml
```

上线后：

```bash
cd /www/wwwroot/wx-coupon-prod
scripts/docker_prod_up.sh
./status.sh prod
curl -i http://127.0.0.1:8080/migration
curl -i http://127.0.0.1:8080/api/migration/status
```

预期：

- `/migration` 未登录也应返回 HTML 200。
- `/api/migration/status` 未登录应返回 401。
- `/healthz`、`/readyz` 应返回 OK。

## 9. 接下来建议开发计划

优先级 1：完善迁移和备份闭环

- 后台导入前增加“预览迁移包内容”交互，复用现有 `/api/migration/inspect`。
- 后台最近迁移包列表增加下载按钮。
- 支持从“导入前自动备份”一键回滚。
- 把旧 `backup.sh` / `restore.sh` 逐步统一到新的迁移核心逻辑，避免两套备份规则不一致。
- 做一次完整“新服务器安装 -> 导入迁移包 -> 微信回调 smoke”的演练。

优先级 2：彻底收口运行数据

- 确认所有业务数据都只从 `runtime-data/` 读取。
- 清理或归档根目录残留的运行 JSON/DB 文件，清理前先备份并确认 Git 策略。
- 明确哪些数据属于核心迁移数据，哪些只是临时缓存。
- 继续减少 legacy path 兼容分支。

优先级 3：继续后台化配置

- 扫描 Python 常量、TOML、JSON、环境变量中仍需要手改的业务开关。
- 能后台配置的接入 `/wechat-account-settings` 或新的系统设置页。
- 不适合后台配置的开关写入文档，说明原因。

优先级 4：后台体验优化

- 公众号配置页继续按类别拆分或折叠，减少长页面滚动。
- 危险操作加二次确认。
- 增加操作成功/失败提示的细节。
- 数据迁移页面增加更明确的敏感数据提示。

优先级 5：安全和稳定性

- 登录态迁移到 HttpOnly SameSite Cookie。
- 后台操作审计日志。
- 导入、删除、保存配置等高风险操作增加更严格权限校验。
- 轮换历史暴露过的密钥。
- 增加更多自动化 smoke：后台登录、公众号配置保存、迁移导出/导入、微信回调。

可选：远程 Docker 镜像

- 当前服务器本地 build 已可用。
- 后续可加 CI 构建并 push 镜像，服务器只 `docker pull`，减少生产构建时间。
- 不是当前最高优先级。

## 10. 常用文件入口

后端路由：

```text
routes/auth.py
routes/material.py
routes/wechat.py
routes/migration.py
routes/site_verification.py
```

配置和运行时：

```text
config/config.py
wechat_account_store.py
utils/path_utils.py
utils/runtime_identity.py
utils/runtime_migration.py
```

后台页面：

```text
html/dashboard.html
html/wechat_account_settings.html
html/migration.html
html/material_upload.html
```

商家券：

```text
text_processors/merchant_coupon_processor.py
utils/merchant_coupon_storage.py
utils/merchant_coupon_utils.py
runtime-data/merchant_coupons/merchant_coupons.db
```

美团小程序/链接：

```text
miniprogram/
text_processors/meituan_miniprogram_link_processor.py
text_processors/meituan_link.py
text_processors/meituan_shop_query_processor.py
link_handlers/
```

脚本：

```text
scripts/docker_dev_up.sh
scripts/docker_prod_up.sh
scripts/business_smoke_check.py
scripts/migrate_runtime_data.py
scripts/export_migration_package.py
scripts/import_migration_package.py
scripts/site_verification_file.py
scripts/proxy_check.py
```

## 11. 验收清单

每次较大改动后至少跑：

```bash
cd /www/wwwroot/wx-coupon-dev
python3 -m py_compile 变更的_py文件
./status.sh dev
```

涉及生产部署时跑：

```bash
cd /www/wwwroot/wx-coupon-prod
./backup.sh
scripts/docker_prod_up.sh
./status.sh prod
```

涉及微信回调时跑：

```bash
./wechat_check.sh http://154.219.115.75/wechat --token TOKEN --encoding-aes-key AES_KEY --appid APPID
```

涉及迁移时跑：

```bash
./export_migration.sh
./import_migration.sh 路径 --target-dir /tmp/wx-coupon-import-test --yes
```

后台人工验收：

- `http://154.219.115.75/index` 能登录。
- 工作台显示 PROD，微信回调为 `http://154.219.115.75/wechat`。
- `/wechat-account-settings` 能保存当前公众号配置。
- `/migration` 能导出迁移包。
- 微信公众号消息回复仍正常。

## 12. 当前完成度

按用户最终目标估算：

```text
90% 左右
```

核心体验已经成型：

- Docker dev/prod 跑通。
- 生产 IP 入口跑通。
- 公众号回调跑通。
- 后台配置主要业务能力跑通。
- 迁移包导出/导入跑通。
- 安装、检查、备份、恢复、诊断脚本基本齐全。

剩余工作主要是：

- 迁移/备份闭环继续打磨。
- 运行数据彻底规范化和旧文件清理。
- 后台体验和安全加固。
- 自动化 smoke 覆盖更完整。
