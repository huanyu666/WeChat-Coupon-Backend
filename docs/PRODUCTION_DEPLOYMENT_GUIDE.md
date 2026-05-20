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

## 2. 最快全新部署

推荐先用一键部署脚本：

```bash
mkdir -p /www/wwwroot
cd /www/wwwroot
git clone https://github.com/huanyu666/WeChat-Coupon-Backend.git wx-coupon-prod
cd wx-coupon-prod
git checkout docker版
./deploy.sh
```

如果公网地址识别不对，显式指定：

```bash
./deploy.sh --public-url http://你的服务器IP:8080
```

如果已经有正式短链域名，直接在部署时写进去：

```bash
./deploy.sh --public-url https://98vx.cn
```

如果需要把默认短链有效期一起写入部署结果：

```bash
./deploy.sh --public-url https://98vx.cn --shortlink-ttl-seconds 604800
```

脚本会创建 `.env`、写入 `runtime-data/system_settings.runtime.json` 里的短链默认配置、启动容器、执行 `status.sh prod`，最后打印后台登录地址。全新部署在浏览器里创建第一个管理员。

部署完成后常用入口：

```text
主后台:        https://你的域名/login
客户登录注册:  https://你的域名/web/login
客户订单查询:  https://你的域名/web/query
客户管理后台:  https://你的域名/web/admin
商家查询登录:  https://你的域名/web/shop-login
商家查询页:    https://你的域名/web/shop-query
```

`/web/*` 是 `meituan-query` 提供的客户查询 Web。它不需要单独容器，生产部署会在同一个 `app` 容器内启动 `meituan-query`，FastAPI 再把 `/web/*` 代理到 `/run/wx_service/meituan-query.sock`。

## 3. 手动全新部署

如果需要手动控制配置：

```bash
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

这一步只解决容器环境变量。短链实际读取优先级是：

1. `runtime-data/system_settings.runtime.json` 的 `shortlink_config`
2. `.env` 里的 `GO_SHORTLINK_PUBLIC_BASE_URL`

说明：

- prod 应配置为真实可访问的正式域名或公网地址
- dev 可以使用 `http://127.0.0.1:18080` 或单独的测试域名
- 不要求 dev 与 prod 共享同一个短链公开域名

如果要手动把默认短链域名和 TTL 写入运行时配置，执行：

```bash
python3 scripts/configure_shortlink_settings.py \
  --public-base-url https://98vx.cn \
  --ttl-seconds 604800
```

启动：

```bash
./scripts/docker_prod_up.sh
./status.sh prod
```

全新部署且没有导入迁移包时，第一次打开 `/login` 会显示“首次设置管理员”。在浏览器里创建第一个管理员后，初始化入口自动关闭。

如果是迁移部署，后台账号会随迁移包恢复，直接使用旧账号登录。

注意账号边界：

- `/login` 是主后台账号，用于公众号、系统设置、短链和迁移等管理。
- `/web/login` 是客户查询系统账号，用于客户注册和订单查询。
- `/web/admin` 是客户查询系统管理员后台，用于审核客户账号、分配查询次数、管理客户用户。
- 主后台管理员和客户查询系统管理员是两套账号，不共享登录态。

登录后台后配置：

```text
/wechat-account-settings
/system-settings
```

短链相关配置：

- `/system-settings` -> 短链设置：可修改短链公开域名和默认有效期
- 短链路径固定为 `/key/{code}`
- 过期清理固定为 `Asia/Shanghai` 每天 `00:00`

账号级配置在 `/wechat-account-settings`，全局业务配置和管理员账号管理在 `/system-settings`。最终版不要求日常手改 TOML/JSON，也不要求用命令行创建或重置管理员。

管理员密码重置：

```text
/system-settings -> 管理员账号
```

该功能只对已登录管理员开放；未登录状态不能远程重置管理员。

## 4. 从旧服务器迁移

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

## 5. 验收检查

轻量检查：

```bash
./status.sh prod
./doctor.sh prod
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

真实公众号最小验收 checklist：

```text
向真实公众号发送一条美团小程序链接
查日志是否出现 “被动回复短链后未降级” 或 “被动回复已降级为精简版”
查日志是否出现 “短链创建成功” 与 “短链批量转换完成”
查 /readyz 中 shortlink_public_base_url / TTL / cleanup 配置是否符合预期
```

## 6. 发布更新

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

## 7. 回滚恢复

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

## 8. 日志和排错

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

短链配置验收：

```bash
curl -fsS http://127.0.0.1:8080/readyz
```

重点看返回 JSON 中：

```text
shortlink_public_base_url
shortlink_default_ttl_seconds
shortlink_cleanup_timezone
shortlink_cleanup_time
```

如果使用正式 HTTPS 域名，再验证一次外部短链入口：

```bash
curl -i https://你的短链域名/key/测试code
```

返回 `302 Found` 或应用层 `404 Not Found` 都说明宝塔/Nginx 已经把请求转进应用；如果是站点证书错误或 Nginx 自己的 404，问题在反代或 SSL。

端口冲突时修改 `.env` 的 `WX_HTTP_PORT` 和 `GO_SHORTLINK_PUBLIC_BASE_URL`，然后重建。

## 9. 宝塔部署办法

短链配置可以做到“部署脚本内自动写入应用配置”，但不能做到“项目脚本完全接管宝塔和 DNS”。边界如下：

- 可以自动化：`.env`、运行时短链域名、默认 TTL、容器启动、应用健康检查
- 不能由项目完全自动化：DNS 解析、宝塔站点绑定、SSL 证书签发、宝塔外层 Nginx 特殊规则

推荐顺序：

1. 先执行部署：

```bash
./deploy.sh --public-url https://你的短链域名
```

2. 在宝塔创建或修改站点：

```text
域名: 你的短链域名
反向代理目标: http://127.0.0.1:8080
```

3. 在宝塔申请并启用 SSL 证书。

4. 验证：

```bash
curl -fsS https://你的短链域名/readyz
```

5. 登录后台检查 `/system-settings` 中短链配置是否与部署值一致。

## 10. 安全边界

- 不提交 `.env`、`runtime-data/`、`logs/`、`backups/`。
- 不把真实公众号密钥、管理员密码、迁移包发到公开渠道。
- 迁移包可能包含真实配置和数据库，传输后按生产数据保管。
- 如果历史代码曾出现真实密钥，部署完成后应轮换密钥。
