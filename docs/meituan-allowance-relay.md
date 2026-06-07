# 美团津贴 Relay 挂机宝部署手册

本手册对应当前已经跑通的挂机宝方案，目标是让大陆挂机宝中转这一个接口：

```text
https://adapi.waimai.meituan.com/adhub/lite/landingPage/getAds
```

当前验证通过的形态是：

- 主站通过后台“津贴 Relay 节点池”调用挂机宝
- 挂机宝运行 `scripts/meituan_allowance_relay.py`
- 挂机宝使用 `systemd` 守护
- 挂机宝面板端口映射为：
  - 外网端口 `43814`
  - 内网端口 `18080`
- relay 服务实际监听内网 `18080`

如果后续多开挂机宝做分流，优先复用这份手册，保持每台机器的目录、服务名和端口约定一致。

## 1. 适用场景

适用于以下情况：

- 主站服务器直连美团津贴接口被风控
- 香港或境外代理出口被风控
- 大陆挂机宝出口可用，打算用它做临时或长期中转

不适用于：

- 想把所有美团接口都搬过去
- 想做复杂负载均衡、鉴权网关、监控平台

这一版只负责“津贴接口中转”。

## 2. 挂机宝准备

第一次拿到新挂机宝后，先 SSH 登录。

```bash
ssh root@你的挂机宝IP -p 你的SSH端口
```

如果是像当前这台机器这样：

- SSH 端口：`43183`
- 面板映射外网端口：`43814`

那登录命令类似：

```bash
ssh root@hbr1.wch1.top -p 43183
```

登录后先确认系统版本：

```bash
cat /etc/os-release
uname -a
```

## 3. 换国内源并安装基础环境

下面以 Debian/Ubuntu 系为主，优先使用国内镜像。

先备份原始源：

```bash
cp /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true
cp -r /etc/apt/sources.list.d /etc/apt/sources.list.d.bak 2>/dev/null || true
```

如果是 Debian 12，可以直接写清华源：

```bash
cat >/etc/apt/sources.list <<'EOF'
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm main contrib non-free non-free-firmware
deb https://mirrors.tuna.tsinghua.edu.cn/debian/ bookworm-updates main contrib non-free non-free-firmware
deb https://mirrors.tuna.tsinghua.edu.cn/debian-security bookworm-security main contrib non-free non-free-firmware
EOF
```

如果是 Ubuntu，请把代号替换成实际版本，比如 `jammy`、`noble`。

更新并安装基础包：

```bash
apt-get update
apt-get install -y python3 python3-pip python3-venv curl ca-certificates git
```

确认版本：

```bash
python3 --version
pip3 --version
curl --version
```

## 4. 创建目录并放置 Relay 脚本

当前约定目录：

```text
/root/meituan
```

创建目录：

```bash
mkdir -p /root/meituan
cd /root/meituan
```

把主站仓库里的这个文件放到挂机宝：

```text
scripts/meituan_allowance_relay.py
```

推荐从主站直接拷过去：

```bash
scp -P 主机SSH端口 /www/wwwroot/wx-coupon-prod/scripts/meituan_allowance_relay.py root@挂机宝IP:/root/meituan/
```

放好后确认：

```bash
ls -l /root/meituan/meituan_allowance_relay.py
```

## 5. 安装 Python 依赖

为了避免污染系统环境，推荐单独建虚拟环境。

```bash
cd /root/meituan
python3 -m venv .venv
source /root/meituan/.venv/bin/activate
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple fastapi uvicorn httpx
```

确认：

```bash
python -c "import fastapi, uvicorn, httpx; print('ok')"
```

## 6. 先手动启动自测

当前脚本默认端口是 `43183`，但这台挂机宝面板映射的是：

- 外网 `43814`
- 内网 `18080`

所以这里必须明确指定 relay 监听 `18080`。

先手动跑一遍：

```bash
cd /root/meituan
source /root/meituan/.venv/bin/activate
MEITUAN_ALLOWANCE_RELAY_PORT=18080 python meituan_allowance_relay.py
```

看到类似输出说明起来了：

```text
Uvicorn running on http://0.0.0.0:18080
```

另开一个 SSH 窗口验证：

```bash
curl http://127.0.0.1:18080/healthz
ss -lntp | grep 18080
```

期望返回：

```json
{"ok":true,"endpoint":"https://adapi.waimai.meituan.com/adhub/lite/landingPage/getAds","secret_enabled":false}
```

如果这里都不通，先不要继续往下走。

## 7. 配置 systemd 守护和开机自启

手动启动确认没问题后，改成 `systemd` 常驻。

先停止手动前台进程，回到那个正在跑 relay 的窗口，按：

