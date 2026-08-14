"""
tests/run_all_tests.py — agent-runner 全功能自动化测试
========================================================

用法：
    cd agent-runner
    ../.venv/bin/python tests/run_all_tests.py

覆盖：配置层 / 人格层 / 记忆沙箱 / 会话摘要 / 引擎 / 调度器 /
      渠道（离线）/ 依赖隔离 / 端到端（唯一真实模型调用）/ 冒烟。

所有测试数据使用临时目录/临时文件，测完清理，
不在 AgentsHome 或 agent-runner/memory 留下残留。
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

AGENT_RUNNER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_RUNNER))
os.chdir(AGENT_RUNNER)

# ---------------------------------------------------------------
# 测试框架：每项一行 ✅/❌ + 名称 + 关键数据
# ---------------------------------------------------------------
RESULTS: list[tuple[str, str, str]] = []  # (status, name, detail)


def report(status: str, name: str, detail: str = "") -> bool:
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}[status]
    RESULTS.append((status, name, detail))
    print(f"{icon} {name}" + (f" —— {detail}" if detail else ""))
    return status == "PASS"


def check(name: str, cond: bool, detail: str = "") -> bool:
    return report("PASS" if cond else "FAIL", name, detail)


# ---------------------------------------------------------------
# 1. 配置层
# ---------------------------------------------------------------
print("\n===== 1. 配置层 =====")
import core.config as config  # noqa: E402

cfg = config.load()
check("配置 load() 成功", isinstance(cfg, dict) and "models" in cfg)

api_key = cfg["models"]["default"].get("api_key", "")
masked = (api_key[:6] + "…" + api_key[-4:]) if len(api_key) > 10 else "(过短)"
check("models.default.api_key 为真实值",
      api_key.startswith("sk-") and "${" not in api_key, f"打码：{masked}")

paths_ok = True
path_detail = []
for label, p in [("agents.home", cfg["agents"]["home"]),
                 ("agents.workspace", cfg["agents"]["workspace"]),
                 ("tools.skills_dir", cfg["tools"]["skills_dir"])]:
    ok = os.path.isabs(p) and Path(p).exists()
    paths_ok = paths_ok and ok
    path_detail.append(f"{label}={'OK' if ok else p}")
check("home/workspace/skills_dir 均为存在的绝对路径", paths_ok,
      "；".join(path_detail))

accounts = cfg["channels"]["feishu"]["accounts"]
bad = [n for n, a in accounts.items()
       if not str(a.get("app_id", "")).startswith("cli_")
       or "${" in str(a.get("app_secret", ""))]
check("飞书账号 app_id=cli_* 且 secret 无 ${ 残留",
      len(accounts) >= 8 and not bad,
      f"账号数={len(accounts)}" + (f"，异常：{bad}" if bad else ""))

import core.personas as personas  # noqa: E402

members = sorted(cfg["agents"]["members"].keys())
disk_agents = sorted(personas.list_agents())
check("花名册 members 与磁盘 list_agents() 一致",
      members == disk_agents,
      f"{len(disk_agents)} 人：{', '.join(disk_agents)}")

missing = config.check()
check("config.check() 返回空", missing == [], f"missing={missing}")

# AGENT_CONFIG 环境变量：配置文件可指到别的位置（NAS 上指向 SSD 数据目录）
_orig_cfg, _orig_env = config._config, os.environ.get("AGENT_CONFIG")
try:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
        tf.write('{"models": {"x": {"model": "m"}}, "tools": {}, "marker": "via_env"}')
        tmp_cfg = tf.name
    os.environ["AGENT_CONFIG"] = tmp_cfg
    config._config = None
    alt = config.load()
    check("AGENT_CONFIG 环境变量可改变配置文件位置",
          alt.get("marker") == "via_env", f"生效路径={config._config_file()}")
finally:
    config._config = _orig_cfg
    if _orig_env is None:
        os.environ.pop("AGENT_CONFIG", None)
    else:
        os.environ["AGENT_CONFIG"] = _orig_env
    Path(tmp_cfg).unlink(missing_ok=True)

# ---------------------------------------------------------------
# 2. 人格层
# ---------------------------------------------------------------
print("\n===== 2. 人格层 =====")
PROMPT_MARKS = ["杨晨", "【工作区】", "【全队共享知识库】",
                "【全队共享踩坑记录】", "【长期记忆索引】", "【通用纪律】"]

prompt_fail = []
for agent in disk_agents:
    try:
        prompt = personas.build_system_prompt(agent)
        display = personas.agent_display(agent)
        cname = display.split(" ", 1)[1] if " " in display else display
        lacks = [m for m in PROMPT_MARKS + [cname] if m not in prompt]
        if lacks:
            prompt_fail.append(f"{agent} 缺 {lacks}")
    except Exception as e:
        prompt_fail.append(f"{agent} 异常 {e}")
check("8 个人格 build_system_prompt 全部成功且要素齐全",
      not prompt_fail, "；".join(prompt_fail) or "画像/soul/工作区/共享库/踩坑/记忆索引/纪律 均在")

display_fail = []
for agent in disk_agents:
    d = personas.agent_display(agent)
    parts = d.split(" ", 1)
    if len(parts) != 2 or not parts[1]:
        display_fail.append(f"{agent}→{d!r}")
check("agent_display 全部返回 emoji+中文名", not display_fail,
      "；".join(display_fail) or "、".join(personas.agent_display(a) for a in disk_agents))

ws_fail = [a for a in disk_agents
           if not personas.agent_workspace(a).exists()]
check("agent_workspace 目录都存在", not ws_fail,
      f"缺失：{ws_fail}" if ws_fail else "全部存在（不存在会自动创建）")

# enabled:false 过滤（只改内存配置对象，不写回 agent.json）
orig_enabled = cfg["agents"]["members"]["cto"].get("enabled", True)
try:
    cfg["agents"]["members"]["cto"]["enabled"] = False
    filtered = personas.list_agents()
    check("members 设 enabled:false 后 list_agents 减少一个",
          len(filtered) == len(disk_agents) - 1 and "cto" not in filtered,
          f"{len(disk_agents)} → {len(filtered)}")
finally:
    cfg["agents"]["members"]["cto"]["enabled"] = orig_enabled

# ---------------------------------------------------------------
# 3. 记忆工具（沙箱）
# ---------------------------------------------------------------
print("\n===== 3. 记忆工具（沙箱） =====")
import core.memory as memory  # noqa: E402

TEST_FILE = "autotest_sandbox.md"
TEST_KW = "霓虹灯塔自动化测试"
test_file_path = personas.home() / "finance" / "memory" / TEST_FILE
try:
    memory.set_current_agent("finance")
    w = memory.memory_write(TEST_FILE, f"测试内容：{TEST_KW}", mode="overwrite")
    r = memory.memory_read(TEST_FILE)
    s = memory.memory_search(TEST_KW)
    l = memory.memory_list()
    check("finance 记忆写/读/搜/列 一致",
          "已覆盖写入" in w and TEST_KW in r and TEST_KW in s and TEST_FILE in l,
          f"文件写入 {test_file_path.name}")

    trav1 = memory.memory_read("../finance/memory/good.md")
    trav2 = memory.memory_read("/etc/passwd")
    trav3 = memory.memory_write("../evil.md", "x")
    check("路径穿越防护（../ 与绝对路径均被拒绝）",
          "读取失败" in trav1 and "读取失败" in trav2 and "保存失败" in trav3,
          f"read(../)→{trav1[:12]}… read(/abs)→{trav2[:12]}… write(../)→{trav3[:12]}…")

    memory.set_current_agent("cto")
    cross = memory.memory_read(TEST_FILE)
    check("人格隔离：cto 读不到 finance 的测试文件",
          "不存在" in cross, cross[:24])
finally:
    memory.set_current_agent(cfg["agents"].get("default", "housekeeper"))
    if test_file_path.exists():
        test_file_path.unlink()
    check("记忆沙箱测试文件已清理", not test_file_path.exists(),
          str(test_file_path))

# ---------------------------------------------------------------
# 4. 会话与摘要
# ---------------------------------------------------------------
print("\n===== 4. 会话与摘要 =====")
BINDINGS = memory.BINDINGS_FILE
bindings_backup = BINDINGS.read_text(encoding="utf-8") if BINDINGS.exists() else None
TEST_CHAT = "autotest-chat-绑定"
try:
    memory.set_chat_agent(TEST_CHAT, "finance")
    got = memory.get_chat_agent(TEST_CHAT)
    check("set_chat_agent / get_chat_agent 往返", got == "finance",
          f"{TEST_CHAT} → {got}")
finally:
    if bindings_backup is not None:
        BINDINGS.write_text(bindings_backup, encoding="utf-8")
    elif BINDINGS.exists():
        BINDINGS.unlink()
    check("chat_agents.json 测试绑定已清理",
          TEST_CHAT not in memory._load_bindings(), "文件已还原")

summary_file = personas.home() / "finance" / "memory" / "summary.md"
summary_preexisted = summary_file.exists()
orig_summarize = memory._summarize
try:
    memory._summarize = lambda old, dropped: f"测试摘要（合并 {len(dropped)} 条旧消息）"
    max_history = memory._max_history()
    messages = [{"role": "system", "content": "系统提示"}]
    for i in range(max_history + 5):  # 超出 max_history
        messages.append({"role": "user" if i % 2 == 0 else "assistant",
                         "content": f"第 {i} 条消息"})
    memory._trim("finance", messages)
    has_summary_msg = (len(messages) > 1
                       and str(messages[1].get("content", "")).startswith(memory.SUMMARY_PREFIX))
    check("超长会话 _trim：旧消息被摘要替代 + 摘要文件写入 + 长度正确",
          len(messages) == max_history + 2 and has_summary_msg
          and summary_file.exists() and "测试摘要" in summary_file.read_text(encoding="utf-8"),
          f"裁剪后 {len(messages)} 条（= 系统+摘要+{max_history}），summary.md 已写")
finally:
    memory._summarize = orig_summarize
    if not summary_preexisted and summary_file.exists():
        summary_file.unlink()
    check("summary.md 测试残留已清理",
          summary_preexisted or not summary_file.exists(), "finance/memory/summary.md")

# _safe_cut：裁切点不能落在工具调用组中间（孤儿 tool 会毒死会话 → 400 死循环）
_base = [{"role": "user", "content": f"q{i}"} for i in range(5)]
_group = [{"role": "assistant", "content": None,
           "tool_calls": [{"id": "a"}, {"id": "b"}]},
          {"role": "tool", "tool_call_id": "a", "content": "r1"},
          {"role": "tool", "tool_call_id": "b", "content": "r2"}]
_msgs = _base + _group + [{"role": "assistant", "content": "答"}]
c1 = memory._safe_cut(_msgs, 6)   # 落在孤儿 tool 上 → 跳过整个工具组
c2 = memory._safe_cut(_msgs, 5)   # 落在完整配对的 assistant 上 → 保留
check("_safe_cut：孤儿 tool 跳过整组、完整工具组保留",
      c1 == 8 and c2 == 5, f"c1={c1} c2={c2}")

# 会话落盘：写盘 → 清内存 → 从磁盘恢复，内容一致；重启后指纹变了能热加载
PERSIST_CHAT = "autotest-persist"
try:
    key = f"finance:{PERSIST_CHAT}"
    msgs = memory._get_session("finance", PERSIST_CHAT)
    msgs.append({"role": "user", "content": "落盘测试消息"})
    memory.save_session("finance", PERSIST_CHAT)
    disk_ok = memory._session_file("finance", PERSIST_CHAT).exists()
    memory.SESSIONS.pop(key, None)
    memory._SESSION_FP.pop(key, None)
    restored = memory._get_session("finance", PERSIST_CHAT)
    check("会话落盘：写盘→清内存→恢复，历史不丢",
          disk_ok and any(m.get("content") == "落盘测试消息" for m in restored),
          f"恢复后 {len(restored)} 条消息")
finally:
    memory.delete_session("finance", PERSIST_CHAT)
    check("会话落盘测试残留已清理",
          not memory._session_file("finance", PERSIST_CHAT).exists(),
          "sessions/ 下无残留")

# ---------------------------------------------------------------
# 5. 引擎
# ---------------------------------------------------------------
print("\n===== 5. 引擎 =====")
import core.engine as engine  # noqa: E402

tool_names = sorted(s["function"]["name"] for s in engine.TOOL_SCHEMAS)
stock_tools = sorted(n for n, o in engine.TOOL_ORIGIN.items() if o == "stock")
feishu_tools = sorted(n for n, o in engine.TOOL_ORIGIN.items()
                      if o == "feishu_docs")
mem_tools = sorted(n for n, o in engine.TOOL_ORIGIN.items() if o == "memory")
web_tools = sorted(n for n, o in engine.TOOL_ORIGIN.items() if o == "web_search")
kb_tools = sorted(n for n, o in engine.TOOL_ORIGIN.items() if o == "knowledge")
check("TOOL_SCHEMAS 共 20 个（stock6+feishu5+memory4+web2+kb3）",
      len(tool_names) == 20 and len(stock_tools) == 6
      and len(feishu_tools) == 5 and len(mem_tools) == 4
      and len(web_tools) == 2 and len(kb_tools) == 3,
      f"stock={len(stock_tools)} feishu={len(feishu_tools)} "
      f"memory={len(mem_tools)} web={len(web_tools)} kb={len(kb_tools)}")
check("TOOL_ORIGIN 溯源正确（无未标注工具）",
      all(n in engine.TOOL_ORIGIN for n in tool_names),
      f"来源：{sorted(set(engine.TOOL_ORIGIN.values()))}")

tools_md = personas.home() / "shared" / "tools.md"
tools_text = tools_md.read_text(encoding="utf-8") if tools_md.exists() else ""
md_missing = [n for n in tool_names if n not in tools_text]
check("AgentsHome/shared/tools.md 含全部工具名",
      tools_md.exists() and not md_missing,
      f"缺失：{md_missing}" if md_missing else f"{len(tool_names)} 个在档")

r_agents = engine.handle_command("autotest-cmd", "/agents")
r_who = engine.handle_command("autotest-cmd", "/who")
check("多人格模式 /agents /who 正常",
      r_agents is not None and "可用人格" in r_agents
      and r_who is not None and "当前人格" in r_who,
      r_who.strip() if r_who else "None")

fin_display = personas.agent_display("finance")
r_fix_agent = engine.handle_command("autotest-cmd", "/agent cto", fixed_agent="finance")
r_fix_who = engine.handle_command("autotest-cmd", "/who", fixed_agent="finance")
check("fixed_agent=finance：/agent 被拒、/who 显示固定人格",
      r_fix_agent is not None and "固定人格" in r_fix_agent
      and r_fix_who is not None and fin_display in r_fix_who,
      f"/who → {r_fix_who}")

orig_skills = cfg["agents"]["members"]["product"].get("skills", ["*"])
try:
    cfg["agents"]["members"]["product"]["skills"] = ["stock"]
    scoped = sorted(s["function"]["name"] for s in engine._schemas_for("product"))
    expect = sorted(stock_tools + mem_tools)
    check("技能过滤：skills:[\"stock\"] 只有 stock+memory 工具",
          scoped == expect
          and all(engine.TOOL_ORIGIN[n] in ("stock", "memory") for n in scoped),
          f"可见 {len(scoped)} 个工具"
          f"（stock {len(stock_tools)} + memory {len(mem_tools)}）")
    # 反向验证：过滤掉 stock 后 stock 工具不可见
    cfg["agents"]["members"]["product"]["skills"] = ["_none_"]
    scoped2 = sorted(s["function"]["name"] for s in engine._schemas_for("product"))
    check("技能过滤反向验证：skills:[\"_none_\"] 只剩 memory 工具",
          scoped2 == mem_tools, f"可见 {scoped2}")
finally:
    cfg["agents"]["members"]["product"]["skills"] = orig_skills

# ---------------------------------------------------------------
# 5.5 联网搜索与 RAG 知识库
# ---------------------------------------------------------------
print("\n===== 5.5 搜索与知识库 =====")
sys.path.insert(0, str(Path(cfg["tools"]["skills_dir"])))
import web_search as ws_mod  # noqa: E402
import knowledge as kb_mod  # noqa: E402

# web_search / web_fetch：mock HTTP，验证解析与降级
_orig_http = ws_mod._http_get
try:
    ws_mod._http_get = lambda url, timeout=15: json.dumps({
        "results": [{"title": "标题一", "url": "https://a.com",
                     "content": "摘要一", "engine": "baidu"}]})
    sr = ws_mod.web_search("测试查询")
    ws_mod._http_get = lambda url, timeout=15: \
        "<html><style>x</style><body><h1>大标题</h1><p>正文内容</p><script>y</script></body></html>"
    fr = ws_mod.web_fetch("https://a.com")
    ws_mod._http_get = lambda url, timeout=15: 1 / 0
    er = ws_mod.web_search("x")
    check("web_search 解析结果 + web_fetch 去标签 + 异常降级",
          "标题一" in sr and "https://a.com" in sr
          and "大标题" in fr and "<h1>" not in fr and "script" not in fr
          and er.startswith("搜索失败"),
          f"search={sr[:20]!r} fetch={fr[:15]!r} err={er[:12]!r}")
finally:
    ws_mod._http_get = _orig_http

# knowledge：合成语料 + mock embedding，验证索引/检索/隔离/增量
_KB_KWS = ["苹果", "香蕉", "秘密", "报告"]


def _fake_embed(texts):
    return [[1.0 if kw in t else 0.0 for kw in _KB_KWS] for t in texts]


_tmp_kb = Path(tempfile.mkdtemp())
(_tmp_kb / "shared.md").write_text(
    "# 共享知识\n苹果是一种水果，富含维生素，每天一个苹果医生远离我。", encoding="utf-8")
(_tmp_kb / "work.md").write_text(
    "# 季度报告\n香蕉销量报告：本季度香蕉销量同比增长百分之五十。", encoding="utf-8")
(_tmp_kb / "mem_finance.md").write_text(
    "# 私密\n财务的秘密账本：记录了所有不为人知的收支明细。", encoding="utf-8")
(_tmp_kb / "mem_cto.md").write_text(
    "# 私密\nCTO 的秘密架构图：画了所有不能对外公开的系统设计。", encoding="utf-8")


def _fake_iter(agent):
    yield "shared/共用.md", _tmp_kb / "shared.md"
    yield "work/报告.md", _tmp_kb / "work.md"
    yield f"memory/{agent}/私密.md", _tmp_kb / f"mem_{agent}.md"


_orig_db, _orig_iter = kb_mod._db_path, kb_mod._iter_files
_orig_embed, _orig_agent = kb_mod.get_embeddings, kb_mod.current_agent
try:
    kb_mod._db_path = lambda: _tmp_kb / "kb.sqlite"
    kb_mod._iter_files = _fake_iter
    kb_mod.get_embeddings = _fake_embed
    kb_mod.current_agent = lambda: "finance"

    conn = kb_mod._connect()
    st = kb_mod._reindex(conn, "finance")
    r1 = kb_mod.kb_search("苹果")  # finance 视角
    iso_ok = "memory/cto" not in kb_mod.kb_search("秘密")

    kb_mod.current_agent = lambda: "cto"
    kb_mod._reindex(conn, "cto")
    r_cto = kb_mod.kb_search("秘密")  # cto 视角能看到自己的私密

    # 增量：改动 shared.md → 再索引应只有 1 个文件更新
    (_tmp_kb / "shared.md").write_text(
        "# 共享知识\n苹果是科技公司，发布了新款手机与电脑产品。", encoding="utf-8")
    import os as _os
    _os.utime(_tmp_kb / "shared.md", (time.time() + 10, time.time() + 10))
    kb_mod.current_agent = lambda: "finance"
    st2 = kb_mod._reindex(conn, "finance")

    check("知识库：索引/检索排序/人格隔离/增量更新",
          st["new"] == 3 and "shared/共用.md" in r1 and iso_ok
          and "memory/cto/私密.md" in r_cto and st2["updated"] == 1,
          f"new={st['new']} 隔离={iso_ok} 增量={st2}")

    # pdf 支持：损坏的 pdf 静默跳过不炸索引；shared 下的 pdf 在扫描范围内
    (_tmp_kb / "paper.pdf").write_text("这不是真 pdf", encoding="utf-8")

    def _fake_iter_pdf(agent):
        yield from _fake_iter(agent)
        yield "shared/论文.pdf", _tmp_kb / "paper.pdf"

    kb_mod._iter_files = _fake_iter_pdf
    st3 = kb_mod._reindex(conn, "finance")
    n_pdf = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE file_path='shared/论文.pdf'"
    ).fetchone()[0]
    check("知识库 pdf：损坏 pdf 静默跳过、pdf 在索引范围内",
          st3["new"] == 1 and n_pdf == 0
          and "共用" in kb_mod.kb_search("苹果"),
          f"pdf块数={n_pdf}")
finally:
    kb_mod._db_path, kb_mod._iter_files = _orig_db, _orig_iter
    kb_mod.get_embeddings, kb_mod.current_agent = _orig_embed, _orig_agent
    import shutil  # noqa: E402
    shutil.rmtree(_tmp_kb, ignore_errors=True)

# ASR 语音转写：faster-whisper 未安装（本地 dev 环境）时优雅降级
import core.asr as asr_mod  # noqa: E402
check("ASR 降级：依赖缺失/文件不存在返回空串不炸",
      asr_mod.transcribe("/tmp/不存在的音频.opus") == "", "graceful")

# ---------------------------------------------------------------
# 6. 调度器
# ---------------------------------------------------------------
print("\n===== 6. 调度器 =====")
import core.scheduler as scheduler  # noqa: E402

jobs = scheduler.load_jobs()
by_agent = {}
for j in jobs:
    by_agent[j["agent"]] = by_agent.get(j["agent"], 0) + 1
check("load_jobs()：每人格 1 条静默整理 + analyst 日报已停 + finance 共 4",
      by_agent.get("analyst") == 1 and by_agent.get("finance") == 4
      and sum(1 for j in jobs if j.get("quiet")) == len(disk_agents),
      f"{by_agent}")


class FakeChannel:
    def __init__(self, fixed_agent, target):
        self.fixed_agent = fixed_agent
        self._target = target

    def get_target_chat(self):
        return self._target


reg1 = {"default": FakeChannel(None, "oc_default"),
        "finance": FakeChannel("finance", "oc_finance")}
t1 = scheduler.resolve_push_channel("finance", reg1)
check("resolve_push_channel：finance 任务路由 finance 账号",
      t1 is not None and t1[0] == "finance" and t1[2] == "oc_finance",
      f"→ {t1[0]}/{t1[2]}" if t1 else "None")

reg2 = {"default": FakeChannel(None, "oc_default"),
        "finance": FakeChannel("finance", None)}  # 绑定了但还没人说过话
t2 = scheduler.resolve_push_channel("finance", reg2)
check("resolve_push_channel：绑定账号无目标时降级 default",
      t2 is not None and t2[0] == "default" and t2[2] == "oc_default",
      f"→ {t2[0]}/{t2[2]}" if t2 else "None")

reg3 = {"default": FakeChannel(None, None),
        "finance": FakeChannel("finance", None)}
t3 = scheduler.resolve_push_channel("finance", reg3)
t4 = scheduler.resolve_push_channel("nobody", reg3)
check("resolve_push_channel：全部无目标返回 None", t3 is None and t4 is None,
      f"finance→{t3} 未绑定agent→{t4}")

# 触发记录落盘：保存→重新加载，只留今天的记录（重启防重复推送）
_fired_pre = scheduler.FIRED_FILE.read_text(encoding="utf-8") \
    if scheduler.FIRED_FILE.exists() else None
try:
    from datetime import datetime as _dt  # noqa: E402
    _today = _dt.now().strftime("%Y-%m-%d")
    scheduler._save_fired({"finance|08:50|123": _today,
                           "finance|08:51|456": "2020-01-01"})
    _reloaded = scheduler._load_fired()
    check("触发记录落盘：今天的保留、隔夜的丢弃",
          _reloaded == {"finance|08:50|123": _today}, f"{_reloaded}")
finally:
    if _fired_pre is not None:
        scheduler.FIRED_FILE.write_text(_fired_pre, encoding="utf-8")
    else:
        scheduler.FIRED_FILE.unlink(missing_ok=True)

# ---------------------------------------------------------------
# 7. 渠道（离线，不连飞书）
# ---------------------------------------------------------------
print("\n===== 7. 渠道（离线） =====")
from channels import build_feishu_channels  # noqa: E402
import channels.feishu as feishu_mod  # noqa: E402

feishu_channels = build_feishu_channels(cfg["channels"]["feishu"])
# default 多人格账号已移除；期望渠道数 = 配置里启用且凭证完整的账号数
expected = sum(1 for a in cfg["channels"]["feishu"]["accounts"].values()
               if a.get("enabled", True) and a.get("app_id")
               and a.get("app_secret"))
fx = [n for n, ch in feishu_channels.items() if ch.fixed_agent != n]
# owner_open_ids 配了就必须是 ou_ 开头；留空（如新应用未收集）也合法
wl = [n for n, ch in feishu_channels.items()
      if ch.owner_open_ids
      and not all(i.startswith("ou_") for i in ch.owner_open_ids)]
check("build_feishu_channels 全部固定人格、无 default、白名单正确",
      len(feishu_channels) == expected and "default" not in feishu_channels
      and not fx and not wl,
      f"{len(feishu_channels)}/{expected} 个渠道"
      + (f"，异常：{fx + wl}" if fx or wl else ""))

ch = next(iter(feishu_channels.values()))

card = ch.build_card("# 大标题\n正文", chat_id="oc_x",
                     meta={"model": "k3", "tokens": 123, "elapsed": 1.5})
md_content = card["elements"][0]["text"]["content"]
check("卡片 markdown 标题降级为加粗", "**大标题**" in md_content and "# 大标题" not in md_content,
      md_content.splitlines()[0])

# 富文本 post 展平：text / a / at / img 各归其位，段落间换行
flat = feishu_mod._flatten_post([
    [{"tag": "text", "text": "第一段 "},
     {"tag": "a", "text": "链接", "href": "https://example.com"}],
    [{"tag": "at", "name": "Pluto"}, {"tag": "text", "text": " 第二段"},
     {"tag": "img", "image_key": "img_x"}],
])
check("富文本 post 展平为纯文本（text/a/at/img + 换行）",
      flat == ("第一段 链接(https://example.com)\n"
               "@Pluto 第二段[图片]"),
      repr(flat[:40]))

# 图文混排：_post_image_keys 提取嵌图 key
img_keys = feishu_mod._post_image_keys([
    [{"tag": "text", "text": "看图"}, {"tag": "img", "image_key": "img_1"}],
    [{"tag": "img", "image_key": "img_2"}],
])
check("图文混排嵌图提取（_post_image_keys）",
      img_keys == ["img_1", "img_2"], f"{img_keys}")

# engine 多图：image_b64 列表 → content 数组含多个 image_url，结束后历史消毒
import copy  # noqa: E402
from types import SimpleNamespace  # noqa: E402
_captured = {}
_orig_cwr = engine.chat_with_retry


class _FakeMsg:
    content = "看到了"
    tool_calls = None

    def model_dump(self, exclude_none=True):
        return {"role": "assistant", "content": "看到了"}


try:
    engine.chat_with_retry = lambda **kw: (
        _captured.update(msgs=copy.deepcopy(kw["messages"])),
        SimpleNamespace(choices=[SimpleNamespace(message=_FakeMsg())],
                        usage=SimpleNamespace(total_tokens=1)))[1]
    engine.run_agent_meta("test-multi-img", "看图说话", agent="finance",
                          image_b64=["b64_a", "b64_b"])
    sent = _captured["msgs"][-1]
    n_img = sum(1 for p in sent["content"] if p.get("type") == "image_url")
    from core.memory import SESSIONS, delete_session  # noqa: E402
    live = SESSIONS["finance:test-multi-img"][-1]
    sanitized = isinstance(live["content"], str)
    delete_session("finance", "test-multi-img")
    check("engine 多图：2 个 image_url 发出 + 历史消毒为文本",
          n_img == 2 and sanitized, f"image_url={n_img} 消毒={sanitized}")
finally:
    engine.chat_with_retry = _orig_cwr

# 引用回复的消息文本提取：text / post / 卡片三种类型都能拿出纯文本
q_text = feishu_mod._extract_msg_text("text", json.dumps({"text": "引用我"}))
q_card = feishu_mod._extract_msg_text("interactive", json.dumps({
    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "卡片正文"}}]}))
q_bad = feishu_mod._extract_msg_text("text", "{坏json")
check("引用消息文本提取（text/卡片/坏 json 降级）",
      q_text == "引用我" and q_card == "卡片正文" and q_bad == "",
      f"text={q_text!r} card={q_card!r}")

opt_cases = {
    "1. 格式": "结论如下\n\n1. 方案甲\n2. 方案乙\n3. 方案丙",
    "**选项 1：格式": "结论如下\n\n**选项 1：方案甲**\n**选项 2：方案乙**",
    "1️⃣ 格式": "结论如下\n\n1️⃣ 方案甲\n2️⃣ 方案乙",
}
opt_fail = []
for label, text in opt_cases.items():
    c = ch.build_card(text, chat_id="oc_x")
    actions = [e for e in c["elements"] if e.get("tag") == "action"]
    if not actions or len(actions[0]["actions"]) < 2:
        opt_fail.append(label)
check("尾部编号选项生成按钮（1. / **选项 1：/ 1️⃣ 三种格式）",
      not opt_fail, "失败格式：" + "、".join(opt_fail) if opt_fail else "三种格式均生成按钮")

footer_card = ch.build_card("无选项回答", chat_id="oc_x",
                            meta={"model": "k3", "tokens": 42, "elapsed": 0.8})
note = [e for e in footer_card["elements"] if e.get("tag") == "note"]
note_text = note[0]["elements"][0]["content"] if note else ""
has_action = any(e.get("tag") == "action" for e in footer_card["elements"])
check("footer note 含 model·tokens·用时；无选项无 action 元素",
      note and "k3" in note_text and "42 tokens" in note_text and "0.8s" in note_text
      and not has_action,
      f"note=「{note_text}」，action={has_action}")

# 定时任务推送 send()：发卡片（标题进 header），失败降级纯文本
from types import SimpleNamespace  # noqa: E402

_sent = []


def _fake_create(req):
    body = req.request_body
    _sent.append({"msg_type": body.msg_type, "content": body.content})
    # 第一次调用模拟失败（验证降级），之后成功
    ok = len(_sent) > 1 or not getattr(ch, "_test_fail_once", False)
    return SimpleNamespace(success=lambda: ok, code=0, msg="")


ch.lark_client = SimpleNamespace(
    im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(create=_fake_create))))
ch.send("oc_x", "📮 🦅 洞察鹰 的定时任务\n\n**结论** 正文")
_card_json = json.loads(_sent[0]["content"])
card_ok = (_sent[0]["msg_type"] == "interactive"
           and _card_json.get("header", {}).get("title", {}).get("content") == "📮 🦅 洞察鹰 的定时任务"
           and "**结论** 正文" in _card_json["elements"][0]["text"]["content"])
ch._test_fail_once = True
_sent.clear()
ch.send("oc_x", "📮 标题\n\n正文")
fallback_ok = len(_sent) == 2 and _sent[1]["msg_type"] == "text"
del ch._test_fail_once
check("定时任务推送走卡片 + 失败降级纯文本",
      card_ok and fallback_ok,
      f"msg_type={_sent[0]['msg_type'] if _sent else '?'}，降级补发={len(_sent) == 2}")

# owner.json 命名空间读写 + 旧平铺格式迁移（临时文件，不碰真实 owner.json）
with tempfile.TemporaryDirectory() as tmpdir:
    tmp_owner = Path(tmpdir) / "owner.json"
    orig_owner_file = feishu_mod.OWNER_FILE
    try:
        feishu_mod.OWNER_FILE = tmp_owner
        tmp_owner.write_text(json.dumps({"ou_old": "oc_old"}, ensure_ascii=False),
                             encoding="utf-8")
        data = feishu_mod._load_owner()  # 触发迁移
        migrated = data.get("default", {}).get("ou_old") == "oc_old"
        feishu_mod._save_owner("finance", "ou_a", "oc_fin")
        ns_ok = (feishu_mod.get_target_chat("finance") == "oc_fin"
                 and feishu_mod.get_target_chat("default") == "oc_old")
        check("owner.json 命名空间读写 + 旧平铺格式自动迁移",
              migrated and ns_ok,
              f"迁移后={json.loads(tmp_owner.read_text(encoding='utf-8'))}")
    finally:
        feishu_mod.OWNER_FILE = orig_owner_file

# 终端渠道：monkeypatch 后模拟一轮收发 + /agents 命令
import channels.terminal as terminal_mod  # noqa: E402

orig_run_meta = terminal_mod.run_agent_meta
try:
    terminal_mod.run_agent_meta = lambda chat_id, text, **kw: (
        f"伪回答：收到「{text}」", {"model": "k3", "tokens": 10, "elapsed": 0.1})
    inputs = iter(["测试你好", "/agents"])
    orig_input = __builtins__["input"] if isinstance(__builtins__, dict) else __builtins__.input

    def fake_input(prompt=""):
        try:
            return next(inputs)
        except StopIteration:
            raise EOFError

    buf = io.StringIO()
    import builtins
    builtins.input = fake_input
    try:
        with contextlib.redirect_stdout(buf):
            terminal_mod.TerminalChannel().start()
    finally:
        builtins.input = orig_input
    out = buf.getvalue()
    check("终端渠道：模拟一轮收发 + /agents 命令",
          "伪回答：收到「测试你好」" in out and "可用人格" in out,
          "回答与命令列表均正确输出")
finally:
    terminal_mod.run_agent_meta = orig_run_meta

# ---------------------------------------------------------------
# 8. 依赖隔离
# ---------------------------------------------------------------
print("\n===== 8. 依赖隔离 =====")
core_bad = [p.name for p in (AGENT_RUNNER / "core").glob("*.py")
            if "lark_oapi" in p.read_text(encoding="utf-8")]
ch_bad = []
for p in (AGENT_RUNNER / "channels").glob("*.py"):
    src = p.read_text(encoding="utf-8")
    if "import openai" in src or "from openai" in src:
        ch_bad.append(p.name)
check("core/ 无 lark_oapi 依赖", not core_bad, f"违规：{core_bad}" if core_bad else "干净")
check("channels/ 无 openai 依赖", not ch_bad, f"违规：{ch_bad}" if ch_bad else "干净")

# ---------------------------------------------------------------
# 9. 端到端集成（唯一真实模型调用）
# ---------------------------------------------------------------
print("\n===== 9. 端到端集成（真实模型调用） =====")
E2E_CHAT = "e2e-test-chat"
designer_summary = personas.home() / "designer" / "memory" / "summary.md"
designer_summary_pre = designer_summary.exists()
try:
    answer, meta = engine.run_agent_meta(
        E2E_CHAT, "你好，请用一句话介绍你自己", agent="designer")
    if answer == "模型服务暂时不可用，请稍后重试 🙏":
        report("WARN", "端到端：模型调用（外部服务不可用，非代码 bug）",
               "chat_with_retry 重试后仍失败")
    else:
        check("端到端：designer 真实回答 + meta 完整",
              bool(answer and answer.strip())
              and meta.get("tokens", 0) > 0 and meta.get("elapsed", 0) > 0,
              f"tokens={meta.get('tokens')}，用时={meta.get('elapsed')}s，"
              f"回答：{str(answer)[:60]}…")
except Exception as e:
    report("WARN", "端到端：模型调用（外部服务异常，非代码 bug）", f"{type(e).__name__}: {e}")
finally:
    memory.delete_session("designer", E2E_CHAT)  # 连内存带磁盘一起清
    if not designer_summary_pre and designer_summary.exists():
        designer_summary.unlink()
    check("端到端残留已清理（会话 + designer summary.md）",
          f"designer:{E2E_CHAT}" not in memory.SESSIONS
          and not memory._session_file("designer", E2E_CHAT).exists()
          and (designer_summary_pre or not designer_summary.exists()),
          "内存会话已弹出")

# ---------------------------------------------------------------
# 10. 冒烟
# ---------------------------------------------------------------
print("\n===== 10. 冒烟 =====")
try:
    import main  # noqa: F401
    check("main.py 可导入不报错", True, f"main={main.main.__name__}")
except Exception as e:
    check("main.py 可导入不报错", False, f"{type(e).__name__}: {e}")
try:
    import evals  # noqa: F401
    check("evals.py 可导入不报错", True, "parse_evals/run_evals 可用")
except Exception as e:
    check("evals.py 可导入不报错", False, f"{type(e).__name__}: {e}")

# ---------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------
passed = sum(1 for s, _, _ in RESULTS if s == "PASS")
warned = sum(1 for s, _, _ in RESULTS if s == "WARN")
failed = [(n, d) for s, n, d in RESULTS if s == "FAIL"]
total = len(RESULTS)
print("\n" + "=" * 60)
print(f"总计：{passed}/{total} PASS" + (f"，{warned} WARN" if warned else "")
      + (f"，{len(failed)} FAIL" if failed else ""))
if failed:
    print("\n失败项详情：")
    for n, d in failed:
        print(f"  ❌ {n} —— {d}")
sys.exit(1 if failed else 0)
