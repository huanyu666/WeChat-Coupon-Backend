"""
Offline-only helpers for running async code from legacy sync entrypoints.
"""
import asyncio


def run_async_legacy_only(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("在线代码禁止使用同步兼容入口，请改用 async 方法")
