# 排行榜来源 1 国内 Relay 部署指南

本服务部署在已有的第二台国内挂机宝上，但使用独立目录、端口、Python 虚拟环境和 systemd 服务。它不修改正在运行的订单 Relay 或津贴 Relay。

链路如下：

```text
主站排行榜 V2 -> 国内来源 1 Relay -> naiba666.com
```

Relay 仅允许四种固定操作：会话检查、登录、活动目录和榜单页。它不提供任意 URL 转发。来源 1 Cookie 只保存在国内机 `/root/meituan-rankings-relay/source1-session.json`，权限为 `600`。

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

不要在环境文件写来源 1 账号或密码。主站后台只在需要重新登录时通过受密钥保护的 Relay 请求发送；Relay 将会话 Cookie 持久化到本机状态文件。

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
{"ok":true,"service":"order-rankings-source1-relay"}
```

验证国内机可抓活动目录：

```bash
curl -X POST http://127.0.0.1:18081/relay/order-rankings/source1 \
  -H 'Content-Type: application/json' \
  -H 'X-Order-Rankings-Relay-Secret: 你的随机密钥' \
  -d '{"operation":"activities"}'
```

结果应包含 `"success":true` 和来源 1 的活动 `data`。确认后在临时进程窗口按 `Ctrl+C`。

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

- 来源 1 国内 Relay 地址：`http://国内挂机宝IP:外网端口`
- 来源 1 Relay 密钥：第 3 步生成的随机密钥
- 来源 1 账号、密码，并启用来源 1 登录采集

保存后点击“测试来源 1 登录”。测试通过后，活动发现、榜单采集和 Cookie 复用均从国内 Relay 执行；主站不会再直接访问 `naiba666.com`。

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
