"""
channels/feishu/_loop_proxy.py — 事件循环兼容层
=================================================

修复 lark-oapi ws 客户端的模块级共享 event loop 问题。
lark_oapi.ws.client 在 import 时创建了一个全局 loop，所有 ws.Client
实例共用。多账号各起一个线程时，先到的线程把 loop 跑起来后，其余线程
再调 run_until_complete 就报 "This event loop is already running"。
把模块全局 loop 换成线程本地代理：每个渠道线程拿到自己独立的 loop。
"""

import asyncio
import threading
import time

import lark_oapi.ws.client as _ws_client

_LOOPS: list = []
_LOOPS_LOCK = threading.Lock()


class _ThreadLoopProxy:
    """把 run_until_complete / create_task 分发到调用线程自己的 event loop"""

    def __init__(self) -> None:
        self._local = threading.local()

    def _loop(self) -> asyncio.AbstractEventLoop:
        loop = getattr(self._local, "loop", None)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._local.loop = loop
            with _LOOPS_LOCK:
                _LOOPS.append(loop)
        return loop

    def run_until_complete(self, coro):
        return self._loop().run_until_complete(coro)

    def create_task(self, coro):
        return self._loop().create_task(coro)


_ws_client.loop = _ThreadLoopProxy()


def shutdown_event_loops(wait: float = 1.5) -> None:
    """优雅退出：取消所有渠道 loop 上的挂起任务（SDK 的缓存清理任务等），
    让 Ctrl+C / kill 时不再刷 'Task was destroyed but it is pending'"""
    with _LOOPS_LOCK:
        loops = [l for l in _LOOPS if not l.is_closed()]

    def _cancel_all(loop) -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for loop in loops:
        try:
            loop.call_soon_threadsafe(_cancel_all, loop)
        except RuntimeError:
            pass
    if loops:
        time.sleep(wait)
