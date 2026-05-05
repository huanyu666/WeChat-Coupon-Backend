from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

try:
    from defusedxml import ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET


@dataclass
class HttpResult:
    status: int
    body: str
    headers: dict[str, str]


def _default_base_url() -> str:
    explicit = str(os.getenv("WX_SMOKE_BASE_URL") or "").strip()
    if explicit:
        return explicit

    dev_port = str(os.getenv("WX_DEV_HTTP_PORT") or "").strip()
    if dev_port:
        return f"http://127.0.0.1:{dev_port}"

    http_port = str(os.getenv("WX_HTTP_PORT") or "").strip()
    if http_port:
        return f"http://127.0.0.1:{http_port}"

    return "http://127.0.0.1:18080"


def _env_flag(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def _build_url(base_url: str, path: str, query: dict[str, str] | None = None) -> str:
    normalized_base = str(base_url or "").strip().rstrip("/")
    normalized_path = "/" + str(path or "").lstrip("/")
    url = normalized_base + normalized_path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


def _request(
    *,
    base_url: str,
    method: str,
    path: str,
    timeout: float,
    use_system_proxy: bool,
    query: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    text_body: str | None = None,
    headers: dict[str, str] | None = None,
) -> HttpResult:
    request_headers = dict(headers or {})
    request_headers.setdefault("Accept", "application/json, text/plain, */*")
    body_bytes: bytes | None = None
    if json_body is not None:
        body_bytes = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif text_body is not None:
        body_bytes = text_body.encode("utf-8")
        request_headers.setdefault("Content-Type", "text/xml; charset=utf-8")

    request = urllib.request.Request(
        _build_url(base_url, path, query),
        data=body_bytes,
        headers=request_headers,
        method=method.upper(),
    )
    opener = urllib.request.build_opener() if use_system_proxy else urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.getcode() or 0)
            body = response.read().decode("utf-8", errors="replace")
            headers_dict = {key.lower(): value for key, value in response.headers.items()}
            return HttpResult(status=status, body=body, headers=headers_dict)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        headers_dict = {key.lower(): value for key, value in exc.headers.items()}
        return HttpResult(status=int(exc.code or 0), body=body, headers=headers_dict)


def _json_payload(result: HttpResult, context: str) -> dict[str, Any]:
    try:
        payload = json.loads(result.body)
    except json.JSONDecodeError as exc:
        excerpt = result.body.replace("\n", "\\n")[:240]
        raise ValueError(f"{context} response is not valid JSON: status={result.status} body={excerpt!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{context} response is not a JSON object")
    return payload


def _extract_cookie_header(result: HttpResult, cookie_name: str = "wx_coupon_session") -> str:
    raw_cookie = str(result.headers.get("set-cookie") or "").strip()
    if not raw_cookie:
        return ""
    for part in raw_cookie.split(","):
        cookie_pair = part.strip().split(";", 1)[0].strip()
        if cookie_pair.startswith(f"{cookie_name}="):
            return cookie_pair
    return ""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _check_protected_endpoint(
    *,
    base_url: str,
    timeout: float,
    use_system_proxy: bool,
    path: str,
) -> None:
    result = _request(
        base_url=base_url,
        method="GET",
        path=path,
        timeout=timeout,
        use_system_proxy=use_system_proxy,
    )
    _assert(result.status == 401, f"{path} without authenticated session should return 401, got {result.status}")
    print(f"BUSINESS_SMOKE_PROTECTED_OK path={path} status={result.status}")


def _check_page_ok(
    *,
    base_url: str,
    timeout: float,
    use_system_proxy: bool,
    path: str,
) -> None:
    result = _request(
        base_url=base_url,
        method="GET",
        path=path,
        timeout=timeout,
        use_system_proxy=use_system_proxy,
    )
    _assert(result.status == 200, f"{path} should return 200, got {result.status}")
    print(f"BUSINESS_SMOKE_PAGE_OK path={path} status={result.status}")


