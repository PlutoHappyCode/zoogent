"""
core/engine.py — Agent 主循环
==============================

职责：技能加载、模型调用（带重试）、主循环、斜杠命令、tools.md 生成。
渠道无关：channels/ 只负责收发，所有智能都在这里。

分层：
  channels/     渠道层（飞书/终端，收发 + 渲染）
  core/         Agent 层（循环 + 会话 + 人格 + 记忆）
  AgentsHome/   人格层（所有 Agent 的"家"，独立于代码，可整体备份迁移）
  skills/*.py   技能层（即插即用的工具）
"""

import importlib.util
import json
import re
import time
from pathlib import Path

from openai import APIError, RateLimitError

from .config import BASE_DIR, get
from .log import get_logger
from .memory import (
    MEMORY_FUNCTIONS,
    MEMORY_SCHEMAS,
    _get_session,
    _trim,
    get_chat_agent,
    pop_pending,
    save_pending,
    save_session,
    session_lock,
    set_chat_agent,
    set_current_agent,
)
from .models import agent_model_name, get_client, model_id
from .personas import agent_display, agent_skills, home, list_agents

log = get_logger("engine")


# ===============================================================
# 技能加载器
# ===============================================================

TOOL_ORIGIN: dict[str, str] = {}  # 工具名 → 来源技能文件词干（记忆工具为 "memory"）


def load_skills() -> tuple[list, dict]:
    schemas, functions = [], {}
    skills_dir = Path(get()["tools"]["skills_dir"])
    disabled = set(get()["tools"].get("disabled", []))
    for path in sorted(skills_dir.glob("*.py")):
        if path.name.startswith("_") or path.stem in disabled:
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"skills.{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            skill_funcs = getattr(module, "FUNCTIONS", {})
            schemas.extend(getattr(module, "SCHEMAS", []))
            for name in skill_funcs:
                if name in functions:
                    log.warning("技能 %s 的工具 %s 重名，已跳过", path.name, name)
                    continue
                functions[name] = skill_funcs[name]
                TOOL_ORIGIN[name] = path.stem
            log.info("🧩 技能已加载：%s（%d 个工具）", path.name, len(skill_funcs))
        except Exception as e:
            log.warning("技能 %s 加载失败：%s，已跳过", path.name, e)
    return schemas, functions


TOOL_SCHEMAS, TOOL_FUNCTIONS = load_skills()

if get().get("tools", {}).get("memory_tools", True):
    for s in MEMORY_SCHEMAS:
        name = s["function"]["name"]
        if name not in TOOL_FUNCTIONS:
            TOOL_SCHEMAS.append(s)
    TOOL_FUNCTIONS.update(MEMORY_FUNCTIONS)
    for name in MEMORY_FUNCTIONS:
        TOOL_ORIGIN.setdefault(name, "memory")


def _schemas_for(agent: str) -> list:
    """该人格可见的工具 schema：skills: ["*"] = 全部；
    否则按技能文件名（skills 目录里的 .py 词干）过滤，记忆工具始终可用。"""
    allowed = agent_skills(agent)
    if "*" in allowed:
        return TOOL_SCHEMAS
    keep = set(allowed) | {"memory"}
    return [s for s in TOOL_SCHEMAS
            if TOOL_ORIGIN.get(s["function"]["name"]) in keep]


def write_tools_md() -> None:
    """启动时根据实际加载的工具生成 AgentsHome/shared/tools.md。
    按技能分组，写明每个技能对应的文件位置——
    人看的文档和机器的事实永远一致，不会漂移"""
    skills_dir = Path(get()["tools"]["skills_dir"])
    project_root = BASE_DIR.parent

    def _origin_path(origin: str) -> str:
        if origin == "memory":
            return "agentRunner/core/memory.py 内置"
        p = skills_dir / f"{origin}.py"
        try:
            return str(p.relative_to(project_root))
        except ValueError:
            return str(p)

    groups: dict[str, list] = {}
    for s in TOOL_SCHEMAS:
        origin = TOOL_ORIGIN.get(s["function"]["name"], "?")
        groups.setdefault(origin, []).append(s["function"])

    lines = [
        "# 工具清单（自动生成，请勿手改）",
        "",
        f"> 由 agentRunner 启动时生成，模型：{model_id()}",
        "> 手写的工具使用约定请写在 shared/tools_custom.md",
        "",
        "每个分组 = 一个技能文件；新增技能 = 往 skills 目录丢一个新 .py，重启生效。",
        "",
    ]
    for origin in sorted(groups):
        lines.append(f"## {origin}（{_origin_path(origin)}）")
        for f in groups[origin]:
            params = list(f.get("parameters", {}).get("properties", {}))
            param_str = f"（参数：{', '.join(params)}）" if params else ""
            lines.append(f"- **{f['name']}**{param_str}：{f.get('description', '')}")
        lines.append("")
    try:
        out = home() / "shared" / "tools.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        log.warning("tools.md 生成失败：%s", e)


