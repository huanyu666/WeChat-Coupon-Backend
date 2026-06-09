# 美团津贴 Relay 挂机宝部署手册

本手册对应当前主站已经接入的“美团津贴 Relay 节点池”方案，适用于你现在这类新买的大陆挂机宝：

- 系统：`Ubuntu 22.04.1 LTS`
- 第一次 SSH 登录
- 机器里还没有 Python 环境和 Relay 服务

这份手册的目标很单纯：

- 在挂机宝上部署一个只负责中转美团津贴接口的小服务
- 用 `systemd` 守护
- 让主站后台把它当作一个标准 Relay 节点接进去

当前中转的接口只有这一个：

```text
https://adapi.waimai.meituan.com/adhub/lite/landingPage/getAds
```

不做别的接口，不做复杂网关，不做数据库。

## 1. 这份手册适合什么情况

适用于：

- 主站服务器直连津贴接口被风控
- 海外 / 香港代理出口也不稳
- 大陆挂机宝出口可以正常访问津贴接口
- 你打算把挂机宝接进主站的 Relay 节点池

不适用于：

- 想把整个项目都部署到挂机宝
- 想让挂机宝同时承担 Nginx、数据库、主站 Web
- 想做完整反向代理平台

这一版只负责“津贴接口中转”。

## 1.1 这类挂机宝最常见的两个坑

在你之前实际操作里，已经遇到过两类非常典型的问题：

### 坑 1：系统里有 `python3`，但没有 `pip`

表现通常是：

```text
/usr/bin/python3: No module named pip
```

这不是 Python 坏了，只是机器太干净，还没装：

```text
python3-pip
python3-venv
```

所以后面的文档里会先装系统包，再建虚拟环境，不直接假设 `pip` 已经存在。

### 坑 2：服务商给你的二级域名不一定能用

比如这类地址：

```text
hbr1.wch1.top
```

它看起来像“你的外网地址”，但本质上它是服务商的二级域名，不等于你的真实公网 IP。

在国内机房场景下，如果这个域名没有备案，访问时可能会：

- 直接跳“未备案接入”拦截页
- 流量根本到不了你机器的 `18080`
- 你以为是 FastAPI 没启动，实际上是域名在机房入口就被拦截了

所以这份文档的一个硬规则是：

- **外网验证优先使用真实公网 IP**
- **主站后台优先填公网 IP + 端口**
- 服务商赠送二级域名只能当“可选项”，不能当默认方案

## 2. 当前约定

为了后续多挂机宝分流方便，建议统一按下面的约定走：

- Relay 代码目录：`/root/meituan`
- 服务名：`meituan-allowance-relay`
- Relay 机器内监听端口：`18080`
- 挂机宝面板做端口映射：`外网端口 -> 18080`

也就是说：

- 机器内部服务永远监听 `18080`
- 每台机器只变化：
  - 外网 IP
  - SSH 端口
  - Relay 外网端口

## 3. 第一次 SSH 登录

先登录机器。

通用格式：

```bash
ssh root@你的挂机宝IP -p 你的SSH端口
```

例如：

```bash
ssh root@你的挂机宝IP -p 你的SSH端口
```

登录后先确认系统版本：

```bash
cat /etc/os-release
uname -a
```

你这台机器预期类似：

```text
PRETTY_NAME="Ubuntu 22.04.1 LTS"
VERSION_CODENAME=jammy
```

这里最关键的是确认：

- 是 `Ubuntu`
- 代号是 `jammy`

后面换源就按 `jammy` 来写。

## 4. 先做基础准备

第一次登录建议先执行：

```bash
apt-get update
apt-get install -y curl ca-certificates
```

如果这里已经很慢，再换国内源。

## 5. 换 Ubuntu 22.04 国内源

这台机器是 `Ubuntu 22.04.1`，代号 `jammy`。

先备份原始源：

```bash
cp /etc/apt/sources.list /etc/apt/sources.list.bak.$(date +%Y%m%d-%H%M%S)
```

然后写清华源：

```bash
cat >/etc/apt/sources.list <<'EOF'
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-backports main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-security main restricted universe multiverse
EOF
```

更新索引：

```bash
apt-get update
```

