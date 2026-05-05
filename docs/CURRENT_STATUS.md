# wx-coupon 当前状态

最后更新：2026-05-05

## 结论

项目当前是生产可用的后期收尾版本，不是半成品，也不是冻结版。

当前最新事实源：

- `docs/VERSION_INVENTORY_2026-05-04.md`
- `docs/AI_DEVELOPMENT_HANDOFF.md`
- `README.md`

历史交接文档仍保留用于追溯，但如果内容与上述文件冲突，以上述最新事实源为准。

## 已完成主能力

- Docker dev/prod 部署、健康检查和 smoke 入口已完成。
- 后台登录已切到 HttpOnly Cookie 会话，首次管理员可在 `/login` 初始化。
- 公众号账号级配置通过 `/wechat-account-settings` 维护。
- 全局业务配置通过 `/system-settings` 维护。
- 数据迁移包导出、导入前预览、导入、回滚和迁移演练链路已完成。
- 内置短链系统已完成，公开路径固定为 `/key/{code}`。
- 运行数据主目录已收口到 `runtime-data/`。

## 当前收尾重点

- 继续用真实公众号样本观察回复链路和短链降级情况。
- 清理旧文档里的过时阶段结论，避免后续接手误判成熟度。
- 继续扫描少量仍从旧 TOML 模板读取的业务配置点，优先迁到现有后台页面。
- 逐步减少 legacy 运行路径兼容代码，但不直接删除服务器上的历史数据文件。
- 补强自动化回归，减少后台页面和微信消息链路只能手工验收的问题。

## Git 与部署边界

- `/www/wwwroot/wx-coupon-dev` 是开发源。
- `/www/wwwroot/wx-coupon-prod` 是生产部署实例，不作为直接开发源。
- 同步 prod 前必须先备份，并先在 dev 跑通 `./status.sh dev`。
- 禁止使用 `git add .`；提交前按文件白名单 staging。
- 不提交 `.env`、`runtime-data/`、`logs/`、`backups/`、`config.toml`、数据库文件和真实密钥。
