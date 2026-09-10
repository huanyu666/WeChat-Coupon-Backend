"use strict";

const command = process.argv[2];

if (command === "auth-get-code") {
  console.log(JSON.stringify({ ok: true, type: "auth_link", url: "https://example.test/authorize" }));
} else if (command === "qrcode") {
  console.log(JSON.stringify({ ok: true, imageUrl: "https://p0.meituan.net/test-qr.png" }));
} else if (command === "auth-poll-token") {
  console.log(JSON.stringify({ ok: true, token: "secret-test-token" }));
} else if (command === "issue") {
  const channelIndex = process.argv.indexOf("--channel");
  const channel = channelIndex >= 0 ? process.argv[channelIndex + 1] : "workbuddy";
  const isTabbit = channel === "tabbit";
  console.log(JSON.stringify({
    ok: true,
    success: true,
    code: 200,
    channel,
    coupon_count: isTabbit ? 2 : 1,
    count_str: isTabbit ? "Tabbit优惠券2张" : "外卖优惠券1张",
    display_coupons: [{
      name: isTabbit ? "Tabbit券" : "外卖券",
      discount_info: "满10元减2元",
      valid_period: "2026-09-08 至 2026-09-09"
    }]
  }));
} else {
  console.log(JSON.stringify({ ok: false, error: "UNKNOWN" }));
}
