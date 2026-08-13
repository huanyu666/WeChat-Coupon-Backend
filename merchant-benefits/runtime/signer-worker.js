/**
 * Resident H5Guard signer worker. The browser shim and H5Guard runtime are
 * initialized once, then this worker signs one request at a time over IPC.
 */
'use strict';

const fs   = require('fs');
const path = require('path');
const { parentPort } = require('worker_threads');

const TARGET_URL = 'https://offsiteact.meituan.com/act/ge/queryPoiByRecallBiz';
const METHOD = 'POST';
const DIR    = __dirname;
const TARGET = new URL(TARGET_URL);
if (!/(^|\.)offsiteact\.meituan\.com$/i.test(TARGET.hostname)) {
    process.stderr.write('[web-h5sign] 仅支持 offsiteact.meituan.com 网页接口\n');
    process.exit(1);
}
const WEB_APP_KEY = 'dbb66eaa8eb2ae544627840d52ccde503fd7ceb4d45f43f0c8c7851e173cacec';

const UA_WEB = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0';

function resolveUa(arg) {
    return arg || UA_WEB;
}
const UA = resolveUa(process.env.MERCHANT_BENEFITS_USER_AGENT);

// ─── 异常捕获 ─────────────────────────────────────────
process.on('unhandledRejection', function(r) {
    process.stderr.write('[h5sign rejection] ' + String(r).slice(0, 200) + '\n');
});

// ─── 浏览器环境shim ───────────────────────────────────
global.window = global;
global.self   = global;
global.addEventListener = function() {};
global.removeEventListener = function() {};
global.dispatchEvent = function() { return false; };
global.getComputedStyle = function() { return {}; };
global.matchMedia = function() { return { matches: false, addListener: function(){}, removeListener: function(){} }; };
global.innerWidth = 1920;
global.innerHeight = 1040;
global.outerWidth = 1920;
global.outerHeight = 1080;

global.document = {
    cookie: '', referrer: '', title: '', readyState: 'complete',
    createElement: function(t) {
        return { style: {}, tagName: t, setAttribute: function(){}, getAttribute: function(){return null;}, appendChild: function(){}, removeChild: function(){} };
    },
    getElementById: function() { return null; },
    getElementsByTagName: function() { return []; },
    querySelector: function() { return null; },
    querySelectorAll: function() { return []; },
    head: { appendChild: function(){}, removeChild: function(){} },
    body: { appendChild: function(){}, removeChild: function(){}, style: {} },
    addEventListener: function() {}, removeEventListener: function() {}, dispatchEvent: function() {},
    createEvent: function() { return { initEvent: function(){} }; },
    documentElement: { lang: 'zh-CN', style: {} },
};

global.location = {
    href: TARGET.href,
    hostname: TARGET.hostname,
    host: TARGET.host,
    protocol: 'https:',
    pathname: TARGET.pathname,
    search: TARGET.search, hash: '',
    origin: TARGET.origin,
};

try {
    Object.defineProperty(global, 'navigator', {
        value: {
            userAgent: UA,
            platform: 'Win32',
            language: 'zh-CN',
            languages: ['zh-CN', 'zh', 'en-US', 'en'],
            onLine: true, cookieEnabled: true,
            plugins:   { length: 0, item: function() { return null; }, namedItem: function() { return null; }, refresh: function() {} },
            mimeTypes: { length: 0, item: function() { return null; }, namedItem: function() { return null; } },
            vendor: 'Google Inc.',
            maxTouchPoints: 0,
            hardwareConcurrency: 8,
            deviceMemory: 8,
            connection: { effectiveType: '4g', rtt: 50, downlink: 10, addEventListener: function(){} },
            permissions: { query: function() { return Promise.resolve({ state: 'prompt' }); } },
        },
        writable: true, configurable: true,
    });
} catch(e) {}

global.screen = { width: 1920, height: 1080, availWidth: 1920, availHeight: 1040, colorDepth: 24, pixelDepth: 24 };
global.history = { length: 1, state: null, pushState: function(){}, replaceState: function(){} };
global.performance = {
    now: function() { return Date.now(); },
    timing: { navigationStart: Date.now() - 1000 },
    getEntriesByType: function() { return []; }, mark: function(){}, measure: function(){},
};

