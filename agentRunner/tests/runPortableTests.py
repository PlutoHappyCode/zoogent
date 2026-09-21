#!/usr/bin/env python3
"""
tests/runPortableTests.py — 可移植自测（干净 clone / CI 可跑）
==============================================================

与 runAllTests.py 的分工：
  runAllTests.py      —— 私有数据用例：真飞书账号、真 9 人格、真 RAG 库、
                         真实模型调用。只能在有完整 AgentsHome 的本机/NAS 跑。
  runPortableTests.py —— 纯逻辑用例：配置解析、人格组装、记忆沙箱、会话/绑定/
                         摘要、飞书 parser、引擎兜底。用 tempfile 造最小 fixture
                         （2 人格 + 最小 agent.json），不碰任何私有数据。

用法：
    cd agentRunner
    python tests/runPortableTests.py
"""

import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

AGENT_RUNNER = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENT_RUNNER))
os.chdir(AGENT_RUNNER)

# ---------------------------------------------------------------
# 测试框架：每项一行 ✅/❌ + 名称 + 关键数据
# ---------------------------------------------------------------
RESULTS: list[tuple[str, str, str]] = []


def report(status: str, name: str, detail: str = "") -> bool:
    icon = {"PASS": "✅", "FAIL": "❌", "WARN": "⚠️"}[status]
    RESULTS.append((status, name, detail))
    print(f"{icon} {name}" + (f" —— {detail}" if detail else ""))
    return status == "PASS"


def check(name: str, cond: bool, detail: str = "") -> bool:
    return report("PASS" if cond else "FAIL", name, detail)


# ---------------------------------------------------------------
# 0. 最小 fixture：临时 AgentsHome（2 人格）+ JSONC agent.json
#    必须在 import core 之前设好环境变量，config 是懒加载 + 全局缓存
# ---------------------------------------------------------------
TMP = Path(tempfile.mkdtemp(prefix="zoogent-portable-")).resolve()
HOME = TMP / "AgentsHome"
WS = TMP / "Zootopia" / "homework"
SKILLS = HOME / "skills"          # 故意留空：干净环境没有私有技能
(HOME / "shared" / "learnings").mkdir(parents=True, exist_ok=True)
SKILLS.mkdir(parents=True, exist_ok=True)

_SOUL = """# SOUL · {emoji} {name}

**name**: {name}
**emoji**: {emoji}

id: {agent}
You are {agent}, not the model.

## Role
- 只做 {agent} 分内的事，不越界。
"""
_RULES = """# RULES · {agent}

1. 先查工具再下结论，不许编事实。
2. 回答用中文，先说结论。
3. 有选择时结尾给 1. 2. 3. 编号选项。
"""
_LEAN = """# {emoji} {name}（极简注入版）

你是 {agent}。先查工具再下结论。
回答用中文、先说结论；有选择时给 1. 2. 3. 选项。
只做分内的事，不越界。
"""

for _a, _n, _e in (("alpha", "AlphaBot", "🤖"), ("beta", "BetaBot", "🦊")):
    _d = HOME / _a
    (_d / "memory" / "journal").mkdir(parents=True, exist_ok=True)
    (_d / "soul.md").write_text(_SOUL.format(agent=_a, name=_n, emoji=_e), encoding="utf-8")
    (_d / "rules.md").write_text(_RULES.format(agent=_a), encoding="utf-8")
    (_d / "lean.md").write_text(_LEAN.format(agent=_a, name=_n, emoji=_e), encoding="utf-8")

(HOME / "shared" / "userBrief.md").write_text(
    "# 用户画像（简版）\n- 称呼：老王；open_id: ou_portable\n", encoding="utf-8")
(HOME / "shared" / "user.md").write_text(
    "# 用户画像（全量）\n" + "- 经历：做过多年的制造与供应链。\n" * 20, encoding="utf-8")
(HOME / "shared" / "capabilities.md").write_text(
    "## 能力边界\n- 不承诺报价，不代替法务。\n", encoding="utf-8")
