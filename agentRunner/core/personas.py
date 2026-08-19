"""
core/personas.py — 人格层：AgentsHome 加载
==========================================

AgentsHome 结构：
  shared/                  → 全队共享信息（user.md 用户画像、tools.md 工具文档、
                             glossary.md 等领域知识、learnings/ 共享踩坑——
                             小文件正文注入，大文件列名）
  <agent_id>/soul.md       → 身份（id/name/emoji/性格/职责）
  <agent_id>/rules.md      → 后天工作原则（可选）
  <agent_id>/cron.md       → 声明式定时任务（scheduler 扫描）
  <agent_id>/memory/       → 长期记忆（索引注入 + 工具按需读取）
  <agent_id>/journal/      → 日记归档
  <agent_id>/inbox/        → 信箱（多 agent 协作，占位）

启用规则：agents.members 未列出的 agent 默认启用；
"enabled": false 的人格从 list_agents 结果过滤（磁盘目录不动）。
"""

import re
from pathlib import Path

from .config import get


def home() -> Path:
    """AgentsHome 根目录"""
    return Path(get()["agents"]["home"])


def workspace_root() -> Path:
    """工作区根目录（交付物输出），默认 AgentsHome 同级 Zootopia/homework"""
    return Path(get()["agents"].get("workspace", "../Zootopia/homework"))


def agent_workspace(agent: str) -> Path:
    """每个 agent 的工作区目录：<workspace_root>/<agent_id>，
    可在 agents.members.<id>.workspace 里覆盖；首次访问自动创建"""
    custom = _member_cfg(agent).get("workspace")
    d = Path(custom) if custom else workspace_root() / agent
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_agent() -> str:
    return get()["agents"].get("default", "housekeeper")


def _member_cfg(agent: str) -> dict:
    return get().get("agents", {}).get("members", {}).get(agent, {})


def is_enabled(agent: str) -> bool:
    return _member_cfg(agent).get("enabled", True)


def agent_skills(agent: str) -> list[str]:
    """该人格允许的技能文件列表（["*"] = 全部）"""
    return _member_cfg(agent).get("skills", ["*"])


def list_agents() -> list[str]:
    """扫描 AgentsHome 下的 <agent_id>/ 目录，返回所有已启用人格 id"""
    root = home()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and (p / "soul.md").exists()
                  and is_enabled(p.name))


def agent_display(agent: str) -> str:
    """人格的展示名（emoji 在前）：优先读 soul.md 身份卡的
    name/emoji 字段，兜底用首行标题；都没有就 🤖 + id"""
    try:
        text = (_agent_dir(agent) / "soul.md").read_text(encoding="utf-8")
    except (ValueError, OSError):
        return f"🤖 {agent}"
    name_m = re.search(r"\*\*name\*\*[:：]\s*(.+)", text)
    emoji_m = re.search(r"\*\*emoji\*\*[:：]\s*(\S+)", text)
    name = name_m.group(1).strip() if name_m else ""
    if not name:
        first = text.splitlines()[0] if text else ""
        name = first.lstrip("# ").replace("SOUL ·", "").split("·")[0].strip() or agent
    if emoji_m:
        return f"{emoji_m.group(1)} {name}"
    m = re.search(r"[\U0001F000-\U0001FAFF☀-➿]", name)
    if m:
        clean = (name[:m.start()] + name[m.end():]).strip()
        return f"{m.group(0)} {clean}"
    return f"🤖 {name}"


def _agent_dir(agent: str) -> Path:
    d = home() / agent
    if not d.exists() or not is_enabled(agent):
        raise ValueError(f"未知人格「{agent}」，可用：{list_agents()}")
    return d


def _memory_dir(agent: str) -> Path:
    d = _agent_dir(agent) / "memory"
    d.mkdir(exist_ok=True)
    return d


def _read_home_file(rel: str) -> str:
    """读 AgentsHome 根级文件，不存在返回空串"""
    p = home() / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _shared_knowledge() -> str:
    """shared/ 共享知识：小文件正文注入，大文件只列名（tools.md 是
    自动生成的工具文档，不注入）"""
    shared_dir = home() / "shared"
    if not shared_dir.exists():
        return ""
    parts = []
    for p in sorted(shared_dir.glob("*.md")):
        if p.name in ("tools.md", "user.md", "userBrief.md"):
            continue  # user.md/userBrief.md 单独注入；tools.md 是给人看的自动文档
        text = p.read_text(encoding="utf-8")
        if len(text) <= 3000:
            parts.append(text.strip())
        else:
            parts.append(f"（{p.name}：{len(text)} 字符，内容过长未注入）")
    return "\n\n".join(parts)


def _shared_capabilities() -> str:
    """共享能力边界（shared/capabilities.md）"""
    cap = _read_home_file("shared/capabilities.md")
    return cap or ""


def _prompt_lean() -> bool:
    """system prompt 瘦身模式开关（agent.json → prompt.lean，默认开）。
    关掉即回到全量注入的 classic 版，观察期发现问题可一行配置回滚"""
    return bool(get().get("prompt", {}).get("lean", True))


def _user_brief() -> str:
    """精简用户画像（shared/userBrief.md：几行摘要 + open_id 映射）。
    全文留在 shared/user.md 由 kb_search 按需检索；brief 缺失时降级全量"""
    return _read_home_file("shared/userBrief.md") or _read_home_file("shared/user.md")


