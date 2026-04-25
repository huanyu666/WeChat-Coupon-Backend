from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
import time
import urllib.parse
import zlib
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from utils import http_client
from utils.proxy_utils import PROXY_API_CONFIG


ERROR_MESSAGES = {
    "206": "ip数量用完",
    "210": "需要添加白名单",
    "406": "提取间隔太快",
    "215": "单次提取数量超过上限",
}
PROXY_API_HEADERS = {
    "Accept": "application/json,text/plain,*/*",
    "Accept-Encoding": "identity",
    "User-Agent": "wx-service-proxy-test/1.0",
}


def build_proxy_api_url(args: argparse.Namespace) -> str:
    raw_api_url = str(args.api_url or PROXY_API_CONFIG.get("api_url") or "").strip()
    if not raw_api_url:
        raise ValueError("代理 API URL 为空")

    parsed = urllib.parse.urlparse(raw_api_url)
    query_params = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    query_params["format"] = "json"
    query_params["number"] = str(args.number)
    query_params["QTY"] = str(args.number)
    query_params["Split"] = str(args.split)
    query_params.pop("split", None)
    order_id = str(query_params.get("orderId") or "").strip()
    if order_id:
        query_params["orderld"] = order_id
    query_params.pop("city", None)
    query_params.pop("ISP", None)
    query_params.pop("province", None)

    return urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query_params)))


def extract_proxy_urls(payload: Dict[str, Any]) -> List[str]:
    status = str(payload.get("status") or "").strip()
    if status.lower() != "success":
        raise RuntimeError(f"代理 API 状态失败: status={status or 'empty'} payload={payload}")

    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"代理 API 未返回可用 data: payload={payload}")

    proxy_urls: List[str] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_ip = str(item.get("IP") or "").strip()
        if not raw_ip or ":" not in raw_ip:
            continue
        proxy_urls.append(f"http://{raw_ip}")

    if not proxy_urls:
        raise RuntimeError(f"代理 API data 中没有有效 IP: payload={payload}")
    return proxy_urls


def parse_proxy_api_json_response(response: http_client.Response) -> Dict[str, Any]:
    raw_bytes = bytes(getattr(response, "content", b"") or b"")
    raw_text = raw_bytes.decode("latin1", errors="ignore").strip()
    if raw_text in ERROR_MESSAGES:
        raise RuntimeError(f"代理 API 错误码 {raw_text}: {ERROR_MESSAGES[raw_text]}")

    raw_candidates: List[bytes] = []
    for candidate in (
        raw_bytes,
        try_gzip_decompress(raw_bytes),
        try_zlib_decompress(raw_bytes, gzip_wrapper=True),
        try_zlib_decompress(raw_bytes, gzip_wrapper=False),
    ):
        if not candidate:
            continue
        if candidate not in raw_candidates:
            raw_candidates.append(candidate)

    candidate_texts: List[str] = []
    for raw_candidate in raw_candidates:
        decoded_error = raw_candidate.decode("latin1", errors="ignore").strip()
        if decoded_error in ERROR_MESSAGES:
            raise RuntimeError(f"代理 API 错误码 {decoded_error}: {ERROR_MESSAGES[decoded_error]}")
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
            try:
                decoded = raw_candidate.decode(encoding).strip()
            except Exception:
                continue
            if decoded and decoded not in candidate_texts:
                candidate_texts.append(decoded)

    for decoded in candidate_texts:
        if decoded in ERROR_MESSAGES:
            raise RuntimeError(f"代理 API 错误码 {decoded}: {ERROR_MESSAGES[decoded]}")
        try:
            payload = json.loads(decoded)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload

    headers = getattr(response, "headers", {}) or {}
    raise RuntimeError(
        "代理 API 返回非 JSON: "
        f"content_type={headers.get('content-type', '')!r} "
        f"content_encoding={headers.get('content-encoding', '')!r} "
        f"raw={raw_bytes[:300]!r}"
    )


def try_gzip_decompress(raw_bytes: bytes) -> bytes:
    try:
        return gzip.decompress(raw_bytes)
    except Exception:
        return b""


def try_zlib_decompress(raw_bytes: bytes, gzip_wrapper: bool) -> bytes:
    wbits = (16 + zlib.MAX_WBITS) if gzip_wrapper else zlib.MAX_WBITS
    try:
        return zlib.decompress(raw_bytes, wbits)
    except Exception:
        return b""