如果你更习惯阿里云，也可以改成阿里云源，但建议整台挂机宝统一一种源，后面排查更省心。

如果你发现 `apt-get update` 或后面的 `pip install` 特别慢，不要硬等，优先先把系统源和 pip 源都切到国内镜像再继续。

## 6. 安装运行 Relay 需要的软件

执行：

```bash
apt-get install -y python3 python3-pip python3-venv git curl ca-certificates
```

确认版本：

```bash
python3 --version
pip3 --version
curl --version
```

预期至少：

- `python3` 可用
- `python3 -m venv` 可用

如果你之前已经试过：

```bash
python3 -m pip install fastapi uvicorn httpx
```

结果报：

```text
No module named pip
```

那就说明系统确实缺 `python3-pip`，继续按本手册这一节安装即可，不用单独折腾 `get-pip.py`。

## 7. 创建 Relay 目录

我们统一放在：

```text
/root/meituan
```

执行：

```bash
mkdir -p /root/meituan
cd /root/meituan
```

确认：

```bash
pwd
ls -la
```

## 8. 把 Relay 脚本传到挂机宝

主站项目里当前使用的脚本是：

```text
scripts/meituan_allowance_relay.py
```

如果你是在主站服务器上操作，可以从主站直接传到挂机宝。

主站文件路径：

```text
/www/wwwroot/wx-coupon-prod/scripts/meituan_allowance_relay.py
```

传输命令格式：

```bash
scp -P 挂机宝SSH端口 /www/wwwroot/wx-coupon-prod/scripts/meituan_allowance_relay.py root@挂机宝IP:/root/meituan/
```

例如：

```bash
scp -P 你的SSH端口 /www/wwwroot/wx-coupon-prod/scripts/meituan_allowance_relay.py root@你的挂机宝IP:/root/meituan/
```

传完后到挂机宝上确认：

```bash
ls -l /root/meituan/meituan_allowance_relay.py
```

## 9. 创建虚拟环境并安装依赖

为了不污染系统 Python，统一用虚拟环境。

执行：

```bash
cd /root/meituan
python3 -m venv .venv
source /root/meituan/.venv/bin/activate
```

升级 pip 并安装依赖：

```bash
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi uvicorn httpx
```

如果你希望以后这台挂机宝所有 `pip` 安装都默认走国内源，可以顺手写一个永久配置：

```bash
mkdir -p ~/.pip
cat >~/.pip/pip.conf <<'EOF'
[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
EOF
```

这样后面再装别的 Python 包时，就不用每次手动加 `-i` 了。

自检：

```bash
python -c "import fastapi, uvicorn, httpx; print('ok')"
```

如果输出：

```text
ok
```

说明依赖没问题。

## 10. 先手动启动一次

当前脚本默认端口是：

```text
43183
```

但我们在挂机宝上统一要求它监听：

```text
18080
```

所以第一次手动启动时，必须明确指定端口。

执行：

```bash
cd /root/meituan
source /root/meituan/.venv/bin/activate
MEITUAN_ALLOWANCE_RELAY_PORT=18080 python meituan_allowance_relay.py
```

看到类似输出说明服务已经跑起来：

```text
Uvicorn running on http://0.0.0.0:18080
```

不要关这个窗口，先开第二个 SSH 窗口做自检。

## 11. 本机自检

第二个 SSH 窗口登录后，执行：

```bash
curl http://127.0.0.1:18080/healthz
ss -lntp | grep 18080
```

正常情况下会看到：

```json
{"ok":true,"endpoint":"https://adapi.waimai.meituan.com/adhub/lite/landingPage/getAds","secret_enabled":false}
```

以及 `18080` 正在监听。

如果这里都不通，先不要继续做 `systemd`。

## 12. 先测 Relay 接口本身

本机可以再打一条最简单的中转请求，确认接口路径没问题：

```bash
curl http://127.0.0.1:18080/relay/meituan/allowance \
  -H 'Content-Type: application/json' \
  -d '{"endpoint":"https://adapi.waimai.meituan.com/adhub/lite/landingPage/getAds","params":{},"headers":{}}'
```

这里有两种常见结果：

1. 返回美团的报错 / 403 页面  
   说明 Relay 服务本身是通的，只是上游参数不完整，这个是正常现象。

