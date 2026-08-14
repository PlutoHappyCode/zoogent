"""欠条机制离线自测：不打真实 API，全部 mock"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

import core
from core import engine, scheduler
from core.memory import load_pending, pop_pending, save_pending

AGENT = core.default_agent()
CHAT = "test_pending_chat"
PASS = []


def check(name, cond, detail=""):
    PASS.append((name, cond))
    print(f"  {'✅' if cond else '❌'} {name} {detail}")


def fake_ok_response(text="补发答案"):
    msg = SimpleNamespace(
        content=text, tool_calls=None,
        model_dump=lambda exclude_none=True: {"role": "assistant", "content": text})
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(total_tokens=10))


# ---- 1. 欠条存取 ----
save_pending(AGENT, CHAT, "terminal", "帮我整理周报")
tasks = load_pending(AGENT)
check("save_pending 落盘", len(tasks) == 1 and tasks[0]["text"] == "帮我整理周报")

save_pending(AGENT, CHAT, "terminal", "新任务覆盖旧的")
tasks = load_pending(AGENT)
check("同一 chat 只留最新一张", len(tasks) == 1 and tasks[0]["text"] == "新任务覆盖旧的")

taken = pop_pending(AGENT, CHAT)
check("pop_pending 核销", taken is not None and load_pending(AGENT) == [])
check("pop 空 chat 返回 None", pop_pending(AGENT, "nonexist") is None)

# ---- 2. 模型失败 → 自动记欠条 + 会话撤回 ----
orig_chat = engine.chat_with_retry
engine.chat_with_retry = lambda **kw: None  # 模拟模型持续不可用
answer, meta = core.run_agent_meta(CHAT, "查一下今天的日程", agent=AGENT,
                                   account="terminal")
engine.chat_with_retry = orig_chat

tasks = load_pending(AGENT)
check("失败后自动记欠条", meta.get("pending") is True and len(tasks) == 1
      and tasks[0]["text"] == "查一下今天的日程", f"answer={answer[:20]}…")

session = core.memory.SESSIONS.get(f"{AGENT}:{CHAT}", [])
check("失败消息已从会话撤回",
      not any(m.get("content") == "查一下今天的日程" for m in session))

# ---- 3. 说「继续」→ 核销欠条并重放原文 ----
seen_inputs = []


def spy_chat(**kw):
    for m in reversed(kw["messages"]):
        if m.get("role") == "user":
            seen_inputs.append(m["content"])
            break
    return fake_ok_response("日程已整理好")


engine.chat_with_retry = spy_chat
answer, meta = core.run_agent_meta(CHAT, "继续", agent=AGENT, account="terminal")
engine.chat_with_retry = orig_chat

check("「继续」核销欠条", load_pending(AGENT) == [])
check("「继续」重放原始任务",
      any("查一下今天的日程" in s and "系统" in s for s in seen_inputs),
      f"实际输入={seen_inputs[-1][:40] if seen_inputs else '无'}…")
check("「继续」正常作答", answer == "日程已整理好" and not meta.get("pending"))

# ---- 4. 哨兵：故障留账 → 恢复自动补发 ----
sent = []


class MockChannel:
    fixed_agent = None

    def send(self, chat_id, text):
        sent.append((chat_id, text))

    def get_target_chat(self):
        return CHAT


save_pending(AGENT, CHAT, "mock_account", "哨兵测试任务")

# 哨兵间隔改成 1 秒，让它快速扫一轮
orig_get = scheduler.get
scheduler.get = lambda: {"scheduler": {"sentinel_seconds": 1}}

# 第一轮：模型仍故障 → 留账
engine.chat_with_retry = lambda **kw: None
scheduler.start_sentinel({"mock_account": MockChannel()})
time.sleep(1.8)
check("模型仍故障时哨兵留账", len(load_pending(AGENT)) == 1 and len(sent) == 0)

# 第二轮：模型恢复 → 补发 + 核销
engine.chat_with_retry = lambda **kw: fake_ok_response("哨兵补发的答案")
time.sleep(1.8)
scheduler.get = orig_get
engine.chat_with_retry = orig_chat

check("恢复后哨兵自动补发", len(sent) == 1 and "哨兵补发的答案" in sent[0][1]
      and "补发" in sent[0][1], f"推送到 {sent[0][0][:10] if sent else '无'}")
check("补发后欠条核销", load_pending(AGENT) == [])
check("哨兵重放不污染会话",
      len([m for m in core.memory.SESSIONS.get(f"{AGENT}:{CHAT}", [])
           if m.get("role") == "user" and "哨兵测试任务" in str(m.get("content"))]) <= 1)

# ---- 清理测试残留 ----
pf = Path(core.home()) / AGENT / "memory" / "pending.json"
pf.unlink(missing_ok=True)
core.memory.delete_session(AGENT, CHAT)  # 连内存带磁盘一起清

failed = [n for n, ok in PASS if not ok]
print(f"\n{'=' * 40}\n结果：{len(PASS) - len(failed)}/{len(PASS)} 通过"
      + (f"，失败：{failed}" if failed else " 🎉"))
sys.exit(1 if failed else 0)
