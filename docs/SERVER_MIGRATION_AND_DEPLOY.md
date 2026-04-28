# 服务器迁移与部署说明

## 文档目的

本文件用于将当前项目从旧服务器迁移到新服务器，并尽可能降低以下风险：

- 配置丢失
- 运行时数据丢失
- OpenResty 与 Python 服务转发异常
- Redis / Go 内部服务不可用
- 页面可访问但后台功能失效

## 项目基本信息

- **项目根目录**：`/opt/1panel/apps/openresty/openresty/www/sites/wx-coupon/index`
- **主服务入口**：`main.py`
- **推荐启动脚本**：`run_server.py`
- **Web 框架**：FastAPI
- **ASGI 服务**：uvicorn
- **前端模板目录**：`web/templates/`
- **静态资源目录**：`web/static/`
- **依赖文件**：`requirements.txt`

## 当前服务结构

### 1. Python 主服务

- 入口：`main.py` / `run_server.py`
- 默认优先通过 **Unix Domain Socket** 启动：
  - `WX_SERVICE_SOCKET_PATH`
  - 默认值：`/run/wx_service-python/wx_service.sock`
- 如果没有 socket 配置，则会监听：
  - `0.0.0.0:80`

### 2. OpenResty / Nginx

当前部署路径显示项目运行在 OpenResty 站点目录下，通常表示：

- 外部流量先到 OpenResty
- 再由 OpenResty 反向代理到 Python 服务
- Python 更推荐通过 UDS socket 提供服务

### 3. Redis

当前代码里 Redis 使用的是 **Unix Socket + 密码**：

- Socket：`/run/redis/redis-server.sock`
- 密码：当前写死在 `utils/redis_async.py`
- DB：`0`

这意味着**新服务器必须确认 Redis socket 路径与认证方式是否一致**，否则会导致部分功能降级或失效。

### 4. Go / 美团内部服务

当前项目依赖一个本地内部服务（Go / meituan-query 相关）：

- Python 检查的内部 socket：
  - `GO_INTERNAL_API_SOCKET_PATH`
  - 默认值：`/run/wx_service/meituan-query-internal.sock`
- 对应健康检查会体现在：
  - `/readyz`

同时项目根目录存在可执行文件：

- `meituan-query`

在 Linux 下，Python 默认**不主动拉起**该服务，代码倾向于认为它应该由 systemd 或外部方式管理。

## 健康检查接口

### 1. `/healthz`

用于检查主服务是否活着。

### 2. `/readyz`

用于检查主服务是否准备就绪，并且会额外暴露：

- Go 服务是否预期为外部托管
- Go internal socket 路径
- Go 健康检查状态

**迁服后优先验证这两个接口。**

## 关键环境变量

### 路径类

- `WX_SERVICE_ROOT`
  - 自定义项目根目录
- `WX_SERVICE_DATA_DIR`
  - 运行时数据目录
- `STATE_DIRECTORY`
  - 若未指定 `WX_SERVICE_DATA_DIR`，可作为运行时数据目录
- `WX_SERVICE_RUNTIME_DIR`
  - 运行时目录
- `RUNTIME_DIRECTORY`
  - 运行时目录兜底
- `WX_SERVICE_LOG_DIR`
  - 日志目录
- `LOGS_DIRECTORY`
  - 日志目录兜底
- `WX_SERVICE_SOCKET_PATH`
  - Python UDS socket 路径
- `WX_SERVICE_REDIS_SOCKET_PATH` / `REDIS_SOCKET_PATH`
  - Redis Unix socket 路径
- `WX_SERVICE_REDIS_PASSWORD` / `REDIS_PASSWORD`
  - Redis 密码；留空可表示无密码
- `WX_SERVICE_REDIS_DB` / `REDIS_DB`
  - Redis DB 编号
- `WX_SERVICE_REDIS_SOCKET_TIMEOUT_SECONDS` / `REDIS_SOCKET_TIMEOUT_SECONDS`
  - Redis 连接超时秒数
- `WX_SERVICE_REDIS_HEALTH_CHECK_INTERVAL_SECONDS` / `REDIS_HEALTH_CHECK_INTERVAL_SECONDS`
  - Redis health check 间隔秒数

### Go / 内部服务类

- `WX_SERVICE_AUTO_START_GO`
  - 是否由 Python 自动拉起 Go / 美团服务
  - Linux 下一般建议交给外部服务管理
- `GO_INTERNAL_API_SOCKET_PATH`
  - Go internal socket 路径
