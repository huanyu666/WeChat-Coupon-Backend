# 短链 404 与空链接问题交接记录

日期：2026-05-01

## 当前用户反馈

用户反馈：

1. 发送小程序到公众号后，回复中的短链点击进入是 `404`
2. 直接发送店铺链接，回复中的短链也是 `404`
3. 用户认为“问题没有解决”

用户提供的回复样本见：

- [回复.txt](/www/wwwroot/wx-coupon-dev/docs/回复.txt)

样本里最关键的现象：

1. `领取商家券① (可切号)` 显示为：

```html
<a href=" ">领取商家券① (可切号)</a>
```

这是一条空链接，不是短链。

2. `领取商家券2(可切号，不一定有)` 显示为：

```html
<a href="http://jd2.top/s9ZTzWRD">领取商家券2(可切号，不一定有)</a>
```

这是一条短链。

## 本轮已确认事实

### 1. 之前的 404 根因

此前服务只有“创建短链”能力，没有“访问短链后跳转”的 Python 路由。

因此：

- `http://jd2.top/<shortKey>`
- `http://jd2.top/key/<shortKey>`

在站点侧没有被接住，点击后直接 `404`。

### 2. 本轮已补的内容

已新增短链跳转路由：

- [routes/shortlink.py](/www/wwwroot/wx-coupon-dev/routes/shortlink.py)

已接入主应用：

- [routes/__init__.py](/www/wwwroot/wx-coupon-dev/routes/__init__.py)
- [main.py](/www/wwwroot/wx-coupon-dev/main.py)

路由行为：

1. 读取 Redis key：`wx:shortlink:key:<shortKey>`
2. 解析其中的 JSON
3. 取出 `url`
4. 返回 `302` 跳转

支持两个入口：

- `/{short_key}`
- `/key/{short_key}`

### 3. 已做的验证

#### dev

`curl` 验证过：

- `http://127.0.0.1:18080/lzVS7`
- `http://127.0.0.1:18080/key/lzVS7`

路径已存在，不再是路由缺失。

#### prod

prod 已同步代码并重建容器。

验证过以下真实短码都返回 `302`：

- `s9ZTzWRD`
- `qjgLCsBz`
- `o7BPCRnp`

同时 Redis 中这几个 key 都存在：

- `wx:shortlink:key:s9ZTzWRD`
- `wx:shortlink:key:qjgLCsBz`
- `wx:shortlink:key:o7BPCRnp`

并且能解析出对应长链接。

## 当前仍未彻底解决的点

### 1. 用户仍反馈“问题没有解决”

虽然服务侧已验证真实短码会 `302`，但用户仍判断无效，说明还有以下可能：

1. 用户点到的是旧回复缓存内容，微信端未刷新
2. `jd2.top` 的外部入口和当前 `8080` 站点之间还有代理层差异
3. 微信内打开短链时存在 Host / 代理 / 缓存行为，与本地 `curl` 结果不同
4. 真正影响用户体验的不是 `领取商家券2`，而是第一条 `领取商家券①` 仍然是空链接

### 2. 最可疑的未解问题：`领取商家券①` 为空链接

样本里第一条不是短链，也不是长链接，而是：

```html
<a href=" ">领取商家券① (可切号)</a>
```

这说明问题不只是“短链访问 404”，还包括“第一条链接生成阶段就已经坏了”。

这条才更可能是用户仍然认为“没解决”的核心原因。

## 下一窗口应优先继续查的方向

### 方向 1：定位为什么会生成 `href=" "`

重点看这些文件：

- [miniprogram/meituan.py](/www/wwwroot/wx-coupon-dev/miniprogram/meituan.py)
- [text_processors/meituan_miniprogram_link_processor.py](/www/wwwroot/wx-coupon-dev/text_processors/meituan_miniprogram_link_processor.py)
- [text_processors/meituan_link.py](/www/wwwroot/wx-coupon-dev/text_processors/meituan_link.py)
- [utils/meituan_utils.py](/www/wwwroot/wx-coupon-dev/utils/meituan_utils.py)

当前怀疑点：

1. `abuild_go_shortlink_html()` 某些情况下返回空字符串
2. 调用方把空字符串继续拼进回复
3. 某层 fallback 没走到长链接，最后被替换成了空 href
4. `_build_miniprogram_anchor()` 固定输出 `href=" "`，而某条本应为短链的内容被错误走成了小程序 anchor 逻辑

### 方向 2：直接抓 prod 实际生成该条回复时的日志

建议在 prod 容器里查与该店铺或该 `poi_id_str` 相关的日志：

- `D5qWceIQDyorVUe3ZHlQ5AI`
- `s9ZTzWRD`
- `qjgLCsBz`
- `o7BPCRnp`

尤其关注：

- `创建短链接成功`
- `创建短链接失败`
- `构建的链接HTML`
- `long_link`
- `full_url`

### 方向 3：从 prod 内部直接构造同一条回复

不是只测短链跳转，而是要在 prod 容器里直接跑对应处理器，拿到完整回复字符串，确认第一条为什么会变成空 href。

## 本轮已改动文件

- [routes/shortlink.py](/www/wwwroot/wx-coupon-dev/routes/shortlink.py)
- [routes/__init__.py](/www/wwwroot/wx-coupon-dev/routes/__init__.py)
- [main.py](/www/wwwroot/wx-coupon-dev/main.py)

另外，本轮之前已存在但与本问题相关的改动包括：

- 商家券 H5 链接改为先走 Go 短链服务
- `GO_SHORTLINK_PUBLIC_BASE_URL` 改为 `http://jd2.top`

## 本轮结论

本轮只解决了“短链路由缺失导致点击 404”的一部分问题。

但从用户给出的回复样本看，完整问题并未闭环，因为：

1. 第一条 `领取商家券①` 仍然是空链接
2. 用户从真实使用结果判断“问题没有解决”

因此下一窗口应把重点放在：

- “为什么生成了空 href”
- “为什么用户端点击仍表现异常”

而不是继续只围绕短链路由本身排查。
