# 美团订单 Relay 挂机宝部署指南

本指南部署的是“订单 Relay”，供公众号和 Web 的订单状态、创建时间、保险订单等请求使用。

它应部署在第二台国内挂机宝上，和现有津贴 Relay 分开：

```text
主站 -> 订单挂机宝 -> 美团订单相关接口
                   -> 代理 IP 接口 -> 美团订单相关接口（直连失败时）
```

不要修改第一台正在运行 `meituan-allowance-relay.service` 的津贴挂机宝。

## 1. 第一次 SSH 后准备环境

登录第二台挂机宝：

```bash
ssh root@你的挂机宝IP -p 你的SSH端口
```

### 1.1 先切换国内软件源

新挂机宝经常默认使用海外 APT/PyPI，下载 Python 依赖会非常慢。先备份当前 APT 源，再替换为清华镜像；这段同时兼容 Debian 和 Ubuntu 常见源文件格式。

```bash
backup_dir=/root/apt-sources-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$backup_dir"
cp -a /etc/apt/sources.list /etc/apt/sources.list.d "$backup_dir"/ 2>/dev/null || true

find /etc/apt -maxdepth 2 -type f \( -name '*.list' -o -name '*.sources' \) -print0 | xargs -0 -r sed -i \
  -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
  -e 's|https://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
  -e 's|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
  -e 's|https://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
  -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
  -e 's|https://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
  -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
  -e 's|https://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g'

apt-get update
```

如果 `apt-get update` 出错，可恢复刚才的源：

```bash
rm -rf /etc/apt/sources.list /etc/apt/sources.list.d
cp -a "$backup_dir"/sources.list /etc/apt/ 2>/dev/null || true
cp -a "$backup_dir"/sources.list.d /etc/apt/ 2>/dev/null || true
apt-get update
```

再把 pip 切换到清华 PyPI 镜像。这个步骤正是解决截图中 `pip install --upgrade pip` 下载极慢的问题：

```bash
mkdir -p /root/.config/pip
cat >/root/.config/pip/pip.conf <<'EOF'
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
EOF
```

确认当前 pip 源：

```bash
python3 -m pip config list
```

### 1.2 安装运行环境

安装 Python、虚拟环境和基础工具：

```bash
apt-get update
apt-get install -y python3 python3-venv python3-pip curl ca-certificates
mkdir -p /root/meituan-order
cd /root/meituan-order
python3 -m venv .venv
/root/meituan-order/.venv/bin/pip install --upgrade pip
/root/meituan-order/.venv/bin/pip install fastapi "uvicorn[standard]" httpx
```

## 2. 上传订单 Relay 文件

从主站上传 `scripts/meituan_order_relay.py`：

```bash
scp -P 挂机宝SSH端口 /www/wwwroot/wx-coupon-prod/scripts/meituan_order_relay.py root@挂机宝IP:/root/meituan-order/
```

挂机宝上确认文件存在：

```bash
ls -l /root/meituan-order/meituan_order_relay.py
```

## 3. 配置代理 IP 接口

订单 Relay 现由主站后台下发代理 IP 接口给挂机宝，供第三方订单接口直连失败时兜底使用，和排行榜 V2 一样不需要把接口固定写入挂机宝环境文件。

已有部署可保留 `MEITUAN_ORDER_PROXY_API_URL` 作为旧订单查询操作的兼容备用；正常情况下请在后台“订单代理 IP 接口”中配置。创建 systemd 环境文件：

```bash
cat >/etc/default/meituan-order-relay <<'EOF'
MEITUAN_ORDER_RELAY_PORT=18080
# 可选兼容备用。第三方订单查询优先使用主站后台下发的订单代理 IP 接口。
MEITUAN_ORDER_PROXY_API_URL=''
# 默认每次请求直取一个新 IP。主站后台也可临时切回缓存代理池。
MEITUAN_ORDER_PROXY_POOL_ENABLED=false
MEITUAN_ORDER_PROXY_POOL_SIZE=3
MEITUAN_ORDER_PROXY_MAX_USE_COUNT=30
MEITUAN_ORDER_PROXY_MAX_AGE_SECONDS=60
MEITUAN_ORDER_PROXY_API_TIMEOUT_SECONDS=2
MEITUAN_ORDER_RELAY_TIMEOUT_SECONDS=10
MEITUAN_ORDER_RELAY_MAX_CONCURRENCY=12

# 默认留空即不校验。需要保护时填长随机字符串，并在主站后台填写同一值。
MEITUAN_ORDER_RELAY_SECRET=
EOF
chmod 600 /etc/default/meituan-order-relay
```

代理厂商白名单应添加这台订单挂机宝的大陆公网 IP，不是主站香港 IP。

第三方订单查询的执行顺序为：

```text
主站 -> 订单挂机宝直连第三方订单接口
                 -> 失败后由订单挂机宝从服务端配置的代理 IP 接口获取 IP 重试
                 -> 两者均失败才回退旧订单查询链路
```

## 4. 手动启动与本机自检

先手动确认服务可启动：

```bash
cd /root/meituan-order
set -a
. /etc/default/meituan-order-relay
set +a
/root/meituan-order/.venv/bin/python meituan_order_relay.py
```

另开一个 SSH 窗口执行：

