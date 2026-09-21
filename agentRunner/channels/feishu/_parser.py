"""
channels/feishu/_parser.py — 消息解析纯函数
==========================================

把飞书原始消息格式转成模型能理解的文本。
所有函数均为纯函数，无外部状态，易于单元测试。
"""

import json
import re

# 能直接当文本读的文件后缀（收到文件消息时提取内容喂给模型）
TEXT_EXTS = {"txt", "md", "csv", "json", "log", "py", "js", "ts", "yaml",
             "yml", "xml", "html", "css", "sh", "sql", "ini", "cfg", "toml",
             "java", "go", "rs", "c", "cpp", "h", "vue", "jsx", "tsx"}
FILE_MAX_BYTES = 5 * 1024 * 1024   # 超过 5MB 的文件拒收
FILE_TEXT_LIMIT = 8000             # 注入 prompt 的文本上限（字符）


def _flatten_post(blocks) -> str:
    """富文本（post）消息 → 纯文本。
    结构：{"title": ..., "content": [[段落=[元素...]], ...]}
    text 取文字、a 取文字(链接)、at 取@人名、img/media 留占位提示"""
    if not isinstance(blocks, list):
        return ""
    lines = []
    for para in blocks:
        if not isinstance(para, list):
            continue
        parts = []
        for el in para:
            if not isinstance(el, dict):
                continue
            tag = el.get("tag")
            if tag == "text":
                parts.append(el.get("text", ""))
            elif tag == "a":
                parts.append(f"{el.get('text', '')}({el.get('href', '')})")
            elif tag == "at":
                parts.append(f"@{el.get('name', '')}")
            elif tag == "img":
                parts.append("[image]")
            elif tag == "media":
                parts.append("(contains a video, not supported; please describe it separately)")
        lines.append("".join(parts))
    return "\n".join(lines).strip()


def _post_image_keys(blocks) -> list[str]:
    """从富文本 post 里提取所有图片的 image_key（图文混排场景）"""
    keys = []
    if not isinstance(blocks, list):
        return keys
    for para in blocks:
        if not isinstance(para, list):
            continue
        for el in para:
            if isinstance(el, dict) and el.get("tag") == "img" \
                    and el.get("image_key"):
                keys.append(el["image_key"])
    return keys


def _extract_msg_text(msg_type: str, content_str: str) -> str:
    """从消息体提取纯文本（引用回复时展示被引用内容用）。
    支持 text / post / 卡片(interactive)；其他类型给个占位说明"""
    try:
        content = json.loads(content_str or "{}")
    except json.JSONDecodeError:
        return ""
    if msg_type == "text":
        return str(content.get("text", "")).strip()
    if msg_type == "post":
        return _flatten_post(content.get("content", []))
    if msg_type == "interactive":
        texts = []
        for el in content.get("elements", []):
            t = el.get("text")
            if isinstance(t, dict) and t.get("content"):
                texts.append(t["content"])
        return "\n".join(texts).strip()
    return f"({msg_type} message)" if msg_type else ""


def _card_md(text: str) -> str:
    """把 markdown 标题转成加粗，其余原样保留。
    飞书卡片不支持 # 标题，这里降级成加粗行。"""
    lines = []
    for line in text.splitlines():
        m = re.match(r"^#{1,6}\s+(.*)", line)
        lines.append(f"**{m.group(1)}**" if m else line)
    return "\n".join(lines)


def _extract_suggestions(text: str) -> list[tuple[str, str]]:
    """从回答尾部提取编号选项，做成按钮（点按钮 = 替用户发这个编号）。
    兼容格式：「1. xxx」「2、xxx」「选项 3：xxx」「**选项 1：xxx**」，
    允许选项后面再跟一两句追问。≥2 个选项才生成按钮。
    返回 [(编号, 选项文字)]，无则空列表"""
    pattern = re.compile(
        r"^\s*(?:\*\*)?(?:选项\s*)?"
        r"(?:(\d+)\s*[.、\)：:]|([1-9])\ufe0f?\u20e3)"
        r"\s*(.+?)(?:\*\*)?\s*$")
    lines = text.rstrip().splitlines()[-8:]
    runs: list[tuple[int, list[tuple[int, str]]]] = []
    current: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m and len(m.group(3)) <= 30:
            label = m.group(3).split("——")[0].replace("**", "").strip()
            num = int(m.group(1) or m.group(2))
            current.append((num, label))
        else:
            if current:
                runs.append((i - 1, current))
                current = []
    if current:
        runs.append((len(lines) - 1, current))

    if not runs:
        return []
    end, last = runs[-1]
    if (len(last) >= 2
            and [n for n, _ in last] == list(range(1, len(last) + 1))
            and len(lines) - 1 - end <= 3):
        return [(str(n), t) for n, t in last]
    return []


def _suggestion_buttons(chat_id: str,
                        options: list[tuple[str, str]]) -> dict:
    """编号选项 → 按钮组。按钮 value 携带 chat_id 和编号，
    回调时当作"用户发了这个编号"处理。
    注意：不要给任何按钮设 primary——飞书点击后卡片不刷新，
    primary 的常驻高亮会被误看成"选中了它" """
    return {
        "tag": "action",
        "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text",
                         "content": f"{num}️⃣ {label[:12]}"},
                "type": "default",
                "value": {"kind": "suggestion", "chat_id": chat_id, "text": num},
            }
            for num, label in options
        ],
    }


def _fmt_tokens(n: int) -> str:
    """token 数人性化：1234 → 1.2K，1234567 → 1.2M"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


def _footer_note(meta: dict | None) -> dict | None:
    """卡片没有原生 footer，用 note 备注元素当页脚。
    模型名始终保留；轮次/耗时/速度/缓存/输入输出有埋点才展示（老 meta 也兼容）"""
    if not meta:
        return None
    parts = [meta["model"]]
    if meta.get("rounds"):
        parts.append(f"{meta['rounds']}轮·{meta.get('steps', 0)}步")
    if meta.get("llm_s") is not None:
        parts.append(f"LLM {meta['llm_s']}s·工具 {meta.get('tool_s', 0)}s")
    out_tokens = meta.get("out_tokens", 0)
    llm_s = meta.get("llm_s", 0)
    if out_tokens and llm_s:
        parts.append(f"{out_tokens / llm_s:.0f} tok/s")
    in_tokens = meta.get("in_tokens", 0)
    cached = meta.get("cached_tokens", 0)
    if in_tokens and cached:
        parts.append(f"缓存命中 {cached * 100 // in_tokens}%")
    if in_tokens or out_tokens:
        parts.append(f"输入 {_fmt_tokens(in_tokens)}·输出 {_fmt_tokens(out_tokens)} tok")
    elif meta.get("tokens"):
        parts.append(f"{meta['tokens']} tokens")
    parts.append(f"用时 {meta['elapsed']}s")
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": " · ".join(parts)}],
    }