def _admin_login_payload(username: str, password: str) -> dict[str, Any]:
    timestamp = int(time.time() * 1000)
    nonce = secrets.token_hex(8)
    stored_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    proof = hashlib.sha256(f"{stored_hash}{timestamp}{nonce}".encode("utf-8")).hexdigest()
    return {
        "username": username,
        "password_hash": proof,
        "timestamp": timestamp,
        "nonce": nonce,
        "remember_me": False,
    }


def _build_account_settings_save_payload(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": str(account.get("id") or "").strip(),
        "name": str(account.get("name") or "").strip(),
        "appid": str(account.get("appid") or "").strip(),
        "app_secret": "",
        "token": "",
        "encoding_aes_key": "",
        "zmkey": "",
        "set_as_default": bool(account.get("is_default")),
        "welcome_message": str(account.get("welcome_message") or ""),
        "default_reply": str(account.get("default_reply") or ""),
        "enabled_text_processors": list(account.get("enabled_text_processors") or []),
        "enabled_miniprogram_appids": list(account.get("enabled_miniprogram_appids") or []),
        "meituan_base_url": str(account.get("meituan_base_url") or ""),
        "meituan_official_cashback_url": str(account.get("meituan_official_cashback_url") or ""),
        "url_mode": str(account.get("url_mode") or "all"),
        "authorized_users": list(account.get("authorized_users") or []),
        "default_code_duration": str(account.get("default_code_duration") or ""),
        "keyword_responses": list(account.get("keyword_responses") or []),
        "meituan_miniprogram_config": dict(account.get("meituan_miniprogram_config") or {}),
        "meituan_merchant_coupon_view_config": dict(account.get("meituan_merchant_coupon_view_config") or {}),
        "meituan_miniprogram_link_processor_config": dict(account.get("meituan_miniprogram_link_processor_config") or {}),
        "meituan_link_config": dict(account.get("meituan_link_config") or {}),
        "merchant_coupon_prompts": dict(account.get("merchant_coupon_prompts") or {}),
        "click_event_responses": list(account.get("click_event_responses") or []),
    }


def _build_system_settings_save_payload(system_settings_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "prompts_config": dict(system_settings_payload.get("prompts_config") or {}),
        "link_config": dict(system_settings_payload.get("link_config") or {}),
        "order_leaderboard_config": dict(system_settings_payload.get("order_leaderboard_config") or {}),
        "shortlink_config": dict(
            (system_settings_payload.get("runtime_store") or {}).get("shortlink_config")
            or system_settings_payload.get("shortlink_config")
            or {}
        ),
    }