def _memory_index_lean(agent: str) -> str:
    """限长记忆索引：只列顶层 .md + 子目录文件数（如 journal/ 32 篇）。
    替代全量文件清单——列表会随记忆增长无限变长，详情交给 memory_list"""
    mem_dir = _memory_dir(agent)
    top_files = sorted(p.name for p in mem_dir.iterdir()
                       if p.is_file() and p.suffix == ".md"
                       and p.name != "summary.md")
    sub_dirs = []
    for d in sorted(mem_dir.iterdir()):
        if not d.is_dir() or d.name == "archive":
            continue
        n = sum(1 for _ in d.rglob("*.md"))
        if n:
            sub_dirs.append(f"- {d.name}/（{n} 篇，用 memory_list 查看）")
    lines = [f"- {f}" for f in top_files] + sub_dirs
    return "\n".join(lines) if lines else "（空）"


def build_system_prompt(agent: str) -> str:
    """提示词组装（两种模式，prompt.lean 切换）：
    lean   —— 瘦身版：画像摘要化、共享知识/踩坑进知识库按需检索、
              记忆索引限长、格式规则压成禁止清单（常驻层 ~700 tokens）
    classic —— 全量注入版（观察期回滚用）"""
    if _prompt_lean():
        return _build_prompt_lean(agent)
    return _build_prompt_classic(agent)


def _build_prompt_lean(agent: str) -> str:
    agent_dir = _agent_dir(agent)
    # 极简注入版：soul+rules 的手工压缩合并版（约 45%），缺失时降级全量
    lean_md = agent_dir / "lean.md"
    if lean_md.exists():
        identity = lean_md.read_text(encoding="utf-8")
    else:
        soul = (agent_dir / "soul.md").read_text(encoding="utf-8")
        rules = _read_home_file(f"{agent}/rules.md")
        identity = f"{soul}\n\n{rules}"

    return f"""[User Profile · Brief]
{_user_brief()}
(Full background via kb_search, e.g. kb_search("用户画像 职业经历"))

{identity}

[Workspace]
- Home (identity/memory): {agent_dir}
- Default output dir (deliverables/temp files): {agent_workspace(agent)}

[Knowledge & Lessons]
Shared knowledge (glossary, full user profile), lessons learned and past reports live in the knowledge base. Search on demand:
- Unsure about Feishu API, deployment, file formats: kb_search("踩坑 " + keyword) first
- Past reports/materials: kb_search; existing method for current task: kb_search("技能 " + task name)

{_shared_capabilities()}

[Long-term Memory] (index only; read details via tools)
{_memory_index_lean(agent)}
Read on demand via memory_read / memory_search; update files via memory_write when task progress changes.

[Rules]
1. Never fabricate facts: always call tools for checkable facts (quotes/memory/history); verify time-sensitive info via web_search first
2. Formatting: bold conclusion in the first sentence; use ### headings (≤4 lines each) for multi-dimension content; "-" for parallel items, "1. 2. 3." for steps; avoid tables
3. Numbered options sparingly: only when user choice or next-step recommendation is needed, end with `1.` `2.` `3.` lines (rendered as buttons); no emoji numbers, no forced options, never say "I can't send buttons"
4. After substantial exchanges (tasks/decisions/new preferences), append key points to memory/journal/<today>.md before ending; write directly without reading first
5. Writing multiple memory files (e.g. journal + task board + good): use one memory_write_batch call; never chain multiple memory_write calls
6. Reply in Chinese, mobile-friendly, no fluff
"""


def _build_prompt_classic(agent: str) -> str:
    agent_dir = _agent_dir(agent)
    user = _read_home_file("shared/user.md")
    soul = (agent_dir / "soul.md").read_text(encoding="utf-8")
    rules = _read_home_file(f"{agent}/rules.md")
    shared_errors = _read_home_file("shared/learnings/errors.md")
    shared_knowledge = _shared_knowledge()

    mem_dir = _memory_dir(agent)
    mem_files = sorted(p.relative_to(mem_dir).as_posix()
                       for p in mem_dir.rglob("*.md")
                       if not p.relative_to(mem_dir).as_posix().startswith("archive/"))
    mem_index = "\n".join(f"- {f}" for f in mem_files) if mem_files else "（空）"

    return f"""[User Profile] (shared)
{user}

{soul}

{rules}

[Workspace]
- Home (identity/memory): {_agent_dir(agent)}
- Default output dir (deliverables/temp files): {agent_workspace(agent)}

[Shared Knowledge] (shared/)
{shared_knowledge}

[Shared Lessons] (learn from predecessors, don't repeat)
{shared_errors}

---
[Long-term Memory Index] (read via memory_read / memory_search on demand;
update via memory_write when task progress changes)
{mem_index}

[Rules]
- Always call tools for checkable facts (quotes, memory); never fabricate
- Verify time-sensitive info (news/announcements/product updates) via web_search first;
  find past materials/reports via kb_search
- Reply in Chinese, mobile-friendly

[Journal]
- After substantial exchanges (tasks, decisions, new preferences), append key points
  to memory/journal/YYYY-MM-DD.md before ending the day
- Record: what was done, owner's new preferences/decisions, todos for tomorrow
- Append if today's file exists; write directly without reading first

[Output Format] (rendered as Feishu card markdown; must be structured)
- First sentence: bold conclusion, no preamble
- ### headings for multi-dimension content, ≤4 lines per section
- Lists: "-" for parallel items, "1. 2. 3." for steps/options
- Bold or `code` for key numbers/code/commands
- No large unformatted text blocks; no fluff

[Option Buttons] (use sparingly)
- Only when user choice or clear next-step recommendation is needed, end with
  `1.` `2.` `3.` numbered lines — rendered as clickable buttons
- If the answer is complete with no worthwhile follow-ups, just end;
  don't force numbered tails on every reply
- No emoji numbers; never say "I can't send buttons"
"""
