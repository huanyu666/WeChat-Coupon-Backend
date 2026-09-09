#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Meituan magical-coupon expand console with card-key billing.

Rules:
  - No public basic-auth on user page
  - Card key required for expand jobs
  - pre_only does NOT consume uses
  - successful do (tier A) consumes 1 use
  - max_uses=0 means unlimited
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, Response, jsonify, request, send_from_directory

_DEFAULT_ROOT = Path(__file__).resolve().parent.parent
ROOT = Path(os.environ.get("MT_ROOT", str(_DEFAULT_ROOT))).expanduser().resolve()
SCRIPTS = ROOT / "scripts"
EXPORTS = ROOT / "exports"
WEB = ROOT / "web"
JOBS = WEB / "jobs"
STATIC = WEB / "static"
CONFIG = ROOT / "config"
CARDS_FILE = CONFIG / "cards.json"
ADMIN_FILE = CONFIG / "admin.json"

JOBS.mkdir(parents=True, exist_ok=True)
STATIC.mkdir(parents=True, exist_ok=True)
EXPORTS.mkdir(parents=True, exist_ok=True)
CONFIG.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(SCRIPTS))

app = Flask(__name__, static_folder=str(STATIC), static_url_path="/static")

_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()
_runner_lock = threading.Lock()
_card_lock = threading.Lock()

DEFAULT_LAT = 40.60353704
DEFAULT_LNG = 120.75256222
DEFAULT_PRE_CHANNEL = os.environ.get("MT_PRE_ONLY_CHANNEL", "wm_wxapp").strip() or "wm_wxapp"

PRESETS = [
    {
        "id": "xingcheng",
        "label": "葫芦岛市兴城市 · 兴城高中",
        "lat": 40.60353704,
        "lng": 120.75256222,
        "note": "大额券 38-24 / 28-18",
    },
    {
        "id": "wangcheng",
        "label": "长沙市望城区",
        "lat": 28.3665,
        "lng": 112.827,
        "note": "能卡出 20-13/12 左右，有效期 1 个月",
    },
    {
        "id": "yuzhou",
        "label": "许昌市禹州市 · 颍顺路",
        "lat": 34.13397825,
        "lng": 113.52310728,
        "note": "能卡出 20-13/12 左右，有效期 1 个月",
    },
    {
        "id": "haifeng",
        "label": "汕尾市海丰县",
        "lat": 22.973452,
        "lng": 115.329502,
        "note": "能卡出 20-13/12 左右，有效期 1 个月",
    },
    {
        "id": "laiyang",
        "label": "烟台市莱阳市",
        "lat": 36.98374057,
        "lng": 120.71443814,
        "note": "大额券 38-24 / 28-18",
    },
    {
        "id": "gongzhuling",
        "label": "长春市公主岭市 · 陶家屯镇",
        "lat": 43.65051533,
        "lng": 124.99881644,
        "note": "大额券 38-24 / 28-18",
    },
    {
        "id": "lixin",
        "label": "安徽利辛县 · 王市镇",
        "lat": 33.18045085,
        "lng": 116.08392476,
        "note": "大额券 38-24 / 28-18",
    },
    {
        "id": "gongan",
        "label": "湖北荆州市 · 公安县书香门邸",
        "lat": 30.29208563,
        "lng": 112.24722599,
        "note": "大额券 38-24 / 28-18",
    },
    {
        "id": "huazhou",
        "label": "化州市中医院",
        "lat": 21.68955664,
        "lng": 110.66757371,
        "note": "大额券 38-22 / 28-16",
    },
    {
        "id": "langxi",
        "label": "安徽郎溪 · 城南乡",
        "lat": 31.12789172,
        "lng": 119.18333029,
        "note": "大额券 38-22 / 28-16",
    },
    {
        "id": "hongze",
        "label": "淮安市洪泽区 · 夕阳红",
        "lat": 33.30129529,
        "lng": 118.87068404,
        "note": "大额券 38-20；APP进店可卡 23-13",
    },
    {
        "id": "guangshan",
        "label": "信阳市光山县",
        "lat": 32.01655100,
        "lng": 114.92590500,
        "note": "APP进店可卡 23-13；小程序膨胀 20-10",
    },
    {
        "id": "dafang",
        "label": "贵州大方县 · 羊场镇",
        "lat": 27.07702602,
        "lng": 105.68190626,
        "note": "易出 20-10 / 22-12",
    },
    {
        "id": "yuanyang",
        "label": "云南省元阳县",
        "lat": 23.22563285,
        "lng": 102.84367407,
        "note": "易出 20-10 / 22-12",
    },
    {
        "id": "guangfeng",
        "label": "江西上饶 · 广丰区",
        "lat": 28.38501181,
        "lng": 118.25451831,
        "note": "易出 20-10 / 27-14",
    },
    {
        "id": "hezhou",
        "label": "贺州市八步区",
        "lat": 24.41029750,
        "lng": 111.57308363,
        "note": "27-14 及大额券；部分号 20-11",
    },
]

