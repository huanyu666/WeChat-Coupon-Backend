"use strict";

const crypto = require("crypto");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const path = require("path");
const QRCode = require("qrcode");

const ROOT = __dirname;
const PUBLIC_DIR = path.join(ROOT, "public");
const CONFIG_FILE = process.env.CONFIG_FILE || path.join(ROOT, "config", "settings.json");
const MERCHANT_QUERY_URL = process.env.MERCHANT_QUERY_URL
  || "https://waimaiyouhui.top/web/api/merchant-coupon/query-first";
const HOST = process.env.HOST || "127.0.0.1";
const PORT = Number(process.env.PORT || 3188);
const COUPON_RUNTIME_DIR = process.env.COUPON_RUNTIME_DIR || path.join(ROOT, "coupon-runtime");
const COUPON_RUNNER = process.env.COUPON_RUNNER || path.join(COUPON_RUNTIME_DIR, "run.js");
const CLAIM_DATA_DIR = process.env.CLAIM_DATA_DIR || path.join(ROOT, "data", "claim-sessions");
const MAX_BODY_BYTES = 32 * 1024;
const SESSION_TTL_SECONDS = 8 * 60 * 60;
const CLAIM_TTL_MS = 10 * 60 * 1000;
const MAX_ACTIVE_CLAIM_FLOWS = Math.max(1, Math.min(100, Number(process.env.MAX_ACTIVE_CLAIM_FLOWS) || 20));
const CLAIM_POOL_TARGET = process.env.CLAIM_POOL_TARGET === undefined
  ? 8
  : Math.max(0, Math.min(16, Number(process.env.CLAIM_POOL_TARGET) || 0));
const CLAIM_POOL_REFRESH_MS = Math.max(
  60 * 1000,
  Math.min(CLAIM_TTL_MS - 2 * 60 * 1000, Number(process.env.CLAIM_POOL_REFRESH_MS) || 5 * 60 * 1000)
);
const PASSPORT_CLIENT_ID = "c6f50b5a1e2f4e2bb00a3e2f58df3ced";
const PASSPORT_SESSION_FILE = process.platform === "win32" ? "" : path.join(
  "/tmp",
  "pt_passport_session_" + crypto.createHash("sha256").update(PASSPORT_CLIENT_ID).digest("hex").slice(0, 16) + ".json"
);
const DEFAULT_CONFIG = Object.freeze({
  claim_enabled: true,
  merchant_coupon_base_url: "https://offsiteact.meituan.com/web/hoae/collection_waimai_v8/index.html"
});

const loginAttempts = new Map();
const claimFlows = new Map();
let claimPoolWarming = 0;

function safeEqual(left, right) {
  const a = Buffer.from(String(left));
  const b = Buffer.from(String(right));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function base64url(value) {
  return Buffer.from(value).toString("base64url");
}

function sessionSecret() {
  const value = process.env.SESSION_SECRET || "";
  if (value.length < 32) throw new Error("SESSION_SECRET must contain at least 32 characters");
  return value;
}

function createSessionToken() {
  const payload = base64url(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS }));
  const signature = crypto.createHmac("sha256", sessionSecret()).update(payload).digest("base64url");
  return payload + "." + signature;
}

function verifySessionToken(token) {
  if (!token || !token.includes(".")) return false;
  const [payload, signature] = token.split(".", 2);
  const expected = crypto.createHmac("sha256", sessionSecret()).update(payload).digest("base64url");
  if (!safeEqual(signature, expected)) return false;
  try {
    const parsed = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    return Number(parsed.exp) > Math.floor(Date.now() / 1000);
  } catch (_) {
    return false;
  }
}

function parseCookies(req) {
  const result = {};
  for (const part of String(req.headers.cookie || "").split(";")) {
    const index = part.indexOf("=");
    if (index <= 0) continue;
    result[part.slice(0, index).trim()] = decodeURIComponent(part.slice(index + 1).trim());
  }
  return result;
}

function isAdmin(req) {
  return verifySessionToken(parseCookies(req).skill_admin);
}

function setSecurityHeaders(res) {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  res.setHeader(
    "Content-Security-Policy",
    "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data: https://*.meituan.net https://*.meituan.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
  );
}

function sendJson(res, status, payload, headers) {
  const body = Buffer.from(JSON.stringify(payload), "utf8");
  setSecurityHeaders(res);
  res.writeHead(status, Object.assign({
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": body.length,
    "Cache-Control": "no-store"
  }, headers || {}));
  res.end(body);
}

