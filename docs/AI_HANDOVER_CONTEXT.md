# AI 交接上下文

> 状态提示：这是早期 UI 和前端安全收敛阶段的历史交接上下文。当前项目已经进入生产可用后的收尾治理阶段；请优先看 `docs/CURRENT_STATUS.md`、`docs/VERSION_INVENTORY_2026-05-04.md` 和 `docs/AI_DEVELOPMENT_HANDOFF.md`。

## 文档目的

本文件用于帮助后续接手的 AI / 开发者快速理解：

- 这段时间主要做了什么
- 用户的偏好是什么
- 哪些文件已经被修改
- 目前最重要的风险和后续建议是什么

## 用户偏好与协作方式

### 用户明确偏好

- 用户偏好**一步一步推进**
- 用户不希望出现**没有必要的错误和回归**
- 用户允许在**同类问题、同类模板**范围内适度批量修改
- 用户不喜欢无边界的大重构
- 当前阶段更重视：
  - 安全性
  - 可维护性
  - 迁服可落地性
  - 交接清晰度

### 建议后续 AI 保持的工作方式

- 优先小步、可验证改动
- 同类问题可以批量，但不要跨层级乱改
- 先静态审查，再补丁，再验证
- 优先动模板层和部署层，谨慎动业务逻辑

## 近期开发主线

### 主线 1：关键页面 UI 修复 / 统一

本轮历史中，曾重点处理这些页面的 UI 恢复与样式一致性：

- `html/wechat_account_settings.html`
- `html/check.html`
- `html/order-rankings.html`

目标是恢复玻璃拟态风格、减少样式碎片，并尽量避免脆弱依赖。

### 主线 2：后台模板前端安全收敛

后续开发重点转向：

- `web/templates/admin*.html`

核心目标：

- 修复前端 XSS / 注入风险
- 移除危险的字符串内联 `onclick`
- 给高风险操作补参数校验
- 不大改业务接口

## 已重点处理的文件

### 1. UI / 页面样式相关

- `html/wechat_account_settings.html`
- `html/check.html`
- `html/order-rankings.html`

### 2. 后台安全收敛相关

- `web/templates/admin.html`
- `web/templates/admin_zudui.html`
- `web/templates/admin_ipban.html`
- `web/templates/admin_contact_count.html`
- `web/templates/admin_ip_count.html`
- `web/templates/admin_ip_delete_count.html`
- `web/templates/admin_xiaomagao.html`

## 主要改动摘要

### `web/templates/admin.html`

已完成：

- 新增 `escapeHtml`
- 新增 `escapeAttr`
- 新增 `toSafeInt`
- 新增 `toPositiveInt`
- 用户名显示与弹窗插值转义
- `data-username + this.dataset.username` 取代危险内联字符串参数
- 批量审核 / 批量拒绝 / 删除 / 修改密码 / 修改用户名 的基本参数校验
- 分页页码与总数归一化

### `web/templates/admin_zudui.html`

已完成：

- 替换旧 DOM 版 `escapeHtml`
- 新增 `escapeAttr`
- 新增 `toSafeInt`
- 新增 `toPositiveInt`
- 表格渲染字段转义与数值归一化
- 选中 / 删除记录参数过滤
- 普通列表、疑似垃圾列表、封禁 IP 提交列表分页归一化
- 日期删除参数校验

### `web/templates/admin_ipban.html`

已完成：

- `data-ip + this.dataset.ip` 替换危险内联字符串参数
- IP 列表渲染转义
- 新增 `sanitizeIPs`
- 新增 IP 合法性校验
- 批量封禁 / 解封 / 删数据参数过滤
- 替换旧 DOM 版 `escapeHtml`

### `web/templates/admin_contact_count.html`

已完成：

- `data-contact + this.dataset.contact` 替换危险内联字符串参数
- `contact` / `contact_type` / `*_ago` 字段转义
- `count` 数值归一化
- `min_count` / `page_size` 参数正整数化
- 删除联系方式数据前做空值守卫

### `web/templates/admin_ip_count.html`

已完成：

- `data-ip + this.dataset.ip` 替换危险内联字符串参数
- 表格渲染字段转义
- 新增 `escapeAttr`
- 新增 `toSafeInt`
- 新增 `toPositiveInt`
- 新增 `isValidIP` / `sanitizeIPs`
- 删除动作参数校验
- 分页 `total/page` 归一化

### `web/templates/admin_ip_delete_count.html`

已完成：