```bash
curl http://127.0.0.1:18080/healthz
curl -X POST http://127.0.0.1:18080/relay/meituan/order-query/probe -H 'Content-Type: application/json' -d '{}'
ss -lntp | grep 18080
```

`healthz` 中应有：

```json
{"ok":true,"service":"meituan-order-relay","order_proxy_configured":true}
```

`probe` 成功代表挂机宝可以从本机代理接口获取代理；它不会请求美团。

如果配置了 `MEITUAN_ORDER_RELAY_SECRET`，probe 加请求头：

```bash
curl -X POST http://127.0.0.1:18080/relay/meituan/order-query/probe \
  -H 'Content-Type: application/json' \
  -H 'X-Order-Relay-Secret: 你的密钥' \
  -d '{}'
```

确认后在手动启动窗口按 `Ctrl+C` 停止服务。

## 5. 配置 systemd 守护与自启

```bash
cat >/etc/systemd/system/meituan-order-relay.service <<'EOF'
[Unit]
Description=Meituan Order Relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/root/meituan-order
EnvironmentFile=/etc/default/meituan-order-relay
ExecStart=/root/meituan-order/.venv/bin/python -m uvicorn meituan_order_relay:app --host 0.0.0.0 --port 18080
Restart=always
RestartSec=3
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable meituan-order-relay
systemctl start meituan-order-relay
systemctl status meituan-order-relay --no-pager
```

查看实时日志：

```bash
journalctl -u meituan-order-relay -f
```

## 6. 挂机宝面板端口映射

服务内部端口固定为 `18080`。在第二台挂机宝面板创建单独映射：

```text
外网端口 -> 18080
```

从主站或本地确认：

```bash
curl http://第二台挂机宝公网IP:外网端口/healthz
```

主站后台填写的订单 Relay 地址为：

```text
http://第二台挂机宝公网IP:外网端口/relay/meituan/order-query
```

密钥未配置时，主站后台 Secret 留空；配置后两边必须完全一致。

## 7. 主站后台接入

打开 `/web/admin` 的“查单运行概览”：

1. 在“订单 Relay 节点池”增加节点。
2. 填写节点名称、上一步的完整 Relay 地址、可选 Secret。
3. 点击“测试”，确认 healthz 和代理 probe 都通过。
4. 保存订单 Relay 设置。

订单查询不会回退主站香港代理。节点未配置、节点冷却或全部不可用时，用户会看到明确的订单中转错误。

默认推荐选择“每次直取新 IP”，并设置“坏 IP 自动重试次数”为 `2`、“Relay 排队上限”为 `3` 秒。Relay 本机的硬并发上限仍由 `MEITUAN_ORDER_RELAY_MAX_CONCURRENCY` 控制，建议 2C2G 机器先保持 `12`。达到并发上限后，请求最多等待后台设置的秒数，超时会明确返回“当前繁忙”，不会无限排队。

## 8. 常见故障

- `healthz` 失败：检查 `systemctl status meituan-order-relay`、端口映射和防火墙。
- `probe` 提示未配置代理接口：检查主站后台“订单代理 IP 接口”是否已保存；旧部署才检查 `/etc/default/meituan-order-relay` 的 `MEITUAN_ORDER_PROXY_API_URL`。
- `probe` 提示代理接口获取失败：确认代理厂商白名单加入的是第二台挂机宝公网 IP。
- 主站测试提示密钥错误：检查 `MEITUAN_ORDER_RELAY_SECRET` 与后台节点 Secret。
- 主站请求超时：检查挂机宝日志、外网映射和订单 Relay 后台的请求超时设置。
- 服务升级：上传新版 Python 文件后执行：

```bash
systemctl restart meituan-order-relay
journalctl -u meituan-order-relay -n 100 --no-pager
```

## 9. 已部署订单 Relay 升级到直取代理模式

已有服务不需要重新建虚拟环境。先在订单挂机宝备份当前文件：

```bash
cp /root/meituan-order/meituan_order_relay.py /root/meituan-order/meituan_order_relay.py.bak-$(date +%Y%m%d-%H%M%S)
cp /etc/default/meituan-order-relay /etc/default/meituan-order-relay.bak-$(date +%Y%m%d-%H%M%S)
```

再在主站把新版脚本上传到订单挂机宝：

```bash
scp -P 挂机宝SSH端口 /www/wwwroot/wx-coupon-prod/scripts/meituan_order_relay.py root@第二台挂机宝IP:/root/meituan-order/meituan_order_relay.py
```

回到订单挂机宝，关闭旧的缓存代理池默认值并重启：

```bash
sed -i 's/^MEITUAN_ORDER_PROXY_POOL_ENABLED=.*/MEITUAN_ORDER_PROXY_POOL_ENABLED=false/' /etc/default/meituan-order-relay
systemctl restart meituan-order-relay
systemctl status meituan-order-relay --no-pager
curl http://127.0.0.1:18080/healthz
```

成功后，主站 `/web/admin` 的“订单 Relay 节点池”会出现三项运行参数：

- 代理获取方式：默认“每次直取新 IP”。
- 坏 IP 自动重试次数：默认 `2`，即一次操作最多尝试 3 个 IP。
- Relay 排队上限：默认 `3` 秒，挂机宝的硬并发仍保持 `12`。

保存后点击“测试全部节点”。探测返回 `proxy_mode: direct` 即表示主站到挂机宝已按直取模式工作。
