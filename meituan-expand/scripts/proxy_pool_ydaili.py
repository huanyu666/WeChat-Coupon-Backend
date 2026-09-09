#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""易代理(ydaili) 提取与冷却池。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from curl_cffi import requests as cr


@dataclass
class ProxyPool:
    extract_api: str
    cooldown_seconds: float = 180.0
    extract_interval_ms: int = 800
    proxy_auth: str = ""
    _last_extract_ts: float = 0.0
    _cooldown_until: Dict[str, float] = field(default_factory=dict)

    def _throttle_extract(self) -> None:
        gap = max(0.0, self.extract_interval_ms / 1000.0)
        now = time.time()
        wait = self._last_extract_ts + gap - now
        if wait > 0:
            time.sleep(wait)

    def _normalize(self, line: str) -> Optional[str]:
        line = (line or "").strip()
        if not line:
            return None
        bad = ("白名单", "不足", "错误", "error", "ERROR", "失败", "过期", "无效", "次数")
        if any(x in line for x in bad):
            return None
        if "://" in line:
            return line
        if ":" not in line:
            return None
        # host:port or user:pass@host:port
        auth = (self.proxy_auth or "").strip()
        if auth and "@" not in line:
            return "http://" + auth + "@" + line
        return "http://" + line

    def fetch_one(self, timeout: float = 12.0) -> str:
        """GET 提取 API，返回 http://host:port。

        优先用 requests/urllib：部分代理商提取域名对 curl_cffi 会超时。
        """
        self._throttle_extract()
        text = ""
        status = 0
        last_err: Optional[Exception] = None
        # 1) requests
        try:
            import requests as _rq

            r = _rq.get(self.extract_api, timeout=timeout)
            status = r.status_code
            text = (r.text or "").strip()
        except Exception as e:
            last_err = e
            # 2) urllib
            try:
                import urllib.request

                req = urllib.request.Request(
                    self.extract_api, headers={"User-Agent": "Mozilla/5.0"}
                )
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    status = getattr(resp, "status", 200) or 200
                    text = resp.read().decode("utf-8", "replace").strip()
            except Exception as e2:
                last_err = e2
                # 3) curl_cffi fallback
                try:
                    r = cr.get(self.extract_api, timeout=timeout, impersonate="chrome124")
                    status = r.status_code
                    text = (r.text or "").strip()
                except Exception as e3:
                    self._last_extract_ts = time.time()
                    raise RuntimeError(f"提取失败: {e3}") from e3
        self._last_extract_ts = time.time()
        if status and status != 200:
            raise RuntimeError(f"提取HTTP {status}: {text[:200]}")
        if not text and last_err:
            raise RuntimeError(f"提取为空: {last_err}")
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        if not lines:
            raise RuntimeError(f"提取为空: {text[:200]}")
        # JSON error payloads from some providers
        if lines[0].startswith("{"):
            raise RuntimeError(f"提取不可用: {lines[0][:200]}")
        proxy = self._normalize(lines[0])
        if not proxy:
            raise RuntimeError(f"提取不可用: {lines[0][:200]}")
        return proxy

    def is_cooling(self, proxy: str) -> bool:
        until = self._cooldown_until.get(proxy, 0.0)
        return time.time() < until

    def cool(self, proxy: str, seconds: Optional[float] = None) -> None:
        sec = self.cooldown_seconds if seconds is None else seconds
        self._cooldown_until[proxy] = time.time() + max(1.0, sec)

    def acquire(self, max_tries: int = 5) -> str:
        """提取一条未在冷却中的代理。"""
        last_err = None
        for _ in range(max(1, max_tries)):
            try:
                proxy = self.fetch_one()
            except Exception as e:
                last_err = e
                time.sleep(0.6)
                continue
            if self.is_cooling(proxy):
                # 极速池几乎不会马上重复，仍保守再提
                time.sleep(0.3)
                continue
            return proxy
        raise RuntimeError(f"无法获取可用代理: {last_err}")


def load_pool_from_config(cfg: dict) -> ProxyPool:
    api = (cfg.get("extract_api") or "").strip()
    if not api:
        raise ValueError("config.extract_api 为空")
    return ProxyPool(
        extract_api=api,
        cooldown_seconds=float(cfg.get("cooldown_seconds") or 180),
        extract_interval_ms=int(cfg.get("extract_interval_ms") or 800),
        proxy_auth=str(cfg.get("proxy_auth") or ""),
    )
