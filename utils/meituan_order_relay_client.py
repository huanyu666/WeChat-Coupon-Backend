from __future__ import annotations

import json
from typing import Any

from utils import http_client
from utils.meituan_order_relay_pool import (
    list_order_relay_candidates,
    report_order_relay_result,
)


class OrderRelayError(http_client.RequestException):
    """订单 Relay 已响应但未能完成上游请求。"""

    def __init__(self, message: str, *, error_code: str = "order_relay_error", retryable: bool = True):
        super().__init__(message)
        self.error_code = str(error_code or "order_relay_error")
        self.retryable = bool(retryable)


class OrderRelayUnavailable(OrderRelayError):
    """所有订单 Relay 节点均不可用。"""

    def __init__(self, message: str = "订单中转节点不可用，请稍后重试"):
        super().__init__(message, error_code="order_relay_unavailable", retryable=True)


class OrderRelayResponse:
    def __init__(self, *, status_code: int, data: Any, relay_node: dict[str, Any]):
        self.status_code = int(status_code or 0)
        self._data = data
        self.relay_node = dict(relay_node or {})
        self.headers: dict[str, str] = {"content-type": "application/json"}
        self.text = json.dumps(data, ensure_ascii=False)
        self.content = self.text.encode("utf-8")

    def json(self) -> Any:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise http_client.HTTPError(
                f"订单上游返回 HTTP {self.status_code}",
                response=self,
            )


def _error_text(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        return str(payload.get("detail") or payload.get("error") or payload.get("message") or fallback).strip()
    return fallback


async def request_meituan_order_via_relay(
    *,
    operation: str,
    params: dict[str, Any] | None = None,
    form: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[OrderRelayResponse, dict[str, Any]]:
    config, candidates = list_order_relay_candidates()
    if not candidates:
        raise OrderRelayUnavailable("订单中转节点未配置或均已停用")

    failures: list[str] = []
    for node in candidates:
        relay_url = str(node.get("url") or "").strip().rstrip("/")
        if not relay_url:
            continue
        request_headers = {"Content-Type": "application/json"}
        secret = str(node.get("secret") or "").strip()
        if secret:
            request_headers["X-Order-Relay-Secret"] = secret
        try:
            response = await http_client.post(
                relay_url,
                timeout=max(3, int(node.get("timeout_seconds") or config.get("request_timeout_seconds") or 15)),
                headers=request_headers,
                json={
                    "operation": str(operation or "").strip(),
                    "params": dict(params or {}),
                    "form": dict(form or {}),
                    "headers": dict(headers or {}),
                    "relay_options": dict(node.get("relay_options") or {}),
                },
            )
            status_code = int(getattr(response, "status_code", 0) or 0)
            try:
                payload = response.json()
            except Exception as exc:
                raise RuntimeError("订单 Relay 响应不是 JSON") from exc
            if status_code != 200 or not isinstance(payload, dict):
                raise RuntimeError(_error_text(payload, f"订单 Relay 返回 HTTP {status_code}"))
            if not bool(payload.get("success")):
                error_code = str(payload.get("error_code") or "order_relay_error").strip()
                message = _error_text(payload, "订单 Relay 请求失败")
                # Relay 能正常返回代理或上游失败，不能直接当作挂机宝节点离线。
                report_order_relay_result(node, success=True)
                raise OrderRelayError(message, error_code=error_code, retryable=bool(payload.get("retryable", True)))
            upstream_status = int(payload.get("upstream_status_code") or 0)
            data = payload.get("data")
            if upstream_status <= 0 or not isinstance(data, (dict, list)):
                raise RuntimeError("订单 Relay 响应结构异常")
            report_order_relay_result(node, success=True)
            return OrderRelayResponse(status_code=upstream_status, data=data, relay_node=node), node
        except OrderRelayError:
            raise
        except Exception as exc:
            message = str(exc).strip() or exc.__class__.__name__
            report_order_relay_result(node, success=False, error=message)
            failures.append(f"{str(node.get('name') or relay_url)}: {message[:120]}")

    suffix = f"（已尝试 {len(failures)} 个节点）" if failures else ""
    raise OrderRelayUnavailable(f"订单中转节点不可用，请稍后重试{suffix}")
