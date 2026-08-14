#!/usr/bin/env python3
"""
test_feishu_modules.py — 飞书渠道模块拆分验证脚本
================================================

验证 channels/feishu/ 下各拆分模块的功能是否正常，
覆盖导入、纯函数解析、卡片构建、owner 持久化等关键路径。
"""

import json
import os
import sys
import tempfile
import traceback

# 确保从 agentRunner 目录运行
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.chdir(ROOT)
sys.path.insert(0, ROOT)

# 颜色输出
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

checks_passed = 0
checks_failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global checks_passed, checks_failed
    if ok:
        checks_passed += 1
        print(f"{GREEN}✅ {name}{RESET} — {detail}")
    else:
        checks_failed += 1
        print(f"{RED}❌ {name}{RESET} — {detail}")


def section(title: str) -> None:
    print(f"\n===== {title} =====")


# ============================================================
# 1. 导入验证
# ============================================================
section("1. 导入验证")

try:
    from channels.feishu import FeishuChannel, shutdown_event_loops
    check("FeishuChannel 导入", True)
except Exception as e:
    check("FeishuChannel 导入", False, str(e))
    traceback.print_exc()

try:
    from channels.feishu import (
        _flatten_post, _post_image_keys, _extract_msg_text,
        _card_md, _extract_suggestions, _suggestion_buttons,
        _footer_note, build_card,
    )
    check("_parser 实用函数导入", True)
except Exception as e:
    check("_parser 实用函数导入", False, str(e))

try:
    from channels.feishu import OWNER_FILE, _load_owner, _save_owner, get_target_chat
    check("_owner 函数导入", True)
except Exception as e:
    check("_owner 函数导入", False, str(e))

try:
    from channels.feishu import FILE_MAX_BYTES, FILE_TEXT_LIMIT, TEXT_EXTS
    check("常量导入", FILE_MAX_BYTES == 5 * 1024 * 1024,
          f"FILE_MAX_BYTES={FILE_MAX_BYTES}")
except Exception as e:
    check("常量导入", False, str(e))

# 子模块独立导入
try:
    from channels.feishu._loop_proxy import _ThreadLoopProxy, _LOOPS
    check("_loop_proxy 独立导入", True)
except Exception as e:
    check("_loop_proxy 独立导入", False, str(e))

try:
    from channels.feishu._parser import _flatten_post as p1, _card_md as p2
    check("_parser 独立导入", True)
except Exception as e:
    check("_parser 独立导入", False, str(e))

try:
    from channels.feishu._card import build_card as c1, reply_text as c2
    check("_card 独立导入", True)
except Exception as e:
    check("_card 独立导入", False, str(e))

try:
    from channels.feishu._owner import _load_owner as o1, OWNER_FILE as o2
    check("_owner 独立导入", True)
except Exception as e:
    check("_owner 独立导入", False, str(e))


# ============================================================
# 2. _parser 纯函数测试
# ============================================================
section("2. _parser 消息解析")

# _flatten_post: text / a / at / img / media
flat = _flatten_post([
    [{"tag": "text", "text": "第一段 "},
     {"tag": "a", "text": "链接", "href": "https://example.com"},
     {"tag": "at", "name": "测试用户"},
     {"tag": "img"},
     {"tag": "media"}],
    [{"tag": "text", "text": "第二段"}],
])
expected_flat = "第一段 链接(https://example.com)@测试用户[图片]（包含视频，暂不支持，请单独描述）\n第二段"
check("_flatten_post: text/a/at/img+media 展平",
      flat == expected_flat, repr(flat))

# _flatten_post: 空/异常
check("_flatten_post: 空列表返回空", _flatten_post([]) == "", repr(_flatten_post([])))
check("_flatten_post: 非列表返回空", _flatten_post(None) == "", "")

# _post_image_keys
img_keys = _post_image_keys([
    [{"tag": "img", "image_key": "img_1"},
     {"tag": "text", "text": "hello"},
     {"tag": "img", "image_key": "img_2"}],
])
check("_post_image_keys: 提取多图", img_keys == ["img_1", "img_2"], str(img_keys))

# _extract_msg_text: text
q_text = _extract_msg_text("text", json.dumps({"text": "引用我"}))
check("_extract_msg_text: text 类型", q_text == "引用我", q_text)

# _extract_msg_text: post
q_post = _extract_msg_text("post", json.dumps({
    "content": [[{"tag": "text", "text": "post 内容"}]]
}))
check("_extract_msg_text: post 类型", q_post == "post 内容", q_post)

