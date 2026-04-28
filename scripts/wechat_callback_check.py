from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import ipaddress
from pathlib import Path
from typing import Any

def _detect_project_root() -> Path:
    explicit_root = os.getenv("WX_WECHAT_CHECK_PROJECT_ROOT", "").strip()
    if explicit_root:
        return Path(explicit_root).resolve()
    script_file = globals().get("__file__", "")
    if script_file and script_file not in {"-", "<stdin>"}:
        return Path(script_file).resolve().parent.parent
    return Path.cwd().resolve()


PROJECT_ROOT = _detect_project_root()


def _fetch_text(url: str, timeout: float) -> tuple[int, str, dict[str, str]]:
    request = urllib.request.Request(url, headers={"Accept": "text/plain, application/xml, */*"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.getcode() or 0)
            body = response.read().decode("utf-8", errors="replace")
            headers = {key.lower(): value for key, value in response.headers.items()}
            return status, body, headers
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        headers = {key.lower(): value for key, value in exc.headers.items()}
        return int(exc.code or 0), body, headers


def _signature(*parts: str) -> str:
    return hashlib.sha1("".join(sorted(parts)).encode("utf-8")).hexdigest()


def _validate_aes_key(key: str) -> None:
    if len(key) != 43:
        raise ValueError(f"EncodingAESKey 长度必须是 43，当前是 {len(key)}")
    try:
        raw = base64.b64decode(key + "=")
    except Exception as exc:
        raise ValueError(f"EncodingAESKey 不是有效 base64: {exc}") from exc
    if len(raw) != 32:
        raise ValueError(f"EncodingAESKey 解码后必须是 32 字节，当前是 {len(raw)}")


def _load_account_config(account_id: str) -> dict[str, str]:
    store_path = PROJECT_ROOT / "runtime-data" / "wechat_accounts.runtime.json"
    if not store_path.exists():
        return {}
    raw_data = json.loads(store_path.read_text(encoding="utf-8"))
    accounts = raw_data.get("accounts", {})
    if not isinstance(accounts, dict):
        return {}
    if account_id:
        config = accounts.get(account_id, {})
        return {key: str(value or "") for key, value in config.items()} if isinstance(config, dict) else {}
    default_account_id = str(raw_data.get("default_account_id") or "")
    if default_account_id and isinstance(accounts.get(default_account_id), dict):
        return {key: str(value or "") for key, value in accounts[default_account_id].items()}
    for config in accounts.values():
        if isinstance(config, dict):
            return {key: str(value or "") for key, value in config.items()}
    return {}


def _get_crypt(token: str, aes_key: str, appid: str):
    crypto_path = PROJECT_ROOT / "utils" / "crypto.py"
    spec = importlib.util.spec_from_file_location("wx_coupon_crypto", crypto_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载微信加解密模块: {crypto_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        if exc.name == "Crypto":
            raise RuntimeError(
                "缺少 pycryptodome 依赖，无法执行安全模式 AES 检查；"
                "请用 ./wechat_check.sh 自动进入 Docker 镜像环境执行，"
                "或在宿主机安装 requirements.txt"
            ) from exc
        raise

    return module.WXBizMsgCrypt(token, aes_key, appid)


def _plain_url(base_url: str, token: str, echostr: str) -> str:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(6)
    query = urllib.parse.urlencode(
        {
            "signature": _signature(token, timestamp, nonce),
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": echostr,
        }
    )
    return base_url.rstrip("/") + "?" + query


def _aes_url(base_url: str, token: str, aes_key: str, appid: str, echostr: str) -> str:
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(6)
    crypt = _get_crypt(token, aes_key, appid)
    encrypted_xml = crypt.encrypt_msg(echostr, nonce, timestamp)
    root = ET.fromstring(encrypted_xml)
    encrypt = root.findtext("Encrypt") or ""
    msg_signature = root.findtext("MsgSignature") or ""
    query = urllib.parse.urlencode(
        {
            "signature": _signature(token, timestamp, nonce),
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": encrypt,
            "encrypt_type": "aes",
            "msg_signature": msg_signature,
        }
    )
    return base_url.rstrip("/") + "?" + query


def _check_result(label: str, url: str, expected: str, timeout: float) -> dict[str, Any]:
    status, body, headers = _fetch_text(url, timeout)
    ok = status == 200 and body == expected
    return {
        "label": label,
        "ok": ok,
        "status": status,
        "body": body,
        "server": headers.get("server", ""),
        "url": url,
    }


def _url_warnings(url: str) -> list[str]:
    warnings: list[str] = []
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname or ""
    scheme = (parsed.scheme or "").lower()
    try:
        ipaddress.ip_address(host)
        warnings.append(
            "当前 URL 使用裸 IP。服务可达不代表微信开发者平台一定允许保存；"
            "若后台提示 invalid args,200002，通常需要改用已备案/已验证域名，"
            "或先保留旧 URL 并在旧服务器反代到新服务器。"
        )
    except ValueError:
        pass
    if scheme == "http":
        warnings.append("当前 URL 使用 HTTP；如果微信后台继续拒绝保存，优先改为域名 + HTTPS。")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url", help="微信后台填写的 URL，例如 http://IP/wechat")
    parser.add_argument("--token", default=os.getenv("WX_WECHAT_CHECK_TOKEN", ""))
    parser.add_argument("--encoding-aes-key", default=os.getenv("WX_WECHAT_CHECK_AES_KEY", ""))
    parser.add_argument("--appid", default=os.getenv("WX_WECHAT_CHECK_APPID", ""))
    parser.add_argument("--account-id", default="")
    parser.add_argument("--timeout", type=float, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    config = _load_account_config(args.account_id)
    token = args.token or config.get("token", "")
    aes_key = args.encoding_aes_key or config.get("encoding_aes_key", "")
    appid = args.appid or config.get("appid", "")
    echostr = "wx_coupon_check_" + secrets.token_hex(4)

    results: list[dict[str, Any]] = []
    try:
        if not token:
            raise ValueError("缺少 Token；请传 --token 或先在 runtime-data 里配置公众号")
        results.append(_check_result("plain", _plain_url(args.url, token, echostr), echostr, args.timeout))
        if aes_key or appid:
            if not aes_key or not appid:
                raise ValueError("安全模式检查需要同时提供 EncodingAESKey 和 AppID")
            _validate_aes_key(aes_key)
            results.append(_check_result("aes", _aes_url(args.url, token, aes_key, appid, echostr), echostr, args.timeout))
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.__class__.__name__, "message": str(exc), "results": results}, ensure_ascii=False))
        else:
            for result in results:
                print(f"WECHAT_CALLBACK_CHECK_{result['label'].upper()} status={result['status']} ok={result['ok']} server={result['server']} body={result['body']!r}")
            print(f"WECHAT_CALLBACK_CHECK_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    ok = all(result["ok"] for result in results)
    warnings = _url_warnings(args.url)
    if args.json:
        print(json.dumps({"ok": ok, "warnings": warnings, "results": results}, ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(f"WECHAT_CALLBACK_CHECK_{result['label'].upper()} status={result['status']} ok={result['ok']} server={result['server']} body={result['body']!r}")
        for warning in warnings:
            print(f"WECHAT_CALLBACK_CHECK_WARN {warning}")
        print("WECHAT_CALLBACK_CHECK_OK" if ok else "WECHAT_CALLBACK_CHECK_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
