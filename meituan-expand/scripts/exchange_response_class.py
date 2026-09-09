#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Classify Meituan magical-exchange HTTP responses for runner gating."""

from __future__ import annotations

from typing import Any, Dict, Optional


def classify_exchange_response(
    http_status: int,
    body_text: str = "",
    headers: Optional[Dict[str, str]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a stable classification for asset/pre/do style responses.

    Classes:
      - ok_business: HTTP 200 and code in {0,'0'}
      - business_error: JSON body with non-zero code
      - gateway_lock: openresty/HTML 403 (or x-forbid-reason) — stop IP churn
      - http_error: other non-200
      - transport_empty: empty/unknown
    """
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    text = body_text or ""
    payload = payload or {}

    forbid = headers.get("x-forbid-reason")
    content_type = headers.get("content-type", "")
    html_403 = int(http_status) == 403 and (
        "text/html" in content_type
        or "403 Forbidden" in text
        or text.strip().lower().startswith("<html")
    )
    if html_403 or (int(http_status) == 403 and forbid is not None and not text.strip().startswith("{")):
        return {
            "class": "gateway_lock",
            "http": int(http_status),
            "x_forbid_reason": forbid,
            "retry_with_new_ip": False,
            "retry_with_channel_switch": False,
            "action": "mark_account_cooldown_stop_churn",
        }

    if int(http_status) == 200:
        code = payload.get("code") if isinstance(payload, dict) else None
        if code in (0, "0"):
            return {
                "class": "ok_business",
                "http": 200,
                "code": code,
                "msg": payload.get("msg") if isinstance(payload, dict) else None,
                "retry_with_new_ip": False,
                "action": "continue",
            }
        if isinstance(payload, dict) and ("code" in payload or text.strip().startswith("{")):
            return {
                "class": "business_error",
                "http": 200,
                "code": code,
                "msg": payload.get("msg"),
                "retry_with_new_ip": False,
                "retry_with_channel_switch": True,
                "action": "inspect_business_code",
            }

    if int(http_status) == 403 and text.strip().startswith("{"):
        return {
            "class": "business_forbid_json",
            "http": 403,
            "payload_prefix": text[:200],
            "retry_with_new_ip": False,
            "action": "inspect_json_forbid",
        }

    if int(http_status) >= 400:
        return {
            "class": "http_error",
            "http": int(http_status),
            "retry_with_new_ip": int(http_status) in (429, 502, 503, 504),
            "action": "backoff" if int(http_status) in (429, 502, 503, 504) else "stop_or_inspect",
        }

    return {
        "class": "transport_empty",
        "http": int(http_status),
        "action": "inspect",
    }


def should_stop_proxy_rotation(pre_class: str) -> bool:
    return pre_class in {"gateway_lock", "business_forbid_json", "ok_business", "business_error"}


def is_half_success_do_lock(
    pre_http: int,
    pre_has_inflate: bool,
    do_class: str,
) -> bool:
    """B-tier: pre business-ok, do openresty gateway 403.

    This window must NOT rotate proxy / multi-channel scan — that escalates B→C.
    """
    return (
        int(pre_http) == 200
        and bool(pre_has_inflate)
        and do_class == "gateway_lock"
    )
