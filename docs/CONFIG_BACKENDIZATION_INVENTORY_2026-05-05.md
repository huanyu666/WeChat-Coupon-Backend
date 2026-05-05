# 配置后台化盘点

最后更新：2026-05-05

## 当前结论

业务配置已经大面积后台化。旧 TOML 文件仍保留为默认模板、兼容来源或低频技术配置来源，不应再作为日常业务配置主入口。

## 已后台化的配置

- 公众号基础信息、默认回复、欢迎语、授权用户、URL 模式和启用处理器：`/wechat-account-settings`
- 关键词回复、菜单 CLICK 回复、公众号小程序 appid 列表：`/wechat-account-settings`
- 美团小程序回复、美团小程序链接解析、美团/点评链接识别、商家券查看回复和商家券提示语：`/wechat-account-settings`
- 链接识别提示词、链接处理配置、接单排行榜全局配置和短链配置：`/system-settings`
- 管理员账号重置：`/system-settings`

## 仍保留的 TOML 模板

- `miniprogram/miniprogram_config.toml`
- `text_processors/keyword_responses.toml`
- `text_processors/click_event_responses.toml`
- `link_handlers/link_config.toml`
- `text_processors/merchant_coupon_prompts.toml`
- `text_processors/order_leaderboard.toml`

这些文件的定位是模板和兼容输入。运行时配置优先级应保持为：

1. `runtime-data/wechat_accounts.runtime.json` 和 `runtime-data/system_settings.runtime.json`
2. 旧 TOML 模板默认值
3. 代码内保守默认值

## 下一轮后台化建议

- 高频业务文案和账号级开关继续放入 `/wechat-account-settings`。
- 全局匹配规则、排行榜、短链和链接识别策略继续放入 `/system-settings`。
- Redis、socket、服务端口、部署模式、密钥等低频技术参数继续保留在 `.env`。
- 不新增复杂页面前，优先补齐现有页面已支持但文档仍误导用户去改 TOML 的配置说明。
