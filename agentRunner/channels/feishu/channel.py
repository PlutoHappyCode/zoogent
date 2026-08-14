"""
channels/feishu/channel.py — FeishuChannel 主类
================================================

一个账号 = 一个独立飞书应用 = 一个 FeishuChannel 实例，
每个实例一套 lark.Client + ws.Client + 事件处理器。

两种账号：
  - default（fixed_agent=None）：多人格模式，保留 /agent 切换行为
  - 固定人格账号（fixed_agent 非 None）：所有消息直接以该人格跑

协调者角色：调用 _card.py 发消息、_parser.py 解析、_owner.py 持久化、
engine.py 跑主循环。
"""

import base64
import json
import tempfile
import threading
from pathlib import Path

import lark_oapi as lark
from lark_oapi.api.im.v1 import GetMessageRequest, GetMessageResourceRequest, P2ImMessageReceiveV1
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
from core.config import get
from core.log import get_logger
from core.personas import home

from ..base import Channel
from ._card import (
    add_reaction,
    build_card,
    remove_reaction,
    reply_card,
    reply_text,
    send,
    send_text,
)
from ._owner import _save_owner, get_target_chat
from ._parser import (
    FILE_MAX_BYTES,
    FILE_TEXT_LIMIT,
    TEXT_EXTS,
    _card_md,
    _extract_msg_text,
    _flatten_post,
    _post_image_keys,
)

log = get_logger("feishu")


class FeishuChannel(Channel):
    """一个飞书应用账号。fixed_agent 非 None = 固定人格机器人"""

    def __init__(self, account: str, account_cfg: dict,
                 shared_cfg: dict) -> None:
        self.account = account
        self.fixed_agent: str | None = account_cfg.get("agent")
        self.app_id = account_cfg["app_id"]
        self.app_secret = account_cfg["app_secret"]
        self.owner_open_ids = account_cfg.get("owner_open_ids", [])
        self.whitelist = bool(shared_cfg.get("whitelist", True))
        reactions = shared_cfg.get("reactions", {})
        self.reaction_raise_hand = reactions.get("raise_hand", "RaisingHand")
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
        """固定人格账号无视 chatAgents.json 绑定"""
        return self.fixed_agent or get_chat_agent(chat_id)

    # -----------------------------------------------------------
    # 图片 / 文件 / 语音资源处理
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
    # 便捷封装：把 self 参数传给 _card.py 的函数
    # -----------------------------------------------------------
    def reply_text(self, message_id: str, text: str) -> None:
        reply_text(self.lark_client, self.account, message_id, text)

    def reply_card(self, message_id: str, text: str, title: str = "",
                   chat_id: str = "", meta: dict | None = None) -> None:
        reply_card(self.lark_client, self.account, message_id, text, title,
                   chat_id, meta, self.card_footer, self.card_buttons)

    def build_card(self, text: str, title: str = "", chat_id: str = "",
                   meta: dict | None = None) -> dict:
        return build_card(text, title, chat_id, meta,
                          self.card_footer, self.card_buttons)

    def send(self, chat_id: str, text: str, meta: dict | None = None) -> None:
        send(self.lark_client, self.account, chat_id, text, meta,
             self.card_footer, self.card_buttons)

    def _send_text(self, chat_id: str, text: str) -> None:
        send_text(self.lark_client, self.account, chat_id, text)

    def add_reaction(self, message_id: str, emoji_type: str) -> str | None:
        return add_reaction(self.lark_client, self.account,
                            message_id, emoji_type)

    def remove_reaction(self, message_id: str, reaction_id: str) -> None:
        remove_reaction(self.lark_client, self.account,
                         message_id, reaction_id)

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
        if thinking_reaction:
            self.remove_reaction(message_id, thinking_reaction)
            self.add_reaction(message_id, self.reaction_done)

        self.reply_card(message_id, answer,
                        title=agent_display(agent),
                        chat_id=chat_id, meta=meta)

    def _process_with_reaction(self, chat_id: str, text: str,
                               message_id: str,
                               image_b64: str | None = None,
                               raise_hand_reaction: str | None = None) -> None:
        """后台线程入口：撕掉举手→贴敲键盘→跑 Agent。"""
        if raise_hand_reaction:
            self.remove_reaction(message_id, raise_hand_reaction)
        thinking = self.add_reaction(message_id, self.reaction_processing)
        self.process_and_reply(chat_id, text, message_id, thinking, image_b64)

    # -----------------------------------------------------------
    # 收消息
    # -----------------------------------------------------------
    def on_message(self, data: P2ImMessageReceiveV1) -> None:
        message = data.event.message
        sender_open_id = data.event.sender.sender_id.open_id

        if (self.whitelist and self.owner_open_ids
                and sender_open_id not in self.owner_open_ids):
            log.info("🚫 [%s] 非主人消息已拦截（open_id: %s）",
                     self.account, sender_open_id)
            self.reply_text(message.message_id, "抱歉，我只为我的主人服务 🔒")
            return

        _save_owner(self.account, sender_open_id, message.chat_id)

        content = json.loads(message.content)
        image_b64 = None

        if message.message_type == "text":
            text = content.get("text", "").strip()
            if not text:
                return
        elif message.message_type == "image":
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
                for key in img_keys[:3]:
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

        parent_id = getattr(message, "parent_id", None)
        if parent_id:
            quoted = self._fetch_message_text(parent_id)
            if quoted:
                text = f"【用户引用了一条之前的消息：{quoted[:500]}】\n{text}"

        log.info("📩 [%s:%s] %s", self.account, chat_id[:8], text[:50])

        if text.startswith("/"):
            reply = handle_command(chat_id, text, fixed_agent=self.fixed_agent)
            if reply is not None:
                self.reply_text(message.message_id, reply)
            return

        raise_hand_reaction = self.add_reaction(message.message_id, self.reaction_raise_hand)

        threading.Thread(
            target=self._process_with_reaction,
            args=(chat_id, text, message.message_id, image_b64, raise_hand_reaction),
            daemon=True,
        ).start()

    # -----------------------------------------------------------
    # 卡片按钮回调：点建议按钮 = 替用户发那个编号
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

        toast = CallBackToast()
        toast.type = "success"
        toast.content = f"已选择 {choice}，处理中 ⌨️"
        response = P2CardActionTriggerResponse()
        response.toast = toast

        if message_id:
            raise_hand_reaction = self.add_reaction(message_id, self.reaction_raise_hand)
            threading.Thread(target=self._process_with_reaction,
                             args=(chat_id, choice, message_id, None, raise_hand_reaction),
                             daemon=True).start()
        return response

    # -----------------------------------------------------------
    # 启动（阻塞式：飞书 SDK 的 ws client 占住调用线程）
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
            .register_p2_im_message_reaction_created_v1(lambda _data: None)
            .register_p2_im_message_reaction_deleted_v1(lambda _data: None)
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
