"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const temporaryRoot = fs.mkdtempSync(path.join(os.tmpdir(), "skill-admin-test-"));
process.env.CONFIG_FILE = path.join(temporaryRoot, "settings.json");
process.env.ADMIN_PASSWORD = "correct-test-password";
process.env.SESSION_SECRET = "test-session-secret-that-is-longer-than-32-characters";
process.env.CLAIM_DATA_DIR = path.join(temporaryRoot, "claim-sessions");
process.env.COUPON_RUNTIME_DIR = path.join(__dirname, "..", "fixtures");
process.env.COUPON_RUNNER = path.join(__dirname, "..", "fixtures", "fake-coupon-runner.js");
process.env.CLAIM_POOL_TARGET = "0";

let upstream;
let app;
let origin;
let serverModule;

function listen(server) {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server.address())));
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function request(method, requestPath, body, headers) {
  const target = new URL(requestPath, origin);
  const payload = body === undefined ? null : Buffer.from(JSON.stringify(body));
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: target.hostname,
      port: target.port,
      path: target.pathname + target.search,
      method,
      headers: Object.assign({
        Accept: "application/json"
      }, payload ? {
        "Content-Type": "application/json",
        "Content-Length": payload.length
      } : {}, headers || {})
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: JSON.parse(Buffer.concat(chunks).toString("utf8"))
      }));
    });
    req.on("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

test.before(async () => {
  upstream = http.createServer(async (req, res) => {
    const chunks = [];
    for await (const chunk of req) chunks.push(chunk);
    const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
    const hasCoupon = !String(body.link).includes("no-coupon");
    const result = {
      success: true,
      requested_poi_id_str: "myo_test",
      poi_id_str: "myo_test",
      merchant_name: "测试门店",
      merchant_image: "",
      coupon_info: hasCoupon ? {
        poi_id_str: "myo_test",
        shop_name: "测试门店",
        coupon_amount: 200,
        order_amount_limit: 1000
      } : null,
      cashback_info: hasCoupon ? {
        user_max_ratio: 2000,
        user_max_commission: 500
      } : null
    };
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify(result));
  });
  const upstreamAddress = await listen(upstream);
  process.env.MERCHANT_QUERY_URL = "http://127.0.0.1:" + upstreamAddress.port + "/query";

  delete require.cache[require.resolve("../server")];
  serverModule = require("../server");
  serverModule.saveConfig({
    claim_enabled: true,
    merchant_coupon_base_url: "https://offsiteact.meituan.com/page?p=123&poi_id_str=old"
  });
  app = serverModule.createApp();
  const address = await listen(app);
  origin = "http://127.0.0.1:" + address.port;
});

test.after(async () => {
  await close(app);
  await close(upstream);
  fs.rmSync(temporaryRoot, { recursive: true, force: true });
});

test("admin can log in and update the claim switch", async () => {
  const login = await request("POST", "/api/admin/login", { password: "correct-test-password" });
  assert.equal(login.status, 200);
  const cookie = login.headers["set-cookie"][0].split(";", 1)[0];

  const update = await request("PUT", "/api/admin/config", {
    claim_enabled: false,
    merchant_coupon_base_url: "https://offsiteact.meituan.com/page?p=123"
  }, {
    Cookie: cookie,
    "X-Admin-Request": "1"
  });
  assert.equal(update.status, 200);
  assert.equal(update.body.config.claim_enabled, false);

  const publicConfig = await request("GET", "/api/public/config");
  assert.equal(publicConfig.body.claim_enabled, false);
});

test("merchant query appends poi_id_str and formats coupon and cashback", async () => {
  const result = await request("POST", "/api/merchant/query", { link: "http://dpurl.cn/example" });
  assert.equal(result.status, 200);
  assert.equal(result.body.merchant_name, "测试门店");
  assert.equal(result.body.coupon_text, "满10元减2元");
  assert.equal(result.body.cashback_text, "20%，最高返5元");
  assert.equal(new URL(result.body.merchant_coupon_url).searchParams.get("poi_id_str"), "myo_test");
  assert.match(result.body.reply_text, /领取链接：\[点击领取商家券\]\(https:\/\//);
});

test("merchant query reports no coupon without returning a claim link", async () => {
  const result = await request("POST", "/api/merchant/query", { link: "no-coupon" });
  assert.equal(result.status, 200);
  assert.equal(result.body.has_coupon, false);
  assert.equal(result.body.coupon_text, "该店铺无商家券");
  assert.equal(result.body.merchant_coupon_url, null);
  assert.match(result.body.reply_text, /商家券：该店铺无商家券/);
});

test("one authorization is followed by concurrent WorkBuddy and Tabbit claims", async () => {
  serverModule.saveConfig({
    claim_enabled: true,
    merchant_coupon_base_url: "https://offsiteact.meituan.com/page?p=123"
  });
  const login = await request("POST", "/api/admin/login", { password: "correct-test-password" });
  const cookie = login.headers["set-cookie"][0].split(";", 1)[0];
  const headers = {
    Cookie: cookie,
    "X-Admin-Request": "1"
  };

  const started = await request("POST", "/api/claim/start", {}, headers);
  assert.equal(started.status, 200);
  assert.equal(started.body.status, "awaiting_authorization");
  assert.equal(started.body.auth_url, "https://example.test/authorize");
  assert.match(started.body.qr_image_url, /^data:image\/png;base64,/);

  let polled;
  for (let attempt = 0; attempt < 30; attempt += 1) {
    polled = await request("POST", "/api/claim/poll", { flow_id: started.body.flow_id }, headers);
    if (polled.body.status === "claimed") break;
    await delay(40);
  }
  assert.equal(polled.status, 200);
  assert.equal(polled.body.status, "claimed");
  assert.equal(polled.body.result.coupon_count, 3);
  assert.equal(polled.body.result.channels.workbuddy.state, "claimed");
  assert.equal(polled.body.result.channels.tabbit.state, "claimed");
  assert.deepEqual(polled.body.result.display_coupons.map((coupon) => coupon.source), ["WorkBuddy", "Tabbit"]);
  assert.doesNotMatch(JSON.stringify(polled.body), /secret-test-token/);
  assert.equal(fs.existsSync(path.join(process.env.CLAIM_DATA_DIR, started.body.flow_id)), false);
});

test("startup cleanup removes only orphan claim directories", () => {
  const orphan = "a".repeat(36);
  const keep = "keep-this-directory";
  fs.mkdirSync(path.join(process.env.CLAIM_DATA_DIR, orphan), { recursive: true });
  fs.mkdirSync(path.join(process.env.CLAIM_DATA_DIR, keep), { recursive: true });

  assert.equal(serverModule.cleanupOrphanClaimFiles(), 1);
  assert.equal(fs.existsSync(path.join(process.env.CLAIM_DATA_DIR, orphan)), false);
  assert.equal(fs.existsSync(path.join(process.env.CLAIM_DATA_DIR, keep)), true);
});
