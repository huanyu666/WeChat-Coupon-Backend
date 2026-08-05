# 来源 1 登录会话证据（脱敏）

## 登录契约

- 页面：`GET /md/login.html`
- 请求：`POST /md/api.php?action=login`
- 编码：`application/x-www-form-urlencoded; charset=UTF-8`
- 字段：`username=***`、`password=***`
- 页面代码未观察到客户端加密、签名、nonce、验证码或浏览器指纹。

## 会话验证

1. 用户在当前内置浏览器完成退出后重新登录。
2. 同一内置浏览器会话访问 `GET /md/index.html` 成功，页面标题为“奶爸羊毛查询系统”。
3. 同一会话访问 `GET /md/ranking.php?brand=<URL 编码商家>&time=<URL 编码场次>` 成功，页面标题为“麦当劳 12:00场 排行榜”，显示参与人数 522。
4. 无 Cookie 的独立 GET 同一榜单 URL 返回 HTTP 200，但正文是 `alert("请先登录");location.href="login.html";`。

结论：来源 1 榜单需要人工登录后的可用会话。浏览器导航与受保护榜单访问构成成功登录的脱敏后置证据；会话的 Cookie/localStorage/sessionStorage 载体、名称、值、属性及成功登录响应头均未读取、导出或保存。
