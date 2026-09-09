# AI 开发交接

## 1. 目标

将此目录的“优惠券膨胀查询/执行”能力接入父项目 `WeChat Coupon Backend`。
父项目是 FastAPI 应用，使用 Docker Compose、Redis，并已有统一的运行时数据目录。
推荐把本目录改造成父项目内部模块，不再长期维护独立 Flask 服务。

## 2. 已确认的入口和调用链

```text
web/static/index.html
  -> POST /api/jobs
  -> web/app.py::_worker
     -> pre_only: web/app.py::_run_pre_only
     -> proxy: scripts/pure_mttouch_proxy_once.py
        -> scripts/proxy_pool_ydaili.py
        -> scripts/exchange_response_class.py
```

Web API：

- `GET /api/health`
- `GET /api/meta`
- `POST /api/card/check`
- `POST /api/jobs`
- `GET /api/jobs`
- `GET /api/jobs/<job_id>`
- `GET/POST /api/admin/cards...`

`POST /api/jobs` 的主要输入是 `link`、`mode`、`client_id`、`lat`、`lng`、
`delay`、`max_proxy`、`card_code`。当前公开模式只有 `pre_only` 和 `proxy`。

## 3. 保留文件的职责

- `web/app.py`：HTTP API、卡密、任务线程、结果归一化、`pre_only` 实现。
- `scripts/pure_mttouch_proxy_once.py`：代理模式单次完整流程。
- `scripts/proxy_pool_ydaili.py`：从提取 API 获取代理并处理冷却。
- `scripts/exchange_response_class.py`：业务响应分类。

## 4. 当前已知问题

按优先级处理：

1. 任务 JSON 保存完整链接和 token。接入父系统时只保存加密值或摘要，日志必须脱敏。
2. 代理配置文件包含供应商密钥。已从清理后的源码移除，只保留示例；应改用环境变量或父系统密钥配置。
3. 卡密、管理员密钥、任务全部用本地 JSON；并发和多实例下不可靠，应迁移到父项目 Redis/数据库。
4. `_runner_lock`、`_card_lock` 都是进程内锁，原部署被迫限制为单 worker。
5. Web 前端强制输入卡密，但后端 `_card_optional` 允许空卡密，产品规则不一致。
6. 历史 Linux 日志出现 `exports/` 写权限错误。容器中应统一写到父项目 `/data` 或专用 volume。
7. 当前任务由 daemon thread 执行，进程重启会丢失运行中状态，也没有可靠重试/幂等键。
8. 业务请求和响应结构高度依赖外部接口字段，需要契约测试和可观测日志。
9. 源文件中已有部分中文发生乱码，应先通过业务语义和前端现状确认后统一修复文案。
10. 旧目录没有可移植的端到端测试；清理掉的测试依赖原作者本机文件和账号样本。

## 5. 推荐接入方案

第一阶段只接入已经独立、可移植的 `pre_only` 和 `proxy` 路径：

1. 新建父项目服务层，例如 `services/meituan_expand/`。
2. 把请求构造、响应分类、代理获取拆成无 Flask 依赖的 Python 函数。
3. 在父项目 FastAPI 新增路由，不在同一容器内再启动 Flask。
4. 使用父项目 Redis 保存任务状态、幂等键、限流和分布式锁。
5. 运行结果写入父项目 `WX_SERVICE_DATA_DIR`，日志写入 `WX_SERVICE_LOG_DIR`。
6. 将代理密钥、管理员配置全部放入环境变量或现有配置系统。
7. 为 `pre_only` 建立第一个窄端到端测试，再迁移 `proxy` 提交流程。
8. 如未来需要直连 signer，拿到完整依赖和协议后作为独立能力重新实现。

不建议直接把 `web/app.py` 挂到 FastAPI。它同时承担 HTTP、存储、调度、计费和业务请求，
直接复用会把单进程锁和本地文件状态带入现有系统。

## 6. 建议的数据模型

```text
ExpandJob
  id, owner_id, mode, status, lat, lng
  token_ciphertext/token_hash, user_id_hash
  request_fingerprint, result_summary, error_code
  created_at, started_at, finished_at

ExpandCredential
  id, provider, encrypted_config, enabled, updated_at

ExpandQuota
  owner_id/card_id, limit, used, enabled, version
```

任务状态建议：`queued -> running -> pre_ok/success/no_coupon/blocked/error`。
扣次数必须与成功结果在同一事务或幂等流程中完成。

## 7. Linux 和容器边界

父项目当前容器入口是 FastAPI/uvicorn，HTTP 端口为容器内 `80`；Redis 同时提供 TCP
和 `/run/redis/redis-server.sock`。建议复用现有 `app` 服务，不新增公网端口。

需要补充的依赖只有：

```text
curl_cffi>=0.6
```

若暂时以独立服务验证，可使用 `web/mt-expand.service`；正式接入后应删除 Flask、Gunicorn
和独立 Nginx 这一层。

## 8. 第一轮开发验收

- `pre_only` 可通过父项目 API 提交并查询状态。
- 输入 token 不以明文出现在日志、Redis 普通字段或响应中。
- 重复提交同一请求不会并发执行两次。
- 容器重启后任务状态可恢复或明确标记失败。
- 代理密钥仅来自环境/密钥配置。
- 至少有响应分类单元测试、代理配置测试、API 契约测试和一个 mock 端到端测试。
- `docker compose -f docker-compose.dev.yml up -d --build` 后通过父项目健康检查。

## 9. 历史证据摘要

- `pre_only` 历史任务曾返回 HTTP 200、业务 `code=0`，识别出 6 个候选目标。
- `proxy` 历史任务曾因 `/opt/meituan-expand/exports` 无写权限而在落盘阶段失败。
- 原始抓包、完整任务 JSON、导出结果和解包目录均已移入清理前备份，不留在交接源码中。

完整清理范围和备份哈希见 `docs/CLEANUP_REPORT.md`。
