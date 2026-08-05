# 三方排行榜数据源接口分析

分析日期：2026-07-29（浏览器时区：Asia/Shanghai）。所有样例均为采样时刻快照；来源 3 数据会随记录补充而变化。

## 结论摘要

| 来源 | 可采集路径 | 数据形态 | 登录/认证结论 | 可作为新榜单来源 |
| --- | --- | --- | --- | --- |
| 1 `naiba666.com` | 排期：`GET /md/api.php?action=get_activities`；榜单：`GET /md/ranking.php` | 排期 JSON；榜单服务端渲染 HTML | 排期无 Cookie；榜单 Cookie/会话认证必需 | 是，优先来源；采集器必须解决认证边界 |
| 2 `mt.liliabc.fun` | `GET /page/wm/md` | HTML 内嵌 `shopData`；秒级 Unix 时间戳数组 | 无 Cookie 实测可用 | 是，适合作为补充和交叉校验 |
| 3 `waimaiyouhui.top` | `GET /api/order-rankings/query` | JSON；毫秒级接单时间，游标分页 | 无 Cookie 实测首页及下一页均可用 | 是，适合作为补充；不要使用 `rank_token` |

同一测试口径为“麦当劳 + 2026-07-29 + 12:00”。三者不能直接把记录数当作同一集合：来源 1 为 522 人的秒分布，来源 2 为 626 条当前公众号数据且明确排除抢跑用户，来源 3 为动态的毫秒级排行记录（本次复核为 64 条）。来源 1/2 的精度为秒，来源 3 为毫秒；因此只可比较场次覆盖、数量级与最后时间，不能强行合并为同一名次集合。

## Cookie 验证矩阵

所有“无 Cookie”测试均使用全新 HTTP 请求，未复用浏览器会话，也未读取 Cookie 值。

| 来源 | 请求 | 无 Cookie 结果 | 结论 |
| --- | --- | --- | --- |
| 1 | `POST api.php?action=login` | 未提交真实凭证；登录页代码确认字段为 `username/password` | 登录是建立会话的前置请求，不是数据采集接口；不得自动保存凭证 |
| 1 | `GET api.php?action=check_login` | `200 {"code":0,"msg":"未登录"}` | 会话检查接口依赖登录状态 |
| 1 | `GET api.php?action=get_activities` | `200 application/json`，`code=1` | **不需要 Cookie** |
| 1 | `GET ranking.php?brand&time` | `200`，正文跳转 `login.html` | **需要 Cookie/会话** |
| 2 | `GET /page/wm/md?key&name&t=0` | `200 text/html`，含 `shopData` | **不需要 Cookie** |
| 3 | `GET /api/order-rankings/query` 首页 | `200 application/json`，50 条和非空游标 | **不需要 Cookie** |
| 3 | 同接口带上页返回的 `cursor` | `200 application/json`，14 条，`has_more=false` | **不需要 Cookie**，游标可用 |

接口优先策略：来源 1 仅对活动排期使用 JSON，排行榜统计必须保留认证 HTML；来源 2 没有可用 JSON，保留 HTML；来源 3 全量使用 JSON。不得调用来源 1 的账号/token 相关 API 代替排行榜接口。

## 传输与请求安全矩阵

| 来源/请求 | 传输 | 应用层加密/签名/nonce | 验证码/指纹 | 显式认证头 | Cookie 结论 |
| --- | --- | --- | --- | --- | --- |
| 1 登录 `POST api.php?action=login` | HTTPS | 登录页代码直接提交表单编码 `username/password`；未观察到客户端加密、签名或 nonce | 未观察到 | 无 | 初始登录请求不依赖已有会话；成功后的会话载体未读取 |
| 1 排期 `GET get_activities` | HTTPS | 无请求体；未观察到签名或 nonce | 未观察到 | 无 | 不需要 |
| 1 榜单 `GET ranking.php` | HTTPS | 查询参数明文 URL 编码；未观察到签名或 nonce | 未观察到 | 无 | 需要人工登录后的会话；值不导出 |
| 2 HTML `GET /page/wm/md` | HTTPS | 未发现 CryptoJS/Web Crypto/AES/RSA/HMAC、签名或 nonce | 未观察到 | 无 | 不需要 |
| 3 JSON `GET /api/order-rankings/query` | HTTPS | 未发现加密、签名或 nonce | 未观察到 | `Accept: application/json` | 不需要；禁止 `rank_token` |