function makeStorage() {
    const s = {};
    return {
        getItem: function(k) { return k in s ? s[k] : null; },
        setItem: function(k, v) { s[k] = String(v); },
        removeItem: function(k) { delete s[k]; },
        clear: function() { Object.keys(s).forEach(function(k) { delete s[k]; }); },
        key: function(i) { return Object.keys(s)[i] || null; },
        get length() { return Object.keys(s).length; },
    };
}
global.localStorage = makeStorage();
global.sessionStorage = makeStorage();

// 缺失的浏览器全局
global.alert   = function() {};
global.confirm = function() { return true; };
global.prompt  = function() { return ''; };
global.requestAnimationFrame = function(cb) { setTimeout(cb, 16); return 0; };
global.cancelAnimationFrame  = function() {};
global.WebSocket = function() { this.send = function(){}; this.close = function(){}; this.addEventListener = function(){}; };
global.MutationObserver     = function() { this.observe = function(){}; this.disconnect = function(){}; };
global.IntersectionObserver = function() { this.observe = function(){}; this.disconnect = function(){}; };
global.ResizeObserver       = function() { this.observe = function(){}; this.disconnect = function(){}; };
global.URL            = require('url').URL;
global.URLSearchParams = URLSearchParams;
global.Image  = function() { this.src = ''; };
global.Audio  = function() { return { play: function() { return Promise.resolve(); }, pause: function() {} }; };

// 网页 H5Guard 通过 setRequestHeader 写入 mtgsig。
global._capturedSignedUrls = [];
global._capturedMtgsigs = [];
function FakeXHR() {
    this.readyState = 0; this.status = 200;
    this.responseText = '{}'; this.response = '{}';
    this.onload = null; this.onerror = null; this._url = ''; this._headers = {};
}
FakeXHR.prototype.open = function(method, url) {
    this._url = url; this.readyState = 1;
    if (process.env.MT_H5_DEBUG === '1') process.stderr.write('[h5sign debug] original open ' + String(method) + ' ' + String(url).slice(0, 500) + '\n');
    if (typeof url === 'string' && url.indexOf('mtgsig=') !== -1) {
        global._capturedSignedUrls.push(url);
    }
};
FakeXHR.prototype.setRequestHeader = function(name, value) {
    const key = String(name).toLowerCase();
    this._headers[key] = String(value);
    if (key === 'mtgsig') {
        global._capturedMtgsigs.push(String(value));
        if (process.env.MT_H5_DEBUG === '1') {
            process.stderr.write('[h5sign debug] captured mtgsig header length=' + String(value).length + '\n');
        }
    }
};
FakeXHR.prototype.getResponseHeader = function() { return null; };
FakeXHR.prototype.send = function() {
    if (process.env.MT_H5_DEBUG === '1') process.stderr.write('[h5sign debug] original send url=' + String(this._url).slice(0, 500) + '\n');
    this.readyState = 4;
    const self = this;
    process.nextTick(function() { if (typeof self.onload === 'function') self.onload(); });
};
FakeXHR.prototype.abort = function() {};
FakeXHR.prototype.addEventListener = function(e, f) {
    if (e === 'load') this.onload = f;
    if (e === 'error') this.onerror = f;
};
FakeXHR.prototype.removeEventListener = function() {};
global.XMLHttpRequest = FakeXHR;

global.fetch = function() {
    return Promise.resolve({
        ok: true, status: 200,
        json: function() { return Promise.resolve({}); },
        text: function() { return Promise.resolve('{}'); },
        headers: { get: function() { return null; } },
    });
};

// KNB stub
global.KNB = {
    env: { isTitans: true }, isTitans: true,
    ready: function(cb) { if (typeof cb === 'function') setTimeout(cb, 5); return Promise.resolve(global.KNB); },
    addRequestSignature: function(opts) {
        const r = { statusCode: 200, responseText: '{}', code: 0, mtgsig: '{}', signature: 'h1.9stub' };
        if (typeof opts.success === 'function') setTimeout(function() { opts.success(r); }, 5);
        return Promise.resolve(r);
    },
    sign: function(opts) {
        const r = { code: 0, signature: 'h1.9stub' };
        if (typeof opts.success === 'function') setTimeout(function() { opts.success(r); }, 5);
        return Promise.resolve(r);
    },
    getfp: function(cb) {
        if (typeof cb === 'function') setTimeout(function() { cb(''); }, 5);
        return Promise.resolve('');
    },
    getId: function() { return Promise.resolve(''); },
    initWithKey: function() { return Promise.resolve(global.KNB); },
};

// 屏蔽H5guard内部console输出
console.log  = function() {};
console.warn = function() {};
console.info = function() {};
console.debug = function() {};
// 保留error但只写到stderr
console.error = function() {
    const msg = Array.prototype.slice.call(arguments).join(' ');
    if (msg.length < 200) process.stderr.write('[h5] ' + msg + '\n');
};

