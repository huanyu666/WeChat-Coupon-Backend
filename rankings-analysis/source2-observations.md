# 来源 2 观察记录

- 最简请求：`GET /page/wm/md?key=mt-time-07291200&name=%E9%BA%A6%E5%BD%93%E5%8A%B3`，已实测返回 `shopData`。
- `t` 是可选的个人秒级定位时间；为避免泄露个人位置，本文件和 cURL 均省略其真实值。
- 页面脚本未使用 fetch/XHR/WebSocket/SSE；`shopData` 位于初始 HTML。
- `key=mt-time-07291130` 返回空榜，页面文本为“暂无排行榜数据”。
- 不存在的 `name` 回退到 `shopData` 第一项“华莱士”。
- 前端提供 30 分钟粒度选择，并将其无转换地写入 key 的 `HHmm` 部分。
- 无 Cookie 独立 GET 返回 HTTP 200；未观察到认证页、认证请求头、客户端加密、签名、nonce 或限流提示。
