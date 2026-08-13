'use strict';

const assert = require('assert');
const { cashbackIdentity, mapCashbackResult } = require('./result-mapper');
const cashbackQuery = require('./runtime/web-cashback-query');

const classified = {
    decision: 'HAS_CASHBACK',
    firstMerchant: {
        poiIdStr: 'canonical-poi',
        name: '袁记云饺（两江店）',
        cashback: {
            orderRatePercent: 6,
            orderMaxYuan: 10,
            reviewRatePercent: 3,
            reviewMaxYuan: 5,
            totalMaxYuan: 15,
        },
    },
};

const identity = cashbackIdentity(classified, 'canonical-poi', '袁记云饺(理工大学两江校区店)');
assert.strictEqual(identity.poi_matches, true);
assert.strictEqual(identity.name_matches, false);
const mapped = mapCashbackResult(classified, 'canonical-poi', '袁记云饺(理工大学两江校区店)');
assert.strictEqual(mapped.cashback.status, 'has_cashback');
assert.strictEqual(mapped.name_mismatch, true);

const mismatched = cashbackIdentity(classified, 'another-poi', '袁记云饺（两江店）');
assert.strictEqual(mismatched.poi_matches, false);

const upstream = {
    ret: 0,
    infos: [
        { poi_id_str: 'recommended-poi', poiBaseInfo: { name: '推荐店铺' }, planActivityInfoList: [{}] },
        {
            poi_id_str: 'canonical-poi',
            poiBaseInfo: { name: '袁记云饺(理工大学两江校区店)' },
            planActivityInfoList: [{
                userMaxRatio: 2000,
                userMaxCommission: 2250,
                unifyToUserCommentRatio: 500,
                unifyToUserCommentCommission: 150,
                userSignStatus: 'CAN_SIGN',
            }],
        },
    ],
};
const target = cashbackQuery.classify({ status: 200, text: JSON.stringify(upstream) }, 'canonical-poi');
assert.strictEqual(target.decision, 'UNKNOWN');
assert.strictEqual(target.reason, 'FIRST_POI_MISMATCH');
assert.strictEqual(target.firstMerchant.poiIdStr, 'recommended-poi');

const missing = cashbackQuery.classify({ status: 200, text: JSON.stringify(upstream) }, 'absent-poi');
assert.strictEqual(missing.decision, 'UNKNOWN');
assert.strictEqual(missing.reason, 'FIRST_POI_MISMATCH');

const smartBody = cashbackQuery.buildBodyFromPageUrl(
    'https://offsiteact.meituan.com/web/hoae/order_cashback_activity/index.html' +
    '?bizId=biz&scene=CPS_SELF_SRC&activityId=17&poi_id_str=canonical-poi',
    29.688253,
    106.600316,
);
assert.strictEqual(smartBody.object.recallBizId, 'cpsPromotionOrderMulti');
assert.strictEqual(smartBody.object.pageNo, 1);

const positiveFixture = {
    ret: 0,
    infos: [{
        poi_id_str: 'canonical-poi',
        poiBaseInfo: { name: '虾满客小龙虾美蛙干锅大闸蟹（空港店）' },
        planActivityInfoList: [{
            userMaxRatio: 2000,
            userMaxCommission: 2250,
            unifyToUserCommentRatio: 500,
            unifyToUserCommentCommission: 150,
            userSignStatus: 'CAN_SIGN',
            validInventory: 3,
            totalInventory: 5,
        }],
    }],
};
const positive = cashbackQuery.classify(
    { status: 200, text: JSON.stringify(positiveFixture) },
    'canonical-poi',
);
assert.strictEqual(positive.decision, 'HAS_CASHBACK');
assert.strictEqual(positive.firstMerchant.cashback.orderRatePercent, 20);
assert.strictEqual(positive.firstMerchant.cashback.orderMaxYuan, 22.5);
assert.strictEqual(positive.firstMerchant.cashback.reviewRatePercent, 5);
assert.strictEqual(positive.firstMerchant.cashback.reviewMaxYuan, 1.5);
assert.strictEqual(positive.firstMerchant.cashback.totalMaxYuan, 24);
assert.strictEqual(positive.firstMerchant.cashback.validInventory, 3);

process.stdout.write('merchant-benefits identity tests passed\n');
