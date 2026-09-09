# meituan-expand

这是从历史研发目录清理出的最小交接源码集，目标运行环境为 Linux。

## 当前能力

- Flask 管理页面和任务 API：`web/app.py`
- `pre_only`：只查询可膨胀目标，不提交兑换
- `proxy`：通过代理执行查询和兑换
- 卡密管理、任务状态和本地 JSON 持久化

## 目录

```text
meituan-expand/
  config/                    # 仅保留无密钥示例
  docs/                      # 清理、交接、回滚记录
  scripts/                   # Web 入口实际依赖的最小脚本闭包
  web/                       # Flask 入口、静态页面和 Linux 部署文件
  .env.example
  .gitignore
  AI_HANDOVER.md
```

运行时会自动创建 `exports/`、`web/jobs/`、`config/admin.json` 和
`config/cards.json`。代理配置需从示例复制为 `config/proxy_xiongmao.json`。

## Linux 快速检查

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r web/requirements.txt
cp config/proxy_xiongmao.example.json config/proxy_xiongmao.json
export MT_ROOT="$PWD"
export MT_ADMIN_KEY='replace-with-a-long-random-secret'
python web/app.py
```

另一个终端执行：

```bash
curl -fsS http://127.0.0.1:8765/api/health
```

接入现有系统前先阅读 [AI_HANDOVER.md](AI_HANDOVER.md)。