def _check_admin_login(
    *,
    base_url: str,
    timeout: float,
    use_system_proxy: bool,
    username: str,
    password: str,
    require_no_login_token: bool,
) -> None:
    for page_path in ("/index", "/material", "/wechat-account-settings", "/system-settings", "/migration"):
        _check_page_ok(
            base_url=base_url,
            timeout=timeout,
            use_system_proxy=use_system_proxy,
            path=page_path,
        )

    login_result = _request(
        base_url=base_url,
        method="POST",
        path="/api/auth/login",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        json_body=_admin_login_payload(username, password),
    )
    login_payload = _json_payload(login_result, "admin login")
    _assert(login_result.status == 200, f"admin login returned {login_result.status}: {login_payload}")
    _assert(login_payload.get("success") is True, f"admin login success != true: {login_payload}")
    token = str(login_payload.get("token") or "").strip()
    if require_no_login_token:
        _assert(not token, f"admin login should not return token, got payload: {login_payload}")
    cookie_header = _extract_cookie_header(login_result)
    _assert(bool(cookie_header), "admin login did not set the session cookie")

    auth_headers = {"Cookie": cookie_header}

    cookie_verify_result = _request(
        base_url=base_url,
        method="GET",
        path="/api/auth/verify",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        headers={"Cookie": cookie_header},
    )
    cookie_verify_payload = _json_payload(cookie_verify_result, "auth verify cookie")
    _assert(cookie_verify_result.status == 200, f"cookie auth verify returned {cookie_verify_result.status}: {cookie_verify_payload}")
    _assert(cookie_verify_payload.get("success") is True, f"cookie auth verify success != true: {cookie_verify_payload}")

    overview_result = _request(
        base_url=base_url,
        method="GET",
        path="/api/dashboard/overview",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        headers=auth_headers,
    )
    overview_payload = _json_payload(overview_result, "dashboard overview")
    _assert(overview_result.status == 200, f"dashboard overview returned {overview_result.status}: {overview_payload}")
    _assert(overview_payload.get("success") is True, f"dashboard overview success != true: {overview_payload}")
    _assert(isinstance(overview_payload.get("summary"), dict), f"dashboard overview missing summary: {overview_payload}")

    account_settings_result = _request(
        base_url=base_url,
        method="GET",
        path="/api/wechat/account-settings",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        headers={"Cookie": cookie_header},
    )
    account_settings_payload = _json_payload(account_settings_result, "wechat account settings")
    _assert(
        account_settings_result.status == 200,
        f"wechat account settings returned {account_settings_result.status}: {account_settings_payload}",
    )
    _assert(isinstance(account_settings_payload.get("accounts"), list), f"wechat account settings missing accounts: {account_settings_payload}")

    system_settings_result = _request(
        base_url=base_url,
        method="GET",
        path="/api/system-settings",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        headers={"Cookie": cookie_header},
    )
    system_settings_payload = _json_payload(system_settings_result, "system settings")
    _assert(system_settings_result.status == 200, f"system settings returned {system_settings_result.status}: {system_settings_payload}")
    _assert(isinstance(system_settings_payload.get("prompts_config"), dict), f"system settings missing prompts_config: {system_settings_payload}")
    system_settings_save_result = _request(
        base_url=base_url,
        method="POST",
        path="/api/system-settings",
        timeout=max(timeout, 10.0),
        use_system_proxy=use_system_proxy,
        headers={"Cookie": cookie_header},
        json_body=_build_system_settings_save_payload(system_settings_payload),
    )
    system_settings_save_payload = _json_payload(system_settings_save_result, "system settings save")
    _assert(
        system_settings_save_result.status == 200,
        f"system settings save returned {system_settings_save_result.status}: {system_settings_save_payload}",
    )
    _assert(system_settings_save_payload.get("success") is True, f"system settings save success != true: {system_settings_save_payload}")

    accounts = account_settings_payload.get("accounts") or []
    default_account_id = str(account_settings_payload.get("default_account_id") or "").strip()
    account_to_save = None
    if isinstance(accounts, list):
        for item in accounts:
            if isinstance(item, dict) and str(item.get("id") or "").strip() == default_account_id:
                account_to_save = item
                break
        if account_to_save is None:
            for item in accounts:
                if isinstance(item, dict) and str(item.get("id") or "").strip():
                    account_to_save = item
                    break

    if isinstance(account_to_save, dict) and str(account_to_save.get("appid") or "").strip():
        account_save_result = _request(
            base_url=base_url,
            method="POST",
            path="/api/wechat/account-settings",
            timeout=max(timeout, 10.0),
            use_system_proxy=use_system_proxy,
            headers={"Cookie": cookie_header},
            json_body=_build_account_settings_save_payload(account_to_save),
        )
        account_save_payload = _json_payload(account_save_result, "wechat account settings save")
        _assert(
            account_save_result.status == 200,
            f"wechat account settings save returned {account_save_result.status}: {account_save_payload}",
        )
        _assert(account_save_payload.get("success") is True, f"wechat account settings save success != true: {account_save_payload}")
        print(f"BUSINESS_SMOKE_ACCOUNT_SETTINGS_SAVE_OK account_id={account_to_save.get('id')}")
    else:
        print("BUSINESS_SMOKE_ACCOUNT_SETTINGS_SAVE_SKIPPED missing_account")

    migration_status_result = _request(
        base_url=base_url,
        method="GET",
        path="/api/migration/status",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        headers={"Cookie": cookie_header},
    )
    migration_status_payload = _json_payload(migration_status_result, "migration status")
    _assert(migration_status_result.status == 200, f"migration status returned {migration_status_result.status}: {migration_status_payload}")
    _assert(migration_status_payload.get("success") is True, f"migration status success != true: {migration_status_payload}")

    migration_export_result = _request(
        base_url=base_url,
        method="GET",
        path="/api/migration/export",
        timeout=max(timeout, 10.0),
        use_system_proxy=use_system_proxy,
        query={"include_env": "false"},
        headers={"Cookie": cookie_header, "Accept": "application/gzip, */*"},
    )
    _assert(migration_export_result.status == 200, f"migration export returned {migration_export_result.status}: {migration_export_result.body[:240]!r}")
    _assert(len(migration_export_result.body.encode("utf-8", errors="ignore")) > 0, "migration export returned an empty body")

    logout_result = _request(
        base_url=base_url,
        method="POST",
        path="/api/auth/logout",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        headers=auth_headers,
    )
    logout_payload = _json_payload(logout_result, "auth logout")
    _assert(logout_result.status == 200, f"auth logout returned {logout_result.status}: {logout_payload}")
    _assert(logout_payload.get("success") is True, f"auth logout success != true: {logout_payload}")
    print(f"BUSINESS_SMOKE_ADMIN_LOGIN_OK username={username}")
    print("BUSINESS_SMOKE_COOKIE_AUTH_OK")
    print("BUSINESS_SMOKE_ACCOUNT_SETTINGS_READ_OK")
    print("BUSINESS_SMOKE_SYSTEM_SETTINGS_SAVE_OK")
    print("BUSINESS_SMOKE_SYSTEM_SETTINGS_READ_OK")
    print("BUSINESS_SMOKE_MIGRATION_AUTH_OK")
    print("BUSINESS_SMOKE_LOGIN_TOKEN_ABSENT_OK")