- `GO_SHORTLINK_PUBLIC_BASE_URL`
  - 短链接公开访问域名（如果有配置）

## 迁移时必须备份/迁移的内容

## 1. 代码目录

完整迁移以下目录/文件：

- `main.py`
- `run_server.py`
- `requirements.txt`
- `config/`
- `routes/`
- `handlers/`
- `utils/`
- `web/`
- `html/`
- `text_processors/`
- `link_handlers/`
- `miniprogram/`
- `merchant_coupons/`
- `wechat_account_store.py`
- 其他项目源码目录

## 2. 配置文件

重点检查：

- `config.toml`
- `text_processors/*.toml`
- `link_handlers/*.toml`
- `miniprogram/*.toml`
- 任何站点级 OpenResty / Nginx 配置

## 3. 运行时数据文件

这些文件非常重要，**不要只迁代码不迁数据**：

- `wechat_accounts.runtime.json`
- `activation_codes.json`
- `activation_codes_link.json`
- `activation_codes_meituan_order.json`
- `scenes.json`
- `p_values.json`
- `merchant_coupons.db` / `merchant_coupons/`（如果存在）
- `order_leaderboard.db`（如果已生成）
- 其他运行时生成的 `.db` / `.json`

### 重要说明

项目部分存储已经支持“运行时目录优先、旧根目录兼容”的迁移逻辑，例如：

- `wechat_accounts.runtime.json`
- `activation_codes*.json`
- `scenes.json`
- `p_values.json`
- `merchant_coupons` 数据

因此迁服时建议：

- **明确配置统一的运行时数据目录**
- 不要长期继续依赖项目根目录混放数据

## 4. 外部依赖与二进制

重点确认是否需要迁移：

- `meituan-query`
- Redis 服务
- OpenResty 站点配置
- Python 虚拟环境（建议新服务器重新创建，不直接复制）

## 推荐迁服步骤

### 第一步：旧服务器备份

- [ ] 备份整个项目目录
- [ ] 单独备份 `config.toml`
- [ ] 单独备份 `wechat_accounts.runtime.json`
- [ ] 单独备份所有 `activation_codes*.json`
- [ ] 单独备份 `scenes.json`
- [ ] 单独备份 `p_values.json`
- [ ] 备份数据库类运行时文件（若存在）
- [ ] 备份 OpenResty / Nginx 站点配置
- [ ] 记录 Redis socket 路径与认证方式
- [ ] 记录 Go internal socket 路径与管理方式

### 第二步：新服务器准备

- [ ] 安装 Python 3.11+ / 3.12
- [ ] 安装 OpenResty / Nginx
- [ ] 安装 Redis，并确认 Unix socket 可用
- [ ] 准备 Go / meituan-query 相关服务运行环境
- [ ] 创建项目目录
- [ ] 创建虚拟环境
- [ ] 安装 `requirements.txt`

建议命令示例：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 第三步：恢复代码与数据

- [ ] 上传项目代码
- [ ] 上传配置文件
- [ ] 上传运行时数据文件
- [ ] 恢复 `meituan-query` 二进制（如需要）
- [ ] 校验文件权限

### 第四步：设置运行目录与 socket 目录

建议显式配置：

- `WX_SERVICE_ROOT`
- `WX_SERVICE_DATA_DIR`
- `WX_SERVICE_RUNTIME_DIR`
- `WX_SERVICE_LOG_DIR`
- `WX_SERVICE_SOCKET_PATH`
- `WX_SERVICE_REDIS_SOCKET_PATH`
- `WX_SERVICE_REDIS_PASSWORD`
- `WX_SERVICE_REDIS_DB`
- `GO_INTERNAL_API_SOCKET_PATH`

这样迁服后路径更稳定，也更容易排查问题。

### 第五步：启动 Python 服务

推荐方式：

```bash
source .venv/bin/activate
python run_server.py
```

如果采用 systemd，请让 systemd 负责：

- 工作目录
- 环境变量
- UDS 目录创建
- 自动重启

### 第六步：配置 OpenResty 反向代理

需要确认 OpenResty 正确代理到：

- `WX_SERVICE_SOCKET_PATH` 对应的 UDS

如果暂时不用 UDS，也可以临时改成 TCP 监听验证，但长期建议仍用 UDS。

### 第七步：启动并验证 Go / 内部服务

确认以下其一：

- 已由外部服务管理并成功创建 internal socket
- 或已明确接受短链接/内部能力降级

