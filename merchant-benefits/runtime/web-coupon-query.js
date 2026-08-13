/**
 * Sign and query the web merchant-coupon endpoint, then inspect infos[0] only.
 * Usage:
 *   node web-coupon-query.js <request-body.json|->
 *   node web-coupon-query.js <page-url> [lat lon]
 */
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');
const { randomUUID } = require('crypto');
const { spawnSync } = require('child_process');

const SIGN_URL = 'https://offsiteact.meituan.com/act/ge/queryPoiByRecallBiz';
const REQUEST_URL = SIGN_URL + '?yodaReady=h5&csecplatform=4&csecversion=4.3.0';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0';
const REQUIRED_SIG_FIELDS = ['a1', 'a2', 'a3', 'a5', 'a6', 'a8', 'a9', 'a10', 'x0', 'd1'];
const PAGE_PARAM_EXCLUSIONS = new Set(['rootPvId', 'front_end_params', 'front_card_params', 's', 'um']);
const DEFAULT_LAT = 29.688253;
const DEFAULT_LON = 106.600316;

function boundedTimeout() {
    const parsed = Number.parseInt(String(process.env.MERCHANT_BENEFITS_UPSTREAM_TIMEOUT_MS || ''), 10);
    return Number.isFinite(parsed) ? Math.min(12000, Math.max(2000, parsed)) : 6000;
}

function fail(message, code = 1) {
    if (require.main !== module) {
        const error = new Error(message);
        error.code = code;
        throw error;
    }
    process.stderr.write('[web-coupon-query] ' + message + '\n');
    process.exit(code);
}

function normalizeBody(input) {
    const body = input && input.body && typeof input.body === 'object' ? input.body : input;
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
        fail('request body must be a JSON object');
    }
    const serialized = JSON.stringify(body);
    if (/<[A-Z0-9_:-]+>/i.test(serialized)) {
        fail('replace all <PLACEHOLDER> values before running');
    }
    if (body.recallBizId !== 'cpsSelfCouponAll' || body.pageNo !== 1) {
        fail('request must use recallBizId=cpsSelfCouponAll and pageNo=1');
    }
    if (!body.mediumParams || !body.mediumParams.poi_id_str) {
        fail('mediumParams.poi_id_str is required');
    }
    if (!Number.isFinite(Number(body.lat)) || !Number.isFinite(Number(body.lon))) {
        fail('numeric lat and lon are required');
    }
    if (Number(body.lat) === 0 && Number(body.lon) === 0) {
        fail('lat and lon cannot both be zero for a real merchant query');
    }
    return { object: body, serialized };
}

function readBody(file) {
    let input;
    try {
        input = JSON.parse(fs.readFileSync(file === '-' ? 0 : file, 'utf8'));
    } catch (error) {
        fail('request body read failed: ' + error.message);
    }
    return normalizeBody(input);
}

function buildBodyFromPageUrl(rawUrl, rawLat, rawLon) {
    let pageUrl;
    try {
        pageUrl = new URL(rawUrl);
    } catch (error) {
        fail('invalid page URL');
    }
    if (!/(^|\.)offsiteact\.meituan\.com$/i.test(pageUrl.hostname)) {
        fail('page URL host is not supported');
    }
    if (!pageUrl.pathname.includes('/web/hoae/collection_waimai_v8/')) {
        fail('page URL path is not collection_waimai_v8');
    }

    const hasLat = rawLat !== undefined && rawLat !== '';
    const hasLon = rawLon !== undefined && rawLon !== '';
    if (hasLat !== hasLon) fail('lat and lon must be provided together');
    const lat = hasLat ? Number(rawLat) : DEFAULT_LAT;
    const lon = hasLon ? Number(rawLon) : DEFAULT_LON;
    if (!Number.isFinite(lat) || lat < -90 || lat > 90) fail('lat must be between -90 and 90');
    if (!Number.isFinite(lon) || lon < -180 || lon > 180) fail('lon must be between -180 and 180');

    const mediumParams = {};
    for (const [key, value] of pageUrl.searchParams) {
        if (!PAGE_PARAM_EXCLUSIONS.has(key)) mediumParams[key] = value;
    }
    for (const field of ['recallBizId', 'bizId', 'scene', 'activityId', 'poi_id_str']) {
        if (!mediumParams[field]) fail('page URL missing query parameter: ' + field);
    }

    return normalizeBody({
        lat,
        lon,
        geoType: 'GCJ02',
        geoSource: 'network',
        geoAccuracy: 500,
        mediumParams,
        appContainer: 'UNKNOW',
        rootPvId: randomUUID(),
        pagePvId: randomUUID(),
        pageSessionId: randomUUID(),
        outerPvId: '',
        contentPvId: '',
        recallBizId: 'cpsSelfCouponAll',
        pageNo: 1,
        hasMore: true,
        phone: '',
        channelType: 'SELF',
        categoryTypeList: ['0'],
        riskParams: {},
    });
}