“未观察到”只表示已检查页面脚本和实际最小请求，不能替代服务端安全设计声明。

## 来源 1：naiba666.com

### 页面与接口

1. 入口页：`GET https://naiba666.com/md/index.html`
2. 榜单页：`GET https://naiba666.com/md/ranking.php?brand=<URL 编码商家>&time=<URL 编码 YYYY-MM-DD HH:mm:ss>`
3. 活动排期接口：`GET https://naiba666.com/md/api.php?action=get_activities`

入口页中每个场次链接均调用：

```javascript
openRanking('ranking.php?brand=<encoded>&time=<encoded>')
```

已验证 `麦当劳 + 2026-07-29 12:00:00` 与 `华莱士 + 2026-07-29 12:00:00`。页面为服务端直接渲染，未发现内嵌 XHR/fetch/WebSocket/SSE。榜单页的刷新按钮只是重新发起相同 GET，并增加 `_=Date.now()` 缓存规避参数；前端仅限制 1 秒内重复点击。

`get_activities` 是可直接使用的 JSON 接口，用于获得 `brand`、`time_slots`、活动起止日期、每场数量和最大免单额。无 Cookie 的只读请求实测返回 HTTP 200、`application/json` 与 `code: 1`。它只提供活动/场次元数据，**不包含排行榜人数、秒桶或接单时间**。

已审查入口页中出现的全部 `api.php?action=*` 调用。除 `get_activities` 外，其余均为账号管理、查单、领券、反馈等功能；其中 `query_single` 为 `POST`，请求体包含所选账号的 `token`，不是排行榜数据接口，禁止作为采集路径。

### 观察到的响应语义

`麦当劳 + 12:00`：参与人数 522。页面按“第 N 秒”展示本秒人数、累计人数、占比，并可能展示免单/被割计数。刷新前后参与人数与秒分布一致。

本次已从人工登录后的真实榜单页直接捕获完整 `document.documentElement.outerHTML`，保存为 `source1-rank-response-redacted.html`（18,871 bytes，SHA-256：`3864fc29b238bb2bdbaebe1239575fdd6f63a3f3d5f6f9b29f884e6ad010dd8d`）。该文件不是字段摘要：保留原始 `class`、属性、页面结构和完整秒桶列表，已复核包含 `container`、`rank-list`、`rank-item`、`sec` 等真实 DOM class，以及 `01s, 02s, 03s, 04s, 05s, 06s, 07s, 09s, 10s, 12s, 17s, 18s, 22s, 23s, 32s, 33s` 全部秒桶。文件不含 Cookie、Set-Cookie、Authorization、密码、`rank_token` 或 Bearer 凭证。

`华莱士 + 12:00`：参与人数 277。页面还提供“昨天同品牌”的相邻场次链接，参数格式不变。

字段映射：

| 页面字段 | 含义 | 采集映射 |
| --- | --- | --- |
| 页面标题/首行商家 | 商家名 | `brand` |
| `12:00 场`、日期 | 场次 | `time` 拆为日期与时分秒 |
| 本场参与人数 | 总人数 | `participant_count` |
| `01s` 等 | 相对场次起点的秒桶 | `offset_second` |
| 本秒人数/累计人数/占比 | 聚合统计 | `bucket_count`、`cumulative_count`、`ratio` |

### 认证与限制