2. 返回本地 FastAPI 报错  
   说明 Relay 代码或依赖有问题，需要先修本机。

我们的目标是确认：

- `/relay/meituan/allowance` 路由可访问
- 服务能往上游发请求

## 13. 配置 systemd 守护

手动启动确认没问题后，回到前台运行窗口，按：

```text
Ctrl+C
```

然后创建 `systemd` 服务文件：

```bash
cat >/etc/systemd/system/meituan-allowance-relay.service <<'EOF'
[Unit]
Description=Meituan Allowance Relay
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/meituan
ExecStart=/root/meituan/.venv/bin/python -m uvicorn meituan_allowance_relay:app --host 0.0.0.0 --port 18080
Restart=always
RestartSec=3
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
```

加载并启动：

```bash
systemctl daemon-reload
systemctl enable meituan-allowance-relay
systemctl start meituan-allowance-relay
```

查看状态：

```bash
systemctl status meituan-allowance-relay --no-pager
```

看日志：

```bash
journalctl -u meituan-allowance-relay -f
```

再做一次本机自检：

```bash
ss -lntp | grep 18080
curl http://127.0.0.1:18080/healthz
```

## 14. 可选：加 Relay 密钥

现在主站支持不给 `MEITUAN_ALLOWANCE_RELAY_SECRET`，也就是不校验密钥。

如果你后面想给这台挂机宝加一个简单保护，可以改成下面这种 `systemd` 配置：

```bash
cat >/etc/systemd/system/meituan-allowance-relay.service <<'EOF'
[Unit]
Description=Meituan Allowance Relay
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/meituan
ExecStart=/root/meituan/.venv/bin/python -m uvicorn meituan_allowance_relay:app --host 0.0.0.0 --port 18080
Restart=always
RestartSec=3
User=root
Environment=PYTHONUNBUFFERED=1
Environment=MEITUAN_ALLOWANCE_RELAY_SECRET=改成你自己的长随机字符串

[Install]
WantedBy=multi-user.target
EOF
```

然后执行：

```bash
systemctl daemon-reload
systemctl restart meituan-allowance-relay
```

如果启用了密钥，主站后台这个节点也必须填写同一份密钥。

## 15. 挂机宝面板端口映射

这一步非常关键。

Relay 服务现在监听的是：

```text
18080
```

所以挂机宝面板里必须做端口映射：

```text
外网端口 -> 18080
```

例如：

- 外网端口：`43814`
- 内网端口：`18080`

也就是：

```text
外网 43814 -> 机器内 18080
```

千万不要搞成：

- 服务监听 `43814`
- 面板又转发到 `18080`

这样外网一定不通。

标准建议：

- SSH 端口单独一个
- Relay 外网端口单独一个
- Relay 机器内永远监听 `18080`

补一句很重要的经验：

- 你在挂机宝面板里看到的“业务域名 / 二级域名”
- 和“真实公网 IP”
- 不是一回事

如果后面出现“域名访问打不开，但本机 `127.0.0.1:18080` 正常”的情况，优先怀疑：

- 服务商二级域名被备案策略拦截
- 域名流量根本没有转发到你的程序

不要一上来就怀疑 FastAPI 或 systemd。

## 16. 外网验证

面板映射配好后，先测本机：

```bash
curl http://127.0.0.1:18080/healthz
```

再测外网：

```bash
curl http://你的挂机宝外网IP:你的Relay外网端口/healthz
```

这里的“外网 IP”必须尽量使用：

- 服务商后台显示的**真实公网 IP**
- 或你向服务商确认过的独立公网 IP

不要默认把服务商给你的二级域名当作公网 IP。

如果有域名，也可以测：

```bash
curl http://你的域名:你的Relay外网端口/healthz
```

但顺序一定要是：

1. 先测 `127.0.0.1:18080`
2. 再测 `公网IP:外网端口`
3. 最后才测域名

原因很简单：

- 本机通，说明服务启动没问题
- IP 通，说明面板映射和公网访问没问题
- 只有“IP 通但域名不通”，才基本可以判定是域名备案 / 机房拦截问题

只有满足这两个条件，主站才能正常使用它：

- 本机 `18080` 通
- 外网映射端口也通

## 17. 主站后台如何接入这个新节点