# _extract_msg_text: interactive (卡片)
q_card = _extract_msg_text("interactive", json.dumps({
    "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": "卡片正文"}}]
}))
check("_extract_msg_text: interactive 类型", q_card == "卡片正文", q_card)

# _extract_msg_text: 坏 json 降级
q_bad = _extract_msg_text("text", "{坏json")
check("_extract_msg_text: 坏 json 返回空", q_bad == "", repr(q_bad))

# _extract_msg_text: 其他类型
q_other = _extract_msg_text("file", "{}")
check("_extract_msg_text: 未知类型占位", "file" in q_other, repr(q_other))

# _card_md: 标题降级为加粗
md_result = _card_md("# 大标题\n正文\n## 小标题")
check("_card_md: 标题降级为加粗",
      "**大标题**" in md_result and "**小标题**" in md_result and "# 大标题" not in md_result,
      md_result)

# _extract_suggestions
sug1 = _extract_suggestions("""以下是选项：
1. 查看日志
2. 重启服务
3. 查看状态""")
check("_extract_suggestions: 中文格式",
      [(n, t) for n, t in sug1] == [("1", "查看日志"), ("2", "重启服务"), ("3", "查看状态")],
      str(sug1))

sug2 = _extract_suggestions("""**选项 1：** 查看日志
**选项 2：** 重启服务""")
check("_extract_suggestions: 加粗选项格式",
      len(sug2) == 2 and sug2[0][1] == "查看日志", str(sug2))

sug3 = _extract_suggestions("没有选项的回答")
check("_extract_suggestions: 无选项返回空列表", sug3 == [], str(sug3))

# _suggestion_buttons
btns = _suggestion_buttons("oc_test", [("1", "查看日志"), ("2", "重启服务")])
check("_suggestion_buttons: 结构正确",
      btns["tag"] == "action" and len(btns["actions"]) == 2
      and btns["actions"][0]["value"]["text"] == "1",
      str(btns))

# _footer_note
meta = {"model": "k3", "tokens": 42, "elapsed": 1.5}
footer = _footer_note(meta)
check("_footer_note: 生成 footer",
      footer is not None and "k3" in footer["elements"][0]["content"],
      str(footer))

check("_footer_note: 无 meta 返回 None", _footer_note(None) is None, "")


# ============================================================
# 3. _card 卡片构建测试
# ============================================================
section("3. _card 卡片构建")

# build_card: 基本结构
card = build_card("hello", title="Test", chat_id="oc_x",
                   meta={"model": "k3", "tokens": 10, "elapsed": 0.5},
                   card_footer=True, card_buttons=False)
check("build_card: 有 header", "header" in card and card["header"]["template"] == "blue", "")
check("build_card: 有 elements", len(card["elements"]) >= 1,
      f"{len(card['elements'])} elements")
check("build_card: footer 存在",
      any(e.get("tag") == "note" for e in card["elements"]), "")

# build_card: 无 footer
card_no_footer = build_card("test", card_footer=False, card_buttons=False)
check("build_card: 无 footer 时不生成 note",
      not any(e.get("tag") == "note" for e in card_no_footer["elements"]), "")

# build_card: 建议按钮
card_with_btns = build_card("1. 选项A\n2. 选项B", chat_id="oc_x",
                             card_footer=False, card_buttons=True)
check("build_card: 包含建议按钮",
      any(e.get("tag") == "action" for e in card_with_btns["elements"]), "")


# ============================================================
# 4. _owner 持久化测试
# ============================================================
section("4. _owner 主人档案")

# 临时 owner 文件：通过修改模块级常量实现隔离
import channels.feishu._owner as owner_mod
import tempfile as _tf
from pathlib import Path

tmp_fd, tmp_path = _tf.mkstemp(suffix=".json")
os.close(tmp_fd)
os.unlink(tmp_path)  # 确保文件不存在，_load_owner 返回空
owner_mod.OWNER_FILE = Path(tmp_path)

# 写入测试
owner_mod._save_owner("finance", "ou_a", "oc_fin")
owner_mod._save_owner("finance", "ou_b", "oc_fin2")
owner_mod._save_owner("default", "ou_old", "oc_old")

# 读取验证
target = owner_mod.get_target_chat("finance")
check("get_target_chat: 取第一个主人", target == "oc_fin", target or "None")

data = owner_mod._load_owner()
check("_load_owner: 有 finance 命名空间",
      "finance" in data and len(data["finance"]) == 2, str(data))

check("_load_owner: 有 default 命名空间",
      "default" in data and data["default"]["ou_old"] == "oc_old", "")

# 清理
p = Path(tmp_path)
if p.exists():
    os.unlink(tmp_path)


# ============================================================
# 5. _loop_proxy 测试
# ============================================================
section("5. _loop_proxy 事件循环")