// ─── 加载H5guard ──────────────────────────────────────
const WEB_H5GUARD_JS = path.join(DIR, 'H5guard_web_4.3.0.js');
const H5GUARD_JS = process.env.MT_H5GUARD_JS || WEB_H5GUARD_JS;
eval(fs.readFileSync(H5GUARD_JS, 'utf8'));

if (typeof window.H5guard === 'undefined') {
    process.stderr.write('[h5sign] H5guard未定义\n');
    process.exit(2);
}

if (process.env.MT_H5_DEBUG === '1') {
    process.stderr.write('[h5sign debug] H5guard keys=' + Object.keys(window.H5guard).join(',') + '\n');
}

const initResult = window.H5guard.initWithKey({
        appKey: WEB_APP_KEY,
        xhrHook: true,
        fetchHook: true,
        domains: ['offsiteact.meituan.com', 'offsiteact.st.meituan.com',
            'offsiteact.waimai.st.sankuai.com', 'offsiteact.waimai.test.sankuai.com'],
        openId: '',
        geo: true,
});
if (process.env.MT_H5_DEBUG === '1') {
    process.stderr.write('[h5sign debug] init return=' + String(initResult && typeof initResult.then === 'function' ? 'Promise' : initResult) + '\n');
}

// 加载package.js
try {
    eval(fs.readFileSync(path.join(DIR, 'package-1.1.2.js'), 'utf8'));
} catch(e) {
    process.stderr.write('[h5sign] package.js加载失败: ' + e.message.slice(0, 100) + '\n');
}

if (process.env.MT_H5_DEBUG === '1') {
    process.stderr.write('[h5sign debug] xhr.open=' + String(XMLHttpRequest.prototype.open).slice(0, 180) + '\n');
    process.stderr.write('[h5sign debug] fetch=' + String(global.fetch).slice(0, 120) + '\n');
}

function captureSignature(body) {
    return new Promise(function(resolve, reject) {
    global._capturedSignedUrls = [];
    global._capturedMtgsigs = [];

    const xhr = new FakeXHR();
    xhr.open(METHOD.toUpperCase(), TARGET_URL);
    xhr.setRequestHeader('Content-Type', 'application/json;charset=UTF-8');
    xhr.onload = function() {};
    xhr.onerror = function() {};
    xhr.send(body);

    setTimeout(function() {
        const urls = global._capturedSignedUrls;
        const headerSigs = global._capturedMtgsigs;
        if (headerSigs.length === 0 && urls.length === 0) {
            reject(new Error('MTGSIG_NOT_CAPTURED'));
            return;
        }
        let mtgsigStr = headerSigs.length ? headerSigs[headerSigs.length - 1] : '';
        if (!mtgsigStr) {
            const signedUrl = urls[urls.length - 1];
            const idx = signedUrl.indexOf('mtgsig=');
            if (idx === -1) {
                reject(new Error('MTGSIG_URL_MISSING'));
                return;
            }
            const enc = signedUrl.slice(idx + 7).split('&')[0];
            mtgsigStr = decodeURIComponent(enc);
        }
        // a3/a5/a6/d1 由网页 H5Guard 共同生成，签名后不可改写。
        try {
            const obj = JSON.parse(mtgsigStr);
            if (obj && process.env.MT_H5_DEBUG === '1') {
                process.stderr.write('[web-h5sign] a9=' + String(obj.a9) + ' a3_prefix=' + String(obj.a3).slice(0, 24) + '\n');
            }
        } catch (e) {
            process.stderr.write('[h5sign] mtgsig JSON parse fail, raw output\n');
        }
        resolve(mtgsigStr);
    }, 1200);
    });
}

let signing = Promise.resolve();
setTimeout(function() {
    parentPort.postMessage({ type: 'ready' });
    parentPort.on('message', function(message) {
        const requestId = String(message && message.requestId || '');
        const body = String(message && message.body || '');
        signing = signing.then(async function() {
            if (!requestId || !body) throw new Error('INVALID_SIGN_REQUEST');
            const signature = await captureSignature(body);
            parentPort.postMessage({ type: 'result', requestId, signature });
        }).catch(function(error) {
            parentPort.postMessage({
                type: 'error',
                requestId,
                error: String(error && error.message || 'SIGN_FAILED').slice(0, 160),
            });
        });
    });
}, 600);
