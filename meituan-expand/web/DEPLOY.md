# Linux 独立部署

正式接入父项目后应由 FastAPI 直接承载业务模块。本文件只用于迁移前的独立验证。

## 环境

```bash
cd /opt/meituan-expand
python3 -m venv .venv
. .venv/bin/activate
pip install -U pip
pip install -r web/requirements.txt
cp config/proxy_xiongmao.example.json config/proxy_xiongmao.json
chmod 600 config/proxy_xiongmao.json
```

编辑代理配置后启动：

```bash
export MT_ROOT=/opt/meituan-expand
export MT_HOST=127.0.0.1
export MT_PORT=8765
export MT_ADMIN_KEY="$(openssl rand -hex 24)"
python web/app.py
```

检查：

```bash
curl -fsS http://127.0.0.1:8765/api/health
```

## systemd

仓库提供 `web/mt-expand.service`。复制前确认：

- 项目路径为 `/opt/meituan-expand`
- `www-data` 对 `exports/`、`web/jobs/` 和 `config/` 有写权限
- Gunicorn 保持单 worker；当前锁和本地 JSON 都不是多进程安全的

写入仅 root 可读的环境文件：

```bash
sudo install -m 0600 /dev/null /etc/meituan-expand.env
printf 'MT_ADMIN_KEY=%s\n' "$(openssl rand -hex 24)" | sudo tee /etc/meituan-expand.env >/dev/null
```

```bash
sudo mkdir -p /opt/meituan-expand/exports /opt/meituan-expand/web/jobs
sudo install -m 0644 web/mt-expand.service /etc/systemd/system/mt-expand.service
sudo chown -R www-data:www-data /opt/meituan-expand
sudo systemctl daemon-reload
sudo systemctl enable --now mt-expand
sudo systemctl status mt-expand
```

Nginx 示例见 `web/nginx-expand.conf.example`。