- 用户已在当前内置浏览器完成一次退出后重新登录；登录后可进入 `index.html`，并能访问同会话下的榜单页。见 `source1-login-session-evidence-redacted.md` 与 `source1-login-postcondition-redacted.json`。
- 登录协议已从无 Cookie 登录页确认：`POST https://naiba666.com/md/api.php?action=login`，请求体为表单编码的 `username`、`password`；见 `source1-login-curl-redacted.txt`。`login.html` 的表单本身无 action，由页面 jQuery AJAX 发起该请求。
- 无 Cookie 请求 `GET api.php?action=check_login` 返回 HTTP 200 JSON：`{"code":0,"msg":"未登录"}`。登录成功后会跳转至 `index.html`；本次不抓取成功响应头或会话值，因此只确认“人工登录建立可用会话”，不声明 Cookie 名称、属性或持久化方式。
- 已登录浏览器中，榜单页可正常 GET。无 Cookie 的独立只读 GET 返回 HTTP 200，但正文为 `alert("请先登录");location.href="login.html";`，不含榜单正文。因此结论为：**来源 1 榜单页会话认证必需**。未读取、导出或写入任何 Cookie 值。
- 未观察到服务端限流、验证码或错误页；仅观察到刷新按钮的前端 1 秒节流，不能据此推断服务端限流阈值。

### 实现建议

- 活动/场次发现优先使用 `GET api.php?action=get_activities` 的 JSON，避免从首页 HTML 提取商家和场次。
- 排行榜统计仍只能通过认证后的 `ranking.php?brand&time` HTML 解析；本次未发现等价 JSON、XHR/fetch、WebSocket 或 SSE 接口。
- 不把 `_=timestamp` 作为业务参数；仅在需要规避中间缓存时使用。
- 认证状态不能写入代码、数据库或日志。若正式采集器无法采用合规的短时人工授权会话，则来源 1 不应自动化采集。

## 来源 2：mt.liliabc.fun

### 接口与参数

`GET https://mt.liliabc.fun/page/wm/md`

| 参数 | 规则 | 已验证结果 |
| --- | --- | --- |
| `key` | `mt-time-MMDDHHmm` | 页面前端按月、日、半小时场次拼接；`07291200` 表示 7 月 29 日 12:00 |
| `name` | URL 编码商家名 | 完全匹配 `shopData` 的键时选中该商家；不存在时回退到第一个键“华莱士” |
| `t` | 秒级 Unix 时间戳，个人定位用途 | 页面将其赋给 `myTime`，仅用于标记命中秒桶的“当前所属位置”；不是榜单采集所需参数，必须省略或脱敏 |

页面的“应用”按钮将选择值拼为新 `key` 后整页跳转。已实际切换到 `mt-time-07291130`，页面正确显示 11:30 且返回“暂无排行榜数据”，证明半小时 key 可被页面接受。

### 响应结构

首个 HTML 响应中包含：

```javascript
var shopData = {
  "商家名": [1785297601, 1785297601]
};
var myTime = /* 仅个人定位，交付中已移除 */;
var shopSorts = [1, 1, 1];
```

验证样例（12:00）中的数据范围：华莱士 283 条、林里 17 条、麦当劳 626 条；麦当劳时间戳范围 `1785297601` 到 `1785297608`。页面显示将其格式化为 `2026-07-29 12:00:01` 等，故时间戳为秒级 Unix 时间戳，页面按北京时间展示。

页面脚本中未发现 `fetch`、`XMLHttpRequest`、`WebSocket` 或 `EventSource`，所以数据采集应解析初始 HTML 的 `shopData`，不应等待二次 API。

无 Cookie 独立 GET 已实测返回 HTTP 200、`text/html;charset=UTF-8`，包含 `shopData`，且没有登录页文本。因此来源 2 无认证依赖；其数据路径只有 HTML，未发现可替代 JSON 接口。

### 空值与边界

- `11:30` 场次：`shopData` 为空，页面显示“暂无排行榜数据”。
- 不存在的 `name`：URL 保留原值，前端回退到 `shopData` 第一个键。
- 月、日、时分控件允许全月、日和 30 分钟粒度；月份天数只按常规月份与 2 月 29 天生成，服务端对无效日期的处理未验证。

### 实现建议

- 请求参数只需要 `key` 和可选 `name`；无 `t` 实测仍返回 `shopData`，采集任务不要传递用户的 `t`。
- 解析 `shopData` 时使用 JavaScript/JSON 解析器，不要以正则拆分时间戳数组。
- 空 `shopData` 应作为“场次无数据”，不是协议错误。

## 来源 3：waimaiyouhui.top

### 接口

