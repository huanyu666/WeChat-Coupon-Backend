# 商家券与返现查询运维说明

商家权益查询由主应用和内网 Node 服务 `merchant-benefits` 共同完成。Node 服务不映射公网端口，主应用通过 `http://merchant-benefits:18180` 访问。

## 启用前配置

1. 在公众号设置中确认“美团基础链接”是 `offsiteact.meituan.com` 的 `collection_waimai_v8` HTTPS 页面。
2. 如需返现查询，把“美团官方返现基础链接”设置为 `offsiteact.meituan.com` 的 `order_cashback_activity` HTTPS 页面。系统会自动追加或替换 `poi_id_str`。
3. 旧的 `pages/...` 小程序路径仍可保存并继续供原功能使用，但不能用于返现接口查询。
4. 在 Web 后台“商家权益”中设置默认 GCJ02 经纬度，执行一次完整链路测试。
5. 测试通过后再开启“启用 Web 与公众号权益查询”。新部署默认关闭。

## 部署与检查

```bash
docker compose build merchant-benefits app
docker compose up -d merchant-benefits app
docker compose ps
docker compose logs --tail=100 merchant-benefits app
docker compose exec -T merchant-benefits node -e \
  "fetch('http://127.0.0.1:18180/healthz').then(r=>r.text()).then(console.log)"
```

健康信息应显示 4 个 Worker 全部 ready。默认最大队列为 50，组合查询总超时为 15 秒，上游单次请求超时为 6 秒。

返现查询固定使用网页“智能排序”的 `recallBizId=cpsPromotionOrderMulti`，并只接受返回首店与商家券规范 POI、店名一致的结果。旧值 `cpsSelfPromotionOrder` 会返回另一套商家列表，可能造成页面明确有返现而服务端显示无法确认；升级后缓存协议版本会自动隔离旧查询结果。

## 数据与回滚

缓存和统计保存在 `runtime-data/merchant_benefits/merchant_benefits.db`，已经纳入运行数据迁移和备份。关闭后台功能开关即可立即停止 Web 与公众号查询；Node 服务异常不会阻止主应用启动，也不会中断原有津贴列表或公众号查单回复。
