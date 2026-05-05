# wx-coupon legacy 运行数据收口方案

> 2026-05-05 更新：第一阶段读路径收口已经开始。运行时代码和脚本默认只应使用 `runtime-data/`；迁移导出默认不包含 `legacy_project_root`，只有显式 `--include-legacy` 才会带上旧根目录运行文件。旧迁移包导入兼容仍保留，用于历史恢复。

生成时间：2026-04-30 06:45 UTC

## 当前状态

- 主运行数据目录已经是 `runtime-data/`。
- 本文档描述的是兼容阶段的收口思路；最终版应删除 legacy fallback 和 `WX_DISABLE_LEGACY_RUNTIME_FALLBACK` 开关。
- 目标状态是：激活码、Scene、P 值、商家券目录和商家券 SQLite 全部只读写 `runtime-data/`。

## 小步收口流程

1. 在 dev 以 runtime-only 基线启动，确认后台、公众号配置、激活码、Scene、P 值、商家券链路正常。
2. 在独立迁服演练目录重复同样验证，确认迁移包已包含所有核心数据。
3. prod 完成兼容观察后切换到 runtime-only。
4. 删除各模块的 legacy copy / fallback 分支和相关环境开关。

## 不做的事

- 不删除根目录历史运行文件。
- 不提交真实运行数据。
- 不删除根目录历史运行文件本身；只删除运行时代码里的 legacy 读取和自动复制逻辑。