- 与 `admin_ip_count.html` 同构修复
- 删除按钮安全传值
- 字段转义
- IP 参数校验
- 分页 `total/page` 归一化

### `web/templates/admin_xiaomagao.html`

已完成：

- 替换旧 DOM 版 `escapeHtml`
- 新增 `escapeAttr`
- 新增 `toSafeInt`
- 新增 `toPositiveInt`
- 新增 `isValidIP`
- 列表渲染字段转义
- 错误信息写表格前先转义
- `unbanIP('...')` 改为 `data-ip + this.dataset.ip`
- `deleteRecord(id)` 增加记录 ID 校验
- `banIP()` / `unbanIP()` 增加 IP 合法性校验
- `pageSize` 归一化

## 当前已达成的阶段性结论

### 前端安全层面

在本次处理范围内，`web/templates/admin*.html` 的**最明显高风险前端问题**已经基本收过一轮：

- 用户/联系方式/IP 直接拼接进内联事件
- DOM 版 `escapeHtml`
- 一批表格渲染中的明显未转义字段
- 删除/封禁/解封类操作的基础参数缺失
- 多个分页分支的 `NaN` / 非法页码问题

### 还没做的事情

这并不代表项目已经完全安全，只是完成了：

- 一轮高风险前端收敛
- 一轮参数校验补强
- 一轮迁服前可维护性整理

尚未系统完成的内容包括：

- 更深层的自动化回归测试
- 后端配置外置化
- Redis 硬编码配置治理
- OpenResty / systemd 标准化模板
- 非 `admin*.html` 页面继续审查

## 当前项目运行与部署关键信息

### 服务入口

- `main.py`
- `run_server.py`

### Python 依赖

- 见 `requirements.txt`
- 核心依赖：FastAPI / uvicorn / httpx / redis / jinja2

### 健康检查

- `/healthz`
- `/readyz`

### Python UDS

- 默认：`/run/wx_service-python/wx_service.sock`

### Go internal socket

- 默认：`/run/wx_service/meituan-query-internal.sock`

### Redis

当前代码中 Redis 依赖：

- Unix socket：`/run/redis/redis-server.sock`
- 密码：写死在 `utils/redis_async.py`

### 运行时数据与迁移

已知重要运行时数据包括：

- `wechat_accounts.runtime.json`
- `activation_codes.json`
- `activation_codes_link.json`
- `activation_codes_meituan_order.json`
- `scenes.json`
- `p_values.json`
- 各类 `.db` 数据文件

其中部分模块已支持：

- 运行时目录优先
- 旧根目录兼容迁移

## 关于迁服

用户当前准备迁服务器，因为旧服务器算力较低。

因此后续 AI / 开发者在继续开发前，应该优先参考：

- `docs/SERVER_MIGRATION_AND_DEPLOY.md`
- `docs/NEXT_DEVELOPMENT_PLAN.md`

## 推荐接手顺序

### 第一步：先看部署文档

阅读：

- `docs/SERVER_MIGRATION_AND_DEPLOY.md`

明确：

- Python 服务怎么起
- OpenResty 怎么接
- Redis 怎么接
- Go / 美团内部服务怎么接
- 运行时数据要迁哪些文件

### 第二步：看开发计划

阅读：

- `docs/NEXT_DEVELOPMENT_PLAN.md`

明确：

- 先迁服
- 再验证
- 再继续开发

### 第三步：看本交接文档

重点理解：

- 哪些文件已被动过
- 哪些区域最容易回归
- 哪些用户偏好必须遵守

### 第四步：迁服后先做手工回归

不要直接继续大改。优先检查：

- 后台页能否打开
- 删除/封禁/修改类按钮是否正常
- `/healthz` 和 `/readyz` 是否正常
- 公众号核心链路是否正常

## 后续 AI 的建议工作策略

- 继续保持小步
- 先做验证，再做下一轮修改
- 不要在迁服前后立即做大规模 UI / 业务重构
- 优先处理“部署稳定性、配置外置化、回归验证”

## 相关文件

- `docs/NEXT_DEVELOPMENT_PLAN.md`
- `docs/SERVER_MIGRATION_AND_DEPLOY.md`
- `CHANGELOG.md`
- `message.md`

## 一句话交接总结

当前项目已经完成一轮以 `web/templates/admin*.html` 为核心的前端安全收敛，并且已经进入**迁服优先、验证优先**阶段；后续接手应先完成新服务器部署与回归，再继续做更深层改造。