(HOME / "shared" / "glossary.md").write_text(
    "## 术语\n- 欠条：模型故障时记下的待重放任务。\n", encoding="utf-8")
(HOME / "shared" / "learnings" / "errors.md").write_text(
    "## 踩坑\n- 不要用 shell 写文件，走 file_write。\n", encoding="utf-8")

# JSONC + ${VAR}：验证注释剥离与变量替换（含 "https://" 里的 // 必须存活）
(TMP / "agent.json").write_text("""{
  // 可移植测试用最小配置
  "models": {
    "default": {"model": "portable-model", "api_key": "${PORTABLE_TEST_KEY}",
                "base_url": "https://example.invalid/v1"},
    "embed": {"model": "portable-embed", "api_key": "${PORTABLE_TEST_KEY}",
              "base_url": "https://example.invalid/v1"}
  },
  "agents": {
    "home": "AgentsHome",                    /* 相对配置文件目录解析 */
    "workspace": "Zootopia/homework",
    "default": "alpha",
    "members": {"alpha": {"skills": ["*"]}, "beta": {"skills": ["_none_"]}}
  },
  "tools": {
    "skills_dir": "AgentsHome/skills",
    "workspace": {"posture": "strict", "denylist": ["rm", "sudo"]}
  },
  "prompt": {"lean": true},
  "channels": {"feishu": {"enabled": false, "whitelist": true, "accounts": {}}},
  "marker": "jsonc-ok"
}
""", encoding="utf-8")

os.environ["AGENT_CONFIG"] = str(TMP / "agent.json")
os.environ["PORTABLE_TEST_KEY"] = "portable-fake-value-not-a-secret"
os.environ["PORTABLE_EXTRA_ID"] = "ou_portable_extra"
os.environ["AGENTS_HOME"] = str(HOME)
os.environ["AGENTS_WORKSPACE"] = str(WS)
os.environ["AGENTS_SKILLS_DIR"] = str(SKILLS)

