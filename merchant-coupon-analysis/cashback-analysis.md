# 网页下单返现接口分析

## 1. 最终结论

返现商家列表与商家券共用查询接口，但使用不同的业务召回值。当前网页默认“智能排序”使用：

```text
POST https://offsiteact.meituan.com/act/ge/queryPoiByRecallBiz
     ?yodaReady=h5&csecplatform=4&csecversion=4.3.0

body.recallBizId = cpsPromotionOrderMulti
```

接口需要网页 H5Guard 4.3.0 生成的动态 `mtgsig`。根目录 `web-cashback-query.js` 已完成页面 URL 解析、请求体构造、签名、调用和首店判断。

最终业务入口是根目录 `web-benefits-query.js`：它先通过商家券接口取得当前规范
POI，再把该 POI写入返现页面 URL后请求本接口。`web-cashback-query.js` 保留为单接口
调试入口。

业务只读取 `infos[0]`。请求中的 `poi_id_str` 不会强制把目标商家排到第一位，因此必须比较：

```text
infos[0].poi_id_str == requested poi_id_str
```

不匹配时，按本项目“只判断第一家”的交付规则返回
`NO_CASHBACK/FIRST_POI_MISMATCH`；第二家及后续商家一律忽略。
这表示“目标不是当前接口返回的第一家”，不等同于对任意目标 POI 的全量返现资格作出结论。

最终组合流程不再直接用历史 POI作返现判定。商家券接口返回的首店 POI作为
`canonicalPoiId`，返现接口必须同时满足：

```text
cashback.infos[0].poi_id_str == canonicalPoiId
normalize(cashback.infos[0].poiBaseInfo.name)
  == normalize(coupon.infos[0].poiBaseInfo.name)
```

任一校验失败均返回 `UNKNOWN`。

若上游已知店名，组合入口支持 `--expected-name` 进行第三重校验，避免历史 POI失效
后商家券接口返回推荐首店时被误当作目标商家。未提供店名时，交付结果应理解为“两接口
当前首店一致”，而非历史 POI归属关系的绝对证明。

## 2. 页面 URL

```text
https://offsiteact.meituan.com/web/hoae/order_cashback_activity/index.html
  ?bizId=<BIZ_ID>
  &mediumSrc1=<MEDIUM_SRC_1>
  &scene=CPS_SELF_SRC
  &pageSrc1=CPS_SELF_OUT_SRC_H5_LINK
  &pageSrc2=<PAGE_SRC_2>
  &pageSrc3=<PAGE_SRC_3>
  &activityId=17
  &mediaUserId=<MEDIA_USER_ID>
  &outActivityId=17
  &rootPvId=<ROOT_PV_ID>
  &poi_id_str=<TARGET_POI_ID_STR>
```

当前页面标题为“下单得返现，最低1元吃外卖”，主资源为 `activity_order_cashback` 页面包。

## 3. 请求契约

### 请求头

| 请求头 | 结论 |
|---|---|
| `Content-Type` | `application/json;charset=UTF-8` |
| `User-Agent` | 必须与签名时一致 |
| `content-encoding` | 当前成功请求使用空值 |
| `mtgsig` | 必需，删除后 HTTP 403 |
| `Cookie` | 匿名列表查询重放不需要 |
| `Authorization` | 未观察到 |
| `Origin/Referer` | 删除后匿名重放仍成功 |

### 请求体

```json
{
  "lat": 29.688253,
  "lon": 106.600316,
  "geoType": "GCJ02",
  "geoSource": "network",
  "geoAccuracy": 500,
  "mediumParams": {
    "bizId": "<BIZ_ID>",
    "mediumSrc1": "<MEDIUM_SRC_1>",
    "scene": "CPS_SELF_SRC",
    "pageSrc1": "CPS_SELF_OUT_SRC_H5_LINK",
    "pageSrc2": "<PAGE_SRC_2>",
    "pageSrc3": "<PAGE_SRC_3>",
    "activityId": "17",
    "mediaUserId": "",
    "outActivityId": "17",
    "poi_id_str": "<TARGET_POI_ID_STR>"
  },
  "appContainer": "UNKNOW",
  "rootPvId": "<GENERATED_UUID>",
  "pagePvId": "<GENERATED_UUID>",
  "pageSessionId": "<GENERATED_UUID>",
  "outerPvId": "",
  "contentPvId": "",
  "recallBizId": "cpsPromotionOrderMulti",
  "pageNo": 1,
  "hasMore": true,
  "phone": "",
  "channelType": "SELF",
  "riskParams": {}
}
```

查询器默认使用已经验证的 GCJ02 坐标 `29.688253, 106.600316`，并自动生成三个页面 UUID。查询其他地区时可覆盖坐标。
网页还提供 `cpsPromotionOrderRatio`、`cpsPromotionOrderKa`、`cpsPromotionOrderClosest` 三种排序；交付主流程固定使用默认的 `cpsPromotionOrderMulti`，以保持与页面首屏一致。