function sendFile(res, filePath) {
  const types = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml"
  };
  try {
    const body = fs.readFileSync(filePath);
    setSecurityHeaders(res);
    res.writeHead(200, {
      "Content-Type": types[path.extname(filePath)] || "application/octet-stream",
      "Content-Length": body.length,
      "Cache-Control": "no-store, max-age=0"
    });
    res.end(body);
  } catch (_) {
    sendJson(res, 404, { success: false, error: "页面不存在" });
  }
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let length = 0;
    req.on("data", (chunk) => {
      length += chunk.length;
      if (length > MAX_BODY_BYTES) {
        reject(Object.assign(new Error("请求内容过大"), { status: 413 }));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf8");
        resolve(raw ? JSON.parse(raw) : {});
      } catch (_) {
        reject(Object.assign(new Error("JSON 格式错误"), { status: 400 }));
      }
    });
    req.on("error", reject);
  });
}

function loadConfig() {
  try {
    const data = JSON.parse(fs.readFileSync(CONFIG_FILE, "utf8"));
    return validateConfig(Object.assign({}, DEFAULT_CONFIG, data));
  } catch (_) {
    return Object.assign({}, DEFAULT_CONFIG);
  }
}

function validateConfig(input) {
  const claimEnabled = input.claim_enabled === true;
  const rawUrl = String(input.merchant_coupon_base_url || "").trim();
  if (!rawUrl) throw new Error("商家券活动链接不能为空");
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch (_) {
    throw new Error("商家券活动链接格式错误");
  }
  if (!["http:", "https:"].includes(parsed.protocol)) {
    throw new Error("商家券活动链接必须使用 HTTP 或 HTTPS");
  }
  parsed.searchParams.delete("poi_id_str");
  return {
    claim_enabled: claimEnabled,
    merchant_coupon_base_url: parsed.toString()
  };
}

function saveConfig(input) {
  const config = validateConfig(input);
  fs.mkdirSync(path.dirname(CONFIG_FILE), { recursive: true });
  const temporary = CONFIG_FILE + "." + process.pid + ".tmp";
  fs.writeFileSync(temporary, JSON.stringify(config, null, 2) + "\n", { mode: 0o600 });
  fs.renameSync(temporary, CONFIG_FILE);
  return config;
}

function formatMoney(cents) {
  const value = Number(cents);
  if (!Number.isFinite(value)) return "";
  return (value / 100).toFixed(2).replace(/\.00$/, "").replace(/(\.\d)0$/, "$1");
}

function formatCoupon(coupon) {
  if (!coupon) return "该店铺无商家券";
  const amount = formatMoney(coupon.coupon_amount);
  const threshold = formatMoney(coupon.order_amount_limit);
  if (!amount) return String(coupon.desc || "有商家券");
  return Number(coupon.order_amount_limit) > 0
    ? "满" + threshold + "元减" + amount + "元"
    : amount + "元商家券";
}

function formatCashback(cashback) {
  if (!cashback) return "暂无返现";
  const ratio = Number(cashback.user_max_ratio);
  const commission = formatMoney(cashback.user_max_commission);
  const pieces = [];
  if (Number.isFinite(ratio)) pieces.push((ratio / 100).toFixed(2).replace(/\.00$/, "") + "%");
  if (commission) pieces.push("最高返" + commission + "元");
  return pieces.length ? pieces.join("，") : "有返现活动";
}

function buildMerchantUrl(baseUrl, poiId) {
  const url = new URL(baseUrl);
  url.searchParams.set("poi_id_str", poiId);
  return url.toString();
}

function scrubSecrets(value) {
  if (Array.isArray(value)) return value.map(scrubSecrets);
  if (!value || typeof value !== "object") return value;
  const clean = {};
  for (const [key, item] of Object.entries(value)) {
    if (/^(access_token|refresh_token|token|authorization)$/i.test(key)) continue;
    clean[key] = scrubSecrets(item);
  }
  return clean;
}

function parseCommandJson(stdout) {
  const lines = String(stdout || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
  if (!lines.length) throw new Error("领券程序没有返回结果");
  return JSON.parse(lines.at(-1));
}

function runCouponCommand(flow, args, timeoutMs, envOverrides) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [COUPON_RUNNER, ...args], {
      cwd: COUPON_RUNTIME_DIR,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: Object.assign({}, process.env, {
        WORKBUDDY_CREDENTIALS_DIR: flow.credentialsDir,
        HUISHENG_LOG_DIR: flow.logDir,
        TMPDIR: flow.tempDir,
        TMP: flow.tempDir,
        TEMP: flow.tempDir,
        HOME: flow.homeDir,
        USERPROFILE: flow.homeDir,
        NODE_OPTIONS: ""
      }, envOverrides || {})
    });
    const stdout = [];
    const stderr = [];
    let size = 0;
    let settled = false;
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (error) reject(error);
      else resolve(result);
    };
    const collect = (target) => (chunk) => {
      size += chunk.length;
      if (size > 256 * 1024) {
        child.kill("SIGKILL");
        finish(new Error("领券程序返回内容过大"));
        return;
      }
      target.push(chunk);
    };
    child.stdout.on("data", collect(stdout));
    child.stderr.on("data", collect(stderr));
    child.on("error", (error) => finish(error));
    child.on("close", (code) => {
      try {
        finish(null, {
          code,
          payload: scrubSecrets(parseCommandJson(Buffer.concat(stdout).toString("utf8"))),
          stderr: Buffer.concat(stderr).toString("utf8")
        });
      } catch (_) {
        finish(new Error("领券程序返回格式错误"));
      }
    });
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      finish(new Error("领券程序响应超时"));
    }, timeoutMs || 25000);
  });
}

