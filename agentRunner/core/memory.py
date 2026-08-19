"""
core/memory.py — 记忆工具 + 会话管理
=====================================

两部分：
  1. 内置记忆工具（memory_list/read/write/search）：
     通过线程本地变量知道"当前是哪个 Agent"，
     每个 Agent 只能碰自己的 memory/ 目录（隔离原则）
  2. 会话管理：key = "人格:chat_id"，超 session.max_history 时
     先摘要存 memory/summary.md 再裁剪，不暴力砍头
"""

import json
import re
import threading
import time
from pathlib import Path

from .config import BASE_DIR, get
from .log import get_logger
from .models import get_client, model_id
from .personas import _agent_dir, _memory_dir, build_system_prompt, default_agent

log = get_logger("memory")

MEMORY_DIR = BASE_DIR / "memory"        # 运行状态（会话绑定等）
MEMORY_DIR.mkdir(exist_ok=True)

_local = threading.local()


def set_current_agent(agent: str) -> None:
    """engine 跑主循环前调用：记忆工具靠它知道当前人格"""
    _local.agent = agent


def current_agent() -> str:
    """当前线程正在服务的人格（飞书技能等需要按人格取凭证时用）"""
    return getattr(_local, "agent", default_agent())


def _current_memory_dir() -> Path:
    agent = getattr(_local, "agent", default_agent())
    return _memory_dir(agent)


def _safe_join(filename: str) -> Path | None:
    """防路径穿越：禁止 ../ 逃出 memory 目录"""
    base = _current_memory_dir().resolve()
    target = (base / filename).resolve()
    return target if str(target).startswith(str(base)) else None


def memory_list() -> str:
    mem_dir = _current_memory_dir()
    files = sorted(p.relative_to(mem_dir).as_posix()
                   for p in mem_dir.rglob("*.md"))
    if not files:
        return "记忆目录是空的"
    return f"共 {len(files)} 个记忆文件：\n" + "\n".join(f"- {f}" for f in files)


def memory_read(filename: str) -> str:
    path = _safe_join(filename)
    if path is None:
        return f"读取失败：{filename} 不是合法的记忆文件路径"
    if not path.exists():
        return f"记忆文件 {filename} 不存在，先用 memory_list 查看现有文件"
    return path.read_text(encoding="utf-8")[:4000]


def memory_write(filename: str, content: str, mode: str = "append") -> str:
    if not filename.endswith(".md"):
        return "保存失败：记忆文件必须是 .md 格式"
    path = _safe_join(filename)
    if path is None:
        return f"保存失败：{filename} 不是合法路径"
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode == "overwrite":
        path.write_text(content, encoding="utf-8")
    else:
        with path.open("a", encoding="utf-8") as f:
            f.write(("\n" if path.exists() else "") + content)
    return f"已{'覆盖' if mode == 'overwrite' else '追加'}写入记忆：{filename}"


def memory_write_batch(writes_json: str) -> str:
    """批量写入：一次调用写多个记忆文件，省去多轮 API 往返。
    writes_json 格式：'[{"filename": "进行中.md", "content": "...", "mode": "append"}, ...]'
    mode 可省略，默认 append"""
    try:
        writes = json.loads(writes_json)
        if not isinstance(writes, list) or not writes:
            raise ValueError("writes_json 必须是非空 JSON 数组")
    except (json.JSONDecodeError, ValueError) as e:
        return f"writes_json 解析失败：{e}"
    results = []
    for w in writes[:10]:  # 单次最多 10 个文件，防失控
        if not isinstance(w, dict) or "filename" not in w or "content" not in w:
            results.append(f"跳过非法条目：{str(w)[:80]}")
            continue
        results.append(memory_write(w["filename"], str(w["content"]),
                                    w.get("mode", "append")))
    return "\n".join(results)


def memory_search(keyword: str) -> str:
    mem_dir = _current_memory_dir()
    hits = []
    for p in sorted(mem_dir.rglob("*.md")):
        rel = p.relative_to(mem_dir).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if keyword in line:
                hits.append(f"{rel}:{i}: {line.strip()[:80]}")
                if len(hits) >= 20:
                    break
    if not hits:
        return f"记忆里没有找到「{keyword}」"
    return f"找到 {len(hits)} 条相关记忆：\n" + "\n".join(hits)


