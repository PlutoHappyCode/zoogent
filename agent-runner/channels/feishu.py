"""
channels/feishu.py — 飞书渠道（长连接模式，多账号版）
======================================================

一个账号 = 一个独立飞书应用 = 一个 FeishuChannel 实例，
每个实例一套 lark.Client + ws.Client + 事件处理器。

两种账号：
  - default（fixed_agent=None）：多人格模式，保留 /agent 切换行为
  - 固定人格账号（fixed_agent 非 None）：所有消息直接以该人格跑，
    /agent、/agents 命令被拒，/who 显示固定人格

配置驱动（agent.json → channels.feishu）：
  reactions.processing / reactions.done（全账号共享的表情代号）
  card.footer / card.buttons（全账号共享的卡片开关）
  accounts.<名字>.app_id / app_secret / owner_open_ids / agent / enabled

主人档案 memory/owner.json 按账号命名空间：
  {"<account>": {"<open_id>": "<chat_id>"}}
旧的平铺格式启动时自动迁移到 "default" 命名空间。
"""

import json
import re
import threading
import time

import base64
import tempfile
from pathlib import Path

import lark_oapi as lark

# ---------------------------------------------------------------
# 修复 lark-oapi ws 客户端的模块级共享 event loop
# ---------------------------------------------------------------
# lark_oapi.ws.client 在 import 时创建了一个全局 loop，所有 ws.Client
# 实例共用。多账号各起一个线程时，先到的线程把 loop 跑起来后，其余线程
# 再调 run_until_complete 就报 "This event loop is already running"。
# 把模块全局 loop 换成线程本地代理：每个渠道线程拿到自己独立的 loop。
import asyncio

import lark_oapi.ws.client as _ws_client

_LOOPS: list = []          # 所有渠道线程创建的 event loop（退出时要清理）
_LOOPS_LOCK = threading.Lock()


class _ThreadLoopProxy:
    """把 run_until_complete / create_task 分发到调用线程自己的 event loop"""

    def __init__(self) -> None:
        self._local = threading.local()

    def _loop(self) -> asyncio.AbstractEventLoop:
        loop = getattr(self._local, "loop", None)
        if loop is None or loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._local.loop = loop
            with _LOOPS_LOCK:
                _LOOPS.append(loop)
        return loop

    def run_until_complete(self, coro):
        return self._loop().run_until_complete(coro)

    def create_task(self, coro):
        return self._loop().create_task(coro)


_ws_client.loop = _ThreadLoopProxy()


def shutdown_event_loops(wait: float = 1.5) -> None:
    """优雅退出：取消所有渠道 loop 上的挂起任务（SDK 的缓存清理任务等），
    让 Ctrl+C / kill 时不再刷 'Task was destroyed but it is pending'"""
    with _LOOPS_LOCK:
        loops = [l for l in _LOOPS if not l.is_closed()]

    def _cancel_all(loop) -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for loop in loops:
        try:
            loop.call_soon_threadsafe(_cancel_all, loop)
        except RuntimeError:
            pass
    if loops:
        time.sleep(wait)  # 给各 loop 一点时间消化取消
from lark_oapi.api.im.v1 import (
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageReactionRequest,
    Emoji,
    GetMessageRequest,
    GetMessageResourceRequest,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    CallBackToast,
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)

from core import (
    agent_display,
    get_chat_agent,
    handle_command,
    run_agent_meta,
)
from core.asr import transcribe
from core.config import BASE_DIR
from core.log import get_logger
from core.personas import home

from .base import Channel

log = get_logger("feishu")

OWNER_FILE = BASE_DIR / "memory" / "owner.json"

# 能直接当文本读的文件后缀（收到文件消息时提取内容喂给模型）
TEXT_EXTS = {"txt", "md", "csv", "json", "log", "py", "js", "ts", "yaml",
             "yml", "xml", "html", "css", "sh", "sql", "ini", "cfg", "toml",
             "java", "go", "rs", "c", "cpp", "h", "vue", "jsx", "tsx"}
