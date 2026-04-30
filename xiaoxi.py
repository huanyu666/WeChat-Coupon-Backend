"""Deprecated legacy script.

This project now serves WeChat callbacks exclusively through the FastAPI app
defined in ``main.py`` and the ``/wechat`` route set in ``routes/wechat.py``.
"""

from __future__ import annotations

import sys


DEPRECATION_MESSAGE = (
    "xiaoxi.py 已退役，不再作为受支持的微信回调入口。"
    "请使用 main.py 提供的 FastAPI 服务和 /wechat 路由。"
)


def main() -> int:
    print(DEPRECATION_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