该坐标是历史验证夹具，不代表调用方当前位置。正式开发应传入页面实际使用的 GCJ02
坐标；位置变化约 20 公里即可导致首店列表变化。

## 4. 首店判断

```text
if HTTP != 200 or ret != 0 or infos is not an array:
    UNKNOWN

if infos is empty:
    NO_CASHBACK / EMPTY_INFOS

first = infos[0]
if first.poi_id_str != requested poi_id_str:
    NO_CASHBACK / FIRST_POI_MISMATCH

plan = first.planActivityInfoList[0]
if plan is missing:
    UNKNOWN / CASHBACK_PLAN_MISSING

return HAS_CASHBACK
```

控制实验中，曾将一个已出现在 `infos[1]` 的商家作为 `poi_id_str` 再次请求，该商家仍未移动到 `infos[0]`。接口数组顺序是本期“第一家”的权威顺序。登录页面实际会自动分页，但本交付按既定要求只读取第 1 页的 `infos[0]`。
针对另一个旧样本 POI 的最新原始回放显示：返回 5 家商家，但该 POI 不在整个 `infos` 数组中。
因此，目标 POI 不在返回列表时，可能是活动/位置筛选结果，也可能是该 POI 没有返现；本接口本身不足以区分两者。

### 历史 POI 与规范 POI 案例

实测历史 POI请求商家券接口后，首店名称仍为同一家店，但响应 POI变为当前规范
值。随后使用规范 POI请求返现接口，返回首店 POI与店名均和商家券接口一致，返现
计划解析成功。相同店铺的历史 POI直接请求返现时则会触发
`FIRST_POI_MISMATCH`。

这表明历史 POI适合作为商家券入口参数，但返现查询应使用商家券接口本次返回的
规范 POI。交付记录使用 `<HISTORICAL_POI>`、`<CANONICAL_POI>` 脱敏表示这两个值。

## 5. 返现字段和单位

页面实际读取 `infos[0].planActivityInfoList[0]`：

| 页面含义 | 字段 | 转换 |
|---|---|---|
| 下单返现比例 | `userMaxRatio` | `/ 100` 得百分比 |
| 下单最高返现金额 | `userMaxCommission` | `/ 100` 得元 |
| 评价追加返现比例 | `unifyToUserCommentRatio` | `/ 100` 得百分比 |
| 评价最高返现金额 | `unifyToUserCommentCommission` | `/ 100` 得元 |
| 合计最高返现 | 上述两个金额之和 | 元 |
| 报名状态 | `userSignStatus` | 枚举 |
| 剩余名额 | `validInventory` | 个 |
| 总名额 | `totalInventory` | 个 |
| 下单时限 | `orderLimitTime` | 秒，页面向上取整为分钟 |

页面源码明确使用 `/100` 计算百分比和元。多个实时样本也具有不同的比例与金额值，单位已交叉验证。

这些字段表示“比例”和“最高返现”，不是下单后必得的固定金额。顶层 `totalAmount`、`tspMarketAmount` 以及 `averageCashback` 不作为本期展示金额。

## 6. 状态枚举

| `userSignStatus` | 含义 |
|---|---|
| `CAN_SIGN` | 未报名，可报名 |
| `SIGN_NO_ORDER` | 已报名，尚未下单 |
| `NO_INVENTORY` | 名额已抢完 |
| `SIGN_AND_ORDER` | 已报名并下单 |
| `ORDER_NO_USE` | 订单未满足/未使用活动 |
| `COMPLETE` | 已完成 |

当前正向样本为 `CAN_SIGN`。即使状态为 `NO_INVENTORY`，活动记录仍存在，应展示为活动已抢完，而不是“无返现活动”。

## 7. 其他页面接口

| 用途 | 方法/路径 | 本期是否需要 |
|---|---|---|
| 用户信息 | `POST /act/ge/getUserInfo` | 否 |
| 页面模块 | `POST /act/ge/getPageModuleInfo` | 否 |
| 返现商家列表 | `POST /act/ge/queryPoiByRecallBiz` | 是 |
| 报名 | `POST /act/mission/open` | 否，写操作 |
| 取消报名 | `POST /act/mission/cancel` | 否，写操作 |
| 用户任务列表 | `POST /act/mission/user/list` | 否 |
| 返现记录 | `POST /act/mission/cash/back` | 否 |
| 奖池 | `POST /act/mission/prizepool` | 否 |

单独展示当前首店返现时只需要返现查询接口；最终交付的历史 POI兼容流程还必须先调用
商家券查询以取得规范 POI。报名接口使用 `poiEventId`、`pvid` 等字段且会产生外部状态变化，
不纳入查询交付。

