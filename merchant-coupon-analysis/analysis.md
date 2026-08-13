# 网页商家券接口分析

## 1. 交付结论

网页商家券只需要一个接口：

```text
POST https://offsiteact.meituan.com/act/ge/queryPoiByRecallBiz
     ?yodaReady=h5&csecplatform=4&csecversion=4.3.0
```

每次请求都用网页 H5Guard 4.3.0 对最终 URL、`POST` 方法和逐字相同的 JSON body 生成新鲜 `mtgsig` 请求头。根目录 `web-coupon-query.js` 已完成签名、请求和首家商家判断。

业务只读取 `infos[0]`。第二家及后续商家一律不参与是否有券的判断。

### 最终业务主流程

商家 POI 可能存在历史值和当前规范值。实测使用一个历史 POI 请求商家券接口时，
返回首店仍是同一家店，但 `infos[0].poi_id_str` 已变成新的规范 POI。两个 POI直接
作字符串比较会产生误判。

最终交付使用以下流程：

```text
历史或当前 POI
  -> 商家券接口 infos[0]
  -> 取得 canonicalPoiId、店名和商家券
  -> 使用 canonicalPoiId 请求返现接口
  -> 校验返现 infos[0].poi_id_str == canonicalPoiId
  -> 校验商家券首店名称 == 返现首店名称
  -> 输出商家券和返现结果
```

根目录 `web-benefits-query.js` 已实现该流程。原始 POI保存在
`identity.requestedPoiId`，规范 POI保存在 `identity.canonicalPoiId`；POI或店名校验
失败时返回 `UNKNOWN`，避免把接口推荐的其他商家当作目标商家。

若上游已经知道店名，可传入 `--expected-name`。组合入口会同时校验商家券首店、返现
首店和上游店名；这能识别“历史 POI失效后接口返回推荐首店”的残余风险。没有上游店名
时，组合入口至少要求商家券和返现的规范 POI、标准化店名一致。

## 2. 请求契约

### 请求头

| 请求头 | 要求 |
|---|---|
| `Content-Type` | `application/json;charset=UTF-8` |
| `User-Agent` | 与生成签名时一致的桌面网页 UA |
| `content-encoding` | 当前成功请求使用空值 |
| `mtgsig` | 必需，网页 H5Guard 4.3.0 动态生成 |
| `Cookie` | 本次成功请求未观察到依赖 |
| `Authorization` | 本次成功请求未观察到依赖 |
| `Referer` | 删除后仍重放成功，不是当前硬依赖 |

### 请求体核心字段

```json
{
  "lat": 29.688253,
  "lon": 106.600316,
  "geoType": "GCJ02",
  "geoSource": "network",
  "geoAccuracy": 500,
  "mediumParams": {
    "recallBizId": "cpsH5Coupon",
    "bizId": "<BIZ_ID>",
    "scene": "CPS_SELF_SRC",
    "activityId": "<ACTIVITY_ID>",
    "poi_id": "-100",
    "poi_id_str": "<POI_ID_STR>"
  },
  "appContainer": "UNKNOW",
  "rootPvId": "<ROOT_PV_ID>",
  "pagePvId": "<PAGE_PV_ID>",
  "pageSessionId": "<PAGE_SESSION_ID>",
  "recallBizId": "cpsSelfCouponAll",
  "pageNo": 1,
  "hasMore": true,
  "phone": "",
  "channelType": "SELF",
  "categoryTypeList": ["0"],
  "riskParams": {}
}
```

完整模板见 `web-coupon-request-body.example.json`。默认使用已验证的 GCJ02 经纬度 `29.688253, 106.600316`；查询其他地区时可覆盖，且不能同时为零。生成签名后不得重新序列化或修改 body。

该坐标是历史验证夹具，不代表调用方当前位置。正式开发应传入页面实际使用的 GCJ02
坐标；位置变化约 20 公里即可导致首店列表变化。

## 3. 响应与字段映射

成功条件：

```text
HTTP 200 && ret == 0 && Array.isArray(infos)
```

只解析 `infos[0]`：

| 业务值 | 字段 |
|---|---|
| 商家 ID | `infos[0].poi_id_str` |
| 商家名称 | `infos[0].poiBaseInfo.name` |
| 商家券存在 | `infos[0].giftInfo.type == 1` |
| 券面额（元） | `giftInfo.coupon_amount / 100` |
| 使用门槛（元） | `giftInfo.order_amount_limit / 100` |
| 券 ID | `giftInfo.gift_id` |
| 券状态 | `giftInfo.status` |

