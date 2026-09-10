"use strict";

const dashboard = document.querySelector("#dashboard");
const loginView = document.querySelector("#loginView");
const loginForm = document.querySelector("#loginForm");
const loginError = document.querySelector("#loginError");
const passwordInput = document.querySelector("#password");
const logoutButton = document.querySelector("#logoutButton");
const claimSwitch = document.querySelector("#claimSwitch");
const merchantBaseUrl = document.querySelector("#merchantBaseUrl");
const urlStatus = document.querySelector("#urlStatus");
const saveButton = document.querySelector("#saveButton");
const saveState = document.querySelector("#saveState");
const claimStartButton = document.querySelector("#claimStartButton");
const claimRestartButton = document.querySelector("#claimRestartButton");
const claimStateBadge = document.querySelector("#claimStateBadge");
const claimProgress = document.querySelector("#claimProgress");
const claimSpinner = document.querySelector("#claimSpinner");
const claimStatusText = document.querySelector("#claimStatusText");
const claimAuthorize = document.querySelector("#claimAuthorize");
const claimAuthLink = document.querySelector("#claimAuthLink");
const claimQrImage = document.querySelector("#claimQrImage");
const claimResult = document.querySelector("#claimResult");
const claimResultTitle = document.querySelector("#claimResultTitle");
const claimCouponCount = document.querySelector("#claimCouponCount");
const claimResultSummary = document.querySelector("#claimResultSummary");
const claimCouponList = document.querySelector("#claimCouponList");
const merchantInput = document.querySelector("#merchantInput");
const queryButton = document.querySelector("#queryButton");
const queryEmpty = document.querySelector("#queryEmpty");
const queryResult = document.querySelector("#queryResult");
const resultMerchant = document.querySelector("#resultMerchant");
const resultCouponState = document.querySelector("#resultCouponState");
const resultCoupon = document.querySelector("#resultCoupon");
const resultCashback = document.querySelector("#resultCashback");
const resultPoi = document.querySelector("#resultPoi");
const resultLink = document.querySelector("#resultLink");
const resultReply = document.querySelector("#resultReply");
const toast = document.querySelector("#toast");

let toastTimer;
let claimPollTimer;
let claimFlowId = sessionStorage.getItem("claim_flow_id") || "";
let claimPolling = false;
let prefetchedClaim = null;
let claimPrefetchPromise = null;
let prefetchedQrImage = null;

async function api(url, options) {
  const response = await fetch(url, Object.assign({
    headers: { "Content-Type": "application/json" }
  }, options || {}));
  let data;
  try {
    data = await response.json();
  } catch (_) {
    data = { success: false, error: "服务器返回格式错误" };
  }
  if (!response.ok) {
    const error = new Error(data.error || "请求失败");
    error.status = response.status;
    throw error;
  }
  return data;
}

function showToast(message, isError) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.toggle("error", Boolean(isError));
  toast.classList.add("visible");
  toastTimer = setTimeout(() => toast.classList.remove("visible"), 2600);
}

function setClaimEnabled(enabled) {
  claimSwitch.setAttribute("aria-checked", enabled ? "true" : "false");
  claimSwitch.querySelector("b").textContent = enabled ? "开启" : "关闭";
  if (!claimPolling) claimStartButton.disabled = !enabled;
}

function claimEnabled() {
  return claimSwitch.getAttribute("aria-checked") === "true";
}

function checkUrl() {
  try {
    const parsed = new URL(merchantBaseUrl.value.trim());
    if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
    urlStatus.textContent = parsed.hostname + " · 格式正常";
    urlStatus.style.color = "var(--accent)";
    return true;
  } catch (_) {
    urlStatus.textContent = "链接格式错误";
    urlStatus.style.color = "var(--danger)";
    return false;
  }
}

function showLogin() {
  clearTimeout(claimPollTimer);
  claimPolling = false;
  dashboard.hidden = true;
  logoutButton.hidden = true;
  loginView.hidden = false;
  setTimeout(() => passwordInput.focus(), 0);
}

function claimRequest(path, body) {
  return api(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Request": "1"
    },
    body: JSON.stringify(body || {})
  });
}

function usablePrefetchedClaim(data) {
  const expiresAt = Date.parse(data?.expires_at || "");
  return Boolean(data?.flow_id && data?.auth_url && data?.qr_image_url)
    && Number.isFinite(expiresAt)
    && expiresAt - Date.now() > 30 * 1000;
}

function preloadClaimQr(data) {
  if (!usablePrefetchedClaim(data)) return;
  const image = new Image();
  image.decoding = "async";
  image.src = data.qr_image_url;
  if (typeof image.decode === "function") image.decode().catch(() => undefined);
  prefetchedQrImage = image;
}

