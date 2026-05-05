# 短链功能交接说明

最后更新：2026-05-04

## 当前结论

短链主链路已经落地，不再是“待设计”状态。

当前已完成：

1. 内置短链服务已在本项目内实现，不依赖外部短链平台。
2. 短链公开路径固定为 `/key/{code}`。
3. 短链存储、创建、解析、过期删除、冲突处理已完成。
4. 公众号被动回复已接入统一短链转换。
5. 小程序解析回复已改为“先生成较完整内容，再统一短链，短链后仍超预算才降级精简版”。
6. 已补充短链日志：
   - `short_key`
   - 原始 URL 长度
   - 目标 URL 长度
   - TTL
   - 过期时间
   - 公开短链域名
   - 批量转换 `matched/success/failed/skipped/reused/existing`
   - 最终回复 `content_bytes/xml_bytes`

## 当前配置

生产当前短链配置：

```text
公开域名: https://98vx.cn
公开路径: /key/{code}
默认 TTL: 604800 秒（7 天）
自动清理: Asia/Shanghai 每天 00:00
```

配置来源优先级：

1. `runtime-data/system_settings.runtime.json` 中的 `shortlink_config`
2. `.env` 中的 `GO_SHORTLINK_PUBLIC_BASE_URL`

环境边界：

- prod 使用真实可访问的正式域名，例如 `https://98vx.cn`
- dev 可使用 `127.0.0.1` 或测试域名，不要求与 prod 完全一致
- 配置检查重点是同一环境内 runtime 与 `.env` 是否冲突，以及 prod 是否误配

部署脚本已支持自动写入默认短链配置：

```bash
./deploy.sh --public-url https://98vx.cn --shortlink-ttl-seconds 604800
```

也可单独执行：

```bash
python3 scripts/configure_shortlink_settings.py \
  --public-base-url https://98vx.cn \
  --ttl-seconds 604800
```

## 本轮已解决的问题

### 1. 短链访问 404

已补 `routes/shortlink.py` 并接入主应用，`/key/{code}` 可返回 `302`。

### 2. 小程序回复过早压缩

旧行为是小程序处理器先把超长回复压成保守版，导致即使后续全局短链成功，内容也已经损失。

现行为：

1. 小程序处理器保留完整回复。
2. 微信被动回复阶段统一做短链替换。
3. 短链替换后根据 `content_bytes/xml_bytes` 和现有字符保护逻辑判断是否仍超预算。
4. 只有仍超预算时才退回精简 fallback。

### 3. 短链成功日志缺失

已补充成功日志，并将关键诊断提升到生产默认可见级别。

## 当前验收状态

已完成：

- dev `status.sh` 通过
- prod `status.sh` 通过
- prod `/healthz` 200
- prod `/readyz` 200
- 生产短链 `GET /key/{code}` 已验证可返回 `302`
- 短链日志已验证可见
- 批量短链转换日志已验证可见
- 超长回复预算 helper 已验证：完整超预算、fallback 合预算
- 真实公众号端到端验收已完成：
  - `2026-05-04 20:49:56 UTC`
  - 日志出现 `被动回复短链后未降级`
  - 日志出现 `短链创建成功`
  - 日志出现 `短链批量转换完成`
  - 旧的 `消息解密失败` 已确认是错误 `EncodingAESKey` 导致，不是服务端主链路故障

持续观察项：

- 真实公众号验收样本目前仍然不多，后续继续保留运营侧观察
- 后续新增样本时，继续对照日志确认是：
  - `被动回复短链后未降级`
  - 或 `被动回复已降级为精简版`

## 剩余事项

现在短链功能本身没有明确的代码尾项，剩余主要是文档同步、配置漂移观察、低风险 legacy 收口。

这不是主链路 blocker，而是收尾治理事项。

## 相关文件

- [routes/shortlink.py](/www/wwwroot/wx-coupon-dev/routes/shortlink.py)
- [routes/wechat.py](/www/wwwroot/wx-coupon-dev/routes/wechat.py)
- [text_processors/meituan_miniprogram_link_processor.py](/www/wwwroot/wx-coupon-dev/text_processors/meituan_miniprogram_link_processor.py)
- [utils/shortlink_service.py](/www/wwwroot/wx-coupon-dev/utils/shortlink_service.py)
- [scripts/configure_shortlink_settings.py](/www/wwwroot/wx-coupon-dev/scripts/configure_shortlink_settings.py)
