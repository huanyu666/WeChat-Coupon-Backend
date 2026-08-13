/**
 * Resolve the current merchant POI through the coupon endpoint, then query cashback.
 * Usage:
 *   node web-benefits-query.js <coupon-page-url> <cashback-page-url> [lat lon] [--expected-name <NAME>]
 */
'use strict';

const couponQuery = require('./web-coupon-query');
const cashbackQuery = require('./web-cashback-query');

function fail(message, code = 1) {
    process.stderr.write('[web-benefits-query] ' + message + '\n');
    process.exit(code);
}

function queryFailure(source, reason, httpStatus) {
    const error = new Error(source + ' ' + reason);
    error.isQueryFailure = true;
    error.source = source;
    error.reason = reason;
    error.httpStatus = httpStatus;
    return error;
}

function parsePayload(httpResult, source) {
    if (httpResult.status !== 200) {
        throw queryFailure(source, 'HTTP_' + httpResult.status, httpResult.status);
    }
    let payload;
    try {
        payload = JSON.parse(httpResult.text);
    } catch (error) {
        throw queryFailure(source, 'INVALID_JSON');
    }
    if (payload.ret !== 0 || !Array.isArray(payload.infos)) {
        throw queryFailure(source, 'BUSINESS_RESPONSE_INVALID');
    }
    return payload;
}

function normalizeName(value) {
    return String(value || '').normalize('NFKC').replace(/\s+/g, '').toLowerCase();
}

function extractCoupon(first) {
    const gift = first && first.giftInfo;
    if (!gift || Number(gift.type) !== 1) {
        return { decision: 'NO_COUPON', coupon: null };
    }
    return {
        decision: 'HAS_COUPON',
        coupon: {
            giftId: gift.gift_id,
            amountYuan: Number(gift.coupon_amount) / 100,
            thresholdYuan: Number(gift.order_amount_limit) / 100,
            status: gift.status,
        },
    };
}

function replacePoiId(rawUrl, poiId) {
    let pageUrl;
    try {
        pageUrl = new URL(rawUrl);
    } catch (error) {
        fail('invalid cashback page URL');
    }
    pageUrl.searchParams.set('poi_id_str', poiId);
    return pageUrl.toString();
}

async function requestCoupon(pageUrl, lat, lon) {
    const body = couponQuery.buildBodyFromPageUrl(pageUrl, lat, lon);
    const mtgsig = couponQuery.sign(body.serialized);
    let response;
    try {
        response = await couponQuery.post(body.serialized, mtgsig);
    } catch (error) {
        throw queryFailure('coupon', 'REQUEST_FAILED');
    }
    return { body, response, payload: parsePayload(response, 'coupon') };
}

async function requestCashback(pageUrl, lat, lon) {
    const body = cashbackQuery.buildBodyFromPageUrl(pageUrl, lat, lon);
    const mtgsig = cashbackQuery.sign(body.serialized);
    let response;
    try {
        response = await cashbackQuery.post(body.serialized, mtgsig);
    } catch (error) {
        throw queryFailure('cashback', 'REQUEST_FAILED');
    }
    return { body, response };
}

