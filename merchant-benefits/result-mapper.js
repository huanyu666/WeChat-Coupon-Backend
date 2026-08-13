'use strict';

function normalizeName(value) {
    return String(value || '')
        .normalize('NFKC')
        .toLowerCase()
        .replace(/[\s\p{P}\p{S}]+/gu, '');
}

function cashbackIdentity(classified, canonicalPoi, canonicalName) {
    const merchant = classified && classified.firstMerchant;
    if (!merchant || String(merchant.poiIdStr || '') !== String(canonicalPoi || '')) {
        return { poi_matches: false, name_matches: false };
    }
    return {
        poi_matches: true,
        name_matches: normalizeName(merchant.name) === normalizeName(canonicalName),
    };
}

function mapCashback(classified) {
    if (!classified || classified.decision === 'UNKNOWN') return { status: 'unknown', reason: String(classified && classified.reason || 'CASHBACK_UNKNOWN') };
    if (classified.decision === 'NO_CASHBACK') {
        if (classified.reason === 'FIRST_POI_MISMATCH') return { status: 'unknown', reason: 'CASHBACK_CANONICAL_POI_MISMATCH' };
        return { status: 'no_cashback', reason: String(classified.reason || '') };
    }
    const value = classified.firstMerchant && classified.firstMerchant.cashback || {};
    return {
        status: 'has_cashback',
        order_ratio: value.orderRatePercent,
        order_max_yuan: value.orderMaxYuan,
        review_ratio: value.reviewRatePercent,
        review_max_yuan: value.reviewMaxYuan,
        total_max_yuan: value.totalMaxYuan,
        sign_status: value.userSignStatus,
        valid_inventory: value.validInventory,
        total_inventory: value.totalInventory,
    };
}

function mapCashbackResult(classified, canonicalPoi, canonicalName) {
    const identity = cashbackIdentity(classified, canonicalPoi, canonicalName);
    return {
        cashback: mapCashback(classified),
        name_mismatch: identity.poi_matches && !identity.name_matches,
    };
}

module.exports = {
    cashbackIdentity,
    mapCashback,
    mapCashbackResult,
    normalizeName,
};