`GET https://waimaiyouhui.top/api/order-rankings/query`

页面源码确认的请求参数：

| 参数 | 含义 | 规则 |
| --- | --- | --- |
| `keyword` | 商家关键词 | 页面按钮提供固定关键词；非支持关键词在页面端回退到默认“大米先生” |
| `date` | 日期 | `YYYY-MM-DD` |
| `slot_time` | 场次 | `HH:mm`；可选值由页面下拉框提供 |
| `page_size` | 分页大小 | 页面常量为 `50` |
| `cursor` | 下一页游标 | 仅在 `has_more` 且 `next_cursor` 非空时发送；视为不透明字符串 |
| `rank_token` | 个人名次定位 | 仅在用户提供时发送；新采集器禁止使用、存储或记录 |

页面的 fetch 使用 `GET` 与唯一显式请求头 `Accept: application/json`，无显式 `Authorization`、签名、nonce、浏览器指纹或 `credentials` 设置。浏览器页面已成功请求数据；本次环境中将 JSON URL 直接作为浏览器标签打开会得到 `ERR_BLOCKED_BY_CLIENT`，但同一 URL 的无 Cookie 只读 HTTP 请求返回 HTTP 200、`application/json`。该限制只影响浏览器的 JSON 文档显示，不影响接口本身。

### 响应与分页

页面代码消费以下字段：`ok` 或 `success`、`message`、`rankings`、`total`、`has_more`、`next_cursor`、可选 `user_rank`。每个 `rankings` 项已确认使用：

| 字段 | 语义 | 样例 |
| --- | --- | --- |
| `rank` | 全场名次 | `1`、`64` |
| `accept_time` | 接单时间，毫秒级字符串 | `2026-07-29 12:00:01.109` |

有效样例 `麦当劳 + 2026-07-29 + 12:00`：浏览器首次观察为首屏 50/62 条，滚动后达到 62/62 条。独立无 Cookie HTTP 验证时数据已更新为 50/64 条，使用首个响应的 `next_cursor` 请求下一页返回 14 条（排名 51 至 64）、`has_more=false`。前端的 `IntersectionObserver` 使用 `rootMargin: '240px 0px'`。这证明响应是动态增长的，交付样例只代表采样时刻。

空结果测试中，未知关键词被页面归一为“大米先生”，页面显示“当前筛选条件下暂无记录”。因此主站不能把任意自由文本直接视为已被接口接受，应使用白名单关键词并在响应前后核验实际查询条件。

### 实现建议

- 发送 `keyword/date/slot_time/page_size=50`；仅当响应 `has_more=true` 且 `next_cursor` 非空时串行请求下一页。
- `cursor` 不透明，不要自行构造或复用到不同商家、日期、场次。
- `rank_token` 必须从采集请求和日志中排除；`user_rank` 也不是公共榜单字段。
- `accept_time` 应保留毫秒精度，转换到统一时区前先确认服务端时区；页面显示为本地中文日期时间，本次未得到响应中的时区标识。

## 三源时间、轮询与容错

| 项目 | 来源 1 | 来源 2 | 来源 3 |
| --- | --- | --- | --- |
| 页面显示时区 | 仅日期和场次，按本地浏览器观察为北京时间口径 | 页面将秒级时间戳显示为北京时间 | 中文日期时间，无明确 offset |
| 请求时间格式 | `time=YYYY-MM-DD HH:mm:ss` | `key=mt-time-MMDDHHmm` | `date=YYYY-MM-DD&slot_time=HH:mm` |
| 时间精度 | 秒桶 | 秒级 Unix 时间戳 | 毫秒级字符串 |
| 数据开始/停止时间 | 未做跨分钟观测，未确认 | 未做跨分钟观测，未确认 | 未做跨分钟观测，未确认 |
| 建议启动时点 | 场次起点；待真实场次确认 | 场次起点；待真实场次确认 | 场次起点；待真实场次确认 |
| 建议轮询间隔 | 暂定 60 秒 | 暂定 60 秒 | 暂定 30 秒、串行分页 |
| 建议最长时长 | 暂定 10 分钟 | 暂定 10 分钟 | 暂定 10 分钟 |
| 停止条件 | 两次总人数与末秒桶不变 | 两次数组长度与最大时间戳不变 | 两次 total 与末条时间不变且无下一页 |
| 失败重试 | 60 秒退避；认证重定向即停 | 60 秒退避 | 60 秒退避；不得并发 cursor |
| 已观察限流特征 | 页面刷新仅 1 秒前端节流 | 未观察到 | 前端单请求超时 180 秒；未观察到限流 |
| 已验证空值 | 未验证 | 11:30 返回空榜 | 无记录页面状态 |

