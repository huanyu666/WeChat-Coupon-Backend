# 下一阶段开发计划

## 目标

在**迁移新服务器**的前提下，继续以**低回归风险**的方式推进项目，优先保证：

- 服务可平稳迁移
- 已改页面不回退
- 当前前端安全修复成果可验证
- 后续 AI / 开发者能快速接手

## 已完成事项

- [x] 关键业务页 UI 恢复与统一（重点页）
- [x] `html/wechat_account_settings.html` 玻璃拟态样式修复
- [x] `html/check.html` 页面样式自包含化整理
- [x] `html/order-rankings.html` 页面样式自包含化整理
- [x] `web/templates/admin.html` 第一轮 XSS / 参数校验收敛
- [x] `web/templates/admin_zudui.html` 前端注入与参数校验收敛
- [x] `web/templates/admin_ipban.html` 前端注入与参数校验收敛
- [x] `web/templates/admin_contact_count.html` 前端注入与参数校验收敛
- [x] `web/templates/admin_ip_count.html` 前端注入与分页安全收尾
- [x] `web/templates/admin_ip_delete_count.html` 前端注入与分页安全收尾
- [x] `web/templates/admin_xiaomagao.html` 前端注入与参数校验收敛
- [x] 迁服/部署文档整理
- [x] AI 交接文档整理

## 迁服前高优先级事项

- [ ] 手工回归 `web/templates/admin.html`
- [ ] 手工回归 `web/templates/admin_zudui.html`
- [ ] 手工回归 `web/templates/admin_ipban.html`
- [ ] 手工回归 `web/templates/admin_contact_count.html`
- [ ] 手工回归 `web/templates/admin_ip_count.html`
- [ ] 手工回归 `web/templates/admin_ip_delete_count.html`
- [ ] 手工回归 `web/templates/admin_xiaomagao.html`
- [ ] 在新服务器验证 `/healthz`
- [ ] 在新服务器验证 `/readyz`
- [ ] 验证 OpenResty 到 Python UDS 转发是否正常
- [ ] 验证 Redis Unix Socket 与密码配置是否匹配新服务器
- [ ] 验证 Go / 美团内部服务 socket 是否可用，或确认降级策略可接受
- [ ] 确认运行时数据文件已完整迁移

## 迁服后高优先级事项

- [ ] 完成一次完整的公众号主流程联调
- [ ] 完成一次后台管理关键功能联调
- [ ] 检查新服务器日志与句柄占用情况
- [ ] 检查 `/readyz` 中 `go_reachable` / `go_health_status`
- [ ] 检查 Redis 连接是否稳定
- [ ] 检查运行时目录、日志目录、socket 目录权限

## 中优先级事项

- [ ] 将 `utils/redis_async.py` 中强绑定的 Redis 连接配置外置化
- [ ] 将更多基础设施参数（socket 路径、运行目录）统一外置到环境变量或部署配置
- [ ] 为 OpenResty / systemd / 1Panel 整理标准化部署模板
- [ ] 为运行时数据目录整理备份与恢复脚本
- [ ] 继续审查非 `admin*.html` 页面中的类似前端注入模式
- [ ] 为关键后台操作补自动化 smoke test 或最小回归脚本

## 低优先级事项

- [ ] 进一步整理老旧 UI 代码与历史遗留样式
- [ ] 评估是否抽取前端通用 escape / 参数校验 helper
- [ ] 审查并减少运行时对历史根目录数据文件的兼容分支
- [ ] 梳理 UI 原型目录 `/UI` 的最终去留

## 推荐执行顺序

### 阶段 1：迁服准备

- [ ] 备份旧服务器代码、配置、运行时数据
- [ ] 按 `docs/SERVER_MIGRATION_AND_DEPLOY.md` 搭建新服务器
- [ ] 启动 Python 服务、Redis、Go 内部服务、OpenResty
- [ ] 完成健康检查与核心页面可用性检查

### 阶段 2：迁服后验证

- [ ] 验证后台管理页关键交互
- [ ] 验证账号配置页与核心业务链路
- [ ] 观察日志、错误率、socket、资源占用

### 阶段 3：继续开发

- [ ] 如迁服稳定，继续做剩余前端安全审查
- [ ] 再考虑更深的结构整理与基础设施外置化

## 执行原则

- 继续保持**单类问题集中处理**
- 优先做**低风险、可验证**的改动
- 不在迁服前进行大规模业务重构
- 每做完一轮修改，先验证，再继续下一轮
- 避免一次同时改动 UI、业务逻辑、部署结构三条线
