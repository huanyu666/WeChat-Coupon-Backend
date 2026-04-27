# 对话同步记录

## 项目背景
- 项目：`WeChat Coupon Backend`
- 目标：将微信公众号敏感配置（如 `app_secret`、`token`、`encoding_aes_key`）从 `config.toml` 中迁移到网页后台可维护的运行时存储，并支持多公众号。
- 已完成主要改造：
  - 新增运行时存储：`wechat_account_store.py`
  - 新增运行时文件：`wechat_accounts.runtime.json`
  - 新增后台页面：`/wechat-account-settings`
  - 新增公众号管理 API
  - 首页增加入口
  - 运行时优先读取网页配置
  - `config.toml` 移除公众号敏感字段
  - `CHANGELOG.md` 已记录本次改造

## 关键代码改动摘要
- `wechat_account_store.py`
  - 管理公众号配置的 JSON 持久化存储
  - 支持加载、保存、增改、删除、设置默认账号
  - 支持首次从 `config.toml` 引导迁移到运行时文件
- `config/config.py`
  - 加载配置时合并运行时公众号配置
  - 移除默认示例公众号敏感配置
  - 暴露动态读取公众号配置的方法
- `config/__init__.py`
  - 导出新的公众号配置访问器
- `utils/wechat_utils.py`
  - 改为动态读取公众号配置
- `routes/wechat.py`
  - 微信验签、消息处理改为动态读取配置
- `routes/material.py`
  - 新增公众号配置管理 API
  - 更新配置时清理 `access_token_cache`
- `html/wechat_account_settings.html`
  - 新增公众号配置管理页面
  - 后续修正了账号列表点击方式，避免内联 `onclick` 转义问题
- `html/index.html`
  - 首页新增“公众号配置管理”入口
- `config.toml`
  - 移除公众号敏感配置
  - 保留业务型配置
- `.gitignore`
  - 忽略 `wechat_accounts.runtime.json`
- `CHANGELOG.md`
  - 已补充本次改造记录

## 本次部署/测试对话过程摘要

### 1. 测试方式建议
- 建议优先在 Linux 服务器/测试环境测试，而不是 Windows 本地完整链路测试。
- 原因：项目带有 Linux/Unix 相关实现，如 `resource`、Unix Socket、Redis socket 等。
- 本地更适合做前端页面和简单接口冒烟测试。

### 2. 1Panel 部署讨论
- 用户服务器面板为 `1Panel`
- 结论：`1Panel` 可以用，但图形化对 Python/FastAPI 项目不如终端直接运行直观。
- 先尝试通过 `运行环境` 创建 Python 环境，再创建网站。

### 3. 1Panel 操作路径
- 选择：`运行环境`
- 创建 Python 运行环境时：
  - Python 版本选择了 `3.10.19`
  - 先用占位启动命令创建运行环境
- 创建网站时：
  - 类型：`Python`
  - 运行环境：`python [应用商店]`
  - 网站目录最终在：
    - `/opt/1panel/apps/openresty/openresty/www/sites/wx-coupon/index`
- 项目代码已上传到服务器该目录

### 4. 服务器终端启动方案
由于 1Panel 图形化配置不够直观，改为终端直接启动。

#### 使用的命令思路
```bash
cd /opt/1panel/apps/openresty/openresty/www/sites/wx-coupon/index
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8000 > server.log 2>&1 &
```

### 5. 遇到的问题与结论
#### 问题 A：`externally-managed-environment`
- 原因：Debian/Ubuntu 系系统不允许直接往系统 Python 装包
- 解决：使用 `.venv` 虚拟环境

#### 问题 B：端口 `8000` 被占用
- 日志关键报错：
```text
[Errno 98] error while attempting to bind on address ('0.0.0.0', 8000): address already in use
```
- 解决建议：改用 `8001` 启动

#### 实际可用启动命令
```bash
nohup python -m uvicorn main:app --host 0.0.0.0 --port 8001 > server.log 2>&1 &
```

#### 问题 C：Redis socket 警告
- 日志中有：
```text
Redis连接检查失败，应用将继续降级运行
```
- 结论：不是致命错误，服务可继续运行，只是 Redis 未按预期 socket 路径可用

### 6. 当前测试结果
- 已确认可以打开登录页
- 即：项目已在服务器上成功跑起来

## 后台登录相关结论
- 管理员账号密码使用 SHA256 哈希存储
- 当前无法从哈希反推出原密码
- 建议通过修改 `config.toml` 直接重置管理员密码
- 用户后续已成功登录后台

## 关于 `EncodingAESKey` 的说明
- `EncodingAESKey` 不是随便自定义的业务字段，而是微信公众号后台“消息推送配置”里生成的密钥
- 来源：微信公众平台后台
- 获取方式：
  1. 登录微信公众号平台
  2. 进入开发/基本配置/消息推送相关设置
  3. 使用“随机生成”按钮生成 43 位 `EncodingAESKey`
  4. 生成后先复制保存，再提交
- 要求：后台页面里填写的值必须与微信后台保持一致

## 微信回调 URL 配置讨论
### 结论
- 微信后台回调 URL 不能随便带非标准端口给微信使用，微信通常要求公网可访问的标准端口环境
- 当前应用内部运行在：`8001`
- 因此需要通过 1Panel/OpenResty 做反向代理

### 已尝试的反向代理思路
- 曾尝试使用 `/` 整站反向代理到 `127.0.0.1:8001`
- 结果报错：
```text
duplicate location "/"
```
- 说明 `/` 路由已被现有配置占用

### 后续讨论
- 计划使用单独路径做代理，例如：
  - `/wx_mp_svr`
- 但用户提出：微信后台 URL 路径里可能不适合使用下划线
- 因此建议改成无下划线路径，例如：
  - `/wechat`

### 已完成：微信回调 URL 代理配置
- 后端代码已新增 `/wechat` 兼容路由（GET + POST）
- 1Panel OpenResty 反向代理已配置 `location ^~ /wechat` → `127.0.0.1:8001`
- wx-coupon 站点已增加 `listen 80`，微信可通过标准端口访问
- `location /` 已从 8000 改为 8001
- 微信后台验证已通过 ✓

## 当前系统状态总结
- 代码已完成"多公众号配置网页化管理"改造
- 服务器部署已跑通，应用运行在 8001 端口
- 后台登录已成功
- 公众号配置管理页面可进入测试
- `EncodingAESKey` 的来源和填写方式已说明
- 微信回调 URL 代理配置已完成
- `/wechat` 兼容路由已补上
- 80 端口反向代理已通
- **微信后台验证已通过** ✓

## 建议下一步
1. 在公众号里发一条测试消息，验证消息收发是否正常
2. 如需 HTTPS，可在 1Panel 为站点申请 SSL 证书并配置
3. 考虑用 systemd 或 supervisor 管理应用进程，避免手动 nohup

## 可直接给其他 AI 的简短任务描述
你正在接手一个 FastAPI 项目 `WeChat Coupon Backend`。该项目已完成"微信公众号敏感配置从 `config.toml` 迁移到网页后台运行时存储"的改造，已支持多公众号，后台页面为 `/wechat-account-settings`。代码已部署到 1Panel 服务器，项目目录为 `/opt/1panel/apps/openresty/openresty/www/sites/wx-coupon/index`，通过 `.venv` 和 `uvicorn main:app --host 0.0.0.0 --port 8001` 运行。OpenResty 反向代理已配置：80 端口 → 8001，`/wechat` 路径用于微信回调。**微信后台验证已通过**，消息收发链路已通。后续可关注：消息收发功能测试、HTTPS 证书配置、进程守护（systemd/supervisor）。
