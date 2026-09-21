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

# ---- 2.5 4xx（400）：不重试、不记欠条、撤回消息、明确告知 ----
import httpx  # noqa: E402
from openai import BadRequestError  # noqa: E402

CHAT4 = CHAT + "_4xx"


def _raise_400(**kw):
    """构造真实的 BadRequestError（openai 2.x 需要 httpx.Response）"""
    req = httpx.Request("POST", "https://x/v1/chat/completions")
    resp = httpx.Response(400, request=req, json={"error": {"message": "bad"}})
    raise BadRequestError("bad request", response=resp, body=None)


engine.chat_with_retry = _raise_400
answer4, meta4 = core.run_agent_meta(CHAT4, "这条请求不合法", agent=AGENT,
                                     account="terminal")
engine.chat_with_retry = orig_chat

_p4 = load_pending(AGENT)
check("4xx 不记欠条、不留待重放",
      meta4.get("pending") is not True
      and not any(t.get("chat_id") == CHAT4 for t in _p4),
      f"pending={meta4.get('pending')} 欠条数={len(_p4)}（仍只留上一节那张）")
check("4xx 告知用户重试也没用", "重试也没用" in answer4, f"answer={answer4[:24]}…")
check("4xx 撤回本轮 user 消息",
      not any(m.get("content") == "这条请求不合法"
              for m in core.memory.SESSIONS.get(f"{AGENT}:{CHAT4}", [])),
      f"会话 {len(core.memory.SESSIONS.get(f'{AGENT}:{CHAT4}', []))} 条")
core.memory.delete_session(AGENT, CHAT4)
from core.sessionLog import log_file as _log_file  # noqa: E402
_log_file(AGENT, CHAT4).unlink(missing_ok=True)

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
      any("查一下今天的日程" in s and "[System:" in s for s in seen_inputs),
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

# ---- 5. 单条欠条异常不连坐（坏账在前，好账在后仍要补发） ----
BAD_AGENT, GOOD_AGENT = "analyst", "housekeeper"  # list_agents() 按字母序，analyst 在前
BAD_CHAT, GOOD_CHAT = "test_pending_bad", "test_pending_good"
_orig_meta = engine.run_agent_meta


def _flaky_meta(chat_id, text, **kw):
    if "坏账" in text:
        raise RuntimeError("模拟单条欠条炸掉")
    return "兜底补发答案", {"pending": False}


try:
    save_pending(BAD_AGENT, BAD_CHAT, "mock_account", "坏账任务")
    save_pending(GOOD_AGENT, GOOD_CHAT, "mock_account", "好账任务")
    engine.run_agent_meta = _flaky_meta
    sent.clear()
    time.sleep(1.8)  # 哨兵线程（interval=1s）扫一轮
    engine.run_agent_meta = _orig_meta
    good_ok = len(load_pending(GOOD_AGENT)) == 0 and any(
        GOOD_CHAT == c and "兜底补发答案" in t for c, t in sent)
    check("单条欠条异常不连坐：坏账留账、好账照常补发",
          good_ok and len(load_pending(BAD_AGENT)) == 1,
          f"好账补发={good_ok} 坏账留账={len(load_pending(BAD_AGENT))}")
finally:
    engine.run_agent_meta = _orig_meta
    for _a, _c in ((BAD_AGENT, BAD_CHAT), (GOOD_AGENT, GOOD_CHAT)):
        pop_pending(_a, _c)
        core.memory.delete_session(_a, _c)

# ---- 清理测试残留 ----
pf = Path(core.home()) / AGENT / "memory" / "pending.json"
pf.unlink(missing_ok=True)
core.memory.delete_session(AGENT, CHAT)  # 连内存带磁盘一起清

failed = [n for n, ok in PASS if not ok]
print(f"\n{'=' * 40}\n结果：{len(PASS) - len(failed)}/{len(PASS)} 通过"
      + (f"，失败：{failed}" if failed else " 🎉"))
sys.exit(1 if failed else 0)
