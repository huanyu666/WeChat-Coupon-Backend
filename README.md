# 微信公众号优惠券后端服务

微信公众号自动回复后端，支持美团、饿了么、京东等平台优惠券获取，以及商家代金券叠加、订单查询等功能。

## 目录

- [系统要求](#系统要求)
- [快速部署（Linux）](#快速部署linux)
- [快速部署（Windows）](#快速部署windows)
- [配置说明](#配置说明)
- [启动服务](#启动服务)
- [健康检查](#健康检查)
- [常见问题](#常见问题)
- [相关文档](#相关文档)

---

## 系统要求

| 组件 | 版本要求 | 说明 |
|------|----------|------|
| Python | 3.11 及以上 | 必须 |
| Redis | 任意近期稳定版 | 必须，通过 Unix Socket 连接 |
| OpenResty / Nginx | 任意近期稳定版 | 必须，用于反向代理 |
| meituan-query（Go 二进制） | 随项目提供 | Linux 下必须独立启动，Windows 下由 Python 自动拉起 |

---

## 快速部署（Linux）

### 第一步：获取代码

```bash
git clone <仓库地址>
cd WeChat-Coupon-Backend
```

### 第二步：创建 Python 虚拟环境并安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
```

### 第三步：配置文件

复制并编辑主配置文件（首次部署）：

```bash
cp config.toml config.toml.bak   # 可选：保留备份
```

然后在 `config.toml` 中根据需要修改 `[admin_users]`、`[account_specific_configs.*]` 等配置节。

> **公众号账号敏感信息**（appid、app_secret、token、encoding_aes_key）请在服务启动后通过后台页面 `/wechat-account-settings` 维护，不要写入 `config.toml`。

### 第四步：配置运行时目录与 Socket（可选但推荐）

建议通过环境变量显式指定各目录，避免运行时文件与代码混放：

```bash
export WX_SERVICE_ROOT=/opt/wx-coupon          # 项目根目录
export WX_SERVICE_DATA_DIR=/var/lib/wx-coupon  # 运行时数据目录
export WX_SERVICE_RUNTIME_DIR=/run/wx_service-python
export WX_SERVICE_LOG_DIR=/var/log/wx-coupon
export WX_SERVICE_SOCKET_PATH=/run/wx_service-python/wx_service.sock
```

### 第五步：创建 Socket 目录

```bash
mkdir -p /run/wx_service-python
chmod 755 /run/wx_service-python
```

### 第六步：启动 Redis

确保 Redis 以 Unix Socket 模式运行，socket 路径默认为 `/run/redis/redis-server.sock`。  
如果路径或密码不同，通过环境变量覆盖：

```bash
export REDIS_SOCKET_PATH=/run/redis/redis-server.sock
export REDIS_PASSWORD=<你的Redis密码>
export REDIS_DB=0  # 可选，默认为 0
```

### 第七步：启动 meituan-query 服务（Go 内部服务）

Linux 下 Python 不会自动拉起该服务，需要由 systemd 或其他方式管理：

```bash
./meituan-query &
# 或者配置 systemd service
```

确认其 Unix Socket 路径（默认 `/run/wx_service/meituan-query-internal.sock`）与 `utils/go_local_api.py` 中一致。

### 第八步：启动 Python 服务

```bash
source .venv/bin/activate
python run_server.py
```

如果使用 systemd，推荐将上述命令写入 service 文件，并设置工作目录与环境变量。

### 第九步：配置 OpenResty / Nginx 反向代理

在站点配置中将流量代理到 Python 服务的 Unix Socket：

```nginx
location / {
    proxy_pass http://unix:/run/wx_service-python/wx_service.sock;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

如果暂时不用 Unix Socket，可以将 `WX_SERVICE_SOCKET_PATH` 设为空字符串，服务将改为监听 `0.0.0.0:80`，然后正常配置 HTTP 反向代理即可。

---

## 快速部署（Windows）

> Windows 部署通常用于本地测试或个人服务器，推荐使用 [NSSM](https://nssm.cc/) 将服务注册为 Windows 服务。

### 第一步：获取代码并安装依赖

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -U pip
pip install -r requirements.txt
```

### 第二步：编辑配置文件

根据实际情况修改 `config.toml`。

### 第三步：启动服务

```bat
python run_server.py
```

Windows 下 Python 会自动尝试启动 `meituan-query.exe`（若该文件存在于项目根目录）。

### 使用 NSSM 注册为 Windows 服务

1. 下载 NSSM 并放入系统 PATH。
2. 在管理员命令提示符中执行：

```bat
nssm install WXAPP "C:\path\to\.venv\Scripts\python.exe" "C:\path\to\run_server.py"
nssm set WXAPP AppDirectory "C:\path\to\WeChat-Coupon-Backend"
nssm start WXAPP
```

之后可使用 `manager_bat\manager.bat` 管理服务：

```bat
manager.bat start    # 启动服务
manager.bat stop     # 停止服务
manager.bat restart  # 重启服务
manager.bat status   # 查看状态
manager.bat logs     # 实时查看日志
```

---

## 配置说明

### 主配置文件：`config.toml`

| 配置节 | 说明 |
|--------|------|
| `[admin_users]` | 管理员账号，密码使用 SHA256 哈希 |
| `[default_wechat_config]` | 默认公众号配置 |
| `[account_specific_configs.<公众号ID>]` | 各公众号的个性化配置（欢迎语、文本处理器、美团链接等） |

### 关键环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `WX_SERVICE_ROOT` | 项目根目录 | 脚本所在目录 |
| `WX_SERVICE_DATA_DIR` | 运行时数据目录 | 同项目根目录 |
| `WX_SERVICE_RUNTIME_DIR` | 运行时目录（socket 等） | 同项目根目录 |
| `WX_SERVICE_LOG_DIR` | 日志目录 | `<项目根>/logs/` |
| `WX_SERVICE_SOCKET_PATH` | Python 服务的 Unix Socket 路径 | `/run/wx_service-python/wx_service.sock` |
| `WX_SERVICE_AUTO_START_GO` | 是否由 Python 自动拉起 Go 服务（`1`/`0`） | Linux 下默认不拉起，Windows 下默认拉起 |
| `GO_INTERNAL_API_SOCKET_PATH` | Go 内部服务 Socket 路径 | `/run/wx_service/meituan-query-internal.sock` |
| `GO_SHORTLINK_PUBLIC_BASE_URL` | 短链接公开访问域名（如有） | 空 |
| `REDIS_SOCKET_PATH` | Redis Unix Socket 路径 | `/run/redis/redis-server.sock` |
| `REDIS_PASSWORD` | Redis 密码 | 内置默认值（建议显式设置） |
| `REDIS_DB` | Redis 数据库编号 | `0` |

### 运行时数据文件

以下文件在运行时生成，包含关键业务数据，**迁移服务器时必须一并迁移**：

- `wechat_accounts.runtime.json` — 公众号账号信息（含 app_secret 等敏感信息）
- `activation_codes.json` / `activation_codes_link.json` / `activation_codes_meituan_order.json` — 激活码数据
- `scenes.json` — 场景数据
- `p_values.json` — P 值数据
- `merchant_coupons/` 或 `merchant_coupons.db` — 商家代金券数据
- `order_leaderboard.db` — 订单排行榜数据

---

## 启动服务

### 直接启动

```bash
# Linux/macOS
source .venv/bin/activate
python run_server.py

# Windows
.venv\Scripts\activate
python run_server.py
```

### 带日志输出启动

```bash
python run_server.py -log
```

---

## 健康检查

服务启动后，可通过以下接口确认运行状态：

| 接口 | 说明 |
|------|------|
| `GET /healthz` | 检查主服务是否运行，返回 `{"ok": true, ...}` |
| `GET /readyz` | 检查主服务及 Go 内部服务是否就绪 |

示例（使用 curl，假设服务监听在 `0.0.0.0:80`）：

```bash
curl http://localhost/healthz
curl http://localhost/readyz
```

---

## 常见问题

**Q：服务启动后出现 502，OpenResty 无法连接后端。**  
A：检查 Unix Socket 文件是否已创建（`/run/wx_service-python/wx_service.sock`），以及 OpenResty 进程用户是否有权限访问该文件。可以临时将 `WX_SERVICE_SOCKET_PATH` 设为空，改用 TCP `0.0.0.0:80` 模式排查。

**Q：`/readyz` 返回 `go_health_check_failed`。**  
A：Linux 下 Python 默认不自动拉起 Go 服务，需要手动启动 `meituan-query`，或通过 systemd 管理。确认 socket 路径与 `GO_INTERNAL_API_SOCKET_PATH` 一致。

**Q：Redis 连接失败，部分功能降级。**  
A：确认 Redis 以 Unix Socket 模式运行，并通过环境变量 `REDIS_SOCKET_PATH`、`REDIS_PASSWORD`、`REDIS_DB` 配置连接参数。

**Q：公众号消息无法收发。**  
A：请确保已在后台页面 `/wechat-account-settings` 中正确填写公众号的 appid、app_secret、token、encoding_aes_key，并在微信公众号平台将服务器地址配置为本项目的域名。

---

## 相关文档

- [`docs/SERVER_MIGRATION_AND_DEPLOY.md`](docs/SERVER_MIGRATION_AND_DEPLOY.md) — 服务器迁移详细清单
- [`docs/NEXT_DEVELOPMENT_PLAN.md`](docs/NEXT_DEVELOPMENT_PLAN.md) — 后续开发计划
- [`docs/AI_HANDOVER_CONTEXT.md`](docs/AI_HANDOVER_CONTEXT.md) — AI 交接上下文