`giftInfo.type == 2` 是通用红包，不计为商家券。`totalAmount` 和 `tspMarketAmount` 是聚合展示金额，也不能当作单张商家券面额。

状态枚举：`1=NEW`、`2=RECEIVED`、`3=FAILED`、`4=USED`、`5=EXPIRED`、`6=LOCKED_NEW`、`7=RECEIVE_EXPIRED`。

## 4. 首家商家判断

```text
if HTTP != 200 or ret != 0 or infos is not a non-empty array:
    UNKNOWN

first = infos[0]
if first.poi_id_str != requested poi_id_str:
    UNKNOWN

if first.giftInfo is missing or first.giftInfo.type != 1:
    NO_COUPON

return HAS_COUPON with amount, threshold, status and gift_id
```

HTTP 403 是签名或风控失败，必须返回 `UNKNOWN`，不能当作无券。

上述逻辑是 `web-coupon-query.js` 的独立严格模式。最终组合流程以商家券首店作为
规范 POI来源，再由返现接口完成第二次 POI和店名交叉校验。

## 5. mtgsig 算法

根目录 `web-h5sign.js` 的流程：

1. 补齐网页 `window`、`navigator`、`location`、`XMLHttpRequest`、`fetch` 等运行环境。
2. 加载 `H5guard_web_4.3.0.js` 和 `package-1.1.2.js`。
3. 调用 `H5guard.initWithKey`，启用 XHR/fetch hook。
4. 使用最终方法、URL 和原始 body 触发 XHR。
5. 从 `setRequestHeader('mtgsig', value)` 捕获签名。
6. 使用相同 UA 和 body 立即发送。

签名固定包含 `a1,a2,a3,a5,a6,a8,a9,a10,x0,d1`。当前实测 `a1=1.2`、`x0=4`，`a9` 以 `4.3.0,9,` 开头；其余字段包含动态时间、环境指纹、请求绑定和摘要输出。

旧分析中可复用的是浏览器 shim、XHR hook、package 运行库和动态调用方式。普通 HMAC/MD5/SHA 候选及固定 XOR key 猜测没有通过样本一致性验证，未进入交付实现。详见 `mtgsig-algorithm-analysis.md`。

## 6. 真实验证

| 用例 | 结果 |
|---|---|
| 网页签名生成 | 退出码 0；10 个字段齐全 |
| 无签名控制 | HTTP 403 |
| 修改 body 后复用旧签名 | HTTP 403 |
| 首家无券样本 | HTTP 200；`ret=0`；`NO_COUPON` |
| 首家有券样本 | HTTP 200；`ret=0`；`HAS_COUPON`；面额 4 元；满 13 元可用 |
| 网页 URL 直接输入 | 自动解析参数、生成会话 ID；有券和无券样本均成功 |
| 根目录隔离运行 | 只复制六个组合流程运行文件仍成功请求 |
| 回滚测试 | 恢复后 SHA-256 与基线一致 |

## 7. 运行

最终组合流程：

```powershell
node .\web-benefits-query.js '<COUPON_PAGE_URL>' '<CASHBACK_PAGE_URL>' [LAT] [LON] [--expected-name '<MERCHANT_NAME>']
```

单接口调试仍可使用下面的商家券入口：

```powershell
node .\web-coupon-query.js '<PAGE_URL>'

# 可选：覆盖默认经纬度
node .\web-coupon-query.js '<PAGE_URL>' <LAT> <LON>

# 或传完整 body
node .\web-coupon-query.js .\request-body.json
```

输出只有 `HAS_COUPON`、`NO_COUPON`、`UNKNOWN` 三类，并且只携带第一家商家的解析结果。

建议连接超时 2 秒、总超时 5-10 秒、同一 POI 查询间隔不少于 1 秒，成功结果缓存 30-60 秒。失败时保留旧缓存并标记未知。

## 8. 根目录交付

最终主入口：`web-benefits-query.js`。

配套文件包括：`web-coupon-query.js`、`web-cashback-query.js`、
`web-h5sign.js`、`H5guard_web_4.3.0.js`、`package-1.1.2.js`、
`benefits-query-contract-redacted.json`、`benefits-verification-record.txt`。
回滚文件见 `WEB_DELIVERY_MANIFEST.json`。快速使用见 `WEB_DELIVERY_README.md`，
精确交付白名单和运行文件 SHA-256 也见该清单。历史抓包和分析子目录不属于交付内容，
运行文件也不读取它们。