### 第八步：执行健康检查

依次检查：

```text
/healthz
/readyz
```

重点看：

- 主服务是否返回 `ok: true`
- `redis_reachable` 是否为 `true`
- `redis_socket_path` / `redis_socket_exists` 是否符合预期
- `go_reachable` 是否符合预期
- `go_socket_path` / `go_socket_exists` 是否正确
- `go_shortlink_public_base_url` 是否符合新服务器配置

如需在新服务器快速执行最小校验，可运行：

```bash
python3 scripts/migration_smoke_check.py --base-url http://127.0.0.1
```

如果当前阶段允许 Go 内部服务未就绪或 Redis 暂时不可用，也可以显式放宽：

```bash
python3 scripts/migration_smoke_check.py --base-url http://127.0.0.1 --allow-go-degraded --allow-redis-unreachable
```

## 迁服后手工验证清单

### 公共验证

- [ ] 首页 / 主页面可访问
- [ ] 静态资源可加载
- [ ] `/healthz` 正常
- [ ] `/readyz` 正常

### 后台页验证

- [ ] `admin.html` 用户审核分页正常
- [ ] `admin.html` 用户管理分页正常
- [ ] 修改用户名正常
- [ ] 修改密码正常
- [ ] 删除用户正常
- [ ] 管理员升降级正常

- [ ] `admin_zudui.html` 查询正常
- [ ] 分页正常
- [ ] 删除 / 批量删除正常
- [ ] 疑似垃圾数据查询正常
- [ ] 封禁 IP 数据查询正常

- [ ] `admin_ipban.html` 封禁/解封正常
- [ ] `admin_contact_count.html` 删除联系方式数据正常
- [ ] `admin_ip_count.html` 删除 IP 数据正常
- [ ] `admin_ip_delete_count.html` 删除 IP 数据正常
- [ ] `admin_xiaomagao.html` 登录/删除/封禁/解封正常

### 业务验证

- [ ] `/wechat-account-settings` 可访问
- [ ] 公众号配置可读取
- [ ] 运行时账号配置未丢失
- [ ] 订单查询相关链路可用
- [ ] Redis 相关功能无异常

## 迁服注意事项

### 1. 不要只复制代码

当前项目很多关键状态在运行时文件里，如果只复制代码，通常会出现：

- 后台账号状态不一致
- 激活码/验证码数据丢失
- 运行时公众号账号配置丢失
- 部分排行榜/场景/商家券数据丢失

### 2. Redis 当前配置是硬编码风险点

`utils/redis_async.py` 当前包含：

- 固定 socket 路径
- 固定密码

迁服时要么：

- 新服务器 Redis 完全兼容这套配置

要么：

- 你后续尽快把它改成环境变量化

### 3. Go / 美团内部服务要单独确认

Linux 上 Python 默认不主动拉起该服务，因此迁服后最容易出现：

- 页面打开正常
- 但 `/readyz` 报 `go_health_check_failed`
- 短链接或内部查询链路降级

### 4. UDS 权限经常出问题

新服务器如果出现 502 / 无法连接，请优先检查：

- socket 文件是否创建成功
- OpenResty 用户是否有权限访问 socket
- `/run/...` 目录是否存在且权限正确

### 5. 运行时目录最好与代码目录分离

推荐在新服务器上把以下目录显式拆开：

- 代码目录
- 数据目录
- 运行时目录
- 日志目录

这样更适合做备份、回滚和迁移。

## 建议后续优化

- [x] 将 Redis 配置外置到环境变量
- [ ] 将部署所需环境变量整理成 `.env.example` 或 systemd `Environment=` 模板
- [ ] 将 OpenResty 反代配置整理成可复用模板
- [ ] 将运行时数据目录备份流程脚本化
- [x] 为新服务器准备 smoke check 脚本

## 最低可用迁服标准

如果你时间紧，至少要做到：

- [ ] 代码迁过去
- [ ] `config.toml` 迁过去
- [ ] `wechat_accounts.runtime.json` 迁过去
- [ ] `activation_codes*.json` 迁过去
- [ ] Redis 可用
- [ ] Python 服务可启动
- [ ] OpenResty 能反代到 Python
- [ ] `/healthz` 正常
- [ ] `/readyz` 尽量正常
- [ ] 后台关键页可打开并执行基本操作

## 相关文档

- `docs/NEXT_DEVELOPMENT_PLAN.md`
- `docs/AI_HANDOVER_CONTEXT.md`