def _wechat_signature(token: str, timestamp: str, nonce: str) -> str:
    pieces = sorted([token, timestamp, nonce])
    return hashlib.sha1("".join(pieces).encode("utf-8")).hexdigest()


def _check_wechat_verify(
    *,
    base_url: str,
    timeout: float,
    use_system_proxy: bool,
    token: str,
) -> None:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(6)
    echostr = "wx_coupon_smoke_" + secrets.token_hex(4)
    result = _request(
        base_url=base_url,
        method="GET",
        path="/wechat",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        query={
            "signature": _wechat_signature(token, timestamp, nonce),
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": echostr,
        },
    )
    _assert(result.status == 200, f"wechat verify returned {result.status}: {result.body[:240]!r}")
    _assert(result.body == echostr, f"wechat verify body mismatch: {result.body[:240]!r}")
    print("BUSINESS_SMOKE_WECHAT_VERIFY_OK")


def _check_wechat_message(
    *,
    base_url: str,
    timeout: float,
    use_system_proxy: bool,
    token: str,
    account_id: str,
) -> None:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(6)
    now = str(int(time.time()))
    msg_id = str(int(time.time() * 1000))
    xml = f"""<xml>
<ToUserName><![CDATA[{account_id}]]></ToUserName>
<FromUserName><![CDATA[wx_smoke_user]]></FromUserName>
<CreateTime>{now}</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[subscribe]]></Event>
<MsgId>{msg_id}</MsgId>
</xml>"""
    result = _request(
        base_url=base_url,
        method="POST",
        path="/wechat",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        query={
            "signature": _wechat_signature(token, timestamp, nonce),
            "timestamp": timestamp,
            "nonce": nonce,
        },
        text_body=xml,
        headers={"Accept": "text/plain, application/xml, */*"},
    )
    _assert(result.status == 200, f"wechat message returned {result.status}: {result.body[:240]!r}")
    try:
        root = ET.fromstring(result.body.encode("utf-8"))
    except Exception as exc:
        raise AssertionError(f"wechat message did not return parseable XML: {result.body[:240]!r}") from exc
    msg_type = (root.findtext("MsgType") or "").strip()
    content = (root.findtext("Content") or "").strip()
    _assert(root.tag == "xml" and msg_type == "text" and content, f"wechat message did not return text XML: {result.body[:240]!r}")
    print(f"BUSINESS_SMOKE_WECHAT_MESSAGE_OK account_id={account_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=_default_base_url())
    parser.add_argument("--timeout", type=float, default=float(str(os.getenv("WX_SMOKE_TIMEOUT_SECONDS") or "3").strip()))
    parser.add_argument("--use-system-proxy", action="store_true", default=_env_flag("WX_SMOKE_USE_SYSTEM_PROXY"))
    parser.add_argument("--admin-username", default=str(os.getenv("WX_SMOKE_ADMIN_USERNAME") or "").strip())
    parser.add_argument("--admin-password", default=str(os.getenv("WX_SMOKE_ADMIN_PASSWORD") or ""))
    parser.add_argument("--require-admin-login", action="store_true", default=_env_flag("WX_SMOKE_REQUIRE_ADMIN_LOGIN"))
    parser.add_argument("--allow-login-token", action="store_true", default=_env_flag("WX_SMOKE_ALLOW_LOGIN_TOKEN"))
    parser.add_argument("--wechat-token", default=str(os.getenv("WX_SMOKE_WECHAT_TOKEN") or "").strip())
    parser.add_argument("--wechat-account-id", default=str(os.getenv("WX_SMOKE_WECHAT_ACCOUNT_ID") or "").strip())
    parser.add_argument("--require-wechat-callback", action="store_true", default=_env_flag("WX_SMOKE_REQUIRE_WECHAT_CALLBACK"))
    args = parser.parse_args()

    try:
        _check_protected_endpoint(
            base_url=args.base_url,
            timeout=args.timeout,
            use_system_proxy=args.use_system_proxy,
            path="/api/auth/verify",
        )
        _check_protected_endpoint(
            base_url=args.base_url,
            timeout=args.timeout,
            use_system_proxy=args.use_system_proxy,
            path="/api/dashboard/overview",
        )
        _check_protected_endpoint(
            base_url=args.base_url,
            timeout=args.timeout,
            use_system_proxy=args.use_system_proxy,
            path="/api/migration/status",
        )

        if args.admin_username and args.admin_password:
            _check_admin_login(
                base_url=args.base_url,
                timeout=args.timeout,
                use_system_proxy=args.use_system_proxy,
                username=args.admin_username,
                password=args.admin_password,
                require_no_login_token=not args.allow_login_token,
            )
        elif args.require_admin_login:
            raise ValueError("admin login smoke requires WX_SMOKE_ADMIN_USERNAME and WX_SMOKE_ADMIN_PASSWORD")
        else:
            print("BUSINESS_SMOKE_ADMIN_LOGIN_SKIPPED missing_credentials")

        if args.wechat_token:
            _check_wechat_verify(
                base_url=args.base_url,
                timeout=args.timeout,
                use_system_proxy=args.use_system_proxy,
                token=args.wechat_token,
            )
            if args.wechat_account_id:
                _check_wechat_message(
                    base_url=args.base_url,
                    timeout=args.timeout,
                    use_system_proxy=args.use_system_proxy,
                    token=args.wechat_token,
                    account_id=args.wechat_account_id,
                )
            else:
                print("BUSINESS_SMOKE_WECHAT_MESSAGE_SKIPPED missing_account_id")
        elif args.require_wechat_callback:
            raise ValueError("wechat callback smoke requires WX_SMOKE_WECHAT_TOKEN")
        else:
            print("BUSINESS_SMOKE_WECHAT_CALLBACK_SKIPPED missing_token")
    except Exception as exc:
        print(f"BUSINESS_SMOKE_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    print("BUSINESS_SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