from channels.feishu._loop_proxy import _ThreadLoopProxy, _LOOPS, _LOOPS_LOCK
import threading
import asyncio

# 创建代理并验证每个线程有独立 loop
proxy = _ThreadLoopProxy()
results = {}
errors = []

def run_in_thread(name):
    try:
        loop = proxy._loop()
        results[name] = id(loop)
        # 验证 loop 正在运行
        results[f"{name}_is_running"] = loop.is_running()
    except Exception as e:
        errors.append(str(e))

threads = [threading.Thread(target=run_in_thread, args=(f"t{i}",)) for i in range(3)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("_ThreadLoopProxy: 多线程独立 loop",
      len(results) >= 3 and len(set(results.get(f"t{i}", 0) for i in range(3))) == 3,
      f"results={results}")

check("_ThreadLoopProxy: 无错误", len(errors) == 0, str(errors) if errors else "OK")


# ============================================================
# 6. 向后兼容：feishu_mod 风格导入
# ============================================================
section("6. 向后兼容导入")

import channels.feishu as feishu_mod

check("feishu_mod._flatten_post 可访问",
      hasattr(feishu_mod, "_flatten_post"), "")
check("feishu_mod._post_image_keys 可访问",
      hasattr(feishu_mod, "_post_image_keys"), "")
check("feishu_mod._extract_msg_text 可访问",
      hasattr(feishu_mod, "_extract_msg_text"), "")
check("feishu_mod._card_md 可访问",
      hasattr(feishu_mod, "_card_md"), "")
check("feishu_mod._extract_suggestions 可访问",
      hasattr(feishu_mod, "_extract_suggestions"), "")
check("feishu_mod._suggestion_buttons 可访问",
      hasattr(feishu_mod, "_suggestion_buttons"), "")
check("feishu_mod._footer_note 可访问",
      hasattr(feishu_mod, "_footer_note"), "")
check("feishu_mod.build_card 可访问",
      hasattr(feishu_mod, "build_card"), "")
check("feishu_mod.OWNER_FILE 可访问",
      hasattr(feishu_mod, "OWNER_FILE"), "")
check("feishu_mod._load_owner 可访问",
      hasattr(feishu_mod, "_load_owner"), "")
check("feishu_mod._save_owner 可访问",
      hasattr(feishu_mod, "_save_owner"), "")
check("feishu_mod.get_target_chat 可访问",
      hasattr(feishu_mod, "get_target_chat"), "")
check("feishu_mod.FeishuChannel 可访问",
      hasattr(feishu_mod, "FeishuChannel"), "")
check("feishu_mod.shutdown_event_loops 可访问",
      hasattr(feishu_mod, "shutdown_event_loops"), "")


# ============================================================
# 7. 外部接口验证
# ============================================================
section("7. 外部接口验证")

try:
    from channels import build_feishu_channels
    check("channels.build_feishu_channels 可导入", True)
except Exception as e:
    check("channels.build_feishu_channels 可导入", False, str(e))

try:
    from channels.feishu import shutdown_event_loops
    check("channels.feishu.shutdown_event_loops 可导入", True)
except Exception as e:
    check("channels.feishu.shutdown_event_loops 可导入", False, str(e))

try:
    from channels import Channel
    check("channels.Channel 基类可导入", True)
except Exception as e:
    check("channels.Channel 基类可导入", False, str(e))


# ============================================================
# 8. 子模块间依赖验证
# ============================================================
section("8. 子模块依赖验证")

# _card.py 依赖 _parser.py
try:
    from channels.feishu._card import build_card as bc
    result = bc("test", card_footer=False, card_buttons=False)
    check("_card → _parser 依赖链正常", "elements" in result, "")
except Exception as e:
    check("_card → _parser 依赖链正常", False, str(e))

# channel.py 依赖 _card.py + _owner.py + _parser.py
try:
    from channels.feishu.channel import FeishuChannel as FC
    check("channel 依赖链正常导入", True)
except Exception as e:
    check("channel 依赖链正常导入", False, str(e))


# ============================================================
# 汇总
# ============================================================
total = checks_passed + checks_failed
print(f"\n============================================================")
print(f"总计：{total} 项检查 — {GREEN}{checks_passed} PASS{RESET}", end="")
if checks_failed:
    print(f"  {RED}{checks_failed} FAIL{RESET}")
else:
    print(f"  {GREEN}0 FAIL{RESET}")
print(f"============================================================")

if checks_failed:
    sys.exit(1)
else:
    print(f"\n{GREEN}🎉 所有模块拆分验证通过！{RESET}")
    sys.exit(0)