FILE_MAX_BYTES = 5 * 1024 * 1024   # 超过 5MB 的文件拒收
FILE_TEXT_LIMIT = 8000             # 注入 prompt 的文本上限（字符）


# ---------------------------------------------------------------
# 主人档案：{"<account>": {"<open_id>": "<chat_id>"}}，重启不丢。
# 调度器靠它找到每个机器人"往哪推"
# 多账号各跑一个线程，_load/_save 的读改写用一把可重入锁保护
# ---------------------------------------------------------------
_OWNER_LOCK = threading.RLock()


def _load_owner() -> dict:
    with _OWNER_LOCK:
        if not OWNER_FILE.exists():
            return {}
        data = json.loads(OWNER_FILE.read_text(encoding="utf-8"))
        # 旧平铺格式 {"ou_x": "oc_y"} → 迁移到 "default" 命名空间
        if any(isinstance(v, str) for v in data.values()):
            flat = {k: v for k, v in data.items() if isinstance(v, str)}
            data = {k: v for k, v in data.items() if isinstance(v, dict)}
            data.setdefault("default", {}).update(flat)
            OWNER_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8")
            log.info("📦 owner.json 已迁移为按账号命名空间格式")
        return data


def _save_owner(account: str, open_id: str, chat_id: str) -> None:
    with _OWNER_LOCK:
        data = _load_owner()
        data.setdefault(account, {})[open_id] = chat_id
        OWNER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                              encoding="utf-8")


def get_target_chat(account: str) -> str | None:
    """调度器用：该账号下主人的 chat_id（取第一位主人）"""
    data = _load_owner().get(account, {})
    return next(iter(data.values()), None)


# ---------------------------------------------------------------
# 飞书卡片：msg_type=interactive，正文用 lark_md 渲染 markdown
# 支持 **加粗**、列表、链接、[text](url)、<font color> 等；
# 不支持 # 标题，这里把标题降级成加粗行
# ---------------------------------------------------------------
# ---------------------------------------------------------------
# 富文本（post）消息 → 纯文本
# 粘贴带格式的内容（从卡片复制的加粗/列表）、引用回复都会变成 post 类型。
# 结构：{"title": ..., "content": [[段落=[元素...]], ...]}
# text 取文字、a 取文字(链接)、at 取@人名、img/media 留占位提示，其余跳过
# ---------------------------------------------------------------
def _flatten_post(blocks) -> str:
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
                parts.append("[图片]")  # 图片本体由 _post_image_keys 提取后单独传给模型
            elif tag == "media":
                parts.append("（包含视频，暂不支持，请单独描述）")
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
        # 卡片：把 div/note 里的文本拼起来（bot 自己的卡片就是 markdown 正文）
        texts = []
        for el in content.get("elements", []):
            t = el.get("text")
            if isinstance(t, dict) and t.get("content"):
                texts.append(t["content"])
        return "\n".join(texts).strip()
    return f"（{msg_type} 类型的消息）" if msg_type else ""


def _card_md(text: str) -> str:
    """把 markdown 标题转成加粗，其余原样保留"""
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
        r"(?:(\d+)\s*[.、\)：:]|([1-9])\ufe0f?\u20e3)"  # 1. / 1、 / 1: / 1️⃣
        r"\s*(.+?)(?:\*\*)?\s*$")
    lines = text.rstrip().splitlines()[-8:]
    runs: list[tuple[int, list[tuple[int, str]]]] = []  # (块结束行号, 块内容)
    current: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m and len(m.group(3)) <= 30:
            # 清理选项文字：去掉加粗星号、破折号后的补充说明
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
    # 只认"从 1 开始连续编号、且结束于结尾 3 行以内"的块
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


