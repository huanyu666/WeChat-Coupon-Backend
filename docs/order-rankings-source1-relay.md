# 排行榜来源 1/2 国内 Relay 部署指南

本服务部署在已有的第二台国内挂机宝上，但使用独立目录、端口、Python 虚拟环境和 systemd 服务。它不修改正在运行的订单 Relay 或津贴 Relay。

链路如下：

```text
主站排行榜 V2 -> 国内排行榜 Relay -> 来源 1 / 来源 2
```

Relay 仅允许五种固定操作：来源 1 会话检查、登录、活动目录、榜单页，以及来源 2 场次榜单页。它不提供任意 URL 转发。来源 1 Cookie 只保存在国内机 `/root/meituan-rankings-relay/source1-session.json`，权限为 `600`；来源 2 每次请求使用独立客户端，不复用来源 2 Cookie。

来源 1/2 默认先直连；主站后台启用“代理兜底”后，直连出现超时、连接失败、`403/429/5xx` 或来源 2 缺少 `shopData` 时，主站会把代理接口配置随服务端请求传给 Relay。Relay 在挂机宝上获取 `IP:端口`，调用 cz88 校验返回的 `data.ip` 是否与代理 IP 一致，再通过代理重试。每个日期+活动时间节点只保留一个当前代理，不建立 IP 池，也不做永久黑名单；代理失效后只清除当前租约，后续仍可重新验证并复用同一 IP。

## 1. 上传文件

在主站执行，替换为第二台国内挂机宝的 SSH 地址与端口：

```bash
scp -P 挂机宝SSH端口 \
  /www/wwwroot/wx-coupon-prod/scripts/order_rankings_source1_relay.py \
  root@挂机宝IP:/root/
```

登录国内挂机宝：

```bash
ssh root@挂机宝IP -p 挂机宝SSH端口
mkdir -p /root/meituan-rankings-relay
mv /root/order_rankings_source1_relay.py /root/meituan-rankings-relay/
cd /root/meituan-rankings-relay
```

## 2. 安装独立运行环境

这台机器若已按订单 Relay 文档安装 Python，可直接执行：

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip curl ca-certificates
python3 -m venv /root/meituan-rankings-relay/.venv
/root/meituan-rankings-relay/.venv/bin/pip install --upgrade pip
/root/meituan-rankings-relay/.venv/bin/pip install fastapi "uvicorn[standard]" httpx
```

如果下载速度慢，先按 `docs/meituan-order-relay.md` 的“切换国内软件源”步骤配置 APT 与 PyPI 镜像，再重新执行上面的安装命令。

## 3. 配置服务

生成一个随机 Relay 密钥。该密钥后续同时填入主站后台，避免外网端口被未授权使用：

```bash
openssl rand -hex 32
```

创建环境文件，把生成的值替换进 `ORDER_RANKINGS_RELAY_SECRET`：

```bash
cat >/etc/default/order-rankings-source1-relay <<'EOF'
ORDER_RANKINGS_RELAY_PORT=18081
ORDER_RANKINGS_RELAY_TIMEOUT_SECONDS=8
ORDER_RANKINGS_RELAY_MAX_CONCURRENCY=8
ORDER_RANKINGS_RELAY_STATE_FILE=/root/meituan-rankings-relay/source1-session.json
ORDER_RANKINGS_RELAY_SECRET=替换为随机密钥
EOF
chmod 600 /etc/default/order-rankings-source1-relay
```

不要在环境文件写来源 1 账号、密码或代理接口。来源 1 账号由主站后台在需要登录时发送；代理接口也由主站后台配置后在触发兜底时发送。Relay 将会话 Cookie 持久化到本机状态文件。现有 Relay 密钥仍可按原配置使用，本次代理兜底不新增独立密钥。

## 4. 先本机测试

启动临时进程：

```bash
cd /root/meituan-rankings-relay
set -a
. /etc/default/order-rankings-source1-relay
set +a
/root/meituan-rankings-relay/.venv/bin/python order_rankings_source1_relay.py
```

另开一个 SSH 窗口检查：

```bash
curl http://127.0.0.1:18081/healthz
```

应返回：

```json
{"ok":true,"service":"order-rankings-relay","source2_supported":true}
```

验证国内机可抓来源 1 活动目录：

```bash
curl -X POST http://127.0.0.1:18081/relay/order-rankings/source1 \
  -H 'Content-Type: application/json' \
  -H 'X-Order-Rankings-Relay-Secret: 你的随机密钥' \
  -d '{"operation":"activities"}'