挂好之后，在主站后台：

1. 打开 `/web/admin`
2. 找到“津贴 Relay 节点池”
3. 增加一个节点

节点 URL 填：

```text
http://你的外网IP:你的Relay外网端口/relay/meituan/allowance
```

例如：

```text
http://111.170.14.38:43814/relay/meituan/allowance
```

这里强烈建议：

- **优先填公网 IP**
- **不要优先填服务商送的二级域名**

因为如果那个二级域名在国内线路下被备案策略拦截，主站会直接把它判定成节点不可用。

如果用了密钥，就把同一份密钥也填进去。

现在主站 Relay 池已经支持：

- 健康轮询
- 节点冷却
- 同任务内切节点
- 全 Relay 失败后再走代理池兜底

所以你后面多开几台挂机宝，只要继续往这里加节点就行。

### 17.1 推荐接入顺序

不要一上来就直接去后台填节点。

最稳的顺序是：

1. 先在挂机宝本机验证：

```bash
curl http://127.0.0.1:18080/healthz
```

2. 再在任意外部机器验证：

```bash
curl http://你的真实公网IP:你的Relay外网端口/healthz
```

3. 只有这两个都通了，再去主站后台添加节点

这样最省时间，因为你能很快分清：

- 是 Relay 没跑起来
- 还是端口映射没通
- 还是后台节点配置填错了

### 17.2 后台里具体去哪里填

主站后台地址：

```text
https://98vx.cn/web/admin
```

进入后找到：

```text
津贴列表自动维护 -> 挂机宝 Relay 节点池
```

然后新增一个节点。

### 17.3 每个字段怎么填

建议这样填：

- 节点名称：自己能看懂就行，例如 `河北挂机宝-01`
- URL：

```text
http://真实公网IP:外网端口/relay/meituan/allowance
```

例如：

```text
http://111.170.14.38:43814/relay/meituan/allowance
```

- Secret：
  - 如果你没有设置 `MEITUAN_ALLOWANCE_RELAY_SECRET`，这里留空
  - 如果你设置了，就必须和挂机宝上的值完全一致
- 启用：
  - 勾选启用

这里再强调一次：

- **优先用真实公网 IP**
- **不要默认填服务商给的二级域名**

因为很多时候不是你的程序有问题，而是那个二级域名在机房入口就被拦了。

### 17.4 怎么确认主站已经真正接上了

节点保存后，先看后台 Relay 运行面板里有没有这个节点。

刚接进去时，常见状态是：

- 节点已经显示在列表里
- 状态是可用 / healthy
- `success_count = 0`
- `failure_count = 0`

这说明只是“配置保存成功了”，还不代表它已经实际被使用过。

要确认它真的被主站用上，还要再做一次真实任务验证。

### 17.5 最小验证方法

最简单的验证方式是：

1. 去前台发起一次津贴查询任务
2. 等任务开始跑
3. 回到后台看这个节点的运行状态

重点看这些字段有没有变化：

- 最近使用时间
- 成功次数
- 最近成功时间
- 最近失败时间
- 最近错误

如果任务成功跑过，通常至少会看到：

- `last_used_at` 更新
- `success_count` 增加

如果任务失败，也能从这里看到最近错误内容。

### 17.6 后台里已经保存了，但任务就是不用它，通常查这几项

最常见的原因就这几个：

1. URL 填成了服务商二级域名，不是公网 IP
2. `http://真实公网IP:外网端口/healthz` 实际上根本不通
3. 节点没有勾选启用
4. Secret 填错，和挂机宝上的 `MEITUAN_ALLOWANCE_RELAY_SECRET` 不一致
5. 节点之前连续失败，已经进入冷却

如果是第 5 种，在后台一般可以：

- 查看最近错误
- 点击“清空失败状态”
- 或手动移出冷却

然后再重新跑一次津贴任务。

## 18. 常用运维命令

查看状态：

```bash
systemctl status meituan-allowance-relay --no-pager
```

启动：

```bash
systemctl start meituan-allowance-relay
```

重启：

```bash
systemctl restart meituan-allowance-relay
```

停止：

```bash
systemctl stop meituan-allowance-relay
```

实时日志：

```bash
journalctl -u meituan-allowance-relay -f
```