function runPassportCommand(flow, args, timeoutMs) {
  if (!PASSPORT_SESSION_FILE) return runCouponCommand(flow, args, timeoutMs);
  const sessionFile = path.join(flow.tempDir, path.basename(PASSPORT_SESSION_FILE));
  const backupFile = path.join(flow.rootDir, "passport-session-backup.json");
  const isolationHook = path.join(COUPON_RUNTIME_DIR, "passport-session-isolation.js");
  return (async () => {
    if (fs.existsSync(sessionFile)) {
      fs.copyFileSync(sessionFile, backupFile);
      fs.chmodSync(backupFile, 0o600);
    }
    try {
      return await runCouponCommand(flow, args, timeoutMs, {
        PT_PASSPORT_SESSION_FILE: sessionFile,
        PT_PASSPORT_GLOBAL_SESSION_FILE: PASSPORT_SESSION_FILE,
        NODE_OPTIONS: "--require=" + isolationHook
      });
    } finally {
      if (fs.existsSync(sessionFile)) {
        fs.copyFileSync(sessionFile, backupFile);
        fs.chmodSync(backupFile, 0o600);
      } else if (fs.existsSync(backupFile)) {
        fs.copyFileSync(backupFile, sessionFile);
        fs.chmodSync(sessionFile, 0o600);
      }
    }
  })();
}

function createClaimFlow() {
  fs.mkdirSync(CLAIM_DATA_DIR, { recursive: true, mode: 0o700 });
  const id = crypto.randomBytes(18).toString("hex");
  const rootDir = path.join(CLAIM_DATA_DIR, id);
  const flow = {
    id,
    rootDir,
    credentialsDir: path.join(rootDir, "credentials"),
    tempDir: path.join(rootDir, "tmp"),
    homeDir: path.join(rootDir, "home"),
    logDir: path.join(rootDir, "logs"),
    createdAt: Date.now(),
    updatedAt: Date.now(),
    status: "starting",
    authUrl: "",
    qrImageUrl: "",
    result: null,
    locked: false,
    monitoring: false,
    cancelled: false
  };
  for (const directory of [rootDir, flow.credentialsDir, flow.tempDir, flow.homeDir, flow.logDir]) {
    fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  }
  claimFlows.set(id, flow);
  return flow;
}

function removeClaimFiles(flow) {
  const root = path.resolve(CLAIM_DATA_DIR) + path.sep;
  const target = path.resolve(flow.rootDir);
  if (!target.startsWith(root)) return;
  try { fs.rmSync(target, { recursive: true, force: true }); } catch (_) {}
}

function persistClaimFlow(flow) {
  if (flow.cancelled || claimIsTerminal(flow) || !flow.authUrl || !flow.qrImageUrl) return;
  const stateFile = path.join(flow.rootDir, "flow-state.json");
  const tempFile = stateFile + ".tmp";
  const state = {
    version: 1,
    id: flow.id,
    createdAt: flow.createdAt,
    updatedAt: flow.updatedAt,
    authUrl: flow.authUrl,
    qrImageUrl: flow.qrImageUrl,
    reserved: flow.reserved === true
  };
  fs.writeFileSync(tempFile, JSON.stringify(state), { mode: 0o600 });
  fs.renameSync(tempFile, stateFile);
  fs.chmodSync(stateFile, 0o600);
}

function restoreClaimFlows() {
  fs.mkdirSync(CLAIM_DATA_DIR, { recursive: true, mode: 0o700 });
  let restored = 0;
  let removed = 0;
  const now = Date.now();
  for (const entry of fs.readdirSync(CLAIM_DATA_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory() || !/^[a-f0-9]{36}$/.test(entry.name)) continue;
    const rootDir = path.join(CLAIM_DATA_DIR, entry.name);
    try {
      const state = JSON.parse(fs.readFileSync(path.join(rootDir, "flow-state.json"), "utf8"));
      const createdAt = Number(state.createdAt);
      const valid = state.version === 1
        && state.id === entry.name
        && Number.isFinite(createdAt)
        && now - createdAt < CLAIM_TTL_MS
        && /^https?:\/\//i.test(String(state.authUrl || ""))
        && /^data:image\/png;base64,/i.test(String(state.qrImageUrl || ""));
      if (!valid) throw new Error("invalid flow state");

      const tempDir = path.join(rootDir, "tmp");
      const sessionFile = path.join(tempDir, path.basename(PASSPORT_SESSION_FILE));
      const backupFile = path.join(rootDir, "passport-session-backup.json");
      if (!fs.existsSync(sessionFile) && fs.existsSync(backupFile)) {
        fs.copyFileSync(backupFile, sessionFile);
        fs.chmodSync(sessionFile, 0o600);
      }
      if (PASSPORT_SESSION_FILE && !fs.existsSync(sessionFile)) {
        throw new Error("missing passport session");
      }

      const flow = {
        id: entry.name,
        rootDir,
        credentialsDir: path.join(rootDir, "credentials"),
        tempDir,
        homeDir: path.join(rootDir, "home"),
        logDir: path.join(rootDir, "logs"),
        createdAt,
        updatedAt: Number(state.updatedAt) || createdAt,
        status: "awaiting_authorization",
        message: state.reserved === true ? "等待美团确认授权" : "授权二维码已预生成",
        authUrl: state.authUrl,
        qrImageUrl: state.qrImageUrl,
        result: null,
        locked: false,
        monitoring: false,
        cancelled: false,
        reserved: state.reserved === true
      };
      claimFlows.set(flow.id, flow);
      restored += 1;
      if (flow.reserved) setImmediate(() => monitorClaimFlow(flow).catch(() => undefined));
    } catch (_) {
      removeClaimFiles({ rootDir });
      removed += 1;
    }
  }
  return { restored, removed };
}