```text
Ctrl+C
```

然后写 service 文件：

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

查看日志：

```bash
journalctl -u meituan-allowance-relay -f
```

再次验证本机访问：

```bash
ss -lntp | grep 18080
curl http://127.0.0.1:18080/healthz
```

## 8. 挂机宝面板端口映射

这一台机器已经验证可用的面板配置是：

- 服务名：`meituan`
- 外网端口：`43814`
- 内网端口：`18080`

也就是：

```text
外网 43814 -> 机器内 18080
```

这一步非常关键。

如果你把 relay 监听在 `43814`，但面板仍然转发到 `18080`，外网会不通。

如果你新开机器，也建议沿用同样约定：

- SSH 端口单独分配
- relay 外网端口单独分配
- 内网统一监听 `18080`

## 9. 外网验证

先测本机：

```bash
curl http://127.0.0.1:18080/healthz
```

再测外网映射地址：

```bash
curl http://你的外网IP:43814/healthz
```

或者像当前机器这样：

```bash
curl http://111.170.14.38:43814/healthz
```

如果你有域名映射，也可以测：

```bash
curl http://hbr1.wch1.top:43814/healthz
```

只有“本机通 + 外网也通”，主站才能正常使用。

## 10. 主站配置

主站后台配置：

1. 打开 `/web/admin`
2. 在津贴 Relay 节点池里新增节点
3. URL 填：

```text
http://111.170.14.38:43814/relay/meituan/allowance
```

如果后续要加简单鉴权，只需要：

- 后台节点里填写同一份密钥
- 挂机宝启动环境里也加上：

```bash
MEITUAN_ALLOWANCE_RELAY_SECRET=你自己的长随机字符串
```

如果改了主站代码或容器环境，再重建并重启主站容器：

```bash
cd /www/wwwroot/wx-coupon-prod
docker compose build app
docker compose up -d app
```

## 11. 主站自测

主站验证顺序：

1. relay 健康检查可通
2. 打开主站查询页
3. 创建任务
4. 看任务状态推进
5. 看结果页是否可回看

已在当前主站环境验证成功的一次真实任务结果：

- `task_id`: `d32a76437eb842fc87202733f1c56437`
- 状态：`succeeded`
- `pages_requested`: `30`
- `merchant_count`: `259`
- `stop_reason`: `has_next_page_false`

也就是说，当前这套：

- 挂机宝 relay
- 主站任务化查询
- 分页落库
- 结果页回看

已经整链路跑通。

## 12. 常用运维命令

查看服务状态：

```bash
systemctl status meituan-allowance-relay --no-pager
```

重启服务：

```bash
systemctl restart meituan-allowance-relay
```

停止服务：

```bash
systemctl stop meituan-allowance-relay
```

实时日志：

```bash
journalctl -u meituan-allowance-relay -f
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
curl http://你的外网IP:43814/healthz
```

## 13. 故障排查

### 13.1 本机 18080 不通

检查：

```bash
systemctl status meituan-allowance-relay --no-pager
journalctl -u meituan-allowance-relay -n 80 --no-pager
ss -lntp | grep 18080
```

常见原因：

- service 写错了 Python 路径
- 依赖没装全
- 没有在 `/root/meituan` 下运行
- 端口写错

### 13.2 本机通，外网不通

说明 Python 服务没问题，优先查：

- 挂机宝面板端口映射是否正确
- 外网端口是否开放
- 安全策略是否拦截

这类场景最常见的错法就是：

```text
服务监听 43814，但面板转发到 18080
```

或者反过来。

### 13.3 主站任务创建成功，但马上 failed

如果主站里看到：

```text
网络错误: All connection attempts failed
```

通常表示主站根本连不到 relay，优先检查：

```bash
curl http://挂机宝外网IP:43814/healthz
```

### 13.4 重启后服务没起来

检查是否启用了开机自启：

```bash
systemctl is-enabled meituan-allowance-relay
```

如果不是 `enabled`：

```bash
systemctl enable meituan-allowance-relay
```

## 14. 多挂机宝分流建议

后续如果你要多开挂机宝，建议保持统一规范：

- 目录统一：`/root/meituan`
- 服务名统一：`meituan-allowance-relay`
- 内网监听统一：`18080`
- 每台机器只变外网 IP 和外网端口

推荐记录一张表：

```text
节点名 | 外网IP | SSH端口 | relay外网端口 | relay内网端口 | 状态
```

比如：

```text
hb-01 | 111.170.14.38 | 43183 | 43814 | 18080 | active
hb-02 | x.x.x.x       | xxxxx | xxxxx | 18080 | standby
```

这样后面主站要做轮询、切换、分流，都会轻松很多。
