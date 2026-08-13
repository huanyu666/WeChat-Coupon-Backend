/**
 * Query the web cashback activity using the page's default smart-order list
 * and classify infos[0] only.
 * Usage:
 *   node web-cashback-query.js <cashback-page-url> [lat lon]
 *   node web-cashback-query.js <request-body.json|->
 */
'use strict';

const fs = require('fs');
const path = require('path');
const https = require('https');
const { randomUUID } = require('crypto');
const { spawnSync } = require('child_process');
const upstreamAgent = require('./http-agent');

const SIGN_URL = 'https://offsiteact.meituan.com/act/ge/queryPoiByRecallBiz';
const REQUEST_URL = SIGN_URL + '?yodaReady=h5&csecplatform=4&csecversion=4.3.0';
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0';
const DEFAULT_RECALL_BIZ_ID = 'cpsPromotionOrderMulti';
const DEFAULT_LAT = 29.688253;
const DEFAULT_LON = 106.600316;

function boundedTimeout() {
    const parsed = Number.parseInt(String(process.env.MERCHANT_BENEFITS_UPSTREAM_TIMEOUT_MS || ''), 10);
    return Number.isFinite(parsed) ? Math.min(12000, Math.max(2000, parsed)) : 6000;
}
const REQUIRED_SIG_FIELDS = ['a1', 'a2', 'a3', 'a5', 'a6', 'a8', 'a9', 'a10', 'x0', 'd1'];
const PAGE_PARAM_EXCLUSIONS = new Set(['rootPvId', 'front_end_params', 'front_card_params', 's', 'um']);

function fail(message, code = 1) {
    if (require.main !== module) {
        const error = new Error(message);
        error.code = code;
        throw error;
    }
    process.stderr.write('[web-cashback-query] ' + message + '\n');
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
    if (body.recallBizId !== DEFAULT_RECALL_BIZ_ID || body.pageNo !== 1) {
        fail('request must use recallBizId=cpsPromotionOrderMulti and pageNo=1');
    }
    if (!body.mediumParams || !body.mediumParams.poi_id_str) {
        fail('mediumParams.poi_id_str is required');
    }
    if (!Number.isFinite(Number(body.lat)) || !Number.isFinite(Number(body.lon))) {
        fail('numeric lat and lon are required');
    }
    if (Number(body.lat) === 0 && Number(body.lon) === 0) {
        fail('lat and lon cannot both be zero');
    }
    return { object: body, serialized };
}

function readBody(file) {
    try {
        const input = JSON.parse(fs.readFileSync(file === '-' ? 0 : file, 'utf8'));
        return normalizeBody(input);
    } catch (error) {
        fail('request body read failed: ' + error.message);
    }
}

function buildBodyFromPageUrl(rawUrl, rawLat, rawLon) {
    let pageUrl;
    try {
        pageUrl = new URL(rawUrl);
    } catch (error) {
        fail('invalid cashback page URL');
    }
    if (!/(^|\.)offsiteact\.meituan\.com$/i.test(pageUrl.hostname)) {
        fail('cashback page URL host is not supported');
    }
    if (!pageUrl.pathname.includes('/web/hoae/order_cashback_activity/')) {
        fail('page URL path is not order_cashback_activity');
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
    for (const field of ['bizId', 'scene', 'activityId', 'poi_id_str']) {
        if (!mediumParams[field]) fail('cashback page URL missing query parameter: ' + field);
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
        recallBizId: DEFAULT_RECALL_BIZ_ID,
        pageNo: 1,
        hasMore: true,
        phone: '',
        channelType: 'SELF',
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
            agent: upstreamAgent,
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
                text: Buffer.concat(chunks).toString('utf8'),
            }));
        });
        request.on('timeout', () => request.destroy(new Error('request timeout')));
        request.on('error', reject);
        request.end(serialized);
    });
}

function divide(value, divisor) {
    const number = Number(value);
    return Number.isFinite(number) ? number / divisor : null;
}

function classify(httpResult, requestedPoiId) {
    if (httpResult.status !== 200) {
        return { decision: 'UNKNOWN', reason: 'HTTP_' + httpResult.status, httpStatus: httpResult.status };
    }
    let payload;
    try {
        payload = JSON.parse(httpResult.text);
    } catch (error) {
        return { decision: 'UNKNOWN', reason: 'INVALID_JSON', httpStatus: httpResult.status };
    }
    if (payload.ret !== 0 || !Array.isArray(payload.infos)) {
        return {
            decision: 'UNKNOWN',
            reason: 'BUSINESS_RESPONSE_INVALID',
            httpStatus: httpResult.status,
            ret: payload.ret,
        };
    }
    if (payload.infos.length === 0) {
        return {
            decision: 'NO_CASHBACK',
            reason: 'EMPTY_INFOS',
            httpStatus: httpResult.status,
            ret: payload.ret,
        };
    }

    const first = payload.infos[0] || {};
    const firstPoiId = String(first.poi_id_str || '');
    const firstMerchant = {
        poiIdStr: firstPoiId,
        name: first.poiBaseInfo && first.poiBaseInfo.name,
    };
    if (!firstPoiId || firstPoiId !== String(requestedPoiId)) {
        return {
            decision: 'UNKNOWN',
            reason: 'FIRST_POI_MISMATCH',
            httpStatus: httpResult.status,
            ret: payload.ret,
            firstMerchant,
        };
    }

    const plan = Array.isArray(first.planActivityInfoList) ? first.planActivityInfoList[0] : null;
    if (!plan) {
        return {
            decision: 'UNKNOWN',
            reason: 'CASHBACK_PLAN_MISSING',
            httpStatus: httpResult.status,
            ret: payload.ret,
            firstMerchant,
        };
    }

    const orderMax = divide(plan.userMaxCommission, 100);
    const reviewMax = divide(plan.unifyToUserCommentCommission, 100);
    return {
        decision: 'HAS_CASHBACK',
        httpStatus: httpResult.status,
        ret: payload.ret,
        firstMerchant: {
            ...firstMerchant,
            cashback: {
                orderRatePercent: divide(plan.userMaxRatio, 100),
                orderMaxYuan: orderMax,
                reviewRatePercent: divide(plan.unifyToUserCommentRatio, 100),
                reviewMaxYuan: reviewMax,
                totalMaxYuan: orderMax === null || reviewMax === null ? null : orderMax + reviewMax,
                userSignStatus: plan.userSignStatus,
                validInventory: plan.validInventory,
                totalInventory: plan.totalInventory,
                orderLimitMinutes: plan.orderLimitTime ? Math.ceil(Number(plan.orderLimitTime) / 60) : null,
                planActivityType: plan.planActivityType,
            },
        },
    };
}

async function main() {
    const input = process.argv[2];
    if (!input) {
        fail('usage: node web-cashback-query.js <cashback-page-url> [lat lon] OR <request-body.json|->');
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
    const result = classify(response, body.object.mediumParams.poi_id_str);
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
