# 清理报告

清理日期：2026-08-14

## 清理前基线

- 文件数：2973
- 总大小：16498461 bytes
- 完整备份：`../backups/meituan-expand-before-cleanup-20260814.zip`
- 备份 SHA256：`1E911D30E5C7EE3596196024A51B25A22C0D86CAC5A33AC187885958B89F02C7`
- 备份条目数：5124（含目录条目）
- 文件清单：`../backups/meituan-expand-before-cleanup-20260814-files.csv`
- 清单 SHA256：`50EFE0BC2D8AD38EEFF5CC696E1BA48C54C40C40D47AACE63692CC04A26A96C4`

## 删除范围

- `.venv/`：从 Linux 复制来的旧虚拟环境，不能在当前或新 Linux 路径中复用。
- `OUTPUT/`：2822 个小程序解包/反编译文件，当前运行闭包无引用。
- 根目录 7 个 `.har`：历史抓包，包含会话数据且不参与当前运行。
- `exports/`：13 个历史输出。
- `web/jobs/`：37 个历史任务，包含完整链接、token 和日志。
- `scripts/__pycache__/`、`web/__pycache__/`：Python 缓存。
- `scripts/` 中 57 个实验/缺依赖脚本：临时探针、单账号复现、Playwright 实验、旧 HAR 回放、直连 signer 包装器及依赖原作者本机目录的测试。
- `config/` 中 5 个运行配置：管理员密钥及 4 份代理供应商密钥。
- `web/start.bat`：后续开发和部署均为 Linux。
- `web/templates/`：空目录。

## 保留的运行闭包

- `web/app.py`
- `web/static/index.html`
- `web/static/admin.html`
- `scripts/pure_mttouch_proxy_once.py`
- `scripts/proxy_pool_ydaili.py`
- `scripts/exchange_response_class.py`
- `web/requirements.txt`
- `web/mt-expand.service`
- `web/nginx-expand.conf.example`

## 清理后结果

- 文件数：19
- 总大小：约 128 KB
- Linux 交接包：`../backups/meituan-expand-linux-handover-20260814.tar.gz`
- 交接包校验：`../backups/meituan-expand-linux-handover-20260814.tar.gz.sha256`

## 回滚

Windows 当前工作机：

```powershell
powershell -ExecutionPolicy Bypass -File .\docs\rollback-cleanup.ps1
```

Linux：

```bash
bash docs/rollback-cleanup.sh
```

回滚脚本会先校验备份 SHA256，再把清理后的目录改名保留，不会直接覆盖删除。
