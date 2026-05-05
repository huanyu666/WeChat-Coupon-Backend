# dev/prod 同步边界

最后更新：2026-05-05

## 当前事实

- dev 目录：`/www/wwwroot/wx-coupon-dev`
- prod 目录：`/www/wwwroot/wx-coupon-prod`
- dev 是开发源，prod 是部署实例。
- prod 当前存在大量本地部署差异，并且 Git 状态显示落后远程提交；不要直接在 prod 上执行 `git pull` 或用 dev 覆盖 prod。

## dev 当前改动分组

本轮开始前，dev 工作树已有未提交改动：

- 应保留并纳入交付判断的文档改动：短链交接、生产部署说明、AI 快速交接、版本盘点。
- 应保留并纳入交付判断的脚本改动：dev/prod smoke、doctor、preflight、status、运行时配置检查。
- 不应提交的运行时数据：`.env`、`runtime-data/`、`logs/`、`backups/`、数据库文件、真实配置和密钥。

## prod 同步规则

1. 在 dev 完成修改并通过 `./status.sh dev`。
2. 在 prod 只读执行 `./status.sh prod`，确认当前生产健康。
3. 备份 prod 运行数据和必要配置。
4. 只同步白名单代码文件，不同步 `.env` 和 `runtime-data/`。
5. 重建或重启 prod 后再次执行 `./status.sh prod`。
6. 观察真实公众号日志，确认微信回复和短链行为正常。

## 禁止操作

- 不要在 prod 上直接开发。
- 不要在 prod 上用 `git reset --hard` 或 `git checkout --` 清理脏改动。
- 不要用 `git add .` 提交。
- 不要提交或复制真实密钥、运行时数据库、日志和备份包。
