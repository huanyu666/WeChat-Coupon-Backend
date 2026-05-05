# 代码审查问题记录与修复 - 2026-04-28

> 状态提示：这是 2026-04-28 的历史安全审查记录。文中关于 `localStorage` Bearer Token、legacy 运行路径和部署阶段的描述可能已经过时；当前状态请优先看 `docs/CURRENT_STATUS.md`、`docs/VERSION_INVENTORY_2026-05-04.md` 和 `docs/AI_DEVELOPMENT_HANDOFF.md`。

本文件记录本轮全项目审查发现的问题、已完成修复和仍需要运维处理的事项。

## 已修复

- 代理 API 密钥不再硬编码在代码中，改为读取 `WX_PROXY_API_URL` 或 `PROXY_API_URL`。
- Redis 默认密码不再硬编码；只有显式配置 `WX_SERVICE_REDIS_PASSWORD`、`REDIS_PASSWORD` 或 Redis URL 自带密码时才启用鉴权。
- 旧版微信公众号菜单脚本移除硬编码 AppSecret 和 Token，改为环境变量。
- 公众号配置管理 API 不再返回 `app_secret`、`token`、`encoding_aes_key`、`zmkey` 明文，只返回是否已配置。
- 公众号配置管理页面把敏感字段改成写入型字段；编辑已有公众号时留空会保留原值。
- `/api/wechat/access_token/{account_id}` 不再返回 access token 明文，只返回是否可获取。
- 素材上传接口增加服务端素材类型、扩展名、Content-Type、空文件和大小校验。
- 素材上传结果页面对接口返回内容进行 HTML 转义，并限制外链协议，避免管理端 XSS。
- 登录接口增加基础失败限流，按用户名和客户端来源累计失败次数，成功登录后清理计数。
- 微信回调不再记录原始 XML、解密 XML 或回复 XML 预览，只保留长度、耗时、账号和消息类型等元数据。
- XML 解析路径优先使用 `defusedxml`，并在依赖文件中加入 `defusedxml`。
- 微信 AES 加解密增加 EncodingAESKey 格式和长度校验；加密回复随机前缀改为 `os.urandom(16)`。
- 微信回调增加请求体大小上限 `WX_WECHAT_MAX_BODY_BYTES`，默认 256KB。
- `/youxi` 缺失模板时返回 404，不再抛 500。
- 接单排行链接加密密钥支持 `WX_ORDER_RANKINGS_LINK_SECRET` / `ORDER_RANKINGS_LINK_SECRET` 覆盖；默认值暂保留以兼容现有 Go 辅助程序。
- Docker Compose 和 `.env` 示例补充代理 API、排行密钥、登录限流和微信消息体上限等环境变量。
- `.gitignore` 补充虚拟环境、缓存、日志、运行时 JSON/DB、真实 `config.toml` 等条目。
- 已将当前被忽略的运行时文件从 Git 索引取消跟踪；本地文件仍保留在服务器磁盘上。
- `config.toml` 不再复制进 Docker 镜像；容器优先读取 `WX_SERVICE_CONFIG_FILE` / `CONFIG_FILE`，其次读取 `/data/config.toml`。
- dev/prod 启动脚本会在 `runtime-data/config.toml` 不存在时，从旧根目录 `config.toml` 迁移一份运行时配置并设置 `0600` 权限。
- 管理员初始化脚本默认写入运行时配置文件，缺失时可创建 `runtime-data/config.toml`，避免继续依赖仓库根目录真实配置。
- 后台工作台、`/healthz`、`/readyz` 增加运行环境识别信息，用于明确当前是 dev/prod、公开入口、微信回调 URL 和配置来源。
- 备份脚本已把 `config.toml` 纳入运行时备份，避免迁移到 `runtime-data/config.toml` 后恢复包缺失核心配置。
- dev 端口支持 `WX_DEV_HTTP_BIND`，当前已绑定到 `127.0.0.1:18080`，公网不再暴露 dev 后台。
- 本轮修复已同步到 `/www/wwwroot/wx-coupon-prod`，生产 8080 app 已重建。

## 仍需运维处理

- 轮换历史上已经提交或暴露过的真实密钥和数据：管理员密码哈希、微信公众号 AppSecret / Token、代理 API 密钥、Redis 密码、激活码、p 值、场景数据、加密 token 文件等。
- 生产环境应在安全位置重新生成或恢复运行时数据，不再依赖仓库中的真实数据文件。
- 如果 Go `meituan-query` 也会消费排行 `rank_token`，修改 `WX_ORDER_RANKINGS_LINK_SECRET` 前需要同步更新 Go 侧逻辑，否则旧链接可能无法解析。
- 管理端当前仍使用 `localStorage` Bearer Token；后续建议迁移到 HttpOnly SameSite Cookie，并补充严格 CSP。
- 仍需轮换历史上已经提交或暴露过的真实密钥。

## 本轮验证

- `python3 -m py_compile config/config.py scripts/init_admin_user.py routes/material.py routes/auth.py routes/wechat.py routes/waimai.py utils/crypto.py utils/xml_parser.py utils/order_rankings_link_crypto.py utils/proxy_utils.py utils/redis_async.py text_processors/xiaoxi.py xiaoxi.py fuwu.py` 通过。
- `bash -n scripts/docker_dev_up.sh scripts/docker_prod_up.sh` 通过。
- `./check.sh` 通过；最近一次验证时间为本轮运行时配置迁移修复之后。
- 已重建并重启 dev app 容器，确认 `defusedxml` 已安装进 dev 镜像。
- 重建后 `./status.sh dev` 通过，dev 容器健康，`/healthz`、`/readyz`、业务 smoke 均通过。
- dev 容器内确认当前配置文件路径为 `/data/config.toml`。
- dev 镜像内确认不存在 `/app/config.toml`。
- `git ls-files -ci --exclude-standard | wc -l` 输出 `0`，说明当前被忽略文件已不再被 Git 跟踪。
- dev 容器重启后 `./status.sh dev` 通过，环境识别显示 `mode=development`、`deployment_name=wx-coupon-dev`。
- 在 dev 容器内检查公众号配置序列化结果，不再包含 `app_secret`、`token`、`encoding_aes_key`、`zmkey`、`access_token` 字段。
- 生产重建前已备份运行时数据：`/www/wwwroot/wx-coupon-prod/backups/wx-runtime-backup-20260429-024142.tar.gz`。
- 生产重建前已单独备份 `.env`、根目录 `config.toml` 和公众号运行时配置：`/www/wwwroot/wx-coupon-prod/backups/wx-prod-config-before-sync-20260429-024142.tar.gz`。
- 生产重建脚本迁移配置后再次备份：`/www/wwwroot/wx-coupon-prod/backups/wx-runtime-backup-20260429-024255.tar.gz`，该包已包含 `runtime_data/config.toml`。
- `./status.sh prod` 通过，生产环境识别显示 `mode=production`、`deployment_name=wx-coupon-prod`、`config_file=/data/config.toml`。
- `./proxy_check.sh prod http://154.219.115.75` 通过。
- `./wechat_check.sh http://154.219.115.75/wechat` 明文和 AES GET 校验均通过。
- 生产公众号回调 full smoke 通过：`BUSINESS_SMOKE_WECHAT_VERIFY_OK`、`BUSINESS_SMOKE_WECHAT_MESSAGE_OK`。
- dev `./status.sh dev` 通过，端口映射为 `127.0.0.1:18080->80/tcp`；公网 `http://154.219.115.75:18080/healthz` 连接超时，符合预期。
