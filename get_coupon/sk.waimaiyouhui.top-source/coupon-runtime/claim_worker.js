'use strict';

// Long-lived, stdin-only worker for the main site's claim service. Tokens never
// appear in argv, stdout, logs, or error messages.
const readline = require('readline');
const crypto = require('crypto');
const https = require('https');
const cliguard = require('./vendor/cliguard/js/cliguard.js');

const ENDPOINTS = {
  workbuddy: '/fulishemini/couponActivity/sendCouponWork',
  tabbit: '/fulishemini/couponActivity/sendCouponTabbit'
};
const SCENES = {
  workbuddy: 'a0d4da77f918ab204d86c911fcdd0ce1',
  tabbit: '5a38dc8b7f17f76b9644b78abb41f0bc'
};
const agent = new https.Agent({ keepAlive: true, maxSockets: 8, maxFreeSockets: 4, timeout: 60000 });
const activeRequests = new Map();

function yuan(value) {
  const cents = Number.parseInt(value || 0, 10) || 0;
  const result = cents / 100;
  return Number.isInteger(result) ? result : Number(result.toFixed(2));
}

function dateText(value) {
  let timestamp = Number.parseInt(value || 0, 10) || 0;
  if (!timestamp) return '';
  if (timestamp < 10000000000) timestamp *= 1000;
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  const pad = (number) => String(number).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function coupon(item) {
  const value = item && typeof item === 'object' ? item : {};
  const start = dateText(value.couponStartTime);
  const end = dateText(value.couponEndTime);
  return {
    name: String(value.couponName || '美团优惠券').slice(0, 120),
    tab_name: String(value.tabName || '').slice(0, 80),
    amount_yuan: yuan(value.couponValue),
    threshold_yuan: yuan(value.priceLimit),
    valid_period: (start && end ? `${start} 至 ${end}` : (start || end)).slice(0, 40)
  };
}

function result(id, channel, status, message, extra) {
  const safe = String(message || '').replace(/token\s*[=:]\s*[^\s,;]+/ig, 'token=[redacted]').slice(0, 160);
  return Object.assign({ id, channel, status, message: safe, http_status: 0, business_code: null, coupon_count: 0, coupons: [], duration_ms: 0 }, extra || {});
}

function issue(request) {
  return new Promise((resolve) => {
    const id = String(request.id || '');
    const channel = String(request.channel || '').toLowerCase();
    const token = String(request.token || '');
    const timeout = Math.max(5, Math.min(60, Number(request.timeout_seconds || 35)));
    if (!ENDPOINTS[channel] || !token) {
      resolve(result(id, channel, 'failed', '领券请求参数无效'));
      return;
    }
    const body = Buffer.from(JSON.stringify({ token, aiScene: SCENES[channel], version: 2 }), 'utf8');
    let signedUrl;
    const started = Date.now();
    try {
      signedUrl = cliguard.addCommonParams(`https://media.meituan.com${ENDPOINTS[channel]}`).url;
      const bodyHash = crypto.createHash('md5').update(body.subarray(0, 16200)).digest('hex');
      const signedHeaders = cliguard.signRequest('POST', signedUrl, bodyHash) || {};
      const parsed = new URL(signedUrl);
      const req = https.request({
        hostname: parsed.hostname,
        port: parsed.port || 443,
        path: parsed.pathname + parsed.search,
        method: 'POST',
        agent,
        headers: Object.assign({
          'Content-Type': 'application/json',
          'Content-Length': body.length,
          'X-Requested-With': 'XMLHttpRequest',
          'Accept': 'application/json'
        }, signedHeaders)
      }, (response) => {
        const chunks = [];
        response.on('data', (chunk) => chunks.push(chunk));
        response.on('end', () => {
          activeRequests.delete(id);
          const base = { http_status: Number(response.statusCode || 0), duration_ms: Date.now() - started };
          if (base.http_status === 401) { resolve(result(id, channel, 'token_invalid', 'Token 已失效，请重新登录', base)); return; }
          if (base.http_status === 429) { resolve(result(id, channel, 'rate_limited', '请求过于频繁，请稍后重试', base)); return; }
          if (base.http_status >= 500) { resolve(result(id, channel, 'network_failed', '上游服务暂时不可用，请稍后重试', base)); return; }
          let payload;
          try { payload = JSON.parse(Buffer.concat(chunks).toString('utf8')); } catch (_) { resolve(result(id, channel, 'failed', '美团返回格式异常', base)); return; }
          if (!payload || typeof payload !== 'object' || Array.isArray(payload)) { resolve(result(id, channel, 'failed', '美团返回结构异常', base)); return; }
          const codeNumber = Number(payload.code);
          const data = payload.data;
          const message = String(payload.msg || payload.message || '');
          if (codeNumber === 200) {
            if (!data || typeof data !== 'object' || Array.isArray(data) || !Array.isArray(data.couponList)) {
              resolve(result(id, channel, 'failed', '美团返回结构异常', Object.assign(base, { business_code: codeNumber })));
              return;
            }
            const coupons = data.couponList.slice(0, 100).map(coupon);
            resolve(result(id, channel, coupons.length ? 'succeeded' : 'no_coupon', message || (coupons.length ? '领取成功' : '当前没有可领取神券'), Object.assign(base, { business_code: codeNumber, coupon_count: coupons.length, coupons })));
            return;
          }
          const statuses = { 401: 'token_invalid', 1014: 'already_received', 509: 'rate_limited', 50200: 'rate_limited' };
          const status = statuses[codeNumber] || 'failed';
          const defaults = { token_invalid: 'Token 已失效，请重新登录', already_received: '这个账号今天已经领取过', rate_limited: '请求过于频繁，请稍后重试', failed: '美团领券失败' };
          resolve(result(id, channel, status, message || defaults[status], Object.assign(base, { business_code: codeNumber })));
        });
      });
      req.setTimeout(timeout * 1000, () => req.destroy(new Error('TIMEOUT')));
      activeRequests.set(id, req);
      req.on('error', (error) => {
        activeRequests.delete(id);
        resolve(result(id, channel, error && error.message === 'TIMEOUT' ? 'timeout' : 'network_failed', error && error.message === 'TIMEOUT' ? '请求超时，请稍后重试' : '网络异常，请稍后重试', { duration_ms: Date.now() - started }));
      });
      req.end(body);
    } catch (_) {
      resolve(result(id, channel, 'failed', '领券签名初始化失败'));
    }
  });
}

const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', (line) => {
  let request;
  try { request = JSON.parse(line); } catch (_) { return; }
  if (request && request.cancel && request.id) {
    const active = activeRequests.get(String(request.id));
    if (active) active.destroy(new Error('CANCELLED'));
    return;
  }
  issue(request).then((payload) => process.stdout.write(JSON.stringify(payload) + '\n'));
});
input.on('close', () => process.exit(0));