try:
    # -----------------------------------------------------------
    # 1. 配置层（JSONC 注释 / ${VAR} / 相对路径 / 列表归一化）
    # -----------------------------------------------------------
    print("\n===== 1. 配置层 =====")
    import core.config as config  # noqa: E402

    cfg = config.load()
    check("JSONC 注释被剥离、配置可解析",
          cfg.get("marker") == "jsonc-ok" and "models" in cfg)

    check("${VAR} 替换生效且字符串里的 // 未被当注释吃掉",
          cfg["models"]["default"]["api_key"] == os.environ["PORTABLE_TEST_KEY"]
          and cfg["models"]["default"]["base_url"] == "https://example.invalid/v1",
          f"base_url={cfg['models']['default']['base_url']}")

    check("home/workspace/skills_dir 相对路径按配置文件目录解析",
          cfg["agents"]["home"] == str(HOME) and cfg["agents"]["workspace"] == str(WS)
          and cfg["tools"]["skills_dir"] == str(SKILLS),
          f"home={cfg['agents']['home']}")

    _sc = config._strip_comments('{"u": "https://a.com//b"} // 尾注释')
    check("_strip_comments 只剥真注释、不动字符串",
          json.loads(_sc)["u"] == "https://a.com//b")

    # 明文 key 检测：${VAR} 写法不误报、明文写法要告警。
    # 假 key 拼出来写：仓库里不留 API key 前缀字面量，免得干扰 push 前的密钥扫描
    _fake_key = "s" + "k-fake-not-a-secret"
    check("明文 key 检测：${VAR} 写法不误报",
          config._check_plaintext_keys('{"models": {"x": {"api_key": "${K}"}}}') == []
          and config._check_plaintext_keys(
              '{"models": {"x": {"api_key": "%s"}}}' % _fake_key) != [],
          "引用式放行、明文告警")

    # owner_open_ids 归一化：逗号拆分 + 去空白 + 剔空串 + ${VAR} 替换
    _norm = config._normalize(config._substitute({
        "channels": {"feishu": {"accounts": {
            "a": {"owner_open_ids": ["", " ou_a ,ou_b ",
                                     "${PORTABLE_EXTRA_ID}"]}}}}}), TMP)
    check("owner_open_ids 归一化：逗号拆分/去空/剔空串/${VAR}",
          _norm["channels"]["feishu"]["accounts"]["a"]["owner_open_ids"]
          == ["ou_a", "ou_b", os.environ["PORTABLE_EXTRA_ID"]],
          f"{_norm['channels']['feishu']['accounts']['a']['owner_open_ids']}")

    # -----------------------------------------------------------
    # 2. 人格层（组装 / 双模式 / 展示名 / 索引限长）
    # -----------------------------------------------------------
    print("\n===== 2. 人格层 =====")
    import core.personas as personas  # noqa: E402

    agents = personas.list_agents()
    check("list_agents 扫描到 fixture 的 2 个人格", agents == ["alpha", "beta"], f"{agents}")

    check("agent_display 解析 soul.md 的 name/emoji",
          personas.agent_display("alpha") == "🤖 AlphaBot",
          personas.agent_display("alpha"))

    p_lean = personas.build_system_prompt("alpha")
    check("lean 模式：必备段落齐全、注入 lean.md、不带全量踩坑",
          all(m in p_lean for m in ("[User Profile · Brief]", "[Homework]",
                                    "[Knowledge & Lessons]", "[Long-term Memory]",
                                    "[Rules]"))
          and "AlphaBot（极简注入版）" in p_lean and "[Shared Lessons]" not in p_lean,
          f"{len(p_lean)} 字符")

    _orig_lean = cfg["prompt"]["lean"]
    try:
        cfg["prompt"]["lean"] = False
        p_classic = personas.build_system_prompt("alpha")
    finally:
        cfg["prompt"]["lean"] = _orig_lean
    check("classic 模式：全量注入、比 lean 长、含共享踩坑段",
          "[Shared Lessons]" in p_classic and "[User Profile] (shared)" in p_classic
          and len(p_classic) > len(p_lean),
          f"classic={len(p_classic)} vs lean={len(p_lean)}")

    _idx_file = HOME / "alpha" / "memory" / "journal" / "portable_idx.md"
    _idx_file.write_text("x", encoding="utf-8")
    try:
        idx = personas._memory_index_lean("alpha")
        check("记忆索引限长：子目录折叠为计数、不逐文件列出",
              "journal/ (" in idx and "portable_idx" not in idx,
              f"共 {len(idx.splitlines())} 行")
    finally:
        _idx_file.unlink(missing_ok=True)

    # enabled:false 从花名册过滤（就地改内存配置对象，测完还原，不写回文件）
    _beta_cfg = config._config["agents"]["members"]["beta"]
    _had_enabled = "enabled" in _beta_cfg
    _orig_enabled = _beta_cfg.get("enabled", True)
    try:
        _beta_cfg["enabled"] = False
        check("members 设 enabled:false 后 list_agents 过滤掉该人格",
              personas.list_agents() == ["alpha"], f"{personas.list_agents()}")
    finally:
        if _had_enabled:
            _beta_cfg["enabled"] = _orig_enabled
        else:
            _beta_cfg.pop("enabled", None)

    # lean.md 漂移：秒级差静默、明显过期告警（容忍 300s）
    _warns = []
    _orig_warn = personas.log.warning
    _lean_f = HOME / "alpha" / "lean.md"
    _orig_mtime = _lean_f.stat().st_mtime
    try:
        personas.log.warning = lambda *a, **k: _warns.append(a)
        os.utime(_lean_f, (_orig_mtime + 10, _orig_mtime + 10))
        personas.build_system_prompt("alpha")
        _quiet = len(_warns) == 0
        os.utime(_lean_f, (_orig_mtime - 100000, _orig_mtime - 100000))
        personas.build_system_prompt("alpha")
        _warned = len(_warns) == 1
    finally:
        personas.log.warning = _orig_warn
        os.utime(_lean_f, (_orig_mtime, _orig_mtime))
    check("lean.md 漂移警告：秒级差静默、明显过期告警",
          _quiet and _warned, f"静默={_quiet} 告警={_warned}")

    # -----------------------------------------------------------
    # 3. 记忆沙箱（隔离 / 穿越防护 / 原子写）
    # -----------------------------------------------------------
    print("\n===== 3. 记忆工具（沙箱） =====")
    import core.memory as memory  # noqa: E402

    KW = "可移植霓虹灯塔"
    memory.set_current_agent("alpha")
    _w = memory.memory_write("portable.md", f"内容：{KW}", mode="overwrite")
    _r = memory.memory_read("portable.md")
    _s = memory.memory_search(KW)
    _l = memory.memory_list()
    check("记忆写/读/搜/列 一致",
          "Memory overwritten" in _w and KW in _r and KW in _s and "portable.md" in _l,
          "alpha/portable.md")

    _t1 = memory.memory_read("../../agent.json")
    _t2 = memory.memory_read("/etc/passwd")
    _t3 = memory.memory_write("../evil.md", "x")
    check("路径穿越防护：../ 与绝对路径均被拒",
          "Read failed" in _t1 and "Read failed" in _t2 and "Save failed" in _t3,
          f"{_t1[:12]}… / {_t3[:12]}…")

    memory.set_current_agent("beta")
    check("人格隔离：beta 读不到 alpha 的记忆文件",
          "not found" in memory.memory_read("portable.md"),
          memory.memory_read("portable.md")[:24])
    memory.set_current_agent("alpha")

    _residue = [p.name for p in (HOME / "alpha" / "memory").rglob("*.tmp")]
    check("原子写：内存目录不残留 .tmp 半截文件", not _residue, f"{_residue or '干净'}")

    # -----------------------------------------------------------
    # 4. 会话 / 绑定 / 摘要裁切
    # -----------------------------------------------------------
    print("\n===== 4. 会话与绑定 =====")
    memory.set_chat_agent("portable-chat", "beta")
    check("set_chat_agent / get_chat_agent 往返",
          memory.get_chat_agent("portable-chat") == "beta",
          memory.get_chat_agent("portable-chat"))
    check("未绑定 chat 回落 default 人格",
          memory.get_chat_agent("portable-unbound") == "alpha",
          memory.get_chat_agent("portable-unbound"))

    # 损坏兜底：坏文件改名备份 + 回落默认，不抛异常
    _bind_pre = memory.BINDINGS_FILE.read_text(encoding="utf-8") \
        if memory.BINDINGS_FILE.exists() else None
    try:
        memory.BINDINGS_FILE.write_text("{坏 json", encoding="utf-8")
        _fallback = memory.get_chat_agent("portable-chat")
        _backups = list(memory.BINDINGS_FILE.parent.glob("chatAgents.json.corrupted-*"))
        check("绑定文件损坏：不抛异常 + 回落默认 + 坏文件被备份",
              _fallback == "alpha" and len(_backups) >= 1,
              f"回落={_fallback}，备份={_backups[-1].name if _backups else '无'}")
    finally:
        if _bind_pre is not None:
            memory._atomic_write_text(memory.BINDINGS_FILE, _bind_pre)
        for _b in memory.BINDINGS_FILE.parent.glob("chatAgents.json.corrupted-*"):
            _b.unlink(missing_ok=True)

    # _safe_cut：裁切点不能落在工具调用组中间（孤儿 tool 会毒死会话 → 400 死循环）
    _base = [{"role": "user", "content": f"q{i}"} for i in range(5)]
    _group = [{"role": "assistant", "content": None,
               "tool_calls": [{"id": "a"}, {"id": "b"}]},
              {"role": "tool", "tool_call_id": "a", "content": "r1"},
              {"role": "tool", "tool_call_id": "b", "content": "r2"}]
    _msgs = _base + _group + [{"role": "assistant", "content": "答"}]
    check("_safe_cut：孤儿 tool 跳过整组、完整工具组保留",
          memory._safe_cut(_msgs, 6) == 8 and memory._safe_cut(_msgs, 5) == 5,
          f"cut(6)→{memory._safe_cut(_msgs, 6)}，cut(5)→{memory._safe_cut(_msgs, 5)}")

    check("delete_session：内存与磁盘一并清掉",
          (memory.SESSIONS.pop("alpha:portable-sess", None),
           memory.delete_session("alpha", "portable-sess"),
           "alpha:portable-sess" not in memory.SESSIONS
           and not memory._session_file("alpha", "portable-sess").exists())[2])

    # -----------------------------------------------------------
    # 5. 飞书渠道纯函数（离线，不连飞书）
    # -----------------------------------------------------------
    print("\n===== 5. 飞书 parser / 卡片 =====")
    import channels.feishu as feishu_mod  # noqa: E402
    from channels.feishu._card import build_card  # noqa: E402

    _flat = feishu_mod._flatten_post([
        [{"tag": "text", "text": "第一段 "},
         {"tag": "a", "text": "链接", "href": "https://example.com"},
         {"tag": "at", "name": "老王"}],
        [{"tag": "img"}, {"tag": "media"}],
    ])
    check("_flatten_post：text/a/at/img/media 各归其位 + 段落换行",
          _flat == ("第一段 链接(https://example.com)@老王\n"
                    "[image](contains a video, not supported; "
                    "please describe it separately)"),
          repr(_flat[:44]))

    check("_post_image_keys：提取图文混排的嵌图 key",
          feishu_mod._post_image_keys([
              [{"tag": "img", "image_key": "img_1"}, {"tag": "text", "text": "x"}],
              [{"tag": "img", "image_key": "img_2"}]]) == ["img_1", "img_2"])

    check("_extract_msg_text：text / 卡片 / 坏 json 降级",
          feishu_mod._extract_msg_text("text", json.dumps({"text": "引用我"})) == "引用我"
          and feishu_mod._extract_msg_text("interactive", json.dumps({
              "elements": [{"tag": "div",
                            "text": {"tag": "lark_md", "content": "卡片正文"}}]})) == "卡片正文"
          and feishu_mod._extract_msg_text("text", "{坏json") == "")

    _card = build_card("# 大标题\n正文", chat_id="oc_x",
                       meta={"model": "portable", "tokens": 42, "elapsed": 0.8})
    _md = _card["elements"][0]["text"]["content"]
    _note = [e for e in _card["elements"] if e.get("tag") == "note"]
    _note_text = _note[0]["elements"][0]["content"] if _note else ""
    check("build_card：markdown 标题降级为加粗 + footer 含 model/tokens/用时",
          "**大标题**" in _md and "# 大标题" not in _md
          and "portable" in _note_text and "42 tokens" in _note_text
          and "0.8s" in _note_text,
          f"note=「{_note_text}」")

    _opt = build_card("结论如下\n\n1. 方案甲\n2. 方案乙\n3. 方案丙", chat_id="oc_x")
    _actions = [e for e in _opt["elements"] if e.get("tag") == "action"]
    check("尾部编号选项自动生成按钮",
          bool(_actions) and len(_actions[0]["actions"]) >= 2,
          f"{len(_actions[0]['actions']) if _actions else 0} 个按钮")

    check("无选项回答不生成 action 元素",
          not any(e.get("tag") == "action" for e in _card["elements"]))

    # -----------------------------------------------------------
    # 6. 引擎兜底（工具异常 / 4xx 不重试）
    # -----------------------------------------------------------
    print("\n===== 6. 引擎兜底 =====")
    import core.engine as engine  # noqa: E402
    from core.sessionLog import log_file as _log_file  # noqa: E402

    class _FinalMsg:
        content = "看到了"
        tool_calls = None

        def model_dump(self, exclude_none=True):
            return {"role": "assistant", "content": "看到了"}

    _state = {"n": 0}
    _cap: dict = {}

    class _ToolCallMsg:
        content = None
        tool_calls = [SimpleNamespace(id="tc_boom",
                                      function=SimpleNamespace(name="_portable_boom",
                                                               arguments="{}"))]

        def model_dump(self, exclude_none=True):
            return {"role": "assistant", "content": None,
                    "tool_calls": [{"id": "tc_boom", "type": "function",
                                    "function": {"name": "_portable_boom",
                                                 "arguments": "{}"}}]}

    def _flaky(**kw):
        _state["n"] += 1
        if _state["n"] == 1:
            return SimpleNamespace(choices=[SimpleNamespace(message=_ToolCallMsg())],
                                   usage=SimpleNamespace(total_tokens=1))
        _cap["msgs"] = copy.deepcopy(kw["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=_FinalMsg())],
                               usage=SimpleNamespace(total_tokens=1))

    def _boom(**kw):
        raise OSError("portable disk gone")

    _orig_cwr = engine.chat_with_retry
    try:
        engine.TOOL_FUNCTIONS["_portable_boom"] = _boom
        engine.chat_with_retry = _flaky
        _ans, _ = engine.run_agent_meta("portable-tool-err", "触发工具异常", agent="alpha")
        _tool_out = next((m["content"] for m in _cap["msgs"]
                          if m.get("role") == "tool"), "")
        check("工具边界兜底：技能抛异常返回 Tool error、主循环继续",
              _ans == "看到了" and _tool_out.startswith("Tool error: OSError"),
              f"tool→{_tool_out[:32]!r}")
    finally:
        engine.chat_with_retry = _orig_cwr
        engine.TOOL_FUNCTIONS.pop("_portable_boom", None)
        _log_file("alpha", "portable-tool-err").unlink(missing_ok=True)
        memory.delete_session("alpha", "portable-tool-err")

    # 4xx：不重试、不记欠条、撤回本轮 user 消息、明确告知（构造真实 BadRequestError）
    try:
        import httpx  # noqa: E402
        from openai import BadRequestError  # noqa: E402

        def _raise_400(**kw):
            _req = httpx.Request("POST", "https://x/v1/chat/completions")
            _resp = httpx.Response(400, request=_req, json={"error": {"message": "bad"}})
            raise BadRequestError("bad request", response=_resp, body=None)

        engine.chat_with_retry = _raise_400
        _ans4, _meta4 = engine.run_agent_meta("portable-4xx", "这条请求不合法",
                                              agent="alpha", account="terminal")
        _sess4 = memory.SESSIONS.get("alpha:portable-4xx", [])
        check("4xx 不重试不记欠条、撤回消息、告知重试也没用",
              _meta4.get("pending") is not True
              and memory.load_pending("alpha") == []
              and "重试也没用" in _ans4
              and not any(m.get("content") == "这条请求不合法" for m in _sess4),
              f"answer={_ans4[:26]}…")
    finally:
        engine.chat_with_retry = _orig_cwr
        _log_file("alpha", "portable-4xx").unlink(missing_ok=True)
        memory.delete_session("alpha", "portable-4xx")

    # -----------------------------------------------------------
    # 汇总
    # -----------------------------------------------------------
    _passed = sum(1 for s, _, _ in RESULTS if s == "PASS")
    _failed = [r for r in RESULTS if r[0] == "FAIL"]
    print("\n" + "=" * 60)
    print(f"总计：{_passed}/{len(RESULTS)} PASS" +
          (f"，{len(_failed)} FAIL" if _failed else "，全绿 🎉"))
    if _failed:
        print("\n失败项详情：")
        for _, n, d in _failed:
            print(f"  ❌ {n} —— {d}")
    sys.exit(0 if not _failed else 1)
finally:
    shutil.rmtree(TMP, ignore_errors=True)