function cleanupOrphanClaimFiles() {
  fs.mkdirSync(CLAIM_DATA_DIR, { recursive: true, mode: 0o700 });
  const root = path.resolve(CLAIM_DATA_DIR) + path.sep;
  let removed = 0;
  for (const entry of fs.readdirSync(CLAIM_DATA_DIR, { withFileTypes: true })) {
    if (!entry.isDirectory() || !/^[a-f0-9]{36}$/.test(entry.name)) continue;
    const target = path.resolve(CLAIM_DATA_DIR, entry.name);
    if (!target.startsWith(root)) continue;
    try {
      fs.rmSync(target, { recursive: true, force: true });
      removed += 1;
    } catch (_) {}
  }
  return removed;
}

function cleanupExpiredClaimFlows() {
  const now = Date.now();
  for (const [id, flow] of claimFlows) {
    if (!flow.locked && now - flow.createdAt > CLAIM_TTL_MS) {
      removeClaimFiles(flow);
      claimFlows.delete(id);
    }
  }
}

function claimResponse(flow) {
  const response = {
    success: flow.status !== "failed" && flow.status !== "expired",
    flow_id: flow.id,
    status: flow.status,
    message: flow.message || "",
    expires_at: new Date(flow.createdAt + CLAIM_TTL_MS).toISOString()
  };
  if (flow.authUrl && !["claimed", "partial_success", "already_received", "failed", "expired"].includes(flow.status)) {
    response.auth_url = flow.authUrl;
    response.qr_image_url = flow.qrImageUrl || "";
  }
  if (flow.result) response.result = scrubSecrets(flow.result);
  return response;
}

async function issueClaim(flow) {
  if (flow.cancelled) return;
  if (!loadConfig().claim_enabled) {
    flow.status = "failed";
    flow.message = "领券功能暂时关闭";
    removeClaimFiles(flow);
    return;
  }
  flow.status = "claiming";
  flow.message = "授权成功，正在同时领取 WorkBuddy 和 Tabbit 优惠券";
  const channels = [
    { key: "workbuddy", label: "WorkBuddy" },
    { key: "tabbit", label: "Tabbit" }
  ];
  const settled = await Promise.allSettled(channels.map((channel) => {
    return runCouponCommand(
      flow,
      ["issue", "--channel", channel.key, "--keep-auth-on-401", "true"],
      35000
    );
  }));
  const outcomes = channels.map((channel, index) => {
    const item = settled[index];
    const payload = item.status === "fulfilled"
      ? item.value.payload || {}
      : { ok: false, success: false, error: "EXEC_ERROR", message: item.reason?.message || "领取失败" };
    const state = payload.success === true && Number(payload.code) === 200
      ? "claimed"
      : payload.error === "ALREADY_RECEIVED" || Number(payload.code) === 1014
        ? "already_received"
        : "failed";
    return Object.assign({}, channel, { state, payload });
  });
  const claimedCount = outcomes.filter((item) => item.state === "claimed").length;
  const completedCount = outcomes.filter((item) => item.state !== "failed").length;
  const couponCount = outcomes.reduce((total, item) => total + (Number(item.payload.coupon_count) || 0), 0);
  const displayCoupons = outcomes.flatMap((item) => {
    const coupons = Array.isArray(item.payload.display_coupons) ? item.payload.display_coupons : [];
    return coupons.map((coupon) => Object.assign({ source: item.label }, coupon));
  });
  const summary = outcomes.map((item) => {
    if (item.state === "claimed") return item.label + "：领取 " + (Number(item.payload.coupon_count) || 0) + " 张";
    if (item.state === "already_received") return item.label + "：今天已领取";
    return item.label + "：" + (item.payload.message || "领取失败");
  }).join("；");
  const result = {
    success: claimedCount === channels.length,
    partial_success: claimedCount !== channels.length && completedCount > 0,
    coupon_count: couponCount,
    count_str: summary,
    display_coupons: displayCoupons,
    channels: Object.fromEntries(outcomes.map((item) => [item.key, Object.assign({}, item.payload, { state: item.state })]))
  };
  flow.result = result;
  flow.updatedAt = Date.now();
  if (claimedCount === channels.length) {
    flow.status = "claimed";
    flow.message = "WorkBuddy 和 Tabbit 优惠券领取成功";
  } else if (claimedCount > 0 || (completedCount > 0 && completedCount < channels.length)) {
    flow.status = "partial_success";
    flow.message = "两个渠道均已处理，部分渠道未领取成功";
  } else if (outcomes.every((item) => item.state === "already_received")) {
    flow.status = "already_received";
    flow.message = "WorkBuddy 和 Tabbit 今天都已经领取过了";
  } else {
    flow.status = "failed";
    flow.message = "WorkBuddy 和 Tabbit 均领取失败";
  }
  for (const item of outcomes) {
    process.stdout.write("claim flow=" + flow.id.slice(0, 8)
      + " channel=" + item.key
      + " status=" + item.state
      + " code=" + String(item.payload.code || item.payload.error || "unknown")
      + " coupons=" + String(item.payload.coupon_count || 0) + "\n");
  }
  removeClaimFiles(flow);
}

