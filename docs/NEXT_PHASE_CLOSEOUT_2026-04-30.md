# wx-coupon 下一阶段收尾执行记录

执行时间：2026-04-30 06:43-06:50 UTC
dev：`/www/wwwroot/wx-coupon-dev`
prod：`/www/wwwroot/wx-coupon-prod`

## 本轮完成

- 生成工作树交付边界：`docs/WORKTREE_DELIVERY_BOUNDARY_2026-04-30.md`。
- 完成独立目录迁服演练：`docs/MIGRATION_DRILL_2026-04-30.md`。
- 生成 legacy 运行数据收口方案：`docs/LEGACY_RUNTIME_RETIREMENT_2026-04-30.md`。
- 增强 `scripts/business_smoke_check.py`：有后台凭据时额外验证 Cookie-only 会话、公众号配置读取、迁移状态和迁移导出。
- 当时为过渡期保留了兼容开关；最终版中这些兼容项应被删除，不能继续作为默认部署基线。

## 生产同步

- 已同步默认兼容代码到 prod：认证、路径工具、smoke 脚本、Compose 环境项、环境样例和新增文档。
- 同步前运行数据备份：`backups/wx-runtime-backup-20260430-064723.tar.gz`。
- prod 重建自动备份：`backups/wx-runtime-backup-20260430-064746.tar.gz`、`backups/wx-runtime-backup-20260430-064939.tar.gz`。
- 同步前代码备份：`backups/code-next-closeout-before-sync-20260430-064736.tar.gz`。
- 首次 prod 重建时因漏同步 `utils/auth_utils.py` 出现 `SESSION_COOKIE_NAME` import 错误；已补同步并重建，最终状态 healthy。

## 验证结果

- dev：AST 检查通过、内联 JS `node --check` 通过、`scripts/docker_dev_up.sh` 通过。
- 迁服演练：独立目录 `wx-coupon-drill` 启动通过，页面 200，商家券 DB 表存在，微信签名校验和订阅事件 smoke 通过；演练容器已停止。
- prod：`./status.sh prod` 通过；`/index`、`/material`、`/wechat-account-settings`、`/migration` 均返回 200；近期 app 日志未见 ImportError/Traceback/ERROR。

## 后续建议

- 该文档记录的是 2026-04-30 的阶段性收尾，不代表最终版基线；最终版应移除兼容开关并完成 Cookie-only / runtime-only 收口。
- Git 工作树仍未提交，运行数据删除痕迹仍只应记录，不应混入代码提交。