看最近 100 行日志：

```bash
journalctl -u meituan-allowance-relay -n 100 --no-pager
```

查看监听：

```bash
ss -lntp | grep 18080
```

本机健康检查：

```bash
curl http://127.0.0.1:18080/healthz
```

外网健康检查：

```bash
curl http://你的外网IP:你的Relay外网端口/healthz
```

## 19. 升级脚本时怎么做

如果主站这边 `scripts/meituan_allowance_relay.py` 有更新，挂机宝按下面步骤更新：

1. 从主站重新 `scp` 最新脚本到挂机宝
2. 重启服务

命令：

```bash
scp -P 挂机宝SSH端口 /www/wwwroot/wx-coupon-prod/scripts/meituan_allowance_relay.py root@挂机宝IP:/root/meituan/
ssh root@挂机宝IP -p 挂机宝SSH端口 "systemctl restart meituan-allowance-relay && systemctl status meituan-allowance-relay --no-pager"
```

如果只是 Python 脚本改动，通常不需要重建虚拟环境。

## 20. 故障排查

### 20.1 本机 `18080` 不通

先查：

```bash
systemctl status meituan-allowance-relay --no-pager
journalctl -u meituan-allowance-relay -n 100 --no-pager
ss -lntp | grep 18080
```

常见原因：

- 没装依赖
- 虚拟环境路径不对
- `systemd` 的 `ExecStart` 写错
- 脚本不在 `/root/meituan`
- 端口没写成 `18080`

### 20.2 本机通，外网不通

说明 Relay 服务本身没问题，优先查：

- 挂机宝面板端口映射是否正确
- 外网端口是否真的开放
- 面板是否把外网端口转发到了 `18080`
- 你访问的是不是“真实公网 IP”，而不是服务商二级域名

最常见的错法：

```text
服务监听 43814，但面板转发到 18080
```

或者反过来。

还有一种你已经实际遇到过的情况：

```text
本机 127.0.0.1:18080 正常
但访问 hbr1.wch1.top:某端口 时，跳“未备案接入”页面
```

这通常不是你程序的问题，而是：

- 服务商二级域名未备案
- 国内机房 / 网关直接拦截了域名流量

这种场景下正确做法不是重装 Python，而是：

1. 改用真实公网 IP 测试
2. 主站节点也改填公网 IP
3. 只有真的要用域名时，再去考虑备案或换境外域名

### 20.3 `/healthz` 通，但主站节点还是连不上

先在主站服务器上直接测：

```bash
curl http://你的挂机宝外网IP:你的Relay外网端口/healthz
```

如果主站机器这里都不通，那就不是主站代码问题，是网络层还没打通。

如果主站机器用公网 IP 能通，但用服务商二级域名不通，结论基本就是：

- 节点本身没问题
- 域名入口有问题

这时直接把主站后台节点 URL 改成公网 IP 版本即可。

### 20.4 重启机器后服务没起来

检查：

```bash
systemctl is-enabled meituan-allowance-relay
```

如果不是 `enabled`：

```bash
systemctl enable meituan-allowance-relay
```

再看状态：

```bash
systemctl status meituan-allowance-relay --no-pager
```

### 20.5 第一台老挂机宝不是虚拟环境启动的，有影响吗

通常**不会影响它当前已经在跑的服务**。

也就是说：

- 如果第一台机器现在能正常响应 `/healthz`
- 主站也能正常通过它跑津贴

那它即使用的是系统 Python 直接装依赖，也不代表必须立刻重装。

但从长期维护角度，还是建议：

- 新机器统一走 `.venv`
- 新旧节点统一目录结构、端口、systemd 服务名

这样后面你扩到第 2 台、第 3 台时，升级、排错、替换节点都会轻松很多。

所以建议是：

- **老节点能稳定跑，就先别乱动**
- **新节点全部按这份文档统一成虚拟环境方案**
- 等后面有空，再把老节点平滑改造成同一套结构

## 21. 多挂机宝建议

后面如果你要多开挂机宝，建议统一记一张表：

```text
节点名 | 外网IP | SSH端口 | Relay外网端口 | Relay内网端口 | 密钥 | 状态
```

例如：

