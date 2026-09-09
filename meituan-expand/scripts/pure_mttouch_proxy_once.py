#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure mttouch expand via ydaili proxy split."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from curl_cffi import requests as cr

# Keep the runner portable: deployments can override MT_ROOT, while local runs
# resolve the repository from this script instead of a developer-machine path.
ROOT = Path(os.environ.get("MT_ROOT", str(Path(__file__).resolve().parents[1]))).expanduser().resolve()
sys.path.insert(0, str(ROOT / "scripts"))
from exchange_response_class import (  # noqa: E402
    classify_exchange_response,
    is_half_success_do_lock,
    should_stop_proxy_rotation,
)
from proxy_pool_ydaili import load_pool_from_config  # noqa: E402

OUT_DIR = ROOT / "exports"
HOST = "https://market.waimai.meituan.com"
UA_MT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.49 NetType/WIFI Language/zh_CN"
)
UA_WM = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.75(0x18004b46) NetType/WIFI Language/zh_CN "
    "miniProgram/wx2c348cf579062e56"
)


def parse_link(link: str) -> Tuple[str, str, str]:
    text = link.strip()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(text).query)
    token = (query.get("token") or [""])[0].strip()
    userid = (query.get("userId") or query.get("userid") or [""])[0].strip()
    if not token or not userid:
        raise SystemExit("link needs token/userId")
    return text, token, userid


def deg_to_wm(value: float) -> str:
    return str(int(round(float(value) * 1_000_000)))


def enc(body: Dict[str, Any]) -> str:
    return urllib.parse.urlencode(
        {
            key: (
                value
                if isinstance(value, str)
                else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            )
            for key, value in body.items()
        }
    )


def sj(response: Any) -> Dict[str, Any]:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"_raw": str(payload)[:200]}
    except Exception:
        return {"_raw": (response.text or "")[:220]}


