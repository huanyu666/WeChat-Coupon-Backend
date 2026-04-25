from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse


BASE_DIR = Path(__file__).resolve().parent
MTGSIG_SCRIPT_PATH = BASE_DIR / "mtgsig_v4.2.0.js"


class MeituanMtgsigError(RuntimeError):
    pass


def _header_get(headers: Mapping[str, Any], name: str, default: str = "") -> str:
    lowered_name = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered_name:
            return str(value)
    return default


def build_browser_env(
    *,
    headers: Mapping[str, Any],
    cookie: str,
    fallback_url: str = "https://h5.waimai.meituan.com/",
) -> Dict[str, Any]:
    referer = _header_get(headers, "Referer", fallback_url).strip() or fallback_url
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        parsed = urlparse(fallback_url)
        referer = fallback_url

    user_agent = _header_get(headers, "User-Agent").strip()
    accept_language = _header_get(headers, "Accept-Language", "zh-CN,zh;q=0.9")
    languages = [
        segment.split(";", 1)[0].strip()
        for segment in accept_language.split(",")
        if segment.strip()
    ] or ["zh-CN"]
    primary_language = languages[0]

    return {
        "location": {
            "href": referer,
            "origin": f"{parsed.scheme}://{parsed.netloc}",
            "protocol": f"{parsed.scheme}:",
            "host": parsed.netloc,
            "hostname": parsed.hostname or parsed.netloc,
            "pathname": parsed.path or "/",
            "search": f"?{parsed.query}" if parsed.query else "",
            "hash": f"#{parsed.fragment}" if parsed.fragment else "",
        },
        "document": {
            "cookie": cookie,
            "referrer": referer,
            "URL": referer,
            "documentURI": referer,
            "baseURI": referer,
        },
        "navigator": {
            "appCodeName": "Mozilla",
            "appName": "Netscape",
            "appVersion": user_agent,
            "userAgent": user_agent,
            "language": primary_language,
            "languages": languages,
            "platform": "Linux armv8l",
            "vendor": "Google Inc.",
            "product": "Gecko",
            "hardwareConcurrency": 8,
            "maxTouchPoints": 5,
        },
        "screen": {
            "width": 390,
            "height": 844,
            "availWidth": 390,
            "availHeight": 844,
            "colorDepth": 24,
            "pixelDepth": 24,
        },
        "history": {
            "length": 1,
            "state": None,
        },
    }


async def generate_mtgsig(
    *,
    url: str,
    method: str,
    body: str,
    cookie: str,
    env: Dict[str, Any],
    headers: Optional[Mapping[str, Any]] = None,
    request_options: Optional[Mapping[str, Any]] = None,
    timeout: float = 8.0,
) -> str:
    request_headers = dict(headers or {})
    if cookie:
        request_headers["Cookie"] = cookie

    payload = {
        "url": url,
        "method": method.upper(),
        "type": method.upper(),
        "data": body,
        "cookie": cookie,
        "headers": request_headers,
        "contentType": _header_get(request_headers, "Content-Type"),
        "accept": _header_get(request_headers, "Accept"),
        "env": {
            **env,
            "document": {
                **dict(env.get("document") or {}),
                "cookie": cookie,
            },
        },
    }
    if request_options:
        payload.update(dict(request_options))

    try:
        process = await asyncio.create_subprocess_exec(
            "node",
            str(MTGSIG_SCRIPT_PATH),
            json.dumps(payload, ensure_ascii=False),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise MeituanMtgsigError("node 未安装或不可执行") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise MeituanMtgsigError("生成 mtgsig 超时") from exc

    output = stdout.decode("utf-8", errors="replace").strip()
    error_output = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise MeituanMtgsigError(error_output or output or "mtgsig 脚本执行失败")
    if not output:
        raise MeituanMtgsigError("mtgsig 脚本未返回结果")

    try:
        json.loads(output)
    except json.JSONDecodeError as exc:
        raise MeituanMtgsigError(f"mtgsig 输出不是 JSON: {output[:160]}") from exc

    return output