async function startClaimFlow(prepared) {
  cleanupExpiredClaimFlows();
  const activeCount = Array.from(claimFlows.values()).filter((flow) => {
    return flow.reserved === true && !flow.cancelled && !claimIsTerminal(flow);
  }).length;
  if (activeCount >= MAX_ACTIVE_CLAIM_FLOWS) {
    const error = new Error("当前授权人数较多，请稍后重试");
    error.status = 429;
    throw error;
  }
  const flow = createClaimFlow();
  flow.reserved = prepared !== true;
  flow.locked = true;
  let shouldMonitor = false;
  try {
    const started = await runPassportCommand(flow, ["auth-get-code"], 45000);
    const result = started.payload || {};
    if (result.ok !== true) {
      flow.status = "failed";
      flow.message = result.message || result.error || "生成授权链接失败";
      removeClaimFiles(flow);
      return flow;
    }
    if (result.type === "token") {
      await issueClaim(flow);
      return flow;
    }
    if (result.type !== "auth_link" || !/^https?:\/\//i.test(String(result.url || ""))) {
      flow.status = "failed";
      flow.message = "领券程序没有返回有效授权链接";
      removeClaimFiles(flow);
      return flow;
    }
    flow.status = "awaiting_authorization";
    flow.message = prepared === true ? "授权二维码已预生成" : "请打开美团授权页面并确认授权";
    flow.authUrl = result.url;
    flow.updatedAt = Date.now();
    await generateClaimQr(flow);
    persistClaimFlow(flow);
    process.stdout.write("claim flow=" + flow.id.slice(0, 8)
      + " status=awaiting_authorization prepared=" + String(prepared === true) + "\n");
    shouldMonitor = prepared !== true;
    return flow;
  } catch (error) {
    flow.status = "failed";
    flow.message = error.message || "生成授权链接失败";
    removeClaimFiles(flow);
    return flow;
  } finally {
    flow.locked = false;
    if (shouldMonitor) {
      setImmediate(() => {
        monitorClaimFlow(flow).catch(() => undefined);
      });
    }
  }
}

async function generateClaimQr(flow) {
  try {
    const imageUrl = await QRCode.toDataURL(flow.authUrl, {
      errorCorrectionLevel: "M",
      margin: 2,
      width: 280,
      type: "image/png"
    });
    if (!flow.cancelled && /^data:image\/png;base64,/i.test(imageUrl)) {
      flow.qrImageUrl = imageUrl;
      flow.updatedAt = Date.now();
    }
  } catch (_) {}
}

function takePreparedClaimFlow() {
  cleanupExpiredClaimFlows();
  const latestUsableAt = Date.now() - (CLAIM_TTL_MS - 2 * 60 * 1000);
  const flow = Array.from(claimFlows.values()).find((candidate) => {
    return candidate.reserved === false
      && !candidate.cancelled
      && !claimIsTerminal(candidate)
      && candidate.createdAt >= latestUsableAt
      && Boolean(candidate.authUrl)
      && Boolean(candidate.qrImageUrl);
  });
  if (!flow) return null;
  flow.reserved = true;
  flow.message = "请打开美团授权页面并确认授权";
  flow.updatedAt = Date.now();
  persistClaimFlow(flow);
  setImmediate(() => monitorClaimFlow(flow).catch(() => undefined));
  setImmediate(warmClaimPool);
  process.stdout.write("claim flow=" + flow.id.slice(0, 8) + " reserved=true\n");
  return flow;
}