write_tools_md()  # 工具全部注册完，生成 AgentsHome/shared/tools.md


# ===============================================================
# 模型调用
# ===============================================================
def chat_with_retry(messages: list, tools: list | None = None,
                    retries: int = 6, model_name: str | None = None):
    kwargs = {"model": model_id(model_name), "messages": messages,
              "tools": tools if tools is not None else TOOL_SCHEMAS}
    payload_chars = len(json.dumps(messages, ensure_ascii=False))
    for attempt in range(retries):
        t0 = time.time()
        try:
            resp = get_client(model_name).chat.completions.create(**kwargs)
            log.info("⚡ API %s 第%d轮 %.1fs 输入≈%d字符 输出tokens=%s",
                     kwargs["model"], attempt + 1, time.time() - t0,
                     payload_chars,
                     getattr(getattr(resp, "usage", None),
                             "completion_tokens", "?"))
            return resp
        except (RateLimitError, APIError):
            if attempt == retries - 1:
                return None
            time.sleep(min(2 ** (attempt + 1), 30))
    return None


# ===============================================================
# Agent 主循环
# ===============================================================
def run_agent_meta(chat_id: str, user_input: str,
                   agent: str | None = None, account: str = "unknown",
                   max_steps: int | None = None,
                   image_b64: str | None = None) -> tuple[str, dict]:
    """Agent 主循环，返回 (回答, 元数据)。

    max_steps: None = 从 agent.json 读取（agent 级别 > 全局 > 默认 20）
    """
    agent = agent or get_chat_agent(chat_id)
    with session_lock(agent, chat_id):
        return _run_agent_meta(chat_id, user_input, agent, account,
                               max_steps, image_b64)


