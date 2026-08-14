"""
channels/feishu/_card.py — 卡片构建与消息发送
=============================================

飞书消息 API 调用的集中封装：发送、回复、patch、表情。
所有对 lark_client 的直接操作都在这里，方便统一替换和测试。
"""

import json

from lark_oapi.api.im.v1 import (
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    DeleteMessageReactionRequest,
    Emoji,
    PatchMessageRequest,
    PatchMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from core.log import get_logger

from ._parser import (
    _card_md,
    _extract_suggestions,
    _footer_note,
    _suggestion_buttons,
)

log = get_logger("feishu")


def build_card(text: str, title: str = "", chat_id: str = "",
               meta: dict | None = None,
               card_footer: bool = True,
               card_buttons: bool = True) -> dict:
    """拼卡片：标题 + markdown 正文 + 建议按钮（如有）+ footer（如有）"""
    elements: list[dict] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": _card_md(text)}}
    ]
    if chat_id and card_buttons:
        options = _extract_suggestions(text)
        if options:
            elements.append(_suggestion_buttons(chat_id, options))
    if card_footer:
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


def reply_text(lark_client, account: str,
               message_id: str, text: str) -> None:
    """回复纯文本消息"""
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
    response = lark_client.im.v1.message.reply(request)
    if not response.success():
        log.warning("[%s] 回复失败: %s %s", account, response.code, response.msg)


def reply_card(lark_client, account: str,
               message_id: str, text: str, title: str = "",
               chat_id: str = "", meta: dict | None = None,
               card_footer: bool = True,
               card_buttons: bool = True) -> None:
    """用卡片回复 Agent 的答案，失败时降级为纯文本"""
    card = build_card(text, title, chat_id, meta, card_footer, card_buttons)
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
    response = lark_client.im.v1.message.reply(request)
    if not response.success():
        log.warning("[%s] 卡片回复失败（%s %s），降级为纯文本",
                    account, response.code, response.msg)
        reply_text(lark_client, account, message_id, text)


def patch_card(lark_client, account: str,
               message_id: str, text: str, title: str = "",
               chat_id: str = "", meta: dict | None = None,
               card_footer: bool = True,
               card_buttons: bool = True) -> None:
    """用 PatchMessage 接口更新已有卡片（流式卡片内部用）。
    失败只打日志，不打断主流程。"""
    card = build_card(text, title, chat_id, meta, card_footer, card_buttons)
    request = (
        PatchMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            PatchMessageRequestBody.builder()
            .content(json.dumps(card, ensure_ascii=False))
            .msg_type("interactive")
            .build()
        )
        .build()
    )
    response = lark_client.im.v1.message.patch(request)
    if not response.success():
        log.debug("[%s] 卡片 patch 失败: %s %s",
                  account, response.code, response.msg)


def send(lark_client, account: str,
         chat_id: str, text: str, meta: dict | None = None,
         card_footer: bool = True,
         card_buttons: bool = True) -> None:
    """主动推送到某个会话（调度器/哨兵专用）：卡片格式，失败降级纯文本"""
    title, body = "", text
    if text.startswith("📮 ") and "\n\n" in text:
        title, body = text.split("\n\n", 1)
    card = build_card(body, title=title, chat_id=chat_id, meta=meta,
                      card_footer=card_footer, card_buttons=card_buttons)
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
    response = lark_client.im.v1.message.create(request)
    if not response.success():
        log.warning("[%s] 卡片推送失败（%s %s），降级为纯文本",
                    account, response.code, response.msg)
        send_text(lark_client, account, chat_id, text)


def send_text(lark_client, account: str,
              chat_id: str, text: str) -> None:
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
    response = lark_client.im.v1.message.create(request)
    if not response.success():
        log.warning("[%s] 推送失败: %s %s", account, response.code, response.msg)


def add_reaction(lark_client, account: str,
                 message_id: str, emoji_type: str) -> str | None:
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
    response = lark_client.im.v1.message_reaction.create(request)
    if response.success():
        return response.data.reaction_id
    log.warning("[%s] 贴表情失败: %s %s", account, response.code, response.msg)
    return None


def remove_reaction(lark_client, account: str,
                    message_id: str, reaction_id: str) -> None:
    """撕掉之前贴的表情"""
    request = (
        DeleteMessageReactionRequest.builder()
        .message_id(message_id)
        .reaction_id(reaction_id)
        .build()
    )
    lark_client.im.v1.message_reaction.delete(request)