function trimPreparedClaimPool() {
  const prepared = Array.from(claimFlows.values())
    .filter((flow) => {
      return flow.reserved === false
        && !flow.locked
        && !flow.cancelled
        && !claimIsTerminal(flow)
        && Boolean(flow.authUrl)
        && Boolean(flow.qrImageUrl);
    })
    .sort((left, right) => right.createdAt - left.createdAt);
  for (const flow of prepared.slice(CLAIM_POOL_TARGET)) {
    flow.cancelled = true;
    removeClaimFiles(flow);
    claimFlows.delete(flow.id);
  }
}

function warmClaimPool() {
  if (CLAIM_POOL_TARGET < 1 || !loadConfig().claim_enabled) return;
  cleanupExpiredClaimFlows();
  const freshestCreatedAt = Date.now() - CLAIM_POOL_REFRESH_MS;
  const freshPreparedCount = Array.from(claimFlows.values()).filter((flow) => {
    return flow.reserved === false
      && !flow.cancelled
      && !claimIsTerminal(flow)
      && flow.createdAt >= freshestCreatedAt
      && Boolean(flow.authUrl)
      && Boolean(flow.qrImageUrl);
  }).length;
  const needed = Math.max(0, CLAIM_POOL_TARGET - freshPreparedCount - claimPoolWarming);
  if (needed > 0) {
    process.stdout.write("claim pool fresh=" + freshPreparedCount
      + " warming=" + claimPoolWarming
      + " replenish=" + needed + "\n");
  }
  for (let index = 0; index < needed; index += 1) {
    claimPoolWarming += 1;
    startClaimFlow(true)
      .then((flow) => {
        if (claimIsTerminal(flow)) {
          claimFlows.delete(flow.id);
        } else {
          trimPreparedClaimPool();
        }
      })
      .catch((error) => {
        process.stderr.write("claim pool warm failed: " + String(error.message || error) + "\n");
      })
      .finally(() => { claimPoolWarming -= 1; });
  }
}

function claimIsTerminal(flow) {
  return ["claimed", "partial_success", "already_received", "failed", "expired"].includes(flow.status);
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function monitorClaimFlow(flow) {
  if (flow.monitoring || flow.cancelled) return;
  flow.monitoring = true;
  try {
    while (!flow.cancelled && !claimIsTerminal(flow)) {
      await pollClaimFlow(flow);
      if (flow.cancelled || claimIsTerminal(flow)) break;
      await wait(1200);
    }
  } finally {
    flow.monitoring = false;
    if (flow.cancelled) {
      removeClaimFiles(flow);
      claimFlows.delete(flow.id);
    }
  }
}

async function pollClaimFlow(flow) {
  if (flow.cancelled || claimIsTerminal(flow)) return flow;
  if (flow.locked) return flow;
  if (Date.now() - flow.createdAt > CLAIM_TTL_MS) {
    flow.status = "expired";
    flow.message = "授权链接已过期，请重新生成";
    removeClaimFiles(flow);
    return flow;
  }
  flow.locked = true;
  flow.status = "polling";
  flow.message = "等待美团确认授权";
  try {
    const polled = await runPassportCommand(flow, ["auth-poll-token", "--timeout", "8", "--interval", "1"], 18000);
    const result = polled.payload || {};
    if (flow.cancelled) return flow;
    if (result.ok === true) {
      await issueClaim(flow);
      return flow;
    }
    const detail = String(result.message || result.error || "");
    if (result.error === "POLL_FAILED" && !/(取消|风控|拒绝|session.*不存在|会话.*不存在)/i.test(detail)) {
      flow.status = "awaiting_authorization";
      flow.message = "等待美团确认授权";
      return flow;
    }
    flow.status = "failed";
    flow.message = detail || "授权失败";
    removeClaimFiles(flow);
    return flow;
  } catch (error) {
    flow.status = "awaiting_authorization";
    flow.message = error.message === "领券程序响应超时" ? "等待美团确认授权" : error.message;
    return flow;
  } finally {
    flow.updatedAt = Date.now();
    flow.locked = false;
  }
}

async function fetchJson(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs || 15000);
  try {
    const response = await fetch(url, Object.assign({}, options, { signal: controller.signal }));
    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch (_) {
      throw new Error("上游返回了无效 JSON");
    }
    return { status: response.status, data };
  } finally {
    clearTimeout(timer);
  }
}

