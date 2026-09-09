# 接单时间排行榜 V2 接口文档

本文档对应项目中的 `order-rankings-v2`。接口返回来源 1 与来源 2 采集结果合并后的“接单秒数分布”，时间和场次均按北京时间（`Asia/Shanghai`）处理。

## 1. 接入信息

- **服务地址**：将下文的 `{BASE_URL}` 替换为服务实际域名，例如 `https://example.com`。
- **数据格式**：公开 JSON 接口返回 `Content-Type: application/json`；页面接口返回 HTML；公告图片接口返回图片二进制。
- **公开接口鉴权**：`/api/order-rankings/v2/*` 和 `/order-rankings-v2` 不要求登录。是否在站点导航中启用 V2 由服务端配置控制，但不影响直接请求这些公开路径。
- **管理员接口鉴权**：`/web/admin/...` 必须先登录后台，并携带服务设置的管理员会话 Cookie。普通用户会收到 `403`，未登录通常收到 `401`。
- **建议请求头**：JSON 请求使用 `Accept: application/json`；本组公开接口为 `GET`，无需请求体。

## 2. 快速调用流程

1. 按日期请求活动目录，得到活动 `id`。
2. 使用 `activity_id` 请求该活动的排行榜数据。
3. 需要展示公告图片时，直接使用返回的 `image_url` 请求图片。

```bash
# 读取 2026-08-19 的活动
curl -G "{BASE_URL}/api/order-rankings/v2/catalog" \
  --data-urlencode "date=2026-08-19" \
  -H "Accept: application/json"

# 根据目录中的活动 id 查询排行榜
curl -G "{BASE_URL}/api/order-rankings/v2/query" \
  --data-urlencode "activity_id=123" \
  -H "Accept: application/json"
```

## 3. 公开接口

### 3.1 V2 排行榜页面

`GET /order-rankings-v2`

返回可直接给用户访问的排行榜 HTML 页面。页面内部会调用“活动目录”和“排行榜查询”接口。页面默认按当天（北京时间）加载数据。

**查询参数**：无。

**成功响应**：`200 text/html`。

### 3.2 获取活动目录

`GET /api/order-rankings/v2/catalog`

返回指定日期已发布且可用的活动/场次列表。建议先调用此接口，再用返回的 `id` 查询排行榜。

**Query 参数**：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `date` | string | 否 | 日期，格式 `YYYY-MM-DD`；省略时使用服务器当前北京时间日期 |

**成功响应示例**：

```json
{
  "success": true,
  "date": "2026-08-19",
  "activities": [
    {
      "id": 123,
      "merchant_name": "麦当劳",
      "slot_time": "12:00",
      "quantity_per_slot": 500,
      "max_discount": "20.00"
    }
  ]
}
```

**字段说明**：

- `id`：活动唯一 ID，只保证在当前服务数据库中有效；查询时优先使用它。
- `merchant_name`：商家名称。
- `slot_time`：场次开始时间，格式 `HH:mm`，按北京时间解释。
- `quantity_per_slot`：该场次份数；没有来源数据时可能为 `0`。
- `max_discount`：最大优惠金额的字符串表示；没有值时为空字符串。
- `activities` 为空数组表示当天暂无可用活动，不是接口错误。

### 3.3 查询合并排行榜

`GET /api/order-rankings/v2/query`

根据活动 ID，或根据“商家 + 日期 + 场次”查询最新合并数据。推荐使用 `activity_id`，因为目录中的 ID 能避免商家名称规范化带来的歧义。

**Query 参数**：

| 参数 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `activity_id` | integer | 与下方组合二选一 | 活动 ID；传入后优先按 ID 查询 |
| `merchant_name` | string | 组合查询时必填 | 商家名称 |
| `date` | string | 组合查询时必填 | `YYYY-MM-DD` |
| `slot_time` | string | 组合查询时必填 | `HH:mm`，例如 `12:00` |