```text
hb-01 | 111.170.14.38 | 43183 | 43814 | 18080 | 无   | active
hb-02 | x.x.x.x       | xxxxx | xxxxx | 18080 | 有   | standby
hb-03 | x.x.x.x       | xxxxx | xxxxx | 18080 | 有   | standby
```

统一规范建议：

- 目录统一：`/root/meituan`
- 服务名统一：`meituan-allowance-relay`
- 内网端口统一：`18080`
- 主站后台统一从 Relay 节点池管理

这样后面做分流、摘节点、替换节点，都会轻松很多。

## 22. 10 分钟最短执行清单

这一节是给“第二台、第三台挂机宝”准备的极简版。

默认前提：

- 系统是 `Ubuntu 22.04.x`
- 你已经能 SSH 登录
- 你知道这台机器的：
  - SSH 端口
  - 真实公网 IP
  - Relay 外网端口
- 挂机宝面板已经准备把 `外网端口 -> 18080`

### 22.1 挂机宝内执行

先登录：

```bash
ssh root@你的挂机宝IP -p 你的SSH端口
```

换源：

```bash
cp /etc/apt/sources.list /etc/apt/sources.list.bak.$(date +%Y%m%d-%H%M%S)
cat >/etc/apt/sources.list <<'EOF'
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-updates main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-backports main restricted universe multiverse
deb https://mirrors.tuna.tsinghua.edu.cn/ubuntu/ jammy-security main restricted universe multiverse
EOF
apt-get update
```

装环境：

```bash
apt-get install -y python3 python3-pip python3-venv git curl ca-certificates
```

建目录和虚拟环境：

```bash
mkdir -p /root/meituan
cd /root/meituan
python3 -m venv .venv
source /root/meituan/.venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi uvicorn httpx
```

### 22.2 在主站服务器执行

把脚本传过去：

```bash
scp -P 挂机宝SSH端口 /www/wwwroot/wx-coupon-prod/scripts/meituan_allowance_relay.py root@挂机宝IP:/root/meituan/
```

### 22.3 回挂机宝执行

先手动启动测试：

```bash
cd /root/meituan
source /root/meituan/.venv/bin/activate
MEITUAN_ALLOWANCE_RELAY_PORT=18080 python meituan_allowance_relay.py
```

另开一个 SSH 窗口检查：

```bash
curl http://127.0.0.1:18080/healthz
ss -lntp | grep 18080
```

如果正常，再回前台窗口按：

```text
Ctrl+C
```

写 `systemd`：

```bash
cat >/etc/systemd/system/meituan-allowance-relay.service <<'EOF'
[Unit]
Description=Meituan Allowance Relay
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/meituan
ExecStart=/root/meituan/.venv/bin/python -m uvicorn meituan_allowance_relay:app --host 0.0.0.0 --port 18080
Restart=always
RestartSec=3
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable meituan-allowance-relay
systemctl start meituan-allowance-relay
```

启动后检查：

```bash
systemctl status meituan-allowance-relay --no-pager
curl http://127.0.0.1:18080/healthz
```

### 22.4 外网验证

一定先测公网 IP，不要先测服务商二级域名：

```bash
curl http://你的真实公网IP:你的Relay外网端口/healthz
```

### 22.5 主站后台接入

在 `/web/admin` 的津贴 Relay 节点池里新增：

```text
http://你的真实公网IP:你的Relay外网端口/relay/meituan/allowance
```

### 22.6 只看这 5 个检查点

如果你赶时间，只盯下面 5 件事：

1. `python3 -m pip` 不再报 `No module named pip`
2. `curl http://127.0.0.1:18080/healthz` 能通
3. `systemctl status meituan-allowance-relay --no-pager` 是运行中
4. `curl http://真实公网IP:外网端口/healthz` 能通
5. 主站后台节点 URL 用的是 **公网 IP**，不是服务商二级域名

### 22.7 最常见的 3 个翻车点

1. 没装 `python3-pip`

表现：

```text
/usr/bin/python3: No module named pip
```

2. 服务监听端口和面板映射端口搞混

正确关系：

```text
机器内 18080
外网端口 -> 18080
```

3. 拿服务商二级域名当公网地址

如果出现“未备案接入”页面，优先改用真实公网 IP。
