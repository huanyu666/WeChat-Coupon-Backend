# wx-coupon AI 快速交接

最后更新：2026-05-04
开发目录：`/www/wwwroot/wx-coupon-dev`
生产目录：`/www/wwwroot/wx-coupon-prod`
详细盘点：`docs/VERSION_INVENTORY_2026-05-04.md`

## 项目定位

这是一个已完成 Docker 化、后台化和迁移化收口的微信公众号优惠券服务，不是起步项目。

主线已经从“搭主功能”转到：

- 生产验收
- 文档同步
- 配置进一步后台化
- 小范围收尾增强

## 当前状态

- dev：`/www/wwwroot/wx-coupon-dev`，`./status.sh dev` 通过
- prod：`/www/wwwroot/wx-coupon-prod`，`./status.sh prod` 通过
- dev/prod `/healthz`、`/readyz` 均为 200
- 生产短链域名已切为 `https://98vx.cn`
- dev 短链公开地址允许使用本地或测试地址，不要求与 prod 完全一致
- 生产短链默认 TTL 为 7 天，凌晨自动清理
- 公众号回调、后台配置、迁移、短链、商家券、关键词回复、菜单回复、小程序解析主链路均已落地
- 真实公众号验收已补齐：`2026-05-04 20:49:56 UTC` 日志已出现 `被动回复短链后未降级`

## 已完成主能力

- Docker dev/prod Compose
- 一键部署、更新、状态、诊断、备份恢复
- 后台登录和管理员管理
- 公众号配置后台化
- 系统设置后台化
- 数据迁移包导出 / inspect / 导入
- 短链服务、短链路由、短链日志
- 运行数据统一收口到 `runtime-data/`

## 当前真正剩余的尾项

1. 文档持续同步
   - 旧交接文档容易落后于当前真实代码
2. 局部后台化继续推进
   - 仍有少量业务开关和历史路径需要继续收口
3. 配置漂移持续观察
   - dev/prod 运行时配置仍建议持续比对
   - 但不要求 dev 与 prod 使用同一个短链公开域名

## 本轮之后不要再误判的点

- 短链不是“方案中”，而是已实现并已部署。
- 生产不是“仅 IP HTTP”，而是短链域名已接到 `https://98vx.cn`。
- 部署脚本已能写入短链默认域名和 TTL。
- `preflight` 严格模式主要拦 runtime/env 冲突、缺失关键字段、prod 误配，不强制 dev 复用 prod 域名。
- 小程序回复已不是“先压缩再回复”，而是“先完整生成，再统一短链，再按预算决定是否降级”。

## 运维提醒

- 真实公众号端到端验收已经完成，不再是 blocker。
- 微信消息异常时，先查 `/wechat` 回调日志，再核对 `EncodingAESKey / AppID / Token / ToUserName`。
- `runtime_config_check` 的严格模式主要拦同一环境内的 runtime/env 冲突、关键字段缺失和 prod 误配；dev/prod 使用不同短链域名是允许的。