async def fetch_proxy_batch(args: argparse.Namespace) -> Dict[str, Any]:
    url = build_proxy_api_url(args)
    print("=== 代理 API 请求 URL ===")
    print(url)
    print()

    response = await http_client.get(
        url,
        timeout=args.api_timeout,
        headers=PROXY_API_HEADERS,
    )
    response.raise_for_status()
    payload = parse_proxy_api_json_response(response)

    print("=== 代理 API 返回 JSON ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print()
    return payload


async def test_same_proxy_reuse(proxy_url: str, args: argparse.Namespace) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    proxies = {"http": proxy_url, "https": proxy_url}
    started_at = time.time()
    finished_at = started_at + float(args.duration_seconds)
    attempt = 0

    while True:
        now = time.time()
        if now > finished_at and attempt > 0:
            break
        attempt += 1
        result: Dict[str, Any] = {
            "attempt": attempt,
            "ok": False,
            "status_code": None,
            "error": "",
            "body_preview": "",
            "elapsed_seconds": round(now - started_at, 2),
        }
        try:
            response = await http_client.get(
                args.test_url,
                proxies=proxies,
                timeout=args.request_timeout,
                allow_redirects=True,
            )
            result["status_code"] = getattr(response, "status_code", None)
            body_text = response.text.strip()
            result["body_preview"] = body_text[:300]
            result["ok"] = 200 <= int(response.status_code) < 400
        except Exception as exc:
            result["error"] = f"{exc.__class__.__name__}: {str(exc).strip()}"
        results.append(result)
        print(
            f"[{proxy_url}] attempt={result['attempt']} elapsed={result['elapsed_seconds']}s "
            f"ok={result['ok']} status={result['status_code']} error={result['error']}"
        )
        if time.time() >= finished_at:
            break
        if not result["ok"]:
            break
        await asyncio.sleep(float(args.interval_seconds))
    return results


def print_reuse_report(proxy_url: str, results: List[Dict[str, Any]]) -> None:
    print("=== 同一个代理持续可用性测试 ===")
    print(f"proxy = {proxy_url}")
    for item in results:
        print(
            f"attempt={item['attempt']} elapsed={item['elapsed_seconds']}s ok={item['ok']} "
            f"status={item['status_code']} error={item['error']}"
        )
        if item["body_preview"]:
            print(f"body={item['body_preview']}")
    print()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="一次性测试 ydaili JSON 多代理提取，并持续验证同一个代理可用多久。"
    )
    parser.add_argument("--api-url", default="", help="代理 API URL，默认读取 utils.proxy_utils.PROXY_API_CONFIG")
    parser.add_argument("--number", type=int, default=5, help="提取代理数量，同时写入 number 和 QTY")
    parser.add_argument("--split", default="3", help="自定义分隔符参数，同时写入 Split 和 split")
    parser.add_argument("--test-url", default="https://httpbin.org/ip", help="通过代理访问的测试 URL")
    parser.add_argument("--interval-seconds", type=float, default=5.0, help="每次请求之间的间隔秒数")
    parser.add_argument("--duration-seconds", type=float, default=300.0, help="单个代理持续测试总时长秒数")
    parser.add_argument("--api-timeout", type=float, default=8.0, help="代理 API 请求超时秒数")
    parser.add_argument("--request-timeout", type=float, default=8.0, help="代理测试请求超时秒数")
    parser.add_argument("--proxy-index", type=int, default=0, help="从第几个代理开始顺序测试，从 0 开始")
    return parser


async def main_async() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()

    payload = await fetch_proxy_batch(args)
    proxy_urls = extract_proxy_urls(payload)

    print("=== 提取到的代理列表 ===")
    for idx, proxy_url in enumerate(proxy_urls):
        print(f"[{idx}] {proxy_url}")
    print()

    if args.proxy_index < 0 or args.proxy_index >= len(proxy_urls):
        raise RuntimeError(f"--proxy-index 越界: {args.proxy_index}, 可选范围 0..{len(proxy_urls) - 1}")

    all_results: List[Dict[str, Any]] = []
    tested_proxy_count = 0
    for target_proxy in proxy_urls[args.proxy_index:]:
        tested_proxy_count += 1
        print(f"=== 开始测试代理 [{tested_proxy_count}] {target_proxy} ===")
        results = await test_same_proxy_reuse(target_proxy, args)
        all_results.extend(
            {
                **item,
                "proxy_url": target_proxy,
            }
            for item in results
        )
        print_reuse_report(target_proxy, results)
        if results and results[-1]["ok"]:
            print(f"=== 代理 {target_proxy} 已测满 {args.duration_seconds} 秒，切换到下一个代理 ===")
        else:
            print(f"=== 代理 {target_proxy} 已失败，切换到下一个代理 ===")

    success_count = sum(1 for item in all_results if item["ok"])
    print("=== 总结 ===")
    print(f"requested_number={args.number}")
    print(f"api_reported_number={payload.get('number')}")
    print(f"remaining={payload.get('Remaining')}")
    print(f"left_time={payload.get('left_time')}")
    print(f"tested_proxy_count={tested_proxy_count}")
    print(f"duration_seconds={args.duration_seconds}")
    print(f"interval_seconds={args.interval_seconds}")
    print(f"reuse_success={success_count}/{len(all_results)}")
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main_async()))
    finally:
        try:
            asyncio.run(http_client.aclose())
        except Exception:
            pass