def _run_agent_meta(chat_id: str, user_input: str, agent: str,
                    account: str, max_steps: int | None,
                    image_b64: str | None) -> tuple[str, dict]:
    """主循环本体（调用方已持有该会话锁）"""
    set_current_agent(agent)  # 记忆工具靠它知道当前人格
    model_name = agent_model_name(agent)

    if max_steps is None:
        cfg = get()
        agent_cfg = cfg.get("agents", {}).get("members", {}).get(agent, {})
        global_steps = cfg.get("agents", {}).get("max_steps", 20)
        max_steps = agent_cfg.get("max_steps", global_steps)

    tools = _schemas_for(agent)
    t0 = time.time()
    total_tokens = 0
    pending_saved = False

    # 「继续」→ 核销欠条，重放原始任务
    resume_text = None
    if re.match(r"^(继续|continue)\s*$", user_input.strip(), re.I):
        pend = pop_pending(agent, chat_id)
        if pend:
            resume_text = pend["text"]
            user_input = ("[System: this task was interrupted by a model outage; "
                          f"please continue and complete it]\n{pend['text']}")
            log.info("📮 [%s] 收到「继续」，重放欠条：%s", agent, resume_text[:30])

    try:
        messages = _get_session(agent, chat_id)
    except ValueError as e:
        answer = str(e)
    else:
        if image_b64:
            # 视觉消息：OpenAI 标准的 content 数组格式（支持多图）
            images = [image_b64] if isinstance(image_b64, str) else image_b64
            content = [{"type": "text", "text": user_input}]
            content += [{"type": "image_url", "image_url": {
                "url": f"data:image/png;base64,{b}"}} for b in images]
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_input})
        _trim(agent, messages)
        answer = "（处理步骤过多，请换个问法试试）"
        last_call_sig, repeat_count = None, 0
        loop_stuck = False

        for _ in range(max_steps):
            response = chat_with_retry(messages=messages, tools=tools,
                                       model_name=model_name)
            if response is None:
                # 模型持续不可用：把任务记成欠条，恢复后哨兵自动补发
                save_pending(agent, chat_id, account,
                             resume_text or user_input)
                pending_saved = True
                # 撤回没得到回应的用户消息，保持会话干净：
                # 否则哨兵每轮重放都会往历史里塞一条重复消息
                if messages and messages[-1].get("role") == "user":
                    messages.pop()
                answer = ("模型服务暂时不可用 🙏 已把这条任务记在欠条上，"
                          "恢复后我会自动补发；你也可以稍后对我说「继续」。")
                break
            if getattr(response, "usage", None):
                total_tokens += response.usage.total_tokens or 0
            message = response.choices[0].message

            # 截断含 tool_calls 的 assistant 消息内容，节省 token。
            # 模型在 tool_calls 之前常写大量中文推理文本，对后续 tool 调用无用。
            msg_dict = message.model_dump(exclude_none=True)
            if message.tool_calls and msg_dict.get("content"):
                reasoning = str(msg_dict["content"])
                if len(reasoning) > 200:
                    msg_dict["content"] = reasoning[:200] + "… (reasoning omitted)"
            messages.append(msg_dict)

            if not message.tool_calls:
                answer = message.content
                break

            for tc_idx, tool_call in enumerate(message.tool_calls):
                name = tool_call.function.name
                try:
                    args = json.loads(tool_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                # 熔断：同一工具+同参数反复调，说明模型在死循环空转
                # （2026-08-18 授权轮询烧 40 万 token 事故）
                call_sig = f"{name}:{json.dumps(args, sort_keys=True)}"
                if call_sig == last_call_sig:
                    repeat_count += 1
                    if repeat_count >= 2:
                        log.warning("🛑 [%s:%s] 检测到死循环调用 %s，强制终止",
                                    agent, chat_id[:6], name)
                        # 本条及剩余 tool_calls 全部补占位符，否则留下
                        # 孤儿 tool_call，严格校验的 API（kimi）下轮直接 400
                        for tc in message.tool_calls[tc_idx:]:
                            messages.append({
                                "role": "tool", "tool_call_id": tc.id,
                                "content": "(duplicate call detected, circuit-broken, not executed)"})
                        answer = ("我在这个任务上空转了好几圈，先停下了。"
                                  "请换个说法或直接告诉我下一步怎么做。")
                        loop_stuck = True
                        break
                else:
                    repeat_count = 0
                last_call_sig = call_sig
                log.info("🔧 [%s:%s] %s(%s)", agent, chat_id[:6], name, args)
                func = TOOL_FUNCTIONS.get(name)
                try:
                    result = func(**args) if func else f"Error: unknown tool {name}"
                except TypeError as e:
                    result = f"Argument error: {e}; fix and retry"
                messages.append({"role": "tool",
                                 "tool_call_id": tool_call.id,
                                 "content": str(result)})
            if loop_stuck:
                break

        # 历史消毒：图片只服务本轮，换成文本占位符，
        # 不然 base64 留在会话里，之后每轮请求都白烧几千 token
        for m in messages:
            if isinstance(m.get("content"), list):
                texts = [p.get("text", "") for p in m["content"]
                         if p.get("type") == "text"]
                m["content"] = " ".join(texts) + " [image processed]"

    # 会话落盘：重启不丢对话（每轮结束写一次，图片已消毒不占空间）
    save_session(agent, chat_id)

    meta = {"model": model_id(model_name), "tokens": total_tokens,
            "elapsed": round(time.time() - t0, 1), "pending": pending_saved}
    return answer, meta


def run_agent(chat_id: str, user_input: str,
              agent: str | None = None, account: str = "unknown",
              max_steps: int | None = None) -> str:
    """兼容旧调用：只要回答文本（scheduler 等场景用）"""
    return run_agent_meta(chat_id, user_input, agent=agent,
                          account=account, max_steps=max_steps)[0]


def run_proactive(chat_id: str, instruction: str,
                  agent: str | None = None) -> str:
    return run_agent(chat_id, f"[System instruction] {instruction}",
                     agent=agent, account="scheduler")


# ===============================================================
# 斜杠命令：人格管理（渠道无关，渠道只负责收发）
#   /agents          → 列出所有人格
#   /agent <名字>    → 切换当前会话的人格
#   /who             → 当前人格
# ===============================================================
def handle_command(chat_id: str, text: str,
                   fixed_agent: str | None = None) -> str | None:
    """是命令则处理并返回回复文本；不是命令返回 None。
    fixed_agent 非 None = 固定人格账号：禁止切换人格"""
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if fixed_agent:
        display = agent_display(fixed_agent)
        if cmd in ("/agents", "/agent"):
            return (f"我是固定人格 {display}，无需切换；"
                    "要换人去找对应机器人")
        if cmd == "/who":
            return f"当前人格：{display}"
        return "可用命令：/who"

    if cmd in ("/agents", "/agent") and not arg:
        current = get_chat_agent(chat_id)
        lines = [f"{'👉 ' if a == current else '   '}{a}" for a in list_agents()]
        return ("可用人格：\n" + "\n".join(lines) +
                f"\n\n当前：{current}\n切换：/agent <名字>")
    if cmd == "/agent" and arg:
        try:
            set_chat_agent(chat_id, arg)
            return f"已切换到人格「{arg}」✅\n（用 /agents 查看所有人格）"
        except ValueError as e:
            return str(e)
    if cmd == "/who":
        return f"当前人格：{get_chat_agent(chat_id)}"
    return "可用命令：/agents、/agent <名字>、/who"
