'use strict';

const http = require('http');
const path = require('path');
const { randomUUID } = require('crypto');
const { Worker } = require('worker_threads');
const couponQuery = require('./runtime/web-coupon-query');
const cashbackQuery = require('./runtime/web-cashback-query');
const { mapCashbackResult, normalizeName } = require('./result-mapper');

const PORT = boundedInt(process.env.PORT, 18180, 1, 65535);
const WORKER_COUNT = boundedInt(process.env.MERCHANT_BENEFITS_WORKERS, 4, 1, 16);
const QUEUE_LIMIT = boundedInt(process.env.MERCHANT_BENEFITS_QUEUE_LIMIT, 50, 1, 500);
const QUERY_TIMEOUT_MS = boundedInt(process.env.MERCHANT_BENEFITS_TIMEOUT_MS, 15000, 5000, 30000);
const BODY_LIMIT = 64 * 1024;
const WORKER_PATH = path.join(__dirname, 'runtime', 'signer-worker.js');

function boundedInt(raw, fallback, min, max) {
    const parsed = Number.parseInt(String(raw || ''), 10);
    return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
}

function safeError(error) {
    return String(error && error.message || error || 'UNKNOWN').slice(0, 160);
}

function parseUpstream(response, source) {
    if (!response || response.status !== 200) throw new Error(source.toUpperCase() + '_HTTP_' + String(response && response.status || 0));
    let payload;
    try {
        payload = JSON.parse(response.text);
    } catch (error) {
        throw new Error(source.toUpperCase() + '_INVALID_JSON');
    }
    if (payload.ret !== 0 || !Array.isArray(payload.infos)) throw new Error(source.toUpperCase() + '_STRUCTURE_INVALID');
    return payload;
}

function replacePoi(rawUrl, poiId) {
    const parsed = new URL(rawUrl);
    parsed.searchParams.set('poi_id_str', poiId);
    return parsed.toString();
}

class SignerPool {
    constructor(size) {
        this.workers = [];
        this.waiters = [];
        this.pending = new Map();
        this.readyCount = 0;
        for (let index = 0; index < size; index += 1) this.spawn(index);
    }

    spawn(index) {
        const worker = new Worker(WORKER_PATH);
        const slot = { index, worker, ready: false, busy: false };
        this.workers[index] = slot;
        worker.on('message', (message) => {
            if (message && message.type === 'ready') {
                if (!slot.ready) this.readyCount += 1;
                slot.ready = true;
                this.dispatch();
                return;
            }
            const requestId = String(message && message.requestId || '');
            const pending = this.pending.get(requestId);
            if (!pending) return;
            this.pending.delete(requestId);
            slot.busy = false;
            if (message.type === 'result') pending.resolve(String(message.signature || ''));
            else pending.reject(new Error(String(message.error || 'SIGN_FAILED')));
            this.dispatch();
        });
        worker.on('error', (error) => this.replace(slot, error));
        worker.on('exit', (code) => {
            if (code !== 0 && this.workers[index] === slot) this.replace(slot, new Error('SIGNER_EXIT_' + code));
        });
    }

    replace(slot, error) {
        if (this.workers[slot.index] !== slot) return;
        if (slot.ready) this.readyCount = Math.max(0, this.readyCount - 1);
        for (const [requestId, pending] of this.pending.entries()) {
            if (pending.slot !== slot) continue;
            this.pending.delete(requestId);
            pending.reject(error);
        }
        this.spawn(slot.index);
    }

    sign(body) {
        return new Promise((resolve, reject) => {
            this.waiters.push({ body, resolve, reject });
            this.dispatch();
        });
    }

    dispatch() {
        while (this.waiters.length) {
            const slot = this.workers.find((item) => item && item.ready && !item.busy);
            if (!slot) return;
            const job = this.waiters.shift();
            const requestId = randomUUID();
            slot.busy = true;
            this.pending.set(requestId, { ...job, slot });
            slot.worker.postMessage({ requestId, body: job.body });
        }
    }