function prefetchClaim() {
  if (!claimEnabled() || claimFlowId || claimPrefetchPromise) return claimPrefetchPromise;
  claimPrefetchPromise = claimRequest("/api/claim/start", {})
    .then((data) => {
      if (!usablePrefetchedClaim(data)) return null;
      prefetchedClaim = data;
      claimFlowId = data.flow_id;
      sessionStorage.setItem("claim_flow_id", claimFlowId);
      preloadClaimQr(data);
      claimStartButton.textContent = "显示已就绪的授权二维码";
      return data;
    })
    .catch((error) => {
      if (error.status === 401) showLogin();
      return null;
    })
    .finally(() => { claimPrefetchPromise = null; });
  return claimPrefetchPromise;
}

function claimStatusLabel(status) {
  return {
    starting: "生成授权中",
    awaiting_authorization: "等待授权",
    polling: "检查授权中",
    claiming: "正在同时领券",
    claimed: "领取成功",
    partial_success: "部分领取成功",
    already_received: "今日已领取",
    failed: "处理失败",
    expired: "授权已过期"
  }[status] || "处理中";
}

function renderCouponList(result) {
  claimCouponList.replaceChildren();
  const coupons = Array.isArray(result?.display_coupons) ? result.display_coupons : [];
  for (const coupon of coupons) {
    const item = document.createElement("li");
    const top = document.createElement("div");
    const name = document.createElement("strong");
    const discount = document.createElement("span");
    const period = document.createElement("small");
    name.textContent = (coupon.source ? "[" + coupon.source + "] " : "")
      + (coupon.name || coupon.tabName || "美团优惠券");
    discount.textContent = coupon.discount_info || "已领取";
    period.textContent = coupon.valid_period ? "有效期：" + coupon.valid_period : "";
    top.append(name, discount);
    item.append(top);
    if (period.textContent) item.append(period);
    claimCouponList.append(item);
  }
}

function renderClaim(data) {
  const status = data.status || "failed";
  const terminal = ["claimed", "partial_success", "already_received", "failed", "expired"].includes(status);
  claimStateBadge.textContent = claimStatusLabel(status);
  claimStatusText.textContent = data.message || claimStatusLabel(status);
  claimProgress.hidden = false;
  claimSpinner.hidden = terminal || status === "awaiting_authorization";

  if (data.auth_url) {
    claimAuthLink.href = data.auth_url;
    claimAuthorize.hidden = false;
    if (data.qr_image_url) {
      claimQrImage.src = data.qr_image_url;
      claimQrImage.hidden = false;
    } else {
      claimQrImage.removeAttribute("src");
      claimQrImage.hidden = true;
    }
  }

  if (terminal) {
    claimPolling = false;
    clearTimeout(claimPollTimer);
    claimStartButton.disabled = !claimEnabled();
    claimStartButton.textContent = "生成新的授权链接";
    claimResult.hidden = false;
    const result = data.result || {};
    claimResultTitle.textContent = status === "claimed"
      ? "WorkBuddy 与 Tabbit 领取成功"
      : status === "partial_success"
        ? "两个渠道已处理，结果不同"
      : status === "already_received"
        ? "这个账号今天已经领过"
        : data.message || "领取没有完成";
    claimCouponCount.textContent = Number.isFinite(Number(result.coupon_count))
      ? Number(result.coupon_count) + " 张"
      : "—";
    claimResultSummary.textContent = result.count_str || result.message || data.message || "";
    renderCouponList(result);
  } else {
    claimResult.hidden = true;
  }
  return terminal;
}

async function pollClaim() {
  if (!claimFlowId || claimPolling) return;
  claimPolling = true;
  try {
    const data = await claimRequest("/api/claim/poll", { flow_id: claimFlowId });
    const terminal = renderClaim(data);
    if (!terminal) claimPollTimer = setTimeout(pollClaim, 1600);
  } catch (error) {
    if (error.status === 401) {
      showLogin();
      return;
    }
    if (error.status === 404) {
      sessionStorage.removeItem("claim_flow_id");
      claimFlowId = "";
      renderClaim({ status: "expired", message: error.message });
      return;
    }
    claimStatusText.textContent = error.message;
    claimPollTimer = setTimeout(pollClaim, 3000);
  } finally {
    claimPolling = false;
  }
}