async function queryMerchant(link) {
  const input = String(link || "").trim();
  if (!input) return { status: 400, body: { success: false, error: "链接不能为空" } };
  if (input.length > 8192) return { status: 400, body: { success: false, error: "链接内容过长" } };

  let upstream;
  try {
    upstream = await fetchJson(MERCHANT_QUERY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ link: input })
    }, 15000);
  } catch (error) {
    return {
      status: 502,
      body: {
        success: false,
        error: error.name === "AbortError" ? "商家查询超时" : "商家查询暂时不可用"
      }
    };
  }

  const data = upstream.data || {};
  if (upstream.status < 200 || upstream.status >= 300 || data.success !== true) {
    return {
      status: upstream.status >= 400 && upstream.status < 500 ? 400 : 502,
      body: { success: false, error: data.error || "商家查询暂时不可用" }
    };
  }

  const coupon = data.coupon_info || null;
  const cashback = data.cashback_info || null;
  const poiId = String(data.poi_id_str || data.requested_poi_id_str || coupon?.poi_id_str || "").trim();
  const merchantName = String(data.merchant_name || coupon?.shop_name || "未知商家").trim();
  let merchantCouponUrl = null;
  if (coupon && poiId) {
    try {
      merchantCouponUrl = buildMerchantUrl(loadConfig().merchant_coupon_base_url, poiId);
    } catch (_) {}
  }

  const couponText = formatCoupon(coupon);
  const cashbackText = formatCashback(cashback);
  const replyLines = [
    "商家：" + merchantName,
    "商家券：" + couponText,
    "返现：" + cashbackText
  ];
  if (merchantCouponUrl) replyLines.push("领取链接：[点击领取商家券](" + merchantCouponUrl + ")");

  return {
    status: 200,
    body: {
      success: true,
      merchant_name: merchantName,
      merchant_image: data.merchant_image || coupon?.shop_image || "",
      requested_poi_id_str: data.requested_poi_id_str || "",
      poi_id_str: poiId,
      has_coupon: Boolean(coupon),
      coupon_amount: coupon ? Number(coupon.coupon_amount || 0) : 0,
      coupon_amount_yuan: coupon ? formatMoney(coupon.coupon_amount) : "",
      order_amount_limit: coupon ? Number(coupon.order_amount_limit || 0) : 0,
      order_amount_limit_yuan: coupon ? formatMoney(coupon.order_amount_limit) : "",
      coupon_text: couponText,
      merchant_coupon_url: merchantCouponUrl,
      has_cashback: Boolean(cashback),
      cashback_ratio_percent: cashback && Number.isFinite(Number(cashback.user_max_ratio))
        ? Number(cashback.user_max_ratio) / 100
        : null,
      cashback_max_amount_yuan: cashback ? formatMoney(cashback.user_max_commission) : "",
      cashback_text: cashbackText,
      reply_text: replyLines.join("\n")
    }
  };
}

function clientIp(req) {
  return String(req.headers["x-forwarded-for"] || req.socket.remoteAddress || "").split(",")[0].trim();
}

function loginAllowed(ip) {
  const now = Date.now();
  const record = loginAttempts.get(ip);
  if (!record || now - record.startedAt > 10 * 60 * 1000) {
    loginAttempts.set(ip, { startedAt: now, count: 0 });
    return true;
  }
  return record.count < 8;
}

function recordLoginFailure(ip) {
  const record = loginAttempts.get(ip) || { startedAt: Date.now(), count: 0 };
  record.count += 1;
  loginAttempts.set(ip, record);
}