    stats() {
        return {
            configured: this.workers.length,
            ready: this.readyCount,
            busy: this.workers.filter((slot) => slot && slot.busy).length,
            signing_queue: this.waiters.length,
        };
    }
}

const signerPool = new SignerPool(WORKER_COUNT);
const requestQueue = [];
const inflight = new Map();
let activeQueries = 0;
const stats = { started_at: Math.floor(Date.now() / 1000), total: 0, succeeded: 0, unknown: 0, rejected: 0, last_error: '', last_error_at: 0 };

function mapCoupon(first) {
    const gift = first && first.giftInfo;
    if (!gift || Number(gift.type) !== 1) return { status: 'no_coupon' };
    return {
        status: 'has_coupon',
        amount_yuan: Number(gift.coupon_amount) / 100,
        threshold_yuan: Number(gift.order_amount_limit) / 100,
        gift_status: gift.status,
    };
}

async function performQuery(input) {
    const requestedPoi = String(input.requested_poi_id_str || '').trim();
    const expectedName = String(input.merchant_name || '').trim();
    const couponUrl = String(input.coupon_page_url || '').trim();
    const cashbackBaseUrl = String(input.cashback_base_url || '').trim();
    const latitude = Number(input.latitude);
    const longitude = Number(input.longitude);
    if (!requestedPoi || !couponUrl) throw new Error('INVALID_REQUEST');
    if (!Number.isFinite(latitude) || latitude < -90 || latitude > 90 || !Number.isFinite(longitude) || longitude < -180 || longitude > 180) throw new Error('INVALID_COORDINATES');

    const couponBody = couponQuery.buildBodyFromPageUrl(couponUrl, latitude, longitude);
    const couponSignature = await signerPool.sign(couponBody.serialized);
    const couponResponse = await couponQuery.post(couponBody.serialized, couponSignature);
    const couponPayload = parseUpstream(couponResponse, 'coupon');
    if (!couponPayload.infos.length) throw new Error('COUPON_EMPTY_INFOS');
    const couponFirst = couponPayload.infos[0] || {};
    const canonicalPoi = String(couponFirst.poi_id_str || '').trim();
    const canonicalName = String(couponFirst.poiBaseInfo && couponFirst.poiBaseInfo.name || '').trim();
    if (!canonicalPoi || !canonicalName) throw new Error('COUPON_IDENTITY_INCOMPLETE');
    if (expectedName && normalizeName(expectedName) !== normalizeName(canonicalName)) throw new Error('EXPECTED_NAME_MISMATCH');

    const result = {
        status: 'ok',
        original_poi_id_str: requestedPoi,
        canonical_poi_id_str: canonicalPoi,
        merchant_name: canonicalName,
        coupon: mapCoupon(couponFirst),
        cashback: cashbackBaseUrl ? { status: 'unknown' } : { status: 'not_configured' },
        cashback_url: '',
        queried_at: Math.floor(Date.now() / 1000),
    };
    if (!cashbackBaseUrl) return result;

    const cashbackUrl = replacePoi(cashbackBaseUrl, canonicalPoi);
    try {
        const cashbackBody = cashbackQuery.buildBodyFromPageUrl(cashbackUrl, latitude, longitude);
        const cashbackSignature = await signerPool.sign(cashbackBody.serialized);
        const cashbackResponse = await cashbackQuery.post(cashbackBody.serialized, cashbackSignature);
        const classified = cashbackQuery.classify(cashbackResponse, canonicalPoi);
        const mapped = mapCashbackResult(classified, canonicalPoi, canonicalName);
        if (mapped.name_mismatch) {
            result.cashback = { status: 'unknown', reason: 'COUPON_CASHBACK_NAME_MISMATCH' };
            return result;
        }
        result.cashback = mapped.cashback;
        if (result.cashback.status !== 'unknown') result.cashback_url = cashbackUrl;
    } catch (error) {
        result.cashback = { status: 'unknown', reason: safeError(error) };
    }
    return result;
}

