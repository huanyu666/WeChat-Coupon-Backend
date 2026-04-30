"""Deprecated legacy helper.

The supported WeChat callback implementation lives in ``routes/wechat.py`` and
uses runtime account settings instead of standalone token-based scripts.
"""

from __future__ import annotations

import sys


DEPRECATION_MESSAGE = (
    "text_processors/xiaoxi.py 已退役，不再作为受支持入口。"
    "请使用 routes/wechat.py 中的统一微信回调链路。"
)


def main() -> int:
    print(DEPRECATION_MESSAGE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