async function startClaim() {
  clearTimeout(claimPollTimer);
  claimPolling = true;
  claimStartButton.disabled = true;
  claimStartButton.textContent = "正在生成…";
  claimAuthorize.hidden = true;
  claimResult.hidden = true;
  claimProgress.hidden = false;
  claimSpinner.hidden = false;
  claimStateBadge.textContent = "生成授权中";
  claimStatusText.textContent = "正在生成独立授权链接";
  try {
    const pendingPrefetch = claimPrefetchPromise;
    const cached = usablePrefetchedClaim(prefetchedClaim)
      ? prefetchedClaim
      : pendingPrefetch ? await pendingPrefetch : null;
    const data = usablePrefetchedClaim(cached)
      ? cached
      : await claimRequest("/api/claim/start", { previous_flow_id: claimFlowId });
    prefetchedClaim = null;
    prefetchedQrImage = null;
    claimFlowId = data.flow_id || "";
    if (claimFlowId) sessionStorage.setItem("claim_flow_id", claimFlowId);
    const terminal = renderClaim(data);
    if (!terminal) claimPollTimer = setTimeout(pollClaim, 800);
  } catch (error) {
    if (error.status === 401) showLogin();
    renderClaim({ status: "failed", message: error.message });
    showToast(error.message, true);
  } finally {
    claimPolling = false;
    claimStartButton.disabled = !claimEnabled();
    claimStartButton.textContent = "生成新的授权链接";
  }
}

function showDashboard() {
  loginView.hidden = true;
  dashboard.hidden = false;
  logoutButton.hidden = false;
}

async function loadConfig() {
  try {
    const data = await api("/api/admin/config");
    setClaimEnabled(data.config.claim_enabled);
    merchantBaseUrl.value = data.config.merchant_coupon_base_url;
    checkUrl();
    showDashboard();
    if (claimFlowId) setTimeout(pollClaim, 0);
    else setTimeout(prefetchClaim, 0);
  } catch (error) {
    if (error.status === 401) {
      showLogin();
      return;
    }
    showLogin();
    showToast(error.message, true);
  }
}

claimStartButton.addEventListener("click", startClaim);
claimRestartButton.addEventListener("click", startClaim);

claimSwitch.addEventListener("click", () => {
  setClaimEnabled(!claimEnabled());
  saveState.textContent = "有未保存修改";
});

merchantBaseUrl.addEventListener("input", () => {
  checkUrl();
  saveState.textContent = "有未保存修改";
});

saveButton.addEventListener("click", async () => {
  if (!checkUrl()) {
    showToast("先修正商家券活动链接", true);
    return;
  }
  saveButton.disabled = true;
  saveButton.textContent = "保存中…";
  try {
    const data = await api("/api/admin/config", {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        "X-Admin-Request": "1"
      },
      body: JSON.stringify({
        claim_enabled: claimEnabled(),
        merchant_coupon_base_url: merchantBaseUrl.value.trim()
      })
    });
    merchantBaseUrl.value = data.config.merchant_coupon_base_url;
    saveState.textContent = "已同步";
    checkUrl();
    showToast("配置已保存");
  } catch (error) {
    if (error.status === 401) showLogin();
    showToast(error.message, true);
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = "保存配置";
  }
});

queryButton.addEventListener("click", async () => {
  const link = merchantInput.value.trim();
  if (!link) {
    showToast("请先粘贴美团商家链接", true);
    merchantInput.focus();
    return;
  }
  queryButton.disabled = true;
  queryButton.textContent = "查询中…";
  try {
    const data = await api("/api/merchant/query", {
      method: "POST",
      body: JSON.stringify({ link })
    });
    queryEmpty.hidden = true;
    queryResult.hidden = false;
    resultMerchant.textContent = data.merchant_name || "未知商家";
    resultCouponState.textContent = data.has_coupon ? "有商家券" : "无商家券";
    resultCoupon.textContent = data.coupon_text || "该店铺无商家券";
    resultCashback.textContent = data.cashback_text || "暂无返现";
    resultPoi.textContent = data.poi_id_str || "未解析";
    resultReply.textContent = data.reply_text || "";
    if (data.merchant_coupon_url) {
      resultLink.href = data.merchant_coupon_url;
      resultLink.hidden = false;
    } else {
      resultLink.removeAttribute("href");
      resultLink.hidden = true;
    }
  } catch (error) {
    queryResult.hidden = true;
    queryEmpty.hidden = false;
    showToast(error.message, true);
  } finally {
    queryButton.disabled = false;
    queryButton.textContent = "解析并生成商家券链接";
  }
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  loginError.textContent = "";
  const submit = loginForm.querySelector("button");
  submit.disabled = true;
  try {
    await api("/api/admin/login", {
      method: "POST",
      body: JSON.stringify({ password: passwordInput.value })
    });
    passwordInput.value = "";
    await loadConfig();
  } catch (error) {
    loginError.textContent = error.message;
  } finally {
    submit.disabled = false;
  }
});

logoutButton.addEventListener("click", async () => {
  try {
    await api("/api/admin/logout", { method: "POST", body: "{}" });
  } finally {
    showLogin();
  }
});

loadConfig();