function scheduleQuery(key, input) {
    if (inflight.has(key)) return inflight.get(key);
    if (activeQueries >= WORKER_COUNT && requestQueue.length >= QUEUE_LIMIT) {
        stats.rejected += 1;
        return Promise.reject(new Error('QUEUE_FULL'));
    }
    const promise = new Promise((resolve, reject) => requestQueue.push({ key, input, resolve, reject }));
    inflight.set(key, promise);
    drainQueue();
    return promise;
}

function drainQueue() {
    while (activeQueries < WORKER_COUNT && requestQueue.length) {
        const job = requestQueue.shift();
        activeQueries += 1;
        stats.total += 1;
        let timeoutId;
        let settledForCaller = false;
        const operation = performQuery(job.input);
        const timeout = new Promise((resolve, reject) => {
            timeoutId = setTimeout(() => reject(new Error('QUERY_TIMEOUT')), QUERY_TIMEOUT_MS);
        });
        Promise.race([operation, timeout]).then((result) => {
            settledForCaller = true;
            if (result.status === 'ok') stats.succeeded += 1;
            else stats.unknown += 1;
            job.resolve(result);
        }).catch((error) => {
            settledForCaller = true;
            stats.unknown += 1;
            stats.last_error = safeError(error);
            stats.last_error_at = Math.floor(Date.now() / 1000);
            job.reject(error);
        });
        operation.catch((error) => {
            if (!settledForCaller) return;
            if (safeError(error) === stats.last_error) return;
            stats.last_error = safeError(error);
            stats.last_error_at = Math.floor(Date.now() / 1000);
        }).finally(() => {
            clearTimeout(timeoutId);
            activeQueries -= 1;
            inflight.delete(job.key);
            drainQueue();
        });
    }
}

function sendJson(response, statusCode, payload) {
    const body = JSON.stringify(payload);
    response.writeHead(statusCode, { 'Content-Type': 'application/json; charset=utf-8', 'Content-Length': Buffer.byteLength(body) });
    response.end(body);
}

function readJson(request) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        let size = 0;
        request.on('data', (chunk) => {
            size += chunk.length;
            if (size > BODY_LIMIT) {
                reject(new Error('BODY_TOO_LARGE'));
                request.destroy();
                return;
            }
            chunks.push(chunk);
        });
        request.on('end', () => {
            try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); }
            catch (error) { reject(new Error('INVALID_JSON')); }
        });
        request.on('error', reject);
    });
}

const server = http.createServer(async (request, response) => {
    if (request.method === 'GET' && request.url === '/healthz') {
        const workers = signerPool.stats();
        return sendJson(response, workers.ready === WORKER_COUNT ? 200 : 503, {
            ok: workers.ready === WORKER_COUNT,
            service: 'merchant-benefits',
            workers,
            active_queries: activeQueries,
            queue_length: requestQueue.length,
            inflight_keys: inflight.size,
            query_timeout_ms: QUERY_TIMEOUT_MS,
            stats,
        });
    }
    if (request.method !== 'POST' || request.url !== '/v1/merchant-benefits/query') return sendJson(response, 404, { success: false, error: 'NOT_FOUND' });
    try {
        const input = await readJson(request);
        const key = [input.requested_poi_id_str, input.merchant_name, input.coupon_page_url, input.cashback_base_url, input.latitude, input.longitude].map((value) => String(value || '')).join('|');
        const result = await scheduleQuery(key, input);
        return sendJson(response, 200, { success: true, ...result });
    } catch (error) {
        const code = safeError(error);
        const statusCode = code === 'QUEUE_FULL' ? 429 : (code.startsWith('INVALID_') || code === 'BODY_TOO_LARGE' ? 400 : 502);
        return sendJson(response, statusCode, { success: false, status: 'unknown', error: code });
    }
});

server.listen(PORT, '0.0.0.0', () => process.stdout.write('[merchant-benefits] listening on ' + PORT + '\n'));