组合查询示例：

```text
/api/order-rankings/v2/query?merchant_name=%E9%BA%A6%E5%BD%93%E5%8A%B3&date=2026-08-19&slot_time=12%3A00
```

**成功响应示例**：

```json
{
  "success": true,
  "activity": {
    "id": 123,
    "record_date": "2026-08-19",
    "merchant_name": "麦当劳",
    "merchant_key": "麦当劳",
    "slot_time": "12:00",
    "quantity_per_slot": 500,
    "max_discount": "20.00",
    "enabled": 1,
    "updated_at": 1787131200
  },
  "buckets": [
    {
      "activity_id": 123,
      "bucket_second": 1,
      "source1_count": 2,
      "source2_count": 4,
      "merged_count": 6,
      "cumulative_count": 6,
      "ratio": 0.6,
      "updated_at": 1787131260
    },
    {
      "activity_id": 123,
      "bucket_second": 2,
      "source1_count": 1,
      "source2_count": 3,
      "merged_count": 4,
      "cumulative_count": 10,
      "ratio": 1.0,
      "updated_at": 1787131260
    }
  ],
  "total": 10,
  "last_updated_at": 1787131260,
  "source2_match": {
    "status": "matched",
    "source2_merchant_name": "麦当劳",
    "message": "",
    "updated_at": 1787131260
  }
}
```

**响应字段说明**：

- `activity`：活动信息。除目录字段外，可能包含 `record_date`、`merchant_key`、`enabled`、`updated_at` 等内部记录字段；调用方应至少依赖 `id`、`merchant_name`、`record_date`、`slot_time`、`quantity_per_slot`、`max_discount`。
- `buckets`：从第 `1` 秒到当前最大秒数按顺序排列。缺失的秒数也会补成 `0`，因此 `bucket_second` 不一定代表该秒有人，但数组顺序可直接用于绘图。
- `bucket_second`：相对场次开始的秒数，从 `1` 开始。
- `source1_count` / `source2_count`：两个数据源在该秒桶中的人数。公开页面可使用 `merged_count`，不需要依赖来源拆分。
- `merged_count`：该秒桶合并人数，等于两个来源人数之和。
- `cumulative_count`：截至该秒（含该秒）的累计人数。
- `ratio`：累计人数占 `total` 的比例，范围通常为 `0` 到 `1`；例如 `0.6` 表示 `60%`。
- `updated_at`、`last_updated_at`：Unix 时间戳，单位为秒（UTC 基准时间戳；展示时可转换为北京时间）。
- `total`：当前合并数据的总人数，等于最后一个桶的 `cumulative_count`；没有数据时为 `0`。
- `source2_match.status`：来源 2 的商家匹配状态。常见值包括 `pending`、`matched`；该对象可能只有 `status` 字段。

如果活动存在但采集尚未产生秒桶，接口仍返回 `200`，此时 `buckets=[]`、`total=0`，调用方应显示“暂无数据/正在采集”，不要当作 HTTP 错误。

### 3.4 获取公告图片

`GET /api/order-rankings/v2/announcement-image`

返回后台上传的排行榜公告图片。实际媒体类型可能是 `image/jpeg`、`image/png`、`image/webp` 或 `image/gif`，响应带 `Cache-Control: no-cache`。

**成功响应**：`200` 图片二进制。

没有配置图片时返回统一错误 JSON，HTTP `404`。

## 4. 统一错误格式

V2 JSON 错误响应格式如下：

```json
{
  "success": false,
  "error": "未找到排行榜数据"
}
```

常见状态码：

| HTTP 状态码 | 含义 |
| --- | --- |
| `400` | 请求参数或管理设置不合法 |
| `401` | 管理员接口未登录 |
| `403` | 已登录但不是管理员 |
| `404` | 活动、排行榜数据或公告图片不存在 |
| `502` | 管理员触发的上游来源/代理测试失败 |
| `503` | 读取管理员登录状态或服务状态失败 |