MODES = {
    "pre_only": {
        "label": "预膨胀",
        "desc": "代理打 pre · 不发 do · 不计次数",
        "calls_do": False,
    },
    "proxy": {
        "label": "立即膨胀",
        "desc": "代理 pre+do · 成功计 1 次",
        "calls_do": True,
    },
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _parse_link(link: str) -> Dict[str, str]:
    text = (link or "").strip()
    m_token = re.search(r"[?&]token=([^&\s]+)", text)
    m_uid = re.search(r"[?&]userId=([^&\s]+)", text, re.I)
    if not m_token or not m_uid:
        raise ValueError("链接需要包含 userId 与 token")
    token = m_token.group(1)
    return {
        "link": text,
        "userid": m_uid.group(1),
        "token_prefix": token[:8],
    }


def _job_path(job_id: str) -> Path:
    return JOBS / f"{job_id}.json"


def _save_job(job: Dict[str, Any]) -> None:
    _job_path(job["id"]).write_text(
        json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _set_job(job_id: str, **fields: Any) -> Dict[str, Any]:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        job.update(fields)
        job["updated_at"] = _now()
        _save_job(job)
        return dict(job)


# ---------------- cards / admin ----------------

def _default_admin() -> Dict[str, Any]:
    return {
        "admin_key": secrets.token_urlsafe(18),
        "created_at": _now(),
    }


def _load_admin() -> Dict[str, Any]:
    if not ADMIN_FILE.exists():
        data = _default_admin()
        ADMIN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    try:
        data = json.loads(ADMIN_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("admin_key"):
            data = _default_admin()
            ADMIN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    except Exception:
        data = _default_admin()
        ADMIN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data


def _admin_key() -> str:
    env = os.environ.get("MT_ADMIN_KEY", "").strip()
    if env:
        return env
    return str(_load_admin().get("admin_key") or "")


def _require_admin() -> Optional[Any]:
    key = (
        request.headers.get("X-Admin-Key")
        or request.args.get("admin_key")
        or (request.get_json(silent=True) or {}).get("admin_key")
        or ""
    )
    key = str(key).strip()
    if not key or key != _admin_key():
        return jsonify({"error": "管理员密钥错误"}), 401
    return None


def _load_cards() -> Dict[str, Any]:
    if not CARDS_FILE.exists():
        data = {"cards": []}
        CARDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data
    try:
        data = json.loads(CARDS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {"cards": []}
        if not isinstance(data.get("cards"), list):
            data["cards"] = []
        return data
    except Exception:
        return {"cards": []}


def _save_cards(data: Dict[str, Any]) -> None:
    CARDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_card_code(code: str) -> str:
    return re.sub(r"\s+", "", str(code or "")).strip().upper()


def _normalize_client_id(client_id: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]", "", str(client_id or "").strip())
    if len(text) < 8:
        return ""
    return text[:64]


def _public_card(card: Dict[str, Any]) -> Dict[str, Any]:
    max_uses = int(card.get("max_uses") or 0)
    used = int(card.get("used") or 0)
    if max_uses <= 0:
        remain: Any = "无限"
    else:
        remain = max(0, max_uses - used)
    return {
        "code": card.get("code"),
        "note": card.get("note") or "",
        "max_uses": max_uses,
        "used": used,
        "remain": remain,
        "enabled": bool(card.get("enabled", True)),
        "created_at": card.get("created_at"),
        "updated_at": card.get("updated_at"),
    }


def _find_card(code: str) -> Optional[Dict[str, Any]]:
    code = _normalize_card_code(code)
    if not code:
        return None
    data = _load_cards()
    for card in data.get("cards") or []:
        if _normalize_card_code(str(card.get("code") or "")) == code:
            return card
    return None


def _card_available(card: Dict[str, Any]) -> Tuple[bool, str]:
    if not card:
        return False, "卡密不存在"
    if not bool(card.get("enabled", True)):
        return False, "卡密已停用"
    max_uses = int(card.get("max_uses") or 0)
    used = int(card.get("used") or 0)
    if max_uses > 0 and used >= max_uses:
        return False, "卡密次数已用完"
    return True, "ok"


def _card_optional(card_code: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Card key is optional: empty card_code means free usage (no billing)."""
    code = _normalize_card_code(card_code)
    if not code:
        return None, None
    card = _find_card(code)
    if not card:
        return None, "卡密不存在"
    ok, msg = _card_available(card)
    if not ok:
        return card, msg
    return card, None


def _consume_card(code: str) -> Dict[str, Any]:
    """Consume 1 use. max_uses=0 => unlimited (used still +1 for stats)."""
    code = _normalize_card_code(code)
    with _card_lock:
        data = _load_cards()
        for card in data.get("cards") or []:
            if _normalize_card_code(str(card.get("code") or "")) != code:
                continue
            ok, msg = _card_available(card)
            if not ok:
                raise RuntimeError(msg)
            card["used"] = int(card.get("used") or 0) + 1
            card["updated_at"] = _now()
            card["last_used_at"] = _now()
            _save_cards(data)
            return _public_card(card)
        raise RuntimeError("卡密不存在")


def _card_snapshot(code: str) -> Optional[Dict[str, Any]]:
    card = _find_card(code)
    return _public_card(card) if card else None


def _gen_card_code() -> str:
    # readable chunks: XXXX-XXXX-XXXX
    raw = secrets.token_hex(6).upper()
    return f"{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


# ---------------- logging / classify helpers ----------------

def _compact_log_line(line: str) -> Optional[str]:
    text = (line or "").rstrip("\n").strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        return None
    if text.startswith("wrote "):
        return None
    if len(text) > 200:
        text = text[:197] + "…"
    return text


def _append_log(job_id: str, line: str) -> None:
    compact = _compact_log_line(line)
    if compact is None:
        return
    with _lock:
        job = _jobs[job_id]
        logs = job.setdefault("logs", [])
        logs.append(compact)
        if len(logs) > 80:
            job["logs"] = logs[-80:]
        job["updated_at"] = _now()
        _save_job(job)


def _fen_to_yuan(value: Any) -> Optional[float]:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num >= 100:
        return round(num / 100.0, 2)
    return num


def _extract_pre_targets(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    channels = result.get("channels") or []
    merged: Dict[Tuple[str, Any, Any], Dict[str, Any]] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        channel_name = str(channel.get("channel") or channel.get("name") or "")
        for item in channel.get("targets") or []:
            if not isinstance(item, dict):
                continue
            amount = _fen_to_yuan(item.get("targetCouponAmount"))
            limit = _fen_to_yuan(item.get("targetCouponAmountLimit"))
            config_id = str(item.get("targetCouponConfigId") or "")
            key = (config_id, amount, limit)
            target = merged.setdefault(
                key,
                {
                    "config_id": config_id,
                    "amount": amount,
                    "limit": limit,
                    "channels": [],
                },
            )
            if channel_name and channel_name not in target["channels"]:
                target["channels"].append(channel_name)
    return list(merged.values())


def _tier_from_result(result: Dict[str, Any], mode: str) -> str:
    final = result.get("final") if isinstance(result.get("final"), dict) else {}
    stage = str(final.get("stage") or result.get("stage") or "")
    cls = str(final.get("class") or "")

    if mode == "pre_only":
        if stage == "no_coupon_or_region":
            return "NO_COUPON"
        channels = result.get("channels") or []
        if final.get("tier_guess") == "C_gateway_lock" or (
            channels
            and all(isinstance(c, dict) and c.get("class") == "gateway_lock" for c in channels)
        ):
            return "C"
        if not any(isinstance(c, dict) and c.get("hasInflate") for c in channels):
            if any(isinstance(c, dict) and c.get("class") == "gateway_lock" for c in channels):
                return "C"
        if final.get("ok") or final.get("tier_guess") == "pre_ok_do_unknown":
            return "PRE_OK"
        return "?"

    if final.get("ok") is True or (stage == "do" and final.get("http") == 200):
        return "A"
    if (
        stage == "half_success_do_lock"
        or cls == "half_success_do_lock"
        or result.get("half_success_do_lock")
    ):
        return "B"
    if cls == "gateway_lock" or result.get("account_gateway_lock") or stage in {
        "pre",
        "account_gateway_lock_stop_ip_churn",
    }:
        if final.get("tier_guess") == "C_gateway_lock" or result.get("account_gateway_lock"):
            return "C"
        if cls == "gateway_lock" or (stage == "pre" and final.get("http") == 403):
            return "C"
    if stage == "no_coupon_or_region":
        return "NO_COUPON"
    if final.get("tier_guess") == "pre_ok_do_unknown":
        return "PRE_OK"
    return "?"


def _run_proxy(
    link: str, lat: float, lng: float, delay: float, max_proxy: int, job_id: str
) -> Dict[str, Any]:
    out = EXPORTS / f"web_proxy_{job_id}.json"
    cmd = [
        sys.executable,
        str(SCRIPTS / "pure_mttouch_proxy_once.py"),
        "--link",
        link,
        "--lat",
        str(lat),
        "--lng",
        str(lng),
        "--delay",
        str(delay),
        "--max-proxy",
        str(max_proxy),
        "--config",
        str(CONFIG / "proxy_xiongmao.json"),
        "--out",
        str(out),
    ]
    _append_log(job_id, "$ pure_mttouch_proxy_once ...")
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        _append_log(job_id, line.rstrip("\n"))
    code = proc.wait()
    result: Dict[str, Any] = {}
    if out.exists():
        result = json.loads(out.read_text(encoding="utf-8"))
    result["_exit_code"] = code
    result["_out_file"] = str(out)
    return result


def _run_pre_only(
    link: str, lat: float, lng: float, use_proxy: bool, job_id: str
) -> Dict[str, Any]:
    from curl_cffi import requests as cr
    from exchange_response_class import classify_exchange_response
    import urllib.parse

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

    def enc(body: Dict[str, Any]) -> str:
        return urllib.parse.urlencode(
            {
                k: (
                    v
                    if isinstance(v, str)
                    else json.dumps(v, ensure_ascii=False, separators=(",", ":"))
                )
                for k, v in body.items()
            }
        )

    def sj(resp: Any) -> Dict[str, Any]:
        try:
            payload = resp.json()
            return payload if isinstance(payload, dict) else {"_raw": str(payload)[:200]}
        except Exception:
            return {"_raw": (resp.text or "")[:220]}

    def deg(v: float) -> str:
        return str(int(round(float(v) * 1_000_000)))

    info = _parse_link(link)
    token_m = re.search(r"[?&]token=([^&\s]+)", link)
    assert token_m
    token = token_m.group(1)
    userid = info["userid"]
    wm_lat, wm_lng = deg(lat), deg(lng)

    proxy_label = "direct"
    proxies = None
    if use_proxy:
        from proxy_pool_ydaili import load_pool_from_config

        cfg_path = CONFIG / "proxy_xiongmao.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        pool = load_pool_from_config(cfg)
        proxy = pool.acquire()
        proxy_label = proxy.split("@")[-1] if "@" in proxy else proxy
        proxies = {"http": proxy, "https": proxy}
        _append_log(job_id, f"[proxy] {proxy_label}")
    else:
        _append_log(job_id, "[proxy] direct")

    _append_log(job_id, "[mode] PRE_ONLY — do will NOT be called")
    session = cr.Session(impersonate="safari17_2_ios", proxies=proxies)
    cookie = (
        f"token={token}; mt_c_token={token}; isid={token}; oops={token}; "
        f"userId={userid}; u={userid}"
    )
    warm = session.get(
        link,
        headers={"User-Agent": UA_MT, "Cookie": cookie},
        timeout=15,
        allow_redirects=True,
    )
    for k, v in session.cookies.get_dict().items():
        if v and f"{k}=" not in cookie:
            cookie += f"; {k}={v}"
    _append_log(job_id, f"[warm] {warm.status_code}")

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
    ar = session.post(
        HOST + "/vp/magical/welfare/asset_module_v2?" + urllib.parse.urlencode(asset_query),
        data=enc(asset_body),
        headers={
            "User-Agent": UA_WM,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": HOST,
            "Referer": HOST + "/",
            "X-Requested-With": "XMLHttpRequest",
        },
        timeout=30,
    )
    aj = sj(ar)
    _append_log(job_id, f"[asset] {ar.status_code} {aj.get('code')} {aj.get('msg')}")
    data = aj.get("data") or {}
    region_id = str(data.get("regionId") or "")
    region_version = str(data.get("regionVersion") or "")
    selected = None
    for g in data.get("userMagicalCouponGroups") or []:
        if g.get("assetType") == 3 and not g.get("inflated") and g.get("userMmcInfos"):
            info0 = g["userMmcInfos"][0]
            selected = {
                "coupon_view_id": info0.get("couponViewId"),
                "coupon_config_id": str(
                    info0.get("couponConfigIdStr") or info0.get("couponConfigId") or ""
                ),
                "exchange_type": str(g.get("exchangeType") or "11"),
                "num": len(g["userMmcInfos"]),
                "name": g.get("couponName"),
                "amount": g.get("couponAmount"),
            }
            break
    _append_log(job_id, f"[selected] {selected}")
    _append_log(job_id, f"[region] {region_id} {region_version}")

    report: Dict[str, Any] = {
        "started_at": _now(),
        "mode": "pre_only",
        "userid": userid,
        "token_prefix": token[:8],
        "proxy": proxy_label,
        "geo": {"lat": lat, "lng": lng},
        "selected": selected,
        "region": {"region_id": region_id, "region_version": region_version},
        "pre_channel": DEFAULT_PRE_CHANNEL,
        "channels": [],
        "do_called": False,
    }
    if not selected or not region_id:
        report["final"] = {
            "ok": False,
            "stage": "no_coupon_or_region",
            "do_called": False,
        }
        out = EXPORTS / f"web_preonly_{job_id}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["_out_file"] = str(out)
        return report

    channels = [
        {
            "name": "mt_mp",
            "ctype": "mt_mp",
            "app_id": "wxde8ac0a21135c07d",
            "ua": UA_MT,
            "pageSource": "610",
            "wm_appversion": "10.28.01",
        },
        {
            "name": "wm_wxapp",
            "ctype": "wm_wxapp",
            "app_id": "wx2c348cf579062e56",
            "ua": UA_WM,
            "pageSource": "103",
            "wm_appversion": "10.30.01",
        },
    ]
    if DEFAULT_PRE_CHANNEL.lower() not in {"all", "both", "dual", "*"}:
        channels = [ch for ch in channels if ch["name"] == DEFAULT_PRE_CHANNEL]
        if not channels:
            _append_log(job_id, f"[pre_channel] invalid {DEFAULT_PRE_CHANNEL!r}, fallback wm_wxapp")
            channels = [
                {
                    "name": "wm_wxapp",
                    "ctype": "wm_wxapp",
                    "app_id": "wx2c348cf579062e56",
                    "ua": UA_WM,
                    "pageSource": "103",
                    "wm_appversion": "10.30.01",
                }
            ]
    _append_log(job_id, "[pre_channel] " + ",".join(ch["name"] for ch in channels))
    for ch in channels:
        pre_body: Dict[str, Any] = {
            "el_biz": "waimai",
            "el_page": "gundam.loader",
            "gundam_id": "3oMg3O",
            "tenant": "gundam",
            "pageSource": ch["pageSource"],
            "ctype": ch["ctype"],
            "isMini": "1",
            "webview_source": "native",
            "wm_latitude": wm_lat,
            "wm_longitude": wm_lng,
            "wm_actual_latitude": wm_lat,
            "wm_actual_longitude": wm_lng,
            "wm_appversion": ch["wm_appversion"],
            "app_id": ch["app_id"],
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
        if ch["name"] == "wm_wxapp":
            pre_body["wm_ctype"] = "wxapp"
        pre_query = {
            "region_id": region_id,
            "region_version": region_version,
            "gdBs": "0000",
            "pageVersion": "1783565608018",
            "__gd_activid": "552713",
            "__gd_pageid": "560699",
            "__gd_pagev": "1783565608018",
            "__gd_appv": "",
            "__gd_ctype": ch["ctype"],
            "yodaReady": "h5",
            "csecplatform": "4",
            "csecversion": "4.2.4",
        }
        headers = {
            "User-Agent": ch["ua"],
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": HOST,
            "Referer": HOST + "/",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": cookie,
            "dj-token": token,
        }
        pr = session.post(
            HOST
            + "/vp/magical/exchange/pre_exchange_for_magical_coupon?"
            + urllib.parse.urlencode(pre_query),
            data=enc(pre_body),
            headers=headers,
            timeout=30,
        )
        pj = sj(pr)
        pdata = pj.get("data") or {}
        inflate = str(pdata.get("inflateToken") or "")
        targets: List[Dict[str, Any]] = []
        for g in pdata.get("targetCouponGroups") or []:
            for t in g.get("targetCoupons") or []:
                targets.append(
                    {
                        "targetCouponConfigId": str(t.get("targetCouponConfigId")),
                        "targetCouponAmount": int(
                            t.get("targetCouponRealAmount")
                            or t.get("targetCouponAmount")
                            or 0
                        ),
                        "targetCouponAmountLimit": int(
                            t.get("targetCouponRealAmountLimit")
                            or t.get("targetCouponAmountLimit")
                            or 0
                        ),
                    }
                )
        cls = classify_exchange_response(
            int(pr.status_code),
            pr.text or "",
            dict(pr.headers),
            pj if isinstance(pj, dict) else {},
        )
        item = {
            "channel": ch["name"],
            "http": int(pr.status_code),
            "code": pj.get("code"),
            "msg": pj.get("msg"),
            "class": cls.get("class"),
            "hasInflate": bool(inflate),
            "targetCount": len(targets),
            "targets": targets[:8],
        }
        report["channels"].append(item)
        _append_log(
            job_id,
            f"[pre] {ch['name']} {item['http']} {item['code']} class={item['class']} "
            f"inflate={item['hasInflate']} targets={item['targetCount']}",
        )

    any_ok = any(
        c["class"] == "ok_business" and c["hasInflate"] for c in report["channels"]
    )
    all_lock = bool(report["channels"]) and all(
        c["class"] == "gateway_lock" for c in report["channels"]
    )
    if any_ok:
        tier = "pre_ok_do_unknown"
        action = "pre_passed_do_not_auto_fire"
    elif all_lock:
        tier = "C_gateway_lock"
        action = "mark_account_cooldown_stop_churn"
    else:
        tier = "pre_other"
        action = "inspect"
    report["final"] = {
        "ok": any_ok,
        "stage": "pre_only",
        "tier_guess": tier,
        "action": action,
        "do_called": False,
    }
    out = EXPORTS / f"web_preonly_{job_id}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["_out_file"] = str(out)
    _append_log(job_id, "[confirm] do_exchange was NOT called")
    return report


def _worker(job_id: str) -> None:
    with _runner_lock:
        try:
            job = _jobs[job_id]
            _set_job(job_id, status="running", started_at=_now())
            mode = job["mode"]
            link = job["link"]
            lat = float(job["lat"])
            lng = float(job["lng"])
            delay = float(job.get("delay") or 1.0)
            max_proxy = int(job.get("max_proxy") or 3)
            use_proxy_for_pre = bool(job.get("pre_use_proxy"))
            card_code = str(job.get("card_code") or "")

            if mode == "proxy":
                result = _run_proxy(link, lat, lng, delay, max_proxy, job_id)
            elif mode == "pre_only":
                result = _run_pre_only(link, lat, lng, use_proxy_for_pre, job_id)
            else:
                raise ValueError(f"unknown mode: {mode}")

            final = result.get("final") if isinstance(result.get("final"), dict) else {}
            if mode == "proxy":
                tier = _tier_from_result(
                    {
                        "final": final,
                        "half_success_do_lock": final.get("half_success_do_lock"),
                        "account_gateway_lock": final.get("account_gateway_lock"),
                        "stage": final.get("stage"),
                    },
                    mode,
                )
            else:
                tier = _tier_from_result(result, mode)

            summary: Dict[str, Any] = {
                "ok": bool(final.get("ok")),
                "stage": final.get("stage") or result.get("stage"),
                "class": final.get("class") or final.get("do_class"),
                "tier": tier,
                "mode": mode,
                "do_called": bool(final.get("do_called"))
                if "do_called" in final
                else (mode != "pre_only"),
                "userid": result.get("userid") or job.get("userid"),
                "token_prefix": result.get("token_prefix") or job.get("token_prefix"),
                "selected": final.get("selected")
                or result.get("selected")
                or final.get("selected"),
                "region": result.get("region") or final.get("region"),
                "inflated": final.get("inflated_coupons")
                or final.get("inflated")
                or [],
                "pre_targets": _extract_pre_targets(result),
                "channels": result.get("channels"),
                "action": final.get("action"),
                "out_file": result.get("_out_file"),
                "proxy": final.get("proxy") or result.get("proxy"),
                "card_code": card_code,
                "card": _card_snapshot(card_code) if card_code else None,
                "card_consumed": False,
            }
            if not summary["selected"]:
                summary["selected"] = result.get("selected") or final.get("selected")
            if mode == "proxy" and not summary.get("inflated"):
                summary["inflated"] = final.get("inflated_coupons") or []
            if mode == "pre_only":
                summary["inflated"] = []
                summary["do_called"] = False

            if tier == "PRE_OK":
                status = "pre_ok"
            elif tier == "A" and summary["ok"]:
                status = "success"
            elif tier in {"B", "C"}:
                status = "blocked"
            elif tier == "NO_COUPON":
                status = "no_coupon"
            elif summary["ok"]:
                status = "success"
            else:
                status = "failed"

            # consume only on real expand success AND when a card key was provided
            if mode == "proxy" and status == "success" and tier == "A" and card_code:
                try:
                    summary["card"] = _consume_card(card_code)
                    summary["card_consumed"] = True
                    _append_log(job_id, f"[card] consumed 1 use for {card_code}")
                except Exception as card_exc:
                    summary["card_consume_error"] = str(card_exc)
                    _append_log(job_id, f"[card] consume failed: {card_exc}")
            elif mode == "pre_only":
                _append_log(job_id, "[card] pre_only no consume")
            else:
                _append_log(job_id, "[card] no card key, free usage")

            # refresh snapshot
            if card_code and not summary.get("card_consumed"):
                summary["card"] = _card_snapshot(card_code)

            _set_job(
                job_id,
                status=status,
                finished_at=_now(),
                result=result,
                summary=summary,
                tier=tier,
            )
            _append_log(job_id, f"[done] status={status} tier={tier}")
        except Exception as exc:
            _append_log(job_id, f"[error] {type(exc).__name__}: {exc}")
            _set_job(
                job_id,
                status="error",
                finished_at=_now(),
                error=f"{type(exc).__name__}: {exc}",
            )


# ---------------- routes ----------------

@app.get("/")
def index() -> Response:
    return send_from_directory(str(STATIC), "index.html")


@app.get("/admin")
def admin_page() -> Response:
    return send_from_directory(str(STATIC), "admin.html")


@app.get("/api/meta")
def meta():
    return jsonify(
        {
            "presets": PRESETS,
            "modes": MODES,
            "defaults": {
                "lat": DEFAULT_LAT,
                "lng": DEFAULT_LNG,
                "delay": 1.0,
                "max_proxy": 3,
                "mode": "proxy",
                "pre_use_proxy": True,
                "pre_channel": DEFAULT_PRE_CHANNEL,
            },
            "rules": [
                "卡密可选：不填卡密直接免费使用",
                "预膨胀只检查，不计次数",
                "立即膨胀成功才扣 1 次（填卡密时）",
                "次数为 0 表示无限",
            ],
        }
    )


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "time": _now(), "root": str(ROOT)})


@app.post("/api/card/check")
def card_check():
    body = request.get_json(force=True, silent=True) or {}
    code = _normalize_card_code(str(body.get("card") or body.get("card_code") or ""))
    if not code:
        return jsonify({"ok": False, "error": "请输入卡密"}), 400
    card = _find_card(code)
    if not card:
        return jsonify({"ok": False, "error": "卡密不存在"}), 404
    ok, msg = _card_available(card)
    pub = _public_card(card)
    return jsonify({"ok": ok, "error": None if ok else msg, "card": pub})


@app.post("/api/jobs")
def create_job():
    body = request.get_json(force=True, silent=True) or {}
    link = str(body.get("link") or "").strip()
    mode = str(body.get("mode") or "proxy").strip()
    card_code = _normalize_card_code(str(body.get("card") or body.get("card_code") or ""))
    client_id = _normalize_client_id(
        str(body.get("client_id") or request.headers.get("X-Client-Id") or "")
    )

    if mode not in MODES:
        return jsonify({"error": f"mode must be one of {list(MODES)}"}), 400
    if not client_id:
        return jsonify({"error": "缺少客户端标识，请刷新页面重试"}), 400

    card, card_err = _card_optional(card_code)
    if card_err:
        return jsonify({"error": card_err, "card": _public_card(card) if card else None}), 400

    try:
        info = _parse_link(link)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        lat = float(body.get("lat") if body.get("lat") not in (None, "") else DEFAULT_LAT)
        lng = float(body.get("lng") if body.get("lng") not in (None, "") else DEFAULT_LNG)
        delay = float(body.get("delay") if body.get("delay") not in (None, "") else 1.0)
        max_proxy = int(body.get("max_proxy") if body.get("max_proxy") not in (None, "") else 3)
    except (TypeError, ValueError):
        return jsonify({"error": "lat/lng/delay/max_proxy 非法"}), 400

    max_proxy = max(1, min(max_proxy, 6))
    delay = max(0.0, min(delay, 5.0))
    pre_use_proxy = True if body.get("pre_use_proxy") is None else bool(body.get("pre_use_proxy"))
    if mode == "proxy":
        pre_use_proxy = True

    job_id = uuid.uuid4().hex[:12]
    job = {
        "id": job_id,
        "status": "queued",
        "mode": mode,
        "link": info["link"],
        "userid": info["userid"],
        "token_prefix": info["token_prefix"],
        "card_code": card_code,
        "client_id": client_id,
        "lat": lat,
        "lng": lng,
        "delay": delay,
        "max_proxy": max_proxy,
        "pre_use_proxy": pre_use_proxy,
        "created_at": _now(),
        "updated_at": _now(),
        "logs": [],
        "summary": None,
        "tier": None,
        "result": None,
        "error": None,
    }
    with _lock:
        _jobs[job_id] = job
        _save_job(job)

    threading.Thread(target=_worker, args=(job_id,), daemon=True).start()
    return jsonify(
        {
            "id": job_id,
            "status": "queued",
            "userid": info["userid"],
            "card": _public_card(card) if card else None,
            "client_id": client_id,
        }
    )


@app.get("/api/jobs")
def list_jobs():
    client_id = _normalize_client_id(
        str(request.args.get("client_id") or request.headers.get("X-Client-Id") or "")
    )
    # admin can pass admin_key to see all
    admin_key = str(
        request.args.get("admin_key") or request.headers.get("X-Admin-Key") or ""
    ).strip()
    see_all = bool(admin_key) and admin_key == _admin_key()

    if not see_all and not client_id:
        return jsonify({"jobs": [], "total": 0, "scope": "none"})

    with _lock:
        # also load recent disk jobs into memory lightly
        items = list(_jobs.values())
        # merge disk jobs not in memory
        try:
            for path in sorted(JOBS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:200]:
                jid = path.stem
                if jid in _jobs:
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and data.get("id"):
                        items.append(data)
                except Exception:
                    pass
        except Exception:
            pass

        items = sorted(items, key=lambda j: j.get("created_at") or "", reverse=True)
        out = []
        for j in items:
            if not see_all and str(j.get("client_id") or "") != client_id:
                continue
            out.append(
                {
                    "id": j["id"],
                    "status": j["status"],
                    "mode": j["mode"],
                    "userid": j.get("userid"),
                    "token_prefix": j.get("token_prefix"),
                    # do not expose full card to others; own list can show masked
                    "card_code": j.get("card_code") if (see_all or str(j.get("client_id") or "") == client_id) else None,
                    "tier": j.get("tier"),
                    "created_at": j.get("created_at"),
                    "finished_at": j.get("finished_at"),
                    "summary": j.get("summary"),
                }
            )
            if len(out) >= 50:
                break
    return jsonify({"jobs": out, "total": len(out), "scope": "all" if see_all else "self"})


@app.get("/api/jobs/<job_id>")
def get_job(job_id: str):
    lite = str(request.args.get("lite") or "").lower() in {"1", "true", "yes"}
    client_id = _normalize_client_id(
        str(request.args.get("client_id") or request.headers.get("X-Client-Id") or "")
    )
    admin_key = str(
        request.args.get("admin_key") or request.headers.get("X-Admin-Key") or ""
    ).strip()
    see_all = bool(admin_key) and admin_key == _admin_key()

    with _lock:
        job = _jobs.get(job_id)
        if not job:
            path = _job_path(job_id)
            if path.exists():
                job = json.loads(path.read_text(encoding="utf-8"))
                _jobs[job_id] = job
            else:
                return jsonify({"error": "not found"}), 404
        # ownership check
        owner = str(job.get("client_id") or "")
        if not see_all:
            if not client_id or owner != client_id:
                # hide existence
                return jsonify({"error": "not found"}), 404
        payload = dict(job)
    if lite:
        raw = payload.get("result")
        slim_result = None
        if isinstance(raw, dict):
            slim_result = {
                "final": raw.get("final"),
                "selected": raw.get("selected"),
                "region": raw.get("region"),
                "channels": raw.get("channels"),
                "userid": raw.get("userid"),
                "token_prefix": raw.get("token_prefix"),
                "proxy": raw.get("proxy"),
            }
        payload = {
            "id": payload.get("id"),
            "status": payload.get("status"),
            "mode": payload.get("mode"),
            "userid": payload.get("userid"),
            "token_prefix": payload.get("token_prefix"),
            "card_code": payload.get("card_code"),
            "client_id": payload.get("client_id"),
            "tier": payload.get("tier"),
            "created_at": payload.get("created_at"),
            "finished_at": payload.get("finished_at"),
            "summary": payload.get("summary"),
            "logs": (payload.get("logs") or [])[-40:],
            "error": payload.get("error"),
            "result": slim_result,
        }
    else:
        # never return full mttouch link to non-owner (already blocked); strip link for safety in responses
        if "link" in payload and not see_all:
            payload = dict(payload)
            payload["link"] = "[redacted]"
    return jsonify(payload)


# -------- admin APIs --------

@app.get("/api/admin/ping")
def admin_ping():
    denied = _require_admin()
    if denied:
        return denied
    return jsonify({"ok": True, "time": _now()})


@app.get("/api/admin/cards")
def admin_list_cards():
    denied = _require_admin()
    if denied:
        return denied
    data = _load_cards()
    cards = [_public_card(c) for c in (data.get("cards") or [])]
    cards.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return jsonify({"cards": cards, "total": len(cards)})


@app.post("/api/admin/cards")
def admin_create_cards():
    denied = _require_admin()
    if denied:
        return denied
    body = request.get_json(force=True, silent=True) or {}
    try:
        count = int(body.get("count") or 1)
    except (TypeError, ValueError):
        return jsonify({"error": "count 非法"}), 400
    count = max(1, min(count, 100))
    try:
        max_uses = int(body.get("max_uses") if body.get("max_uses") not in (None, "") else 1)
    except (TypeError, ValueError):
        return jsonify({"error": "max_uses 非法"}), 400
    if max_uses < 0:
        return jsonify({"error": "max_uses 不能为负"}), 400
    note = str(body.get("note") or "").strip()
    custom = _normalize_card_code(str(body.get("code") or ""))

    created = []
    with _card_lock:
        data = _load_cards()
        existing = {
            _normalize_card_code(str(c.get("code") or ""))
            for c in (data.get("cards") or [])
        }
        if custom:
            if count != 1:
                return jsonify({"error": "自定义卡密一次只能创建 1 张"}), 400
            if custom in existing:
                return jsonify({"error": "卡密已存在"}), 400
            codes = [custom]
        else:
            codes = []
            while len(codes) < count:
                code = _gen_card_code()
                if code not in existing and code not in codes:
                    codes.append(code)
        for code in codes:
            card = {
                "code": code,
                "max_uses": max_uses,
                "used": 0,
                "enabled": True,
                "note": note,
                "created_at": _now(),
                "updated_at": _now(),
            }
            data.setdefault("cards", []).append(card)
            created.append(_public_card(card))
        _save_cards(data)
    return jsonify({"ok": True, "created": created})


@app.post("/api/admin/cards/<code>/update")
def admin_update_card(code: str):
    denied = _require_admin()
    if denied:
        return denied
    code = _normalize_card_code(code)
    body = request.get_json(force=True, silent=True) or {}
    with _card_lock:
        data = _load_cards()
        target = None
        for card in data.get("cards") or []:
            if _normalize_card_code(str(card.get("code") or "")) == code:
                target = card
                break
        if not target:
            return jsonify({"error": "卡密不存在"}), 404
        if "max_uses" in body and body.get("max_uses") not in (None, ""):
            try:
                mu = int(body.get("max_uses"))
            except (TypeError, ValueError):
                return jsonify({"error": "max_uses 非法"}), 400
            if mu < 0:
                return jsonify({"error": "max_uses 不能为负"}), 400
            target["max_uses"] = mu
        if "used" in body and body.get("used") not in (None, ""):
            try:
                used = int(body.get("used"))
            except (TypeError, ValueError):
                return jsonify({"error": "used 非法"}), 400
            if used < 0:
                return jsonify({"error": "used 不能为负"}), 400
            target["used"] = used
        if "enabled" in body:
            target["enabled"] = bool(body.get("enabled"))
        if "note" in body:
            target["note"] = str(body.get("note") or "")
        if body.get("reset"):
            target["used"] = 0
        target["updated_at"] = _now()
        _save_cards(data)
        return jsonify({"ok": True, "card": _public_card(target)})


@app.post("/api/admin/cards/<code>/delete")
def admin_delete_card(code: str):
    denied = _require_admin()
    if denied:
        return denied
    code = _normalize_card_code(code)
    with _card_lock:
        data = _load_cards()
        before = len(data.get("cards") or [])
        data["cards"] = [
            c
            for c in (data.get("cards") or [])
            if _normalize_card_code(str(c.get("code") or "")) != code
        ]
        if len(data["cards"]) == before:
            return jsonify({"error": "卡密不存在"}), 404
        _save_cards(data)
    return jsonify({"ok": True})


def main() -> None:
    # ensure admin key exists and print once on boot
    adm = _load_admin()
    print(f"ADMIN_KEY={adm.get('admin_key')}")
    print(f"Admin page: /admin")

    for path in sorted(JOBS.glob("*.json"))[-30:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("id"):
                _jobs[data["id"]] = data
        except Exception:
            pass

    host = os.environ.get("MT_HOST", "127.0.0.1")
    port = int(os.environ.get("MT_PORT", "8765"))
    print(f"膨胀控制台 → http://{host}:{port}")
    print(f"MT_ROOT={ROOT}")
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
