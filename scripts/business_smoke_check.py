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
    _assert(result.status == 401, f"{path} without token should return 401, got {result.status}")
    print(f"BUSINESS_SMOKE_PROTECTED_OK path={path} status={result.status}")


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


def _check_admin_login(
    *,
    base_url: str,
    timeout: float,
    use_system_proxy: bool,
    username: str,
    password: str,
) -> None:
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
    _assert(bool(token), "admin login did not return a token")

    auth_headers = {"Authorization": f"Bearer {token}"}
    verify_result = _request(
        base_url=base_url,
        method="GET",
        path="/api/auth/verify",
        timeout=timeout,
        use_system_proxy=use_system_proxy,
        headers=auth_headers,
    )
    verify_payload = _json_payload(verify_result, "auth verify")
    _assert(verify_result.status == 200, f"auth verify returned {verify_result.status}: {verify_payload}")
    _assert(verify_payload.get("success") is True, f"auth verify success != true: {verify_payload}")
    _assert(str(verify_payload.get("username") or "") == username, f"auth verify returned wrong username: {verify_payload}")

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
    _assert("<xml>" in result.body and "<MsgType><![CDATA[text]]>" in result.body, f"wechat message did not return text XML: {result.body[:240]!r}")
    print(f"BUSINESS_SMOKE_WECHAT_MESSAGE_OK account_id={account_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=_default_base_url())
    parser.add_argument("--timeout", type=float, default=float(str(os.getenv("WX_SMOKE_TIMEOUT_SECONDS") or "3").strip()))
    parser.add_argument("--use-system-proxy", action="store_true", default=_env_flag("WX_SMOKE_USE_SYSTEM_PROXY"))
    parser.add_argument("--admin-username", default=str(os.getenv("WX_SMOKE_ADMIN_USERNAME") or "").strip())
    parser.add_argument("--admin-password", default=str(os.getenv("WX_SMOKE_ADMIN_PASSWORD") or ""))
    parser.add_argument("--require-admin-login", action="store_true", default=_env_flag("WX_SMOKE_REQUIRE_ADMIN_LOGIN"))
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

        if args.admin_username and args.admin_password:
            _check_admin_login(
                base_url=args.base_url,
                timeout=args.timeout,
                use_system_proxy=args.use_system_proxy,
                username=args.admin_username,
                password=args.admin_password,
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
