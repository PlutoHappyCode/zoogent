"""并发保护验证：多线程同时压 pending.json / chat_agents.json / 会话锁。

用法：../.venv/bin/python tests/test_concurrency.py
不 mock 模型，只压共享状态；跑完清理临时数据。
"""
import sys
import threading
import time
from pathlib import Path

AGENT_RUNNER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_RUNNER))
os_environ = __import__("os").environ
__import__("os").chdir(AGENT_RUNNER)

import core  # noqa: E402
from core.memory import (  # noqa: E402
    SESSIONS, load_pending, pop_pending, save_pending, session_lock,
    set_chat_agent, get_chat_agent, _pending_file,
)

AGENT = "housekeeper"
THREADS = 16
ROUNDS = 50
CHAT = "stress_chat"


def stress_pending() -> bool:
    """16 线程同时 save/pop 同一 chat 的欠条，文件不损坏、最终状态一致"""
    for _ in range(ROUNDS):
        def worker(i):
            save_pending(AGENT, CHAT, "stress", f"任务{i}")
            load_pending(AGENT)
            pop_pending(AGENT, CHAT)
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
    # 全部 pop 完，文件应为空或不存在
    return load_pending(AGENT) == []


def stress_bindings() -> bool:
    """16 线程同时切换人格绑定，文件始终可解析、不丢到损坏"""
    agents = ["housekeeper", "finance", "cto", "analyst"]
    def worker(i):
        set_chat_agent(CHAT, agents[i % len(agents)])
        get_chat_agent(CHAT)
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS * 4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return get_chat_agent(CHAT) in agents


def stress_session_lock() -> bool:
    """同一会话锁：两个线程同时持有期间互斥，不交错"""
    lock = session_lock(AGENT, CHAT)
    order = []
    def worker(name):
        with lock:
            order.append(f"{name}-enter")
            time.sleep(0.01)
            order.append(f"{name}-exit")
    ts = [threading.Thread(target=worker, args=(n,)) for n in "AB"]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    # 互斥则顺序必然是 enter→exit、enter→exit（不交错）
    return order in (["A-enter", "A-exit", "B-enter", "B-exit"],
                     ["B-enter", "B-exit", "A-enter", "A-exit"])


results = [
    ("pending.json 并发读写不损坏", stress_pending()),
    ("chat_agents.json 并发读写不损坏", stress_bindings()),
    ("同一会话锁互斥（无交错）", stress_session_lock()),
]
for name, ok in results:
    print(f"{'✅' if ok else '❌'} {name}")

# 清理压力测试残留
p = _pending_file(AGENT)
if p.exists():
    p.unlink()
from core.memory import delete_session  # noqa: E402
delete_session(AGENT, CHAT)
from core.memory import BINDINGS_FILE, _load_bindings  # noqa: E402
b = _load_bindings()
b.pop(CHAT, None)
BINDINGS_FILE.write_text(__import__("json").dumps(b, ensure_ascii=False, indent=2),
                         encoding="utf-8")

passed = sum(1 for _, ok in results if ok)
print(f"\n结果：{passed}/{len(results)} 通过")
sys.exit(0 if passed == len(results) else 1)
