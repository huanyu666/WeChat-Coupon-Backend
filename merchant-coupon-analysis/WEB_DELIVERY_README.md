# 网页商家券与返现接口交付

本交付只包含网页 H5 接口。所有运行文件均位于当前根目录，不读取子目录。

运行环境：Node.js 18 或更高版本。

## 最终主流程

正式开发优先使用组合入口：先通过商家券接口取得当前规范 POI 和商家券，
再用规范 POI 查询返现，并校验两个接口返回的首店 POI 与店名。

```powershell
node .\web-benefits-query.js '<COUPON_PAGE_URL>' '<CASHBACK_PAGE_URL>' [LAT] [LON]
# 可选：用上游已知店名阻止失效 POI 被推荐首店替代
node .\web-benefits-query.js '<COUPON_PAGE_URL>' '<CASHBACK_PAGE_URL>' <LAT> <LON> --expected-name '<MERCHANT_NAME>'
```

`CASHBACK_PAGE_URL` 的 `poi_id_str` 可以为空，组合入口会替换为商家券接口返回的
`canonicalPoiId`。输出保留：

- `identity.requestedPoiId`：调用方传入的历史或当前 POI。
- `identity.canonicalPoiId`：商家券接口当前返回的首店 POI。
- `identity.canonicalized`：两个 POI 是否发生变化。
- `identity.couponCashbackPoiMatch`：返现首店是否等于规范 POI。
- `identity.couponCashbackNameMatch`：两个接口的首店名称是否一致。
- `identity.expectedNameMatch`：提供 `--expected-name` 时，上游店名是否匹配。
- `coupon`：商家券判断与券信息。
- `cashback`：返现判断、状态、库存和金额。

只有规范 POI、两接口店名和可选上游店名均通过校验时，顶层才返回 `decision=OK`；
接口异常、返现首店不匹配或店名不匹配均返回 `UNKNOWN`。
组合入口的 `UNKNOWN` 结果会以 JSON 写到 stdout，并以退出码 2 结束，便于上游程序统一处理。

## 单接口运行

直接传入活动网页 URL：

```powershell
node .\web-coupon-query.js '<PAGE_URL>'
```

默认使用抓包验证过的 GCJ02 经纬度 `29.688253, 106.600316`。查询其他地区时可覆盖：

```powershell
node .\web-coupon-query.js '<PAGE_URL>' <LAT> <LON>
```

默认坐标只是历史验证夹具，不代表调用方当前位置。生产开发应传入页面实际使用的
GCJ02 经纬度；位置变化约 20 公里即可导致首店列表变化。

查询器会解析 URL 参数并自动生成页面会话 ID。也可使用完整请求 body：

```powershell
node .\web-coupon-query.js .\request-body.json
```

标准输入方式：

```powershell
Get-Content -Raw -Encoding UTF8 .\request-body.json | node .\web-coupon-query.js -
```

输出只判断第一家商家：

- `HAS_COUPON`：读取 `firstMerchant.coupon`。
- `NO_COUPON`：第一家没有 `giftInfo.type == 1` 的商家券。
- `UNKNOWN`：签名、接口、业务响应或首家 POI 校验失败。

单独生成签名：

```powershell
$url = 'https://offsiteact.meituan.com/act/ge/queryPoiByRecallBiz'
$body = Get-Content -Raw -Encoding UTF8 .\request-body.json
node .\web-h5sign.js $url POST $body
```

签名必须针对最终 body 现用现生成。生成后不要修改 URL、方法、UA、body 或 `mtgsig` 字段。
经纬度不能同时为 `0`；零坐标会得到空商家列表。

## 必需运行文件

- `web-benefits-query.js`
- `web-coupon-query.js`
- `web-cashback-query.js`
- `web-h5sign.js`
- `H5guard_web_4.3.0.js`
- `package-1.1.2.js`
- `web-coupon-request-body.example.json`
- `web-cashback-request-body.example.json`
- `benefits-query-contract-redacted.json`

算法结论见 `mtgsig-algorithm-analysis.md`，接口字段见 `coupon-query-contract-redacted.json`，完整清单与哈希见 `WEB_DELIVERY_MANIFEST.json`。

本次组合流程的回滚文件为 `web-benefits-rollback.ps1` 及两个
`*.rollback-base.js` 基线文件；执行前应复制根目录或使用独立工作副本。

## 返现查询

在返现基础链接末尾拼接实际商家 `poi_id_str` 后执行（基础链接本身的空值不能直接查询）：

```powershell
node .\web-cashback-query.js '<CASHBACK_PAGE_URL>'
```

- `HAS_CASHBACK`：目标 POI 等于 `infos[0].poi_id_str`，返回比例和最高返现金额。
- `NO_CASHBACK`：按当前交付规则，首店为空或与目标 POI 不匹配；后续商家不计入。
  该结果表示首店不匹配，不代表接口已对任意目标 POI 完成全量资格查询。
- `UNKNOWN`：签名、接口、响应或返现计划结构异常。

返现接口复用同一套 `web-h5sign.js`、H5Guard 4.3.0 和 package 1.1.2。默认召回值已与登录网页当前“智能排序”同步为 `cpsPromotionOrderMulti`；网页其他排序值为 `cpsPromotionOrderRatio`、`cpsPromotionOrderKa`、`cpsPromotionOrderClosest`。详细字段见 `cashback-analysis.md`。

登录差异说明：登录页面仍调用同一查询接口，登录 Cookie 及 `getUserInfo`/返现任务上下文主要影响报名状态、名额等用户态字段，不是获取首屏列表的必要条件。交付脚本不保存或发送登录 Cookie，使用匿名 H5Guard 回放查询列表与返现计划。

## 历史 POI 兼容

实测出现过同一店铺对应历史 POI 与当前规范 POI 的情况：使用历史 POI 请求商家券
接口时，首店名称保持一致，但响应中的 `poi_id_str` 已变成另一个当前值。继续用历史
POI直接请求返现会触发 `FIRST_POI_MISMATCH`；改用商家券响应中的规范 POI后，返现
接口首店 POI和店名均匹配并成功返回返现计划。

因此历史 POI只作为入口参数保存，不再作为返现接口的最终查询主键。若上游能提供
店名，应使用 `--expected-name` 防止失效 POI 被推荐列表中的其他首店替代。精确案例
与判定条件见 `analysis.md`、`cashback-analysis.md` 和 `benefits-verification-record.txt`。
