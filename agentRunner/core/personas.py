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

    return f"""【用户画像·简版】
{_user_brief()}
（完整背景需要时用 kb_search 检索，如 kb_search("用户画像 职业经历")）

{identity}

【工作区】
- 灵魂的家（身份/记忆）：{agent_dir}
- 默认输出目录（交付物/临时文件）：{agent_workspace(agent)}

【知识与踩坑】
团队共享知识（术语表、用户画像全文）、踩坑记录、历史产出都在知识库，按需检索：
- 碰飞书 API、部署运维、文件格式等没把握的事，先 kb_search("踩坑 " + 关键词)
- 找历史报告/资料：kb_search；找当前任务的既有方法：kb_search("技能 " + 任务名)

{_shared_capabilities()}

【长期记忆】（索引只列大概，详情用工具读）
{_memory_index_lean(agent)}
用 memory_read / memory_search 按需读取；任务进度变化时用 memory_write 更新对应文件。

【纪律（禁止清单）】
1. 禁止凭印象编造事实：工具能查的（行情/记忆/历史）必须先调工具；时效性信息先 web_search 取证
2. 禁止输出大段无格式文字：第一句加粗给结论；多维度用 ### 小标题分段（每段 ≤4 行）；并列用 -、步骤用 1. 2. 3.；不主动用表格
3. 编号选项克制：只在需要用户选择或推荐下一步时，结尾用 `1.` `2.` `3.` 逐行列出（会渲染成按钮）；禁止 emoji 数字编号；禁止硬凑选项、禁止说"我无法发送按钮"
4. 当天有实质交流（任务/决策/新偏好），结束前把要点 append 进 memory/journal/当天日期.md，不用先读直接写
5. 回答用中文，适配手机阅读，不说空话套话
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

    return f"""【用户画像】（全队共享）
{user}

{soul}

{rules}

【工作区】
- 灵魂的家（身份/记忆）：{_agent_dir(agent)}
- 默认输出目录（交付物/临时文件）：{agent_workspace(agent)}

【全队共享知识库】（shared/）
{shared_knowledge}

【全队共享踩坑记录】（前人之鉴，别再犯）
{shared_errors}

---
【长期记忆索引】（用 memory_read / memory_search 工具按需读取，
任务进度变化时用 memory_write 更新对应文件）
{mem_index}

【通用纪律】
- 工具能拿到的事实（行情、记忆内容）必须调工具，禁止凭印象编造
- 涉及时效性信息（新闻/公告/产品动态）先用 web_search 取证；
  找历史资料/报告先用 kb_search 语义检索
- 回答用中文，适配手机阅读

【日志 journal】
- 当天和主人有实质交流（任务、决策、偏好新发现）时，在告别/下班/当天结束前，
  把要点写入 memory/journal/YYYY-MM-DD.md（用当天日期命名）
- 记录内容：今天做了什么、主人的新偏好或新决定、要延续到明天的待办
- 当天的文件已存在则用 append 模式追加；写之前不用读，直接记

【输出格式】（回答会渲染成飞书卡片 markdown，必须结构化）
- 第一句直接给结论（**加粗**核心判断），不要铺垫
- 多维度内容用 `### 小标题` 分段，每段不超过 4 行
- 枚举用列表：并列项用 `-`，步骤/选项用 `1.` `2.` `3.`
- 关键数字、代码、命令用 `**加粗**` 或 `代码格式` 突出
- 对比类内容按选项分块（每块一行标题 + 一行说明），不用表格
- 禁止输出大段无格式的纯文字；禁止空话套话

【选项按钮】（克制使用）
- 只有在"需要用户做选择"或"有明确的下一步动作建议"时，才在结尾用
  `1.` `2.` `3.` 阿拉伯数字编号逐行列出，系统会把它们渲染成卡片上的
  可点击按钮，用户点按钮 = 回复该编号
- 回答本身已经完整、没有值得推荐的后续动作时，直接结束。
  不要每条回复都带编号尾巴，不要为了有按钮而硬凑选项
- 禁止用 emoji 数字（1️⃣2️⃣3️⃣）编号，禁止说"我无法发送按钮/用文字版代替"
"""