本次只在已结束场次做低频验证，不能从单一历史快照推断数据增长窗口。下列为**上线前待实测的保守方案**，不是已证实的服务端 SLA：

1. 从场次开始时刻起，先在 0、1、3、5、10 分钟各记录一次总条数和最后接单时间。
2. 正式轮询暂设 30 至 60 秒，禁止并发同一“来源 + 商家 + 日期 + 场次”任务；来源 3 单请求前端超时为 180 秒，采集端超时必须短于且不与重试并发。
3. 连续一次网络/5xx 失败后使用 60 秒退避；出现页面限流、验证码或认证重定向时立即停止该来源，不做绕过或密集重试。
4. 达到两次连续读取“总条数和最后时间均不变”后，或到场次开始后 10 分钟（以实测调整）停止；历史场次不轮询。

## 数据模型与开发约束

建议将每次读取保存在独立新表，至少包含：`source`、`merchant_name`、`slot_date`、`slot_time`、`rank`、`accept_time_raw`、`accept_time_precision`、`participant_count`、`bucket_second`、`bucket_count`、`cursor_redacted`、`observed_at`、`response_fingerprint`。不要复用旧自建榜单库，也不要修改现有短链或生产跳转。

优先级建议：来源 1 的活动/场次发现使用 JSON 接口，来源 1 的人数与秒分布仍解析认证 HTML；来源 3 全量使用 JSON 接口并提供精细接单时间；来源 2 作为 HTML 补充和异常校验。三源不一致时保留原始来源与采集时间，不在采集层强行合并为同一排名。

## 原交接要求验收

| 原要求 | 交付状态 | 对应证据 |
| --- | --- | --- |
| 三源接口清单、方法、URL、参数、请求头、认证 | 完成 | 三个来源章节、Cookie 验证矩阵、传输与请求安全矩阵 |
| 每接口脱敏请求与响应样例 | 完成 | 本目录的 `*-curl-redacted.*`、JSON、HTML、会话后置证据文件 |
| 商家、场次、时间、排名/分页映射 | 完成 | 各来源字段映射、来源 3 游标分页章节 |
| 相同商家/日期/场次三源对比 | 完成 | 结论摘要及三源数量、精度、覆盖边界说明 |
| 轮询间隔、启动、结束、失败重试 | 完成，参数为保守建议 | 三源时间、轮询与容错表 |
| 限流、登录、反爬、环境限制 | 完成 | 来源 1 认证限制、来源 3 浏览器 JSON 文档显示限制、逐源限流特征 |
| 实时数据出现与稳定时点 | 待下一真实场次持续观测 | 本次仅分析历史/已结束场次，未将推测写为结论 |

“待下一真实场次持续观测”是数据时序事实的采样限制，不影响现有接口契约、认证、字段和分页结论；正式上线前应按表中的 0/1/3/5/10 分钟节点完成一次低频补测。

## 未确认项

- 来源 1 正式采集器可采用的合规认证续期机制；本次只验证人工登录会话可用，不读取会话载体。
- 来源 1/2/3 的服务端限流阈值和真实时区声明；本次已记录最小请求的状态码和内容类型。
- 来源 3 `next_cursor` 的实际格式；已证实可用，但本次不导出其值。
- 三源在真实场次开始后的数据出现、增长和稳定时点。
- 来源 3 自由关键词的服务端匹配规则；页面对未知值会先归一为默认关键词。

相关脱敏样例位于本目录。所有样例均不含 Cookie、Authorization、账号密码、`rank_token`、完整个人订单标识或来源 2 的个人定位时间。