MEMORY_SCHEMAS = [
    {"type": "function", "function": {
        "name": "memory_list", "description": "列出自己长期记忆目录里的所有文件",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "memory_read", "description": "读取一个记忆文件的内容",
        "parameters": {"type": "object", "properties": {
            "filename": {"type": "string", "description": "相对记忆目录的路径，如「进行中.md」「learnings/errors.md」"}},
            "required": ["filename"]}}},
    {"type": "function", "function": {
        "name": "memory_write", "description": "写入记忆文件（更新任务看板、记录错误/表扬时使用）",
        "parameters": {"type": "object", "properties": {
            "filename": {"type": "string", "description": "相对记忆目录的 .md 路径"},
            "content": {"type": "string", "description": "写入内容"},
            "mode": {"type": "string", "enum": ["append", "overwrite"],
                     "description": "append 追加（默认）/ overwrite 覆盖", "default": "append"}},
            "required": ["filename", "content"]}}},
    {"type": "function", "function": {
        "name": "memory_write_batch",
        "description": "批量写入多个记忆文件。要写多个文件时必须用这个（一次调用搞定），禁止连续多次调 memory_write",
        "parameters": {"type": "object", "properties": {
            "writes_json": {"type": "string", "description":
                "JSON 数组字符串：[{\"filename\": \"进行中.md\", \"content\": \"...\", \"mode\": \"append\"}, ...]，mode 可省略默认 append"}},
            "required": ["writes_json"]}}},
    {"type": "function", "function": {
        "name": "memory_search", "description": "在长期记忆里按关键词全文搜索",
        "parameters": {"type": "object", "properties": {
            "keyword": {"type": "string"}},
            "required": ["keyword"]}}},
]

MEMORY_FUNCTIONS = {
    "memory_list": memory_list,
    "memory_read": memory_read,
    "memory_write": memory_write,
    "memory_write_batch": memory_write_batch,
    "memory_search": memory_search,
}


# ===============================================================
# 会话与路由：key = "人格:chat_id"，同一会话可切换人格
# ===============================================================
SESSIONS: dict[str, list] = {}
_SESSION_FP: dict[str, tuple] = {}  # 会话 key → 人格文件指纹（热加载用）
BINDINGS_FILE = MEMORY_DIR / "chatAgents.json"

# --- 并发保护 ----------------------------------------------------
# 飞书每条消息一个线程，同一 chat 连发两条会并发跑主循环，
# 不加锁会向同一个 messages list 交错 append，污染会话历史。
# 两层锁：
#   _SESSIONS_LOCK   —— 只护 SESSIONS / _SESSION_FP / _session_locks 字典本身
#   每会话一把锁      —— engine 主循环全程持有，同一（人格+chat）串行处理
_SESSIONS_LOCK = threading.Lock()
_session_locks: dict[str, threading.Lock] = {}


def session_lock(agent: str, chat_id: str) -> threading.Lock:
    """取某会话的互斥锁（engine 主循环用它串行同一 chat 的消息处理）"""
    key = f"{agent}:{chat_id}"
    with _SESSIONS_LOCK:
        return _session_locks.setdefault(key, threading.Lock())


def _persona_fingerprint(agent: str) -> tuple:
    """参与组装 system prompt 的全部文件的 (路径, mtime) 指纹：
    soul/rules + shared/*.md + 共享踩坑 + memory/ 下所有 .md（记忆索引）。
    任何一个文件变了，指纹就变 → 会话里的 system prompt 要刷新"""
    files: list[Path] = []
    try:
        agent_dir = _agent_dir(agent)
        files += [agent_dir / "soul.md", agent_dir / "rules.md"]
        shared = agent_dir.parent / "shared"
        if shared.exists():
            files += sorted(shared.glob("*.md"))
            files.append(shared / "learnings" / "errors.md")
        files += sorted(_memory_dir(agent).rglob("*.md"))
    except (ValueError, OSError):
        pass
    return tuple((str(p), p.stat().st_mtime if p.exists() else 0.0)
                 for p in files)


def _max_history() -> int:
    return int(get().get("session", {}).get("max_history", 30))


def _summary_enabled() -> bool:
    return bool(get().get("session", {}).get("summary", True))


def _persist_enabled() -> bool:
    """session.persist = false 可关掉会话落盘（默认开）"""
    return bool(get().get("session", {}).get("persist", True))