def _footer_note(meta: dict | None) -> dict | None:
    """卡片没有原生 footer，用 note 备注元素当页脚：模型 · token · 用时"""
    if not meta:
        return None
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text",
                      "content": f"{meta['model']} · {meta['tokens']} tokens"
                                 f" · 用时 {meta['elapsed']}s"}],
    }


class FeishuChannel(Channel):
    """一个飞书应用账号。fixed_agent 非 None = 固定人格机器人"""

    def __init__(self, account: str, account_cfg: dict,
                 shared_cfg: dict) -> None:
        self.account = account
        self.fixed_agent: str | None = account_cfg.get("agent")
        self.app_id = account_cfg["app_id"]
        self.app_secret = account_cfg["app_secret"]
        self.owner_open_ids = account_cfg.get("owner_open_ids", [])
        # 白名单总开关（全账号共享）：false = 任何人都能和机器人说话，
        # owner_open_ids 配置保留但不执行拦截
        self.whitelist = bool(shared_cfg.get("whitelist", True))
        reactions = shared_cfg.get("reactions", {})
        # 表情回应：处理中贴"敲键盘"，完成时撕掉换"撒花"
        # 注意：emoji_type 区分大小写，非法代号会报 231001
        # 实测合法：Typing(敲键盘) PARTY(撒花) FIREWORKS THUMBSUP OK CLAP WAVE
        self.reaction_processing = reactions.get("processing", "Typing")
        self.reaction_done = reactions.get("done", "PARTY")
        card = shared_cfg.get("card", {})
        self.card_footer = card.get("footer", True)
        self.card_buttons = card.get("buttons", True)

        self.lark_client = (
            lark.Client.builder()
            .app_id(self.app_id)
            .app_secret(self.app_secret)
            .log_level(lark.LogLevel.INFO)
            .build()
        )

    @property
    def name(self) -> str:
        return f"feishu:{self.account}"

    def get_target_chat(self) -> str | None:
        """调度器用：本账号下主人的 chat_id"""
        return get_target_chat(self.account)

    def _current_agent(self, chat_id: str) -> str:
        """固定人格账号无视 chat_agents.json 绑定"""
        return self.fixed_agent or get_chat_agent(chat_id)

    # -----------------------------------------------------------
    # 发消息（两种姿势：reply 回复某条消息 / send 主动推送）
    # -----------------------------------------------------------
    def reply_text(self, message_id: str, text: str) -> None:
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .msg_type("text")
                .build()
            )
            .build()
        )
        response = self.lark_client.im.v1.message.reply(request)
        if not response.success():
            log.warning("[%s] 回复失败: %s %s", self.account, response.code, response.msg)

    def build_card(self, text: str, title: str = "", chat_id: str = "",
                   meta: dict | None = None) -> dict:
        """拼卡片：标题 + markdown 正文 + 建议按钮（如有）+ footer（如有）"""
        elements: list[dict] = [
            {"tag": "div", "text": {"tag": "lark_md", "content": _card_md(text)}}
        ]
        if chat_id and self.card_buttons:
            options = _extract_suggestions(text)
            if options:
                elements.append(_suggestion_buttons(chat_id, options))
        if self.card_footer:
            footer = _footer_note(meta)
            if footer:
                elements.append(footer)

        card: dict = {"config": {"wide_screen_mode": True}, "elements": elements}
        if title:
            card["header"] = {
                "template": "blue",
                "title": {"tag": "plain_text", "content": title},
            }
        return card

    def reply_card(self, message_id: str, text: str, title: str = "",
                   chat_id: str = "", meta: dict | None = None) -> None:
        """用卡片回复 Agent 的答案，失败时降级为纯文本"""
        card = self.build_card(text, title, chat_id, meta)
        request = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .content(json.dumps(card, ensure_ascii=False))
                .msg_type("interactive")
                .build()
            )
            .build()
        )
        response = self.lark_client.im.v1.message.reply(request)
        if not response.success():
            log.warning("[%s] 卡片回复失败（%s %s），降级为纯文本",
                        self.account, response.code, response.msg)
            self.reply_text(message_id, text)

    def send(self, chat_id: str, text: str, meta: dict | None = None) -> None:
        """主动推送到某个会话（调度器/哨兵专用）：卡片格式，失败降级纯文本"""
        title, body = "", text
        if text.startswith("📮 ") and "\n\n" in text:
            # 调度器/哨兵推送格式「📮 标题\n\n正文」→ 标题进卡片头
            title, body = text.split("\n\n", 1)
        card = self.build_card(body, title=title, chat_id=chat_id, meta=meta)
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .content(json.dumps(card, ensure_ascii=False))
                .msg_type("interactive")
                .build()
            )
            .build()
        )
        response = self.lark_client.im.v1.message.create(request)
        if not response.success():
            log.warning("[%s] 卡片推送失败（%s %s），降级为纯文本",
                        self.account, response.code, response.msg)
            self._send_text(chat_id, text)

    def _send_text(self, chat_id: str, text: str) -> None:
        """纯文本推送（send 的降级路径）"""
        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .content(json.dumps({"text": text}, ensure_ascii=False))
                .msg_type("text")
                .build()
            )
            .build()
        )
        response = self.lark_client.im.v1.message.create(request)
        if not response.success():
            log.warning("[%s] 推送失败: %s %s", self.account, response.code, response.msg)

    # -----------------------------------------------------------
    # 表情回应：收到贴 processing，完成撕掉换 done
    # -----------------------------------------------------------
    def add_reaction(self, message_id: str, emoji_type: str) -> str | None:
        """给消息贴表情，返回 reaction_id（删除时要用），失败返回 None"""
        request = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                .build()
            )
            .build()
        )
        response = self.lark_client.im.v1.message_reaction.create(request)
        if response.success():
            return response.data.reaction_id
        log.warning("[%s] 贴表情失败: %s %s", self.account, response.code, response.msg)
        return None

    def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        """撕掉之前贴的表情"""
        request = (
            DeleteMessageReactionRequest.builder()
            .message_id(message_id)
            .reaction_id(reaction_id)
            .build()
        )
        self.lark_client.im.v1.message_reaction.delete(request)

    def _audio_to_text(self, message_id: str, file_key: str) -> str | None:
        """下载语音（opus）→ faster-whisper 转文字，失败返回 None"""
        data = self._download_resource(message_id, file_key, "file")
        if data is None:
            return None
        tmp = tempfile.NamedTemporaryFile(suffix=".opus", delete=False)
        try:
            tmp.write(data)
            tmp.close()
            text = transcribe(tmp.name)
            return text or None
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    # -----------------------------------------------------------
    # 引用回复：按 message_id 拉取被引用消息的纯文本
    # -----------------------------------------------------------
    def _fetch_message_text(self, message_id: str) -> str | None:
        """拉一条消息并提取纯文本（用户引用回复时拼进上下文），失败返回 None"""
        request = GetMessageRequest.builder().message_id(message_id).build()
        try:
            response = self.lark_client.im.v1.message.get(request)
        except Exception as e:
            log.warning("[%s] 拉取引用消息异常: %s", self.account, e)
            return None
        if not response.success():
            log.warning("[%s] 拉取引用消息失败: %s %s",
                        self.account, response.code, response.msg)
            return None
        items = response.data.items if response.data else None
        if not items:
            return None
        msg = items[0]
        return _extract_msg_text(msg.msg_type, msg.body.content) or None

    # -----------------------------------------------------------
    # 图片 / 文件消息：下载资源 → 转成模型能吃的东西
    # -----------------------------------------------------------
    def _download_resource(self, message_id: str, file_key: str,
                           rtype: str) -> bytes | None:
        """下载消息里的图片/文件（im.message.resource.get），失败返回 None"""
        request = (GetMessageResourceRequest.builder()
                   .message_id(message_id).file_key(file_key)
                   .type(rtype).build())
        try:
            response = self.lark_client.im.v1.message_resource.get(request)
        except Exception as e:
            log.warning("[%s] 资源下载异常: %s", self.account, e)
            return None
        if not response.success():
            log.warning("[%s] 资源下载失败: %s %s",
                        self.account, response.code, response.msg)
            return None
        return response.file.read()

    def _image_to_b64(self, message_id: str, image_key: str) -> str | None:
        """下载图片 → base64（k3 已实测支持视觉输入）"""
        data = self._download_resource(message_id, image_key, "image")
        return base64.b64encode(data).decode() if data else None

    def _file_to_prompt(self, message_id: str, file_key: str,
                        file_name: str) -> str:
        """下载文件 → 提取文本 → 拼成用户输入（文本类直读，pdf 用 pypdf）"""
        data = self._download_resource(message_id, file_key, "file")
        if data is None:
            return f"（用户发来文件 {file_name}，但下载失败了，请用户重发）"
        if len(data) > FILE_MAX_BYTES:
            mb = len(data) / 1024 / 1024
            return (f"（用户发来文件 {file_name}（{mb:.1f}MB），"
                    f"超过 5MB 上限，请让用户拆分或压缩后重发）")
        suffix = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        if suffix in TEXT_EXTS:
            content = data.decode("utf-8", errors="replace")[:FILE_TEXT_LIMIT]
        elif suffix == "pdf":
            try:
                import io

                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(data))
                content = "\n".join((p.extract_text() or "")
                                    for p in reader.pages[:20])[:FILE_TEXT_LIMIT]
            except Exception as e:
                return f"（用户发来 PDF {file_name}，解析失败：{e}）"
            # PDF 自动存档进知识库（shared/pdfs/，kb_search 惰性索引）
            try:
                pdf_dir = home() / "shared" / "pdfs"
                pdf_dir.mkdir(parents=True, exist_ok=True)
                dest = pdf_dir / file_name
                if not (dest.exists()
                        and dest.stat().st_size == len(data)):
                    dest.write_bytes(data)
                    log.info("📚 [%s] PDF 已存档入知识库：%s",
                             self.account, file_name)
            except OSError as e:
                log.warning("PDF 存档失败：%s", e)
        else:
            return (f"（用户发来文件 {file_name}：.{suffix} 格式暂时读不了，"
                    "目前支持文本类文件和 pdf，请用户转换格式后重发）")
        return f"【用户发来文件：{file_name}】\n```\n{content}\n```"

    # -----------------------------------------------------------
    # 消息处理流水线（普通消息和按钮回调共用）
    # -----------------------------------------------------------
    def process_and_reply(self, chat_id: str, text: str, message_id: str,
                          thinking_reaction: str | None = None,
                          image_b64: str | None = None) -> None:
        """跑 Agent → 卡片回复"""
        agent = self._current_agent(chat_id)
        try:
            answer, meta = run_agent_meta(chat_id, text, agent=agent,
                                          account=self.account,
                                          image_b64=image_b64)
        except Exception as e:
            answer, meta = f"出错了：{e} 😵", None
        # 完成：撕掉"敲键盘"，换上"撒花"
        if thinking_reaction:
            self.remove_reaction(message_id, thinking_reaction)
            self.add_reaction(message_id, self.reaction_done)
        self.reply_card(message_id, answer,
                        title=agent_display(agent),
                        chat_id=chat_id, meta=meta)

    def _process_with_reaction(self, chat_id: str, text: str,
                               message_id: str,
                               image_b64: str | None = None) -> None:
        """后台线程入口：先贴"敲键盘"再跑 Agent。
        表情放在线程里贴（而不是事件处理器里），让回调 ack 零延迟"""
        thinking = self.add_reaction(message_id, self.reaction_processing)
        self.process_and_reply(chat_id, text, message_id, thinking, image_b64)

    # -----------------------------------------------------------
    # 收消息
    # -----------------------------------------------------------
    def on_message(self, data: P2ImMessageReceiveV1) -> None:
        message = data.event.message
        sender_open_id = data.event.sender.sender_id.open_id

        # 白名单校验（各账号各自的 owner_open_ids，whitelist 总开关可关）
        if (self.whitelist and self.owner_open_ids
                and sender_open_id not in self.owner_open_ids):
            log.info("🚫 [%s] 非主人消息已拦截（open_id: %s）",
                     self.account, sender_open_id)
            self.reply_text(message.message_id, "抱歉，我只为我的主人服务 🔒")
            return

        # 记住主人：主人每说一句话，就刷新他在这个账号下的 chat_id
        _save_owner(self.account, sender_open_id, message.chat_id)

        content = json.loads(message.content)
        image_b64 = None

        if message.message_type == "text":
            text = content.get("text", "").strip()
            if not text:
                return
        elif message.message_type == "image":
            # 图片消息：下载 → base64 → 走视觉模型（k3 已实测支持）
            image_key = content.get("image_key", "")
            log.info("🖼️  [%s] 收到图片，下载中……", self.account)
            image_b64 = self._image_to_b64(message.message_id, image_key)
            if image_b64 is None:
                self.reply_text(message.message_id,
                                "图片下载失败了，麻烦再发一次 🙏")
                return
            text = ("（用户发来一张图片，请看清图片内容后回应；"
                    "如果上文有用户的具体要求，结合要求处理图片）")
        elif message.message_type == "post":
            # 富文本（粘贴带格式内容、引用回复、图文混排）：
            # 文字展平 + 嵌图下载，一起交给视觉模型
            blocks = content.get("content", [])
            text = _flatten_post(blocks)
            title = str(content.get("title", "")).strip()
            if title:
                text = f"{title}\n{text}"
            img_keys = _post_image_keys(blocks)
            if img_keys:
                log.info("🖼️  [%s] 图文混排：%d 张嵌图，下载中……",
                         self.account, len(img_keys))
                image_b64 = []
                for key in img_keys[:3]:  # 最多取 3 张，防 token 爆炸
                    b64 = self._image_to_b64(message.message_id, key)
                    if b64:
                        image_b64.append(b64)
                if not image_b64:
                    self.reply_text(message.message_id,
                                    "图片下载失败了，麻烦再发一次 🙏")
                    return
            if not text and not image_b64:
                return
            if not text:
                text = "（用户发来图片，请看清图片内容后回应）"
        elif message.message_type == "audio":
            # 语音消息：下载 → faster-whisper 转写 → 文字走正常流程
            file_key = content.get("file_key", "")
            log.info("🎤 [%s] 收到语音，转写中……", self.account)
            voice_text = self._audio_to_text(message.message_id, file_key)
            if voice_text is None:
                self.reply_text(message.message_id,
                                "语音没听清 😵 再说一次，打字也行 🙏")
                return
            text = (f"（用户发来一条语音，转写如下，请按语音内容回应）\n"
                    f"{voice_text}")
        elif message.message_type == "file":
            # 文件消息：下载 → 提取文本注入（文本类/pdf，≤5MB）
            file_key = content.get("file_key", "")
            file_name = content.get("file_name", "未命名文件")
            log.info("📎 [%s] 收到文件：%s", self.account, file_name)
            text = self._file_to_prompt(message.message_id, file_key, file_name)
        else:
            log.info("❓ [%s] 暂不支持的消息类型：%s",
                     self.account, message.message_type)
            self.reply_text(
                message.message_id,
                "这种消息我暂时看不懂 📝 目前支持：文字、富文本、语音、图片、文件"
                "（文本类和 pdf，5MB 以内）")
            return

        chat_id = message.chat_id

        # 引用回复：被引用消息的内容不随事件推送（只有 parent_id），
        # 主动拉下来拼进上下文，否则模型只能靠会话历史猜
        parent_id = getattr(message, "parent_id", None)
        if parent_id:
            quoted = self._fetch_message_text(parent_id)
            if quoted:
                text = f"【用户引用了一条之前的消息：{quoted[:500]}】\n{text}"

        log.info("📩 [%s:%s] %s", self.account, chat_id[:8], text[:50])

        # 斜杠命令（core 路由；固定人格账号禁止切换）
        if text.startswith("/"):
            reply = handle_command(chat_id, text, fixed_agent=self.fixed_agent)
            if reply is not None:
                self.reply_text(message.message_id, reply)
            return

        # 表情在后台线程里贴（_process_with_reaction），不占事件处理器时间
        threading.Thread(
            target=self._process_with_reaction,
            args=(chat_id, text, message.message_id, image_b64),
            daemon=True,
        ).start()

    # -----------------------------------------------------------
    # 卡片按钮回调：点建议按钮 = 替用户发那个编号
    # 注意：需在开发者后台「事件订阅」里添加「卡片回传交互」回调
    # -----------------------------------------------------------
    def on_card_action(self, data: P2CardActionTrigger
                       ) -> P2CardActionTriggerResponse | None:
        event = data.event
        value = (event.action.value or {}) if event.action else {}
        if value.get("kind") != "suggestion":
            return None

        chat_id = value.get("chat_id") or (
            event.context.open_chat_id if event.context else "")
        choice = value.get("text", "")
        operator_open_id = event.operator.open_id if event.operator else ""

        if (self.whitelist and self.owner_open_ids
                and operator_open_id not in self.owner_open_ids):
            log.info("🚫 [%s] 非主人按钮已拦截（open_id: %s）",
                     self.account, operator_open_id)
            return None
        if not chat_id or not choice:
            return None

        _save_owner(self.account, operator_open_id, chat_id)
        message_id = event.context.open_message_id if event.context else None
        log.info("🔘 [%s:%s] 按钮选择: %s", self.account, chat_id[:8], choice)

        # toast 即时反馈：让点击者确认"我点的是 3"（卡片本身不刷新）。
        # 关键：response 必须先构造好，且 return 前不做任何网络调用——
        # 飞书卡片回调有秒级超时，ack 慢了用户会看到
        # 「目标回调服务超时未响应」
        toast = CallBackToast()
        toast.type = "success"
        toast.content = f"已选择 {choice}，处理中 ⌨️"
        response = P2CardActionTriggerResponse()
        response.toast = toast

        if message_id:
            threading.Thread(target=self._process_with_reaction,
                             args=(chat_id, choice, message_id),
                             daemon=True).start()
        return response

    # -----------------------------------------------------------
    # 启动（阻塞式：飞书 SDK 的 ws client 占住调用线程，
    # main.py 为每个账号起一个守护线程调用本方法）
    # -----------------------------------------------------------
    def start(self) -> None:
        if self.whitelist and not self.owner_open_ids:
            log.warning("[%s] 未配置 owner_open_ids，任何人都能和这个机器人对话"
                        "（建议尽快配置）", self.account)
        if not self.whitelist:
            log.warning("🔓 [%s] 白名单已关闭，任何人都能对话", self.account)

        event_handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self.on_message)
            # 表情事件（贴/撕 reaction）我们不关心，注册静默处理器避免 SDK 报错刷屏
            .register_p2_im_message_reaction_created_v1(lambda _data: None)
            .register_p2_im_message_reaction_deleted_v1(lambda _data: None)
            # 卡片按钮回调（建议按钮）
            .register_p2_card_action_trigger(self.on_card_action)
            .build()
        )

        ws_client = lark.ws.Client(
            self.app_id,
            self.app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        who = agent_display(self.fixed_agent) if self.fixed_agent else "多人格"
        log.info("🚀 飞书渠道已启动：[%s]（%s）", self.account, who)
        ws_client.start()