def success_list(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data") or {}
    items = data.get("couponMultipleList") or data.get("inflateAndGiftAssetList") or []
    return [
        {
            "couponName": item.get("couponName"),
            "couponAmount": item.get("couponAmount"),
            "orderAmountLimit": item.get("orderAmountLimit"),
        }
        for item in items
    ]


def pick_coupon(asset: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for group in ((asset.get("data") or {}).get("userMagicalCouponGroups") or []):
        if group.get("assetType") != 3 or group.get("inflated"):
            continue
        infos = group.get("userMmcInfos") or []
        if not infos:
            continue
        info = infos[0]
        return {
            "coupon_view_id": info.get("couponViewId"),
            "coupon_config_id": str(info.get("couponConfigIdStr") or info.get("couponConfigId") or ""),
            "exchange_type": str(group.get("exchangeType") or "11"),
            "couponName": group.get("couponName"),
            "couponAmount": group.get("couponAmount"),
            "num": len(infos),
        }
    return None


def extract_targets(pre: Dict[str, Any]) -> Tuple[str, str, List[Dict[str, Any]]]:
    data = pre.get("data") or {}
    inflate = str(data.get("inflateToken") or "")
    biz = "101"
    targets: List[Dict[str, Any]] = []
    for group in data.get("targetCouponGroups") or []:
        biz = str(group.get("couponBizGroupId") or biz)
        for target in group.get("targetCoupons") or []:
            targets.append(
                {
                    "targetCouponConfigId": str(target.get("targetCouponConfigId")),
                    "targetCouponAmount": int(
                        target.get("targetCouponRealAmount") or target.get("targetCouponAmount") or 0
                    ),
                    "targetCouponAmountLimit": int(
                        target.get("targetCouponRealAmountLimit")
                        or target.get("targetCouponAmountLimit")
                        or 0
                    ),
                    "targetAssetType": int(target.get("assetType") or 1),
                }
            )
    return inflate, biz, targets


def run_once(
    proxy: Optional[str],
    account: str,
    token: str,
    userid: str,
    lat: float,
    lng: float,
    delay: float,
) -> Dict[str, Any]:
    wm_lat = deg_to_wm(lat)
    wm_lng = deg_to_wm(lng)
    session = cr.Session(impersonate="safari17_2_ios")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    cookie = (
        "token={0}; mt_c_token={0}; isid={0}; oops={0}; userId={1}; u={1}"
    ).format(token, userid)

    log: Dict[str, Any] = {
        "proxy": proxy.split("@")[-1] if proxy and "@" in proxy else proxy,
        "steps": [],
    }

    try:
        warm = session.get(
            account,
            headers={"User-Agent": UA_MT, "Cookie": cookie},
            timeout=15,
            allow_redirects=True,
        )
        for key, value in session.cookies.get_dict().items():
            if value and "{}=".format(key) not in cookie:
                cookie += "; {}={}".format(key, value)
        log["steps"].append({"name": "warm", "http": int(warm.status_code)})
    except Exception as error:
        return {
            "ok": False,
            "gateway": True,
            "stage": "warm",
            "error": "{}: {}".format(type(error).__name__, error),
            **log,
        }

    asset_body = {
        "el_biz": "waimai",
        "gundam_id": "3oMg3O",
        "tenant": "gundam",
        "gdEntry": "wxTab",
        "pageSource": "103",
        "ctype": "wm_wxapp",
        "wm_ctype": "wxapp",
        "isMini": "1",
        "webview_source": "native",
        "wm_latitude": wm_lat,
        "wm_longitude": wm_lng,
        "wm_actual_latitude": wm_lat,
        "wm_actual_longitude": wm_lng,
        "wm_appversion": "10.30.01",
        "app_id": "wx2c348cf579062e56",
        "userid": userid,
        "token": token,
        "wm_logintoken": token,
        "welfareCenterPageSource": "wm",
    }
    asset_query = {
        "gdBs": "0000",
        "pageVersion": "1783565608018",
        "__gd_activid": "552713",
        "__gd_pageid": "560699",
        "__gd_pagev": "1783565608018",
        "__gd_appv": "",
        "__gd_ctype": "wm_wxapp",
        "yodaReady": "h5",
        "csecplatform": "4",
        "csecversion": "4.2.4",
    }
    try:
        asset_resp = session.post(
            HOST + "/vp/magical/welfare/asset_module_v2?" + urllib.parse.urlencode(asset_query),
            data=enc(asset_body),
            headers={
                "User-Agent": UA_WM,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": HOST,
                "Referer": HOST + "/",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=25,
        )
        asset_json = sj(asset_resp)
        asset_http = int(asset_resp.status_code)
    except Exception as error:
        return {
            "ok": False,
            "gateway": True,
            "stage": "asset_connect",
            "error": "{}: {}".format(type(error).__name__, error),
            **log,
        }

    log["steps"].append(
        {
            "name": "asset",
            "http": asset_http,
            "code": asset_json.get("code"),
            "msg": asset_json.get("msg"),
        }
    )
    print("[asset]", log["proxy"], asset_http, asset_json.get("code"), asset_json.get("msg"))
    if asset_http == 403:
        return {"ok": False, "gateway": True, "stage": "asset", **log}
    if asset_http != 200 or asset_json.get("code") not in (0, "0"):
        return {
            "ok": False,
            "gateway": False,
            "stage": "asset_biz",
            "code": asset_json.get("code"),
            "msg": asset_json.get("msg"),
            **log,
        }

    selected = pick_coupon(asset_json)
    data = asset_json.get("data") or {}
    region_id = str(data.get("regionId") or "")
    region_version = str(data.get("regionVersion") or "")
    log["selected"] = {
        "couponName": (selected or {}).get("couponName"),
        "couponAmount": (selected or {}).get("couponAmount"),
        "num": (selected or {}).get("num"),
        "coupon_config_id": (selected or {}).get("coupon_config_id"),
    }
    log["region"] = {"region_id": region_id, "region_version": region_version}
    if not selected or not region_id or not region_version:
        return {"ok": False, "gateway": False, "stage": "no_coupon_or_region", **log}

    # Dual channel: 美团小程序 mt_mp first, then 外卖小程序 wm_wxapp.
    # Same proxy/cookie session; switch only pre/do shape if one channel fails.
    channels = [
        {
            "name": "mt_mp",
            "ctype": "mt_mp",
            "wm_ctype": None,
            "app_id": "wxde8ac0a21135c07d",
            "pageSource": "610",
            "wm_appversion": "10.28.01",
            "gd_ctype": "mt_mp",
            "gdEntry": None,
            "ua": UA_MT,
            "send_cookie": True,
        },
        {
            "name": "wm_wxapp",
            "ctype": "wm_wxapp",
            "wm_ctype": "wxapp",
            "app_id": "wx2c348cf579062e56",
            "pageSource": "103",
            "wm_appversion": "10.30.01",
            "gd_ctype": "wm_wxapp",
            "gdEntry": "wxTab",
            "ua": UA_WM,
            "send_cookie": False,
        },
    ]

    channel_failures: List[Dict[str, Any]] = []
    for channel in channels:
        pre_body = {
            "el_biz": "waimai",
            "el_page": "gundam.loader",
            "gundam_id": "3oMg3O",
            "tenant": "gundam",
            "pageSource": channel["pageSource"],
            "ctype": channel["ctype"],
            "isMini": "1",
            "webview_source": "native",
            "wm_latitude": wm_lat,
            "wm_longitude": wm_lng,
            "wm_actual_latitude": wm_lat,
            "wm_actual_longitude": wm_lng,
            "wm_appversion": channel["wm_appversion"],
            "app_id": channel["app_id"],
            "userid": userid,
            "token": token,
            "wm_logintoken": token,
            "welfareCenterPageSource": "wm",
            "request_page_source": "54",
            "planToken": "temple_id_93",
            "exchange_type": selected["exchange_type"],
            "coupon_view_id": selected["coupon_view_id"],
            "coupon_config_id": selected["coupon_config_id"],
            "assetType": "3",
        }
        if channel.get("wm_ctype"):
            pre_body["wm_ctype"] = channel["wm_ctype"]
        if channel.get("gdEntry"):
            pre_body["gdEntry"] = channel["gdEntry"]

        pre_query = {
            "region_id": region_id,
            "region_version": region_version,
            "gdBs": "0000",
            "pageVersion": "1783565608018",
            "__gd_activid": "552713",
            "__gd_pageid": "560699",
            "__gd_pagev": "1783565608018",
            "__gd_appv": "",
            "__gd_ctype": channel["gd_ctype"],
            "yodaReady": "h5",
            "csecplatform": "4",
            "csecversion": "4.2.4",
        }
        pre_headers = {
            "User-Agent": channel["ua"],
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": HOST,
            "Referer": HOST + "/",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        # Proven mttouch path uses Cookie/dj-token on mt_mp.
        # wm_wxapp success HAR often auth-in-body only.
        if channel["send_cookie"]:
            pre_headers["Cookie"] = cookie
            pre_headers["dj-token"] = token

        try:
            pre_resp = session.post(
                HOST
                + "/vp/magical/exchange/pre_exchange_for_magical_coupon?"
                + urllib.parse.urlencode(pre_query),
                data=enc(pre_body),
                headers=pre_headers,
                timeout=25,
            )
            pre_json = sj(pre_resp)
            pre_http = int(pre_resp.status_code)
        except Exception as error:
            channel_failures.append(
                {
                    "channel": channel["name"],
                    "stage": "pre_connect",
                    "error": type(error).__name__,
                }
            )
            print("[pre]", log["proxy"], channel["name"], "connect_fail", type(error).__name__)
            continue

        inflate, biz, targets = extract_targets(pre_json)
        pre_class = classify_exchange_response(
            pre_http,
            pre_resp.text or "",
            dict(pre_resp.headers),
            pre_json if isinstance(pre_json, dict) else {},
        )
        log["steps"].append(
            {
                "name": "pre_{}".format(channel["name"]),
                "http": pre_http,
                "code": pre_json.get("code"),
                "msg": pre_json.get("msg"),
                "hasInflate": bool(inflate),
                "targetCount": len(targets),
                "class": pre_class.get("class"),
            }
        )
        print(
            "[pre]",
            log["proxy"],
            channel["name"],
            pre_http,
            pre_json.get("code"),
            pre_json.get("msg"),
            "inflate",
            bool(inflate),
            "class",
            pre_class.get("class"),
        )
        if pre_http == 403 or pre_http == 0 or pre_class.get("class") == "gateway_lock":
            channel_failures.append(
                {
                    "channel": channel["name"],
                    "stage": "pre",
                    "gateway": True,
                    "http": pre_http,
                    "class": pre_class.get("class"),
                    "stop_ip_churn": should_stop_proxy_rotation(str(pre_class.get("class") or "")),
                }
            )
            continue
        if pre_http != 200 or pre_json.get("code") not in (0, "0") or not inflate or not targets:
            channel_failures.append(
                {
                    "channel": channel["name"],
                    "stage": "pre_biz",
                    "gateway": False,
                    "http": pre_http,
                    "code": pre_json.get("code"),
                    "msg": pre_json.get("msg"),
                }
            )
            continue

        time.sleep(max(0.0, delay))
        do_body = dict(pre_body)
        do_body.update(
            {
                "inflateToken": inflate,
                "targetCouponInfosStr": json.dumps(
                    targets, ensure_ascii=False, separators=(",", ":")
                ),
                "couponBizGroupId": biz,
            }
        )
        try:
            do_resp = session.post(
                HOST
                + "/vp/magical/exchange/do_exchange_for_magical_coupon?"
                + urllib.parse.urlencode(pre_query),
                data=enc(do_body),
                headers=pre_headers,
                timeout=25,
            )
            do_json = sj(do_resp)
            do_http = int(do_resp.status_code)
        except Exception as error:
            channel_failures.append(
                {
                    "channel": channel["name"],
                    "stage": "do_connect",
                    "error": type(error).__name__,
                }
            )
            print("[do]", log["proxy"], channel["name"], "connect_fail", type(error).__name__)
            continue

        got = success_list(do_json)
        do_class = classify_exchange_response(
            do_http,
            do_resp.text or "",
            dict(do_resp.headers),
            do_json if isinstance(do_json, dict) else {},
        )
        log["steps"].append(
            {
                "name": "do_{}_unsigned".format(channel["name"]),
                "http": do_http,
                "code": do_json.get("code"),
                "msg": do_json.get("msg"),
                "success_n": len(got),
                "class": do_class.get("class"),
            }
        )
        print(
            "[do]",
            log["proxy"],
            channel["name"],
            do_http,
            do_json.get("code"),
            do_json.get("msg"),
            "n",
            len(got),
            "class",
            do_class.get("class"),
        )
        if do_http == 200 and do_json.get("code") in (0, "0") and got:
            return {
                "ok": True,
                "gateway": False,
                "usedSplit": bool(proxy),
                "channel": channel["name"],
                "stage": "do",
                "inflated_coupons": got,
                **log,
            }
        # B-tier half-success: pre ok + do gateway_lock.
        # Stop immediately — next channel / next proxy escalates to C.
        if is_half_success_do_lock(pre_http, bool(inflate), str(do_class.get("class") or "")):
            log["steps"].append(
                {
                    "name": "half_success_do_lock",
                    "channel": channel["name"],
                    "class": "half_success_do_lock",
                    "action": "mark_account_cooldown_stop_churn",
                    "retry_with_new_ip": False,
                    "retry_with_channel_switch": False,
                }
            )
            print(
                "[stop] B-tier half_success_do_lock on",
                channel["name"],
                "; no channel switch / no proxy rotation",
            )
            return {
                "ok": False,
                "gateway": True,
                "half_success_do_lock": True,
                "stage": "half_success_do_lock_stop_churn",
                "channel": channel["name"],
                "do_class": do_class.get("class"),
                "channel_failures": channel_failures
                + [
                    {
                        "channel": channel["name"],
                        "stage": "do",
                        "gateway": True,
                        "http": do_http,
                        "class": do_class.get("class"),
                        "stop_ip_churn": True,
                    }
                ],
                **log,
            }
        channel_failures.append(
            {
                "channel": channel["name"],
                "stage": "do",
                "gateway": do_http == 403 or do_http == 0,
                "http": do_http,
                "code": do_json.get("code"),
                "msg": do_json.get("msg"),
                "class": do_class.get("class"),
            }
        )
        # do failed (non B-lock): try next channel with a fresh pre on that channel.

    gateway_failed = any(item.get("gateway") for item in channel_failures)
    return {
        "ok": False,
        "gateway": gateway_failed,
        "stage": "all_channels_failed",
        "channel_failures": channel_failures,
        **log,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--link", required=True)
    parser.add_argument("--lat", type=float, default=40.60353704)
    parser.add_argument("--lng", type=float, default=120.75256222)
    parser.add_argument("--max-proxy", type=int, default=6)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument(
        "--config",
        default=str(ROOT / "config" / "proxy_xiongmao.json"),
    )
    parser.add_argument("--out")
    args = parser.parse_args()

    account, token, userid = parse_link(args.link)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    # allow override extract_api via env not needed; user gave same as config
    pool = load_pool_from_config(cfg)

    report: Dict[str, Any] = {
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "pure_mttouch_dual_channel_split",
        "userid": userid,
        "token_prefix": token[:8],
        "channels": ["mt_mp", "wm_wxapp"],
        "attempts": [],
        "final": {},
    }

    for attempt in range(1, args.max_proxy + 1):
        try:
            proxy = pool.acquire()
        except Exception as error:
            print("[proxy] acquire fail", error)
            report["attempts"].append({"stage": "acquire", "error": str(error)})
            time.sleep(1)
            continue

        proxy_label = proxy.split("@")[-1] if "@" in proxy else proxy
        print("[proxy {}/{}] {}".format(attempt, args.max_proxy, proxy_label))
        one = run_once(proxy, account, token, userid, args.lat, args.lng, args.delay)
        one["attempt"] = attempt
        report["attempts"].append(one)
        if one.get("ok"):
            report["final"] = one
            break
        # B-tier: pre ok + do gateway 403 — stop IP churn immediately.
        if one.get("half_success_do_lock") or one.get("stage") == "half_success_do_lock_stop_churn":
            one["account_half_success_lock"] = True
            report["final"] = one
            print("[stop] half_success_do_lock; cooldown, no more proxy rotation")
            break
        # dual-channel HTML gateway lock → account-level lock; stop burning proxies
        pre_steps = [
            step
            for step in (one.get("steps") or [])
            if str(step.get("name") or "").startswith("pre_")
        ]
        gateway_locked = bool(pre_steps) and all(
            step.get("class") == "gateway_lock" or step.get("http") == 403 for step in pre_steps
        )
        if gateway_locked and len(pre_steps) >= 2:
            one["account_gateway_lock"] = True
            one["stage"] = "account_gateway_lock_stop_ip_churn"
            report["final"] = one
            print("[stop] account gateway_lock on both channels; no more proxy rotation")
            break
        if one.get("gateway"):
            try:
                pool.cool(proxy)
            except Exception:
                pass
            continue
        # business fail: stop (no coupon etc.)
        report["final"] = one
        break
    else:
        report["final"] = {"ok": False, "stage": "all_proxy_failed"}

    if not report.get("final"):
        report["final"] = {"ok": False, "stage": "all_proxy_failed"}

    out = Path(args.out) if args.out else OUT_DIR / "pure_mttouch_proxy_{}_{}.json".format(
        userid, time.strftime("%Y%m%d_%H%M%S")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report.get("final") or {}, ensure_ascii=False, indent=2))
    print("wrote", out)
    return 0 if (report.get("final") or {}).get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