# ---------------------------------------------------------------
# 会话落盘：每个（人格+chat）一个 json 文件，重启不丢对话。
# 存 agentRunner/memory/sessions/<agent>--<chat_id>.json：
#   {"messages": [...], "fp": 人格文件指纹}
# 每轮回答结束由 engine 调 save_session 写一次；重启后 _get_session
# 优先从磁盘恢复，指纹变了顺带热加载 system prompt。
# ---------------------------------------------------------------
SESSIONS_DIR = MEMORY_DIR / "sessions"


def _session_file(agent: str, chat_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", chat_id)
    return SESSIONS_DIR / f"{agent}--{safe}.json"


def _sanitize_tool_pairs(messages: list) -> list:
    """工具配对消毒：assistant 的每个 tool_call 必须有对应 tool 响应。
    中断/崩溃可能留下缺响应的 tool_call，qwen 宽容但 kimi 严格校验 → 400，
    缺哪个补哪个占位符，保住上下文不丢（2026-08-18 管家狗 kimi 400 事故）"""
    out = []
    for i, m in enumerate(messages):
        out.append(m)
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        ids = [tc.get("id") for tc in m["tool_calls"] if tc.get("id")]
        # 后面连续 tool 消息已覆盖的 id 不补
        covered = set()
        idx = i + 1
        while idx < len(messages) and messages[idx].get("role") == "tool":
            covered.add(messages[idx].get("tool_call_id"))
            idx += 1
        for tc_id in ids:
            if tc_id not in covered:
                out.append({"role": "tool", "tool_call_id": tc_id,
                            "content": "（工具响应缺失，已忽略）"})
    return out


def _load_session_file(agent: str, chat_id: str) -> tuple[list, tuple] | None:
    """从磁盘恢复会话。文件不存在/损坏返回 None，不炸主流程"""
    f = _session_file(agent, chat_id)
    if not f.exists():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        messages = data.get("messages")
        if not isinstance(messages, list) or not messages:
            return None
        fp = tuple(tuple(x) for x in data.get("fp", ()))
        return _sanitize_tool_pairs(messages), fp
    except (json.JSONDecodeError, OSError):
        return None


def save_session(agent: str, chat_id: str) -> None:
    """把内存里的会话写盘（engine 每轮结束调用，调用方持有会话锁）"""
    if not _persist_enabled():
        return
    key = f"{agent}:{chat_id}"
    messages = SESSIONS.get(key)
    if not messages:
        return
    try:
        SESSIONS_DIR.mkdir(exist_ok=True)
        fp = _SESSION_FP.get(key, ())
        _session_file(agent, chat_id).write_text(
            json.dumps({"messages": messages,
                        "fp": [list(x) for x in fp]}, ensure_ascii=False),
            encoding="utf-8")
    except OSError as e:
        log.warning("[%s] 会话落盘失败：%s", agent, e)


def delete_session(agent: str, chat_id: str) -> None:
    """连内存带磁盘一起删（测试清理用）"""
    key = f"{agent}:{chat_id}"
    with _SESSIONS_LOCK:
        SESSIONS.pop(key, None)
        _SESSION_FP.pop(key, None)
    _session_file(agent, chat_id).unlink(missing_ok=True)


def _load_bindings() -> dict:
    if BINDINGS_FILE.exists():
        return json.loads(BINDINGS_FILE.read_text(encoding="utf-8"))
    return {}


# chatAgents.json 的读改写全程一把锁：/agent 切换与消息路由并发时不丢更新
_BINDINGS_LOCK = threading.Lock()


def get_chat_agent(chat_id: str) -> str:
    with _BINDINGS_LOCK:
        return _load_bindings().get(chat_id, default_agent())


def set_chat_agent(chat_id: str, agent: str) -> None:
    _agent_dir(agent)  # 校验存在
    with _BINDINGS_LOCK:
        bindings = _load_bindings()
        bindings[chat_id] = agent
        BINDINGS_FILE.write_text(
            json.dumps(bindings, ensure_ascii=False, indent=2),
            encoding="utf-8")


SUMMARY_PREFIX = "【前情提要】"


def _summary_file(agent: str) -> Path:
    return _memory_dir(agent) / "summary.md"


def _get_session(agent: str, chat_id: str) -> list:
    key = f"{agent}:{chat_id}"
    fp = _persona_fingerprint(agent)
    # 调用方（engine 主循环）已持有该会话锁，这里的字典操作是安全的
    if key not in SESSIONS:
        loaded = _load_session_file(agent, chat_id) if _persist_enabled() else None
        if loaded is not None:
            # 重启恢复：磁盘会话直接接着用；指纹变了说明停机期间
            # 人格/记忆有改动 → 顺手热加载 system prompt
            messages, saved_fp = loaded
            if saved_fp != fp:
                messages[0] = {"role": "system",
                               "content": build_system_prompt(agent)}
                log.info("🔄 [%s] 停机期间人格/记忆有更新，已热加载进恢复的会话",
                         agent)
            SESSIONS[key] = messages
            _SESSION_FP[key] = fp
            log.info("💾 [%s] 已从磁盘恢复会话（%d 条历史）", agent, len(messages))
            return SESSIONS[key]
        messages = [{"role": "system", "content": build_system_prompt(agent)}]
        # 有历史摘要就带上"前情提要"：重启/换会话也能接住上次的话题
        summary_file = _summary_file(agent)
        if summary_file.exists():
            summary = summary_file.read_text(encoding="utf-8").strip()
            if summary:
                messages.append({"role": "system",
                                 "content": f"{SUMMARY_PREFIX}{summary}"})
        SESSIONS[key] = messages
        _SESSION_FP[key] = fp
    elif _SESSION_FP.get(key) != fp:
        # 热加载：人格/共享/记忆文件有改动 → 原地刷新 system prompt，
        # 对话历史（含前情提要）原样保留，改 soul.md 不用重启
        SESSIONS[key][0] = {"role": "system",
                            "content": build_system_prompt(agent)}
        _SESSION_FP[key] = fp
        log.info("🔄 [%s] 人格/记忆文件有更新，已热加载进会话", agent)
    return SESSIONS[key]


def _summarize(old_summary: str, dropped: list) -> str | None:
    """把要被裁掉的旧对话合并进摘要。失败返回 None（降级为暴力裁剪）"""
    lines = []
    for m in dropped:
        role = m.get("role", "?")
        content = m.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        if role == "tool":
            content = content[:100]
        lines.append(f"{role}: {content[:200]}")
    prompt = (
        f"已有前情提要：{old_summary or '（无）'}\n\n"
        f"新增对话片段：\n" + "\n".join(lines) + "\n\n"
        "请把前情提要更新为一段不超过 300 字的中文摘要，保留："
        "用户偏好、任务进展、关键结论、未完成的约定。只输出摘要本身。"
    )
    try:
        resp = get_client().chat.completions.create(
            model=model_id(), messages=[{"role": "user", "content": prompt}])
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.warning("会话摘要失败（%s），降级为直接裁剪", e)
        return None


def _safe_cut(messages: list, cut: int) -> int:
    """裁切点消毒：不能落在工具调用组中间。
    assistant(tool_calls) 与其后的 tool 响应是一个整体，要么全裁要么全留；
    否则会话里留下孤儿 tool 消息 → API 每次 400 → 会话永久污染
    （2026-07-30 欠条死循环事故根因）"""
    while cut < len(messages):
        m = messages[cut]
        if m.get("role") == "tool":
            cut += 1  # 孤儿 tool 响应（配对 assistant 已被裁），跟着裁掉
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            n = len(m["tool_calls"])
            following = messages[cut + 1:cut + 1 + n]
            if (len(following) == n
                    and all(f.get("role") == "tool" for f in following)):
                break  # 配对完整，可以从这里开始保留
            cut += 1  # 配对不完整，把这条 assistant 也一起裁掉
            continue
        break
    return cut


def _compress_stale_tool_results(messages: list, keep_rounds: int = 2) -> None:
    """压缩陈旧 tool 结果：除最近 keep_rounds 轮外，超长 tool content
    截断到 200 字符。模型只需要最新工具结果的全文，旧的看个摘要就够
    （实测 tool 结果占会话 41%，是 token 膨胀主因之一）"""
    # 从后往前数 user 消息的边界，确定"最近 N 轮"的起点
    user_seen = 0
    cutoff = 0
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_seen += 1
            if user_seen > keep_rounds:
                cutoff = i + 1
                break
    for m in messages[1:cutoff]:  # 不动 messages[0]（system prompt）
        if m.get("role") == "tool":
            content = str(m.get("content", ""))
            if len(content) > 200:
                m["content"] = content[:200] + "…（旧结果已压缩）"


def _trim(agent: str, messages: list) -> None:
    """会话超长时：先把旧对话摘要存档（memory/summary.md），再裁剪。
    替代暴力砍头 —— agent 不会"聊着聊着忘了开头" """
    _compress_stale_tool_results(messages)
    max_history = _max_history()
    overflow = len(messages) - (max_history + 1)
    if overflow <= 0:
        return
    # 第 2 条如果是前情提要，它也要参与更新，不直接保留
    has_summary = (len(messages) > 1
                   and messages[1].get("role") == "system"
                   and str(messages[1].get("content", "")).startswith(SUMMARY_PREFIX))
    head = 2 if has_summary else 1
    old_summary = (str(messages[1]["content"])[len(SUMMARY_PREFIX):]
                   if has_summary else "")
    cut = _safe_cut(messages, head + overflow)
    dropped = messages[head:cut]
    kept = messages[cut:]
    new_summary = _summarize(old_summary, dropped) if _summary_enabled() else None
    if new_summary:
        _summary_file(agent).write_text(new_summary, encoding="utf-8")
        messages[:] = ([messages[0],
                        {"role": "system",
                         "content": f"{SUMMARY_PREFIX}{new_summary}"}]
                       + kept)
        log.info("📝 会话摘要已更新（裁掉 %d 条旧消息）", len(dropped))
    else:
        messages[:] = [messages[0]] + kept


# ===============================================================
# 欠条队列：模型持续失败时把任务落盘，恢复后补发
# ===============================================================
# pending.json 放在 agent 的 memory/ 目录，但模型不可见
# （记忆索引只列 *.md，pending.json 不会被注入 prompt）
#
#   写入：run_agent_meta 连续重试仍失败时
#   核销：用户对同一 chat 说「继续」，或哨兵探测模型恢复后补发成功


def _pending_file(agent: str) -> Path:
    return _memory_dir(agent) / "pending.json"


# pending.json 的读改写全程一把锁：哨兵补发核销和用户说「继续」可能并发，
# 不加锁会双核销（同一任务补发两次）或丢更新
_PENDING_LOCK = threading.Lock()


def load_pending(agent: str) -> list[dict]:
    """读欠条列表。文件不存在或损坏都返回空列表，不炸主流程"""
    with _PENDING_LOCK:
        return _load_pending_unlocked(agent)


def _load_pending_unlocked(agent: str) -> list[dict]:
    """不加锁版本：仅供已持有 _PENDING_LOCK 的内部函数调用"""
    f = _pending_file(agent)
    if not f.exists():
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_pending(agent: str, chat_id: str, account: str, text: str) -> None:
    """记一张欠条。同一 chat_id 只留最新一张（旧任务被新任务覆盖，
    避免故障期间堆积一屏过期任务）"""
    with _PENDING_LOCK:
        tasks = [t for t in _load_pending_unlocked(agent)
                 if t.get("chat_id") != chat_id]
        tasks.append({"chat_id": chat_id, "account": account,
                      "text": text, "ts": int(time.time())})
        try:
            _pending_file(agent).write_text(
                json.dumps(tasks, ensure_ascii=False, indent=2),
                encoding="utf-8")
            log.info("📌 [%s] 已记欠条（%s…）：%s", agent, chat_id[:6], text[:30])
        except OSError as e:
            log.warning("[%s] 欠条写入失败：%s", agent, e)


def pop_pending(agent: str, chat_id: str) -> dict | None:
    """取出并核销某 chat 的欠条；没有则返回 None（检查和核销原子完成）"""
    with _PENDING_LOCK:
        tasks = _load_pending_unlocked(agent)
        keep = [t for t in tasks if t.get("chat_id") != chat_id]
        if len(keep) == len(tasks):
            return None
        taken = next(t for t in tasks if t.get("chat_id") == chat_id)
        try:
            f = _pending_file(agent)
            if keep:
                f.write_text(json.dumps(keep, ensure_ascii=False, indent=2),
                             encoding="utf-8")
            else:
                f.unlink(missing_ok=True)
        except OSError as e:
            log.warning("[%s] 欠条核销失败：%s", agent, e)
        return taken