function sign(serialized) {
    const signer = path.join(__dirname, 'web-h5sign.js');
    const child = spawnSync(process.execPath, [signer, SIGN_URL, 'POST', serialized, UA], {
        cwd: __dirname,
        encoding: 'utf8',
        maxBuffer: 4 * 1024 * 1024,
        timeout: 15000,
    });
    if (child.error) fail('signer failed: ' + child.error.message);
    if (child.status !== 0) fail('signer exit=' + child.status + ': ' + child.stderr.trim());

    const raw = child.stdout.trim();
    let parsed;
    try {
        parsed = JSON.parse(raw);
    } catch (error) {
        fail('signer returned invalid JSON');
    }
    const missing = REQUIRED_SIG_FIELDS.filter((field) => !(field in parsed));
    if (missing.length) fail('mtgsig missing fields: ' + missing.join(','));
    if (!String(parsed.a9).startsWith('4.3.0,9,')) {
        fail('unexpected web H5Guard version in a9');
    }
    return raw;
}

function post(serialized, mtgsig) {
    return new Promise((resolve, reject) => {
        const request = https.request(REQUEST_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json;charset=UTF-8',
                'Content-Length': Buffer.byteLength(serialized),
                'User-Agent': UA,
                'content-encoding': '',
                mtgsig,
            },
            timeout: boundedTimeout(),
        }, (response) => {
            const chunks = [];
            response.on('data', (chunk) => chunks.push(chunk));
            response.on('end', () => resolve({
                status: response.statusCode,
                headers: response.headers,
                text: Buffer.concat(chunks).toString('utf8'),
            }));
        });
        request.on('timeout', () => request.destroy(new Error('request timeout')));
        request.on('error', reject);
        request.end(serialized);
    });
}

function classify(httpResult, body, requestedPoiId) {
    if (httpResult.status !== 200) {
        return { decision: 'UNKNOWN', reason: 'HTTP_' + httpResult.status, httpStatus: httpResult.status };
    }
    let payload;
    try {
        payload = JSON.parse(httpResult.text);
    } catch (error) {
        return { decision: 'UNKNOWN', reason: 'INVALID_JSON', httpStatus: httpResult.status };
    }
    if (payload.ret !== 0 || !Array.isArray(payload.infos) || payload.infos.length === 0) {
        return {
            decision: 'UNKNOWN',
            reason: 'BUSINESS_RESPONSE_INVALID',
            httpStatus: httpResult.status,
            ret: payload.ret,
        };
    }

    const first = payload.infos[0];
    const firstPoiId = String(first.poi_id_str || '');
    if (!firstPoiId || firstPoiId !== String(requestedPoiId)) {
        return {
            decision: 'UNKNOWN',
            reason: 'FIRST_POI_MISMATCH',
            httpStatus: httpResult.status,
            ret: payload.ret,
            firstMerchant: { poiIdStr: firstPoiId, name: first.poiBaseInfo && first.poiBaseInfo.name },
        };
    }

    const gift = first.giftInfo;
    if (!gift || Number(gift.type) !== 1) {
        return {
            decision: 'NO_COUPON',
            httpStatus: httpResult.status,
            ret: payload.ret,
            firstMerchant: { poiIdStr: firstPoiId, name: first.poiBaseInfo && first.poiBaseInfo.name, coupon: null },
        };
    }
    return {
        decision: 'HAS_COUPON',
        httpStatus: httpResult.status,
        ret: payload.ret,
        firstMerchant: {
            poiIdStr: firstPoiId,
            name: first.poiBaseInfo && first.poiBaseInfo.name,
            coupon: {
                giftId: gift.gift_id,
                amountYuan: Number(gift.coupon_amount) / 100,
                thresholdYuan: Number(gift.order_amount_limit) / 100,
                status: gift.status,
            },
        },
    };
}

async function main() {
    const input = process.argv[2];
    if (!input) {
        fail('usage: node web-coupon-query.js <request-body.json|-> OR <page-url> [lat lon]');
    }
    const body = /^https?:\/\//i.test(input)
        ? buildBodyFromPageUrl(input, process.argv[3], process.argv[4])
        : readBody(input === '-' ? '-' : path.resolve(input));
    const mtgsig = sign(body.serialized);
    let response;
    try {
        response = await post(body.serialized, mtgsig);
    } catch (error) {
        fail('request failed: ' + error.message, 2);
    }
    const result = classify(response, body.object, body.object.mediumParams.poi_id_str);
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    if (result.decision === 'UNKNOWN') process.exitCode = 2;
}

if (require.main === module) main();

module.exports = {
    buildBodyFromPageUrl,
    classify,
    post,
    sign,
};
