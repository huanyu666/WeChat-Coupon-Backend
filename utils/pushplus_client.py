from __future__ import annotations

import asyncio
import time
from typing import Any

from utils import http_client
from utils.system_settings_store import load_system_settings_store, normalize_pushplus_config


PUSHPLUS_BASE_URL = "https://www.pushplus.plus"


class PushPlusError(RuntimeError):
    def __init__(self, message: str, *, code: int = 0, retryable: bool = True):
        super().__init__(message)
        self.code = int(code or 0)
        self.retryable = bool(retryable)


class PushPlusClient:
    def __init__(self):
        self._access_key = ""
        self._access_key_expires_at = 0.0
        self._access_key_lock: asyncio.Lock | None = None

    def reset_access_key(self) -> None:
        self._access_key = ""
        self._access_key_expires_at = 0.0

    def get_access_key_status(self) -> dict[str, Any]:
        remaining = max(0, int(self._access_key_expires_at - time.time()))
        return {
            "cached": bool(self._access_key and remaining > 0),
            "expires_in": remaining,
        }

    def get_config(self) -> dict[str, Any]:
        return normalize_pushplus_config(load_system_settings_store().get("pushplus_config", {}))

    @staticmethod
    def _parse_response(response: Any) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception as exc:
            raise PushPlusError("PushPlus 返回了非 JSON 响应", retryable=True) from exc
        if not isinstance(payload, dict):
            raise PushPlusError("PushPlus 返回结构异常", retryable=True)
        return payload

    @staticmethod
    def _payload_code(payload: dict[str, Any], response: Any) -> int:
        try:
            return int(payload.get("code") if payload.get("code") is not None else response.status_code)
        except (TypeError, ValueError):
            return int(getattr(response, "status_code", 0) or 0)

    @staticmethod
    def _error_from_payload(payload: dict[str, Any], response: Any) -> PushPlusError:
        code = PushPlusClient._payload_code(payload, response)
        message = str(payload.get("msg") or payload.get("message") or f"PushPlus 请求失败 ({code})").strip()
        retryable = code not in {400, 401, 403, 805, 900, 903, 905}
        return PushPlusError(message, code=code, retryable=retryable)

    def _require_credentials(self) -> dict[str, Any]:
        config = self.get_config()
        if not config.get("platform_token") or not config.get("secret_key"):
            raise PushPlusError("PushPlus 平台 token 或 secretKey 尚未配置", code=400, retryable=False)
        return config

    async def get_access_key(self, *, force_refresh: bool = False) -> str:
        now = time.time()
        if not force_refresh and self._access_key and self._access_key_expires_at - now > 300:
            return self._access_key
        if self._access_key_lock is None:
            self._access_key_lock = asyncio.Lock()
        async with self._access_key_lock:
            now = time.time()
            if not force_refresh and self._access_key and self._access_key_expires_at - now > 300:
                return self._access_key
            config = self._require_credentials()
            try:
                response = await http_client.request(
                    "POST",
                    f"{PUSHPLUS_BASE_URL}/api/common/openApi/getAccessKey",
                    json={
                        "token": config["platform_token"],
                        "secretKey": config["secret_key"],
                    },
                    timeout=15,
                    stateless_cookies=True,
                )
            except Exception as exc:
                raise PushPlusError(f"获取 PushPlus AccessKey 失败: {exc}", retryable=True) from exc
            payload = self._parse_response(response)
            code = self._payload_code(payload, response)
            if code != 200:
                raise self._error_from_payload(payload, response)
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            access_key = str(data.get("accessKey") or "").strip()
            if not access_key:
                raise PushPlusError("PushPlus 未返回 AccessKey", retryable=True)
            try:
                expires_in = int(data.get("expiresIn") or data.get("expireIn") or 7200)
            except (TypeError, ValueError):
                expires_in = 7200
            self._access_key = access_key
            self._access_key_expires_at = time.time() + max(300, expires_in)
            return access_key

    async def _open_api_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in range(2):
            access_key = await self.get_access_key(force_refresh=attempt > 0)
            try:
                response = await http_client.request(
                    method,
                    f"{PUSHPLUS_BASE_URL}{path}",
                    headers={"access-key": access_key, "Accept": "application/json"},
                    params=params,
                    json=json_body,
                    timeout=15,
                    stateless_cookies=True,
                )
            except Exception as exc:
                raise PushPlusError(f"PushPlus 开放接口请求失败: {exc}", retryable=True) from exc
            payload = self._parse_response(response)
            code = self._payload_code(payload, response)
            if code == 200:
                return payload
            if code in {302, 401} and attempt == 0:
                self.reset_access_key()
                continue
            raise self._error_from_payload(payload, response)
        raise PushPlusError("PushPlus AccessKey 刷新后仍不可用", code=401, retryable=False)

    async def get_personal_qr_code(self, *, content: str, seconds: int = 600, scan_count: int = 1) -> str:
        config = self.get_config()
        params: dict[str, Any] = {
            "content": content,
            "second": max(60, min(int(seconds), 2592000)),
            "scanCount": max(1, min(int(scan_count), 999)),
        }
        if config.get("app_id"):
            params["appId"] = config["app_id"]
        payload = await self._open_api_request(
            "GET",
            "/api/open/friend/getQrCode",
            params=params,
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        qr_image_url = str(data.get("qrCodeImgUrl") or "").strip()
        if not qr_image_url:
            raise PushPlusError("PushPlus 未返回二维码图片地址", retryable=True)
        return qr_image_url

    async def delete_friend(self, friend_id: str) -> None:
        if not str(friend_id or "").strip():
            raise PushPlusError("缺少 PushPlus friendId", code=400, retryable=False)
        await self._open_api_request(
            "GET",
            "/api/open/friend/deleteFriend",
            params={"friendId": str(friend_id).strip()},
        )

    async def send_message(
        self,
        *,
        title: str,
        content: str,
        friend_token: str = "",
    ) -> dict[str, Any]:
        config = self.get_config()
        platform_token = str(config.get("platform_token") or "").strip()
        if not platform_token:
            raise PushPlusError("PushPlus 平台 token 尚未配置", code=903, retryable=False)
        body: dict[str, Any] = {
            "token": platform_token,
            "title": str(title or "平台通知")[:100],
            "content": str(content or ""),
            "template": "markdown",
            "channel": "wechat",
        }
        public_base_url = str(config.get("public_base_url") or "").strip().rstrip("/")
        callback_secret = str(config.get("callback_secret") or "").strip()
        if public_base_url and callback_secret:
            body["callbackUrl"] = f"{public_base_url}/api/pushplus/callback/{callback_secret}"
        if friend_token:
            body["to"] = str(friend_token).strip()
        try:
            response = await http_client.request(
                "POST",
                f"{PUSHPLUS_BASE_URL}/send",
                json=body,
                timeout=20,
                stateless_cookies=True,
            )
        except Exception as exc:
            raise PushPlusError(f"PushPlus 发送请求失败: {exc}", retryable=True) from exc
        payload = self._parse_response(response)
        code = self._payload_code(payload, response)
        if code != 200:
            raise self._error_from_payload(payload, response)

        data = payload.get("data")
        short_code = ""
        if isinstance(data, str):
            short_code = data.strip()
        elif isinstance(data, dict):
            short_code = str(data.get("shortCode") or data.get("short_code") or "").strip()
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and str(item.get("channel") or "wechat") == "wechat":
                    short_code = str(item.get("shortCode") or "").strip()
                    break
        return {
            "code": code,
            "message": str(payload.get("msg") or "请求已受理"),
            "short_code": short_code,
            "raw_data": data,
        }

    async def get_send_result(self, short_code: str) -> dict[str, Any]:
        payload = await self._open_api_request(
            "GET",
            "/api/open/message/sendMessageResult",
            params={"shortCode": str(short_code or "").strip()},
        )
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        try:
            status = int(data.get("status") if data.get("status") is not None else -1)
        except (TypeError, ValueError):
            status = -1
        return {
            "status": status,
            "error_message": str(data.get("errorMessage") or "").strip(),
            "update_time": str(data.get("updateTime") or "").strip(),
        }


_client = PushPlusClient()


def get_pushplus_client() -> PushPlusClient:
    return _client