async function main() {
    const args = process.argv.slice(2);
    const expectedNameIndex = args.indexOf('--expected-name');
    let expectedName;
    if (expectedNameIndex !== -1) {
        expectedName = args[expectedNameIndex + 1];
        if (!expectedName) fail('--expected-name requires a merchant name');
        args.splice(expectedNameIndex, 2);
    }
    const couponPageUrl = args[0];
    const cashbackPageUrl = args[1];
    const lat = args[2];
    const lon = args[3];
    if (!couponPageUrl || !cashbackPageUrl) {
        fail('usage: node web-benefits-query.js <coupon-page-url> <cashback-page-url> [lat lon] [--expected-name <NAME>]');
    }
    if ((lat === undefined) !== (lon === undefined)) {
        fail('lat and lon must be provided together');
    }

    let couponResult;
    try {
        couponResult = await requestCoupon(couponPageUrl, lat, lon);
    } catch (error) {
        if (!error.isQueryFailure) throw error;
        process.stdout.write(JSON.stringify({
            decision: 'UNKNOWN',
            reason: error.reason,
            source: error.source,
            ...(error.httpStatus ? { httpStatus: error.httpStatus } : {}),
        }, null, 2) + '\n');
        process.exitCode = 2;
        return;
    }
    if (couponResult.payload.infos.length === 0) {
        process.stdout.write(JSON.stringify({
            decision: 'UNKNOWN',
            reason: 'COUPON_EMPTY_INFOS',
            requestedPoiId: couponResult.body.object.mediumParams.poi_id_str,
        }, null, 2) + '\n');
        process.exitCode = 2;
        return;
    }

    const couponFirst = couponResult.payload.infos[0];
    const requestedPoiId = String(couponResult.body.object.mediumParams.poi_id_str || '');
    const canonicalPoiId = String(couponFirst.poi_id_str || '');
    const couponMerchantName = couponFirst.poiBaseInfo && couponFirst.poiBaseInfo.name;
    if (!canonicalPoiId || !couponMerchantName) {
        fail('coupon first merchant identity is incomplete', 2);
    }
    const coupon = extractCoupon(couponFirst);

    const canonicalCashbackUrl = replacePoiId(cashbackPageUrl, canonicalPoiId);
    let cashbackResult;
    try {
        cashbackResult = await requestCashback(canonicalCashbackUrl, lat, lon);
    } catch (error) {
        if (!error.isQueryFailure) throw error;
        process.stdout.write(JSON.stringify({
            decision: 'UNKNOWN',
            reason: error.reason,
            source: error.source,
            ...(error.httpStatus ? { httpStatus: error.httpStatus } : {}),
            identity: { requestedPoiId, canonicalPoiId },
        }, null, 2) + '\n');
        process.exitCode = 2;
        return;
    }
    const cashback = cashbackQuery.classify(cashbackResult.response, canonicalPoiId);
    const cashbackMerchant = cashback.firstMerchant || null;
    const poiMatch = Boolean(cashbackMerchant && cashbackMerchant.poiIdStr === canonicalPoiId);
    const nameMatch = Boolean(
        cashbackMerchant &&
        normalizeName(cashbackMerchant.name) === normalizeName(couponMerchantName)
    );
    const expectedNameMatch = expectedName === undefined
        ? null
        : normalizeName(expectedName) === normalizeName(couponMerchantName);

    let decision = 'OK';
    let reason;
    if (expectedNameMatch === false) {
        decision = 'UNKNOWN';
        reason = 'EXPECTED_NAME_MISMATCH';
    } else if (!poiMatch) {
        decision = 'UNKNOWN';
        reason = 'CASHBACK_CANONICAL_POI_MISMATCH';
    } else if (!nameMatch) {
        decision = 'UNKNOWN';
        reason = 'COUPON_CASHBACK_NAME_MISMATCH';
    } else if (cashback.decision === 'UNKNOWN') {
        decision = 'UNKNOWN';
        reason = cashback.reason || 'CASHBACK_UNKNOWN';
    }

    const output = {
        decision,
        ...(reason ? { reason } : {}),
        identity: {
            requestedPoiId,
            canonicalPoiId,
            canonicalized: requestedPoiId !== canonicalPoiId,
            method: requestedPoiId === canonicalPoiId ? 'EXACT_POI' : 'COUPON_FIRST_POI',
            couponCashbackPoiMatch: poiMatch,
            couponCashbackNameMatch: nameMatch,
            ...(expectedName === undefined ? {} : { expectedName, expectedNameMatch }),
            merchantName: couponMerchantName,
        },
        coupon: {
            decision: coupon.decision,
            httpStatus: couponResult.response.status,
            ret: couponResult.payload.ret,
            details: coupon.coupon,
        },
        cashback,
    };
    process.stdout.write(JSON.stringify(output, null, 2) + '\n');
    if (decision === 'UNKNOWN') process.exitCode = 2;
}

main();