function requirePublicApiKey(req, res) {
  const expected = process.env.PUBLIC_API_KEY || "";
  if (!expected) return true;
  const actual = String(req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  if (safeEqual(actual, expected)) return true;
  sendJson(res, 401, { success: false, error: "接口鉴权失败" });
  return false;
}

function createApp() {
  return http.createServer(async (req, res) => {
    const url = new URL(req.url, "http://localhost");
    try {
      if (req.method === "GET" && url.pathname === "/health") {
        sendJson(res, 200, { success: true, service: "skill-admin", version: "0.2.0" });
        return;
      }

      if (req.method === "POST" && url.pathname === "/api/admin/login") {
        const ip = clientIp(req);
        if (!loginAllowed(ip)) {
          sendJson(res, 429, { success: false, error: "登录失败次数过多，请稍后再试" });
          return;
        }
        const body = await readJson(req);
        const expected = process.env.ADMIN_PASSWORD || "";
        if (!expected || !safeEqual(body.password || "", expected)) {
          recordLoginFailure(ip);
          sendJson(res, 401, { success: false, error: "密码错误" });
          return;
        }
        loginAttempts.delete(ip);
        sendJson(res, 200, { success: true }, {
          "Set-Cookie": "skill_admin=" + encodeURIComponent(createSessionToken())
            + "; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=" + SESSION_TTL_SECONDS
        });
        return;
      }

      if (req.method === "POST" && url.pathname === "/api/admin/logout") {
        sendJson(res, 200, { success: true }, {
          "Set-Cookie": "skill_admin=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0"
        });
        return;
      }

      if (url.pathname.startsWith("/api/admin/") && !isAdmin(req)) {
        sendJson(res, 401, { success: false, error: "请先登录" });
        return;
      }

      if (req.method === "GET" && url.pathname === "/api/admin/config") {
        sendJson(res, 200, { success: true, config: loadConfig() });
        return;
      }

      if (req.method === "PUT" && url.pathname === "/api/admin/config") {
        if (req.headers["x-admin-request"] !== "1") {
          sendJson(res, 403, { success: false, error: "请求校验失败" });
          return;
        }
        const config = saveConfig(await readJson(req));
        sendJson(res, 200, { success: true, config });
        return;
      }

      if (req.method === "POST" && url.pathname === "/api/claim/start") {
        if (!isAdmin(req)) {
          sendJson(res, 401, { success: false, error: "请先登录" });
          return;
        }
        if (req.headers["x-admin-request"] !== "1") {
          sendJson(res, 403, { success: false, error: "请求校验失败" });
          return;
        }
        if (!loadConfig().claim_enabled) {
          sendJson(res, 503, { success: false, error: "领券功能暂时关闭" });
          return;
        }
        const body = await readJson(req);
        const previousId = String(body.previous_flow_id || "");
        if (/^[a-f0-9]{36}$/.test(previousId) && claimFlows.has(previousId)) {
          const previous = claimFlows.get(previousId);
          previous.cancelled = true;
          if (!previous.locked) {
            removeClaimFiles(previous);
            claimFlows.delete(previousId);
          }
        }
        const flow = takePreparedClaimFlow() || await startClaimFlow(false);
        setImmediate(warmClaimPool);
        sendJson(res, 200, claimResponse(flow));
        return;
      }

      if (req.method === "POST" && url.pathname === "/api/claim/poll") {
        if (!isAdmin(req)) {
          sendJson(res, 401, { success: false, error: "请先登录" });
          return;
        }
        if (req.headers["x-admin-request"] !== "1") {
          sendJson(res, 403, { success: false, error: "请求校验失败" });
          return;
        }
        cleanupExpiredClaimFlows();
        const body = await readJson(req);
        const flowId = String(body.flow_id || "");
        const flow = /^[a-f0-9]{36}$/.test(flowId) ? claimFlows.get(flowId) : null;
        if (!flow) {
          sendJson(res, 404, { success: false, error: "授权会话已过期或因服务重启失效，请重新生成" });
          return;
        }
        sendJson(res, 200, claimResponse(flow));
        return;
      }

      if (req.method === "POST" && url.pathname === "/api/claim/cancel") {
        if (!isAdmin(req)) {
          sendJson(res, 401, { success: false, error: "请先登录" });
          return;
        }
        if (req.headers["x-admin-request"] !== "1") {
          sendJson(res, 403, { success: false, error: "请求校验失败" });
          return;
        }
        const body = await readJson(req);
        const flowId = String(body.flow_id || "");
        const flow = /^[a-f0-9]{36}$/.test(flowId) ? claimFlows.get(flowId) : null;
        if (flow) {
          flow.cancelled = true;
          if (!flow.locked) {
            removeClaimFiles(flow);
            claimFlows.delete(flowId);
          }
        }
        sendJson(res, 200, { success: true });
        return;
      }

      if (req.method === "GET" && url.pathname === "/api/public/config") {
        if (!requirePublicApiKey(req, res)) return;
        const config = loadConfig();
        sendJson(res, 200, { success: true, claim_enabled: config.claim_enabled });
        return;
      }

      if (req.method === "POST" && url.pathname === "/api/merchant/query") {
        if (!requirePublicApiKey(req, res)) return;
        const result = await queryMerchant((await readJson(req)).link);
        sendJson(res, result.status, result.body);
        return;
      }

      if (req.method === "GET" && ["/", "/index.html"].includes(url.pathname)) {
        sendFile(res, path.join(PUBLIC_DIR, "index.html"));
        return;
      }

      if (req.method === "GET" && ["/app.js", "/styles.css"].includes(url.pathname)) {
        sendFile(res, path.join(PUBLIC_DIR, path.basename(url.pathname)));
        return;
      }

      sendJson(res, 404, { success: false, error: "接口不存在" });
    } catch (error) {
      sendJson(res, error.status || 500, {
        success: false,
        error: error.status ? error.message : "服务器内部错误"
      });
    }
  });
}

if (require.main === module) {
  if (!process.env.ADMIN_PASSWORD) {
    process.stderr.write("ADMIN_PASSWORD is required\n");
    process.exit(1);
  }
  sessionSecret();
  const recovery = restoreClaimFlows();
  process.stdout.write("claim sessions restored=" + recovery.restored
    + " removed=" + recovery.removed + "\n");
  createApp().listen(PORT, HOST, () => {
    process.stdout.write("skill-admin listening on http://" + HOST + ":" + PORT + "\n");
  });
  setImmediate(warmClaimPool);
  const poolTimer = setInterval(warmClaimPool, 30 * 1000);
  poolTimer.unref();
}

module.exports = {
  buildMerchantUrl,
  cleanupOrphanClaimFiles,
  createApp,
  formatCashback,
  formatCoupon,
  loadConfig,
  queryMerchant,
  restoreClaimFlows,
  saveConfig,
  trimPreparedClaimPool,
  validateConfig
};