## 8. 登录页与匿名交付回放差异

登录页面本次实际显示账号状态，并使用同一路径 `/act/ge/queryPoiByRecallBiz`。差异来自请求上下文而非另一套返现接口：

| 项目 | 登录网页 | 根目录交付 |
|---|---|---|
| 主接口 | `/act/ge/queryPoiByRecallBiz` | 相同 |
| 默认召回 | `cpsPromotionOrderMulti` | 已同步为相同 |
| 登录上下文 | Cookie、`getUserInfo`、`/act/mission/cash/back`，影响用户状态/名额展示 | 不携带 Cookie；只做匿名列表与计划字段查询 |
| 风险参数 | `useRisk=true`，`riskParams={}`，设备环境可补指纹字段 | H5Guard 签名 + `riskParams={}` |
| 排序/分页 | 页面支持排序切换并自动加载后续页 | 固定默认排序、第 1 页，按项目要求只判断首店 |

同一目标 POI 的实测回放：`cpsSelfPromotionOrder` 首店为“屿辞清川蛋糕·甜品·下午茶（西政店）”，而页面默认的 `cpsPromotionOrderMulti` 首店为“虾满客小龙虾美蛙干锅大闸蟹（空港店）”，其 POI 为请求目标。由此确认先前差异是 `recallBizId` 不一致，不是 HTTP 复用；匿名调用也能得到列表，登录态主要补充用户相关状态。

## 9. 真实验证

### 首店匹配

```text
HTTP 200
ret = 0
decision = HAS_CASHBACK
orderRatePercent = 60
orderMaxYuan = 18.75
reviewRatePercent = 5
reviewMaxYuan = 1.25
totalMaxYuan = 20
userSignStatus = CAN_SIGN
```

### 首店不匹配

```text
HTTP 200
ret = 0
decision = NO_CASHBACK
reason = FIRST_POI_MISMATCH
```

### 控制实验

- 删除 `mtgsig`：HTTP 403。
- 不发送 Cookie、Authorization、Origin、Referer：HTTP 200、`ret=0`。
- 目标在后续数组项：不改变首店排序，仍按不匹配处理。
- 网页与接口均使用第一项 `planActivityInfoList[0]`。

### 基础链接回放

用户提供的基础链接可以直接作为模板，实际调用时只需将末尾的
`poi_id_str=` 替换为目标商家 ID：

```powershell
$base = 'https://offsiteact.meituan.com/web/hoae/order_cashback_activity/index.html?bizId=<BIZ_ID>&mediumSrc1=<MEDIUM_SRC_1>&scene=CPS_SELF_SRC&pageSrc1=CPS_SELF_OUT_SRC_H5_LINK&pageSrc2=<PAGE_SRC_2>&pageSrc3=<PAGE_SRC_3>&activityId=17&mediaUserId=&outActivityId=17&rootPvId=&poi_id_str='
node .\web-cashback-query.js ($base + '<TARGET_POI_ID_STR>')
```

本轮实时回放确认：旧样本 POI 作为目标时首店不匹配，返回
`NO_CASHBACK/FIRST_POI_MISMATCH`；使用当前接口返回的首店 POI 时返回
`HAS_CASHBACK`，字段换算结果为下单最高 `18.75` 元、评价最高 `1.25` 元、合计最高 `20` 元。

## 10. 运行

```powershell
$base = 'https://offsiteact.meituan.com/web/hoae/order_cashback_activity/index.html?...&poi_id_str='
node .\web-cashback-query.js ($base + '<POI_ID_STR>')
```

可选覆盖坐标：

```powershell
node .\web-cashback-query.js '<CASHBACK_PAGE_URL>' <LAT> <LON>
```

输出只有 `HAS_CASHBACK`、`NO_CASHBACK`、`UNKNOWN` 三类，并且只返回第一家商家的判断。

建议连接超时 2 秒、总超时 5-10 秒、同一 POI 请求间隔不少于 1 秒，成功结果缓存 30-60 秒。HTTP 403、超时和结构异常必须标记为 `UNKNOWN`。

## 11. 交付文件

- `web-benefits-query.js`
- `benefits-query-contract-redacted.json`
- `benefits-verification-record.txt`
- `web-coupon-query.js`
- `web-coupon-request-body.example.json`
- `web-cashback-query.js`
- `web-cashback-request-body.example.json`
- `cashback-query-contract-redacted.json`
- `cashback-response-present.json`
- `cashback-response-no-match.json`
- `cashback-verification-record.txt`
- 共用：`web-h5sign.js`、`H5guard_web_4.3.0.js`、`package-1.1.2.js`
- 回滚：`web-benefits-rollback.ps1`、`web-coupon-query.rollback-base.js`、`web-cashback-query.rollback-base.js`

全部文件位于根目录，不依赖历史抓包或分析子目录。