## 5. 管理员接口（仅服务维护者）

以下接口都位于 `/web/admin/...`，不是给普通朋友调用的开放接口。它们要求浏览器登录后台后携带同一会话 Cookie；响应失败时仍使用 `{"success":false,"error":"..."}`。

| 方法 | 路径 | 请求体/用途 |
| --- | --- | --- |
| `GET` | `/web/admin/order-rankings-v2/preview` | 管理员预览页面 |
| `GET` | `/web/admin/api/order-rankings-v2/settings` | 读取配置和运行状态 |
| `PUT` | `/web/admin/api/order-rankings-v2/settings` | JSON 设置；字段见下方 |
| `PUT` | `/web/admin/api/order-rankings-v2/proxy-settings` | JSON 代理设置 |
| `POST` | `/web/admin/api/order-rankings-v2/announcement/image` | `multipart/form-data`，字段名 `file`，仅 JPG/PNG/WebP/GIF，最大 5 MB |
| `GET` | `/web/admin/api/order-rankings-v2/status` | 配置、运行状态和当天目录 |
| `POST` | `/web/admin/api/order-rankings-v2/sources/probe` | 检查来源健康状态 |
| `POST` | `/web/admin/api/order-rankings-v2/proxy/test` | JSON：`{"record_date":"YYYY-MM-DD","slot_time":"HH:mm"}` |
| `POST` | `/web/admin/api/order-rankings-v2/source1/test-login` | 测试来源 1 登录/Cookie |
| `POST` | `/web/admin/api/order-rankings-v2/source1/activities/refresh` | 刷新来源 1 当日活动目录 |
| `POST` | `/web/admin/api/order-rankings-v2/source1/test-query` | JSON：`merchant_name`、`record_date`、`slot_time` |
| `POST` | `/web/admin/api/order-rankings-v2/source2/test-query` | JSON：`merchant_name`、`record_date`、`slot_time`（商家字段用于统一请求模型） |
| `POST` | `/web/admin/api/order-rankings-v2/runs` | JSON：`merchant_name`、`record_date`、`slot_time`；创建手动采集任务 |

### 管理设置字段摘要

`PUT /web/admin/api/order-rankings-v2/settings` 的 JSON 字段包括：

`collection_enabled`、`public_enabled`、`rank_text_enabled`、`source1_enabled`（布尔）；`source1_username`、`source1_password`、`source1_relay_url`、`source1_relay_secret`（来源配置）；`clear_source1_password`、`clear_source1_credentials`、`clear_source1_relay_url`、`clear_source1_relay_secret`（清除配置的布尔开关）；`proxy_fallback_enabled`（布尔）、`proxy_api_url`（HTTP/HTTPS 地址）、`clear_proxy_api_url`（布尔）、`proxy_validation_cache_seconds`（10-600）、`proxy_retry_count`（0-5）、`window_seconds`（60-3600）；以及公告字段 `announcement_enabled`、`announcement_title`、`announcement_body`、`announcement_image_url`、`announcement_link_url`。

密码、Relay 密钥等敏感字段不要通过朋友的客户端调用或记录到日志。管理员读取配置时服务端会脱敏，不应尝试从响应中恢复原值。

## 6. 调用建议

- 先请求目录，再缓存 `activity_id`；日期变化或目录刷新后重新获取目录。
- 排行榜数据由后台采集任务异步生成，建议在 `buckets=[]` 时按 5-10 秒间隔重试，并设置合理超时。
- 不要把 `source1_count`、`source2_count` 当作稳定的外部业务契约；面向用户展示优先使用 `merged_count`、`cumulative_count`、`ratio`。
- 对 `last_updated_at` 做新鲜度判断；长时间不变化时提示数据可能暂未更新。
- 不要把管理员接口暴露给朋友或第三方客户端，也不要转发管理员 Cookie。