```

结果应包含 `"success":true` 和来源 1 的活动 `data`。

验证国内机可抓来源 2 场次榜单：

```bash
curl -X POST http://127.0.0.1:18081/relay/order-rankings/source1 \
  -H 'Content-Type: application/json' \
  -H 'X-Order-Rankings-Relay-Secret: 你的随机密钥' \
  -d '{"operation":"source2_ranking","record_date":"2026-08-07","slot_time":"10:00"}'
```

结果应包含 `"success":true` 和 `html`，且 `html` 内含 `shopData`。不要在终端或日志打印完整 `html`。确认后在临时进程窗口按 `Ctrl+C`。

## 5. 配置 systemd 自启

```bash
cat >/etc/systemd/system/order-rankings-source1-relay.service <<'EOF'
[Unit]
Description=Order Rankings Source1 Relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/meituan-rankings-relay
EnvironmentFile=/etc/default/order-rankings-source1-relay
ExecStart=/root/meituan-rankings-relay/.venv/bin/python -m uvicorn order_rankings_source1_relay:app --host 0.0.0.0 --port 18081
Restart=always
RestartSec=3
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now order-rankings-source1-relay
systemctl status order-rankings-source1-relay --no-pager
curl http://127.0.0.1:18081/healthz
```

查看日志：

```bash
journalctl -u order-rankings-source1-relay -f
```

## 6. 挂机宝端口映射与主站接入

在第二台挂机宝面板新增端口映射：

```text
外网端口 -> 18081
```

从主站测试：

```bash
curl http://国内挂机宝IP:外网端口/healthz
```

主站 `/web/admin` -> `排行榜 V2` 填写：

- 排行榜国内 Relay 地址（来源 1 + 来源 2）：`http://国内挂机宝IP:外网端口`
- 排行榜 Relay 密钥：第 3 步生成的随机密钥
- 来源 1 账号、密码，并启用来源 1 登录采集
- 是否启用来源直连失败后的代理兜底
- 挂机宝代理 IP 接口、IP 校验缓存时间和代理失败重试次数

保存后点击“测试来源 1 登录”或“测试来源 2 解析”。测试通过后，来源 1 的活动发现和榜单采集、来源 2 的场次榜单采集均从国内 Relay 执行；主站不会再直接访问两个来源站点。启用代理兜底后，Relay 会在直连失败时自己调用后台配置的代理接口，不需要再次 SSH 修改挂机宝。

## 7. 升级与排错

升级脚本：

```bash
scp -P 挂机宝SSH端口 \
  /www/wwwroot/wx-coupon-prod/scripts/order_rankings_source1_relay.py \
  root@挂机宝IP:/root/meituan-rankings-relay/order_rankings_source1_relay.py
ssh root@挂机宝IP -p 挂机宝SSH端口 'systemctl restart order-rankings-source1-relay && systemctl status order-rankings-source1-relay --no-pager'
```

- `healthz` 不通：检查 `systemctl status`、端口映射和防火墙。
- `activities` 失败：先在国内机重新运行本文第 4 步的本机测试，确认不是主站到 Relay 的网络问题。
- 主站提示 Relay 密钥错误：检查环境文件与后台密钥完全一致，然后重启服务。
- 主站登录失败：确认后台账号密码已保存，查看 `journalctl -u order-rankings-source1-relay -n 100 --no-pager`；日志不会打印账号、密码或 Cookie。
- Cookie 失效：主站下一次来源 1 请求会先检查会话，确认失效时通过已保存账号重新登录；无需手工删除 Cookie 文件。
