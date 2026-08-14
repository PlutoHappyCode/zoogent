"""
channels/feishu — 飞书渠道包（按职责分层拆分）
=================================================

模块结构：
  _loop_proxy.py  — 事件循环兼容层（lark-oapi ws 多线程 hack）
  _owner.py       — 主人档案 owner.json 持久化
  _parser.py      — 消息解析纯函数（post/text/interactive → 纯文本）
  _card.py        — 卡片构建与消息发送（所有 lark_client API 调用）
  channel.py      — FeishuChannel 主类（协调者）

对外导出：FeishuChannel, shutdown_event_loops
以及供测试/外部调用的实用函数（保持向后兼容）
"""

from .channel import FeishuChannel
from ._loop_proxy import shutdown_event_loops
from ._owner import OWNER_FILE, _load_owner, _save_owner, get_target_chat
from ._parser import (
    FILE_MAX_BYTES,
    FILE_TEXT_LIMIT,
    TEXT_EXTS,
    _card_md,
    _extract_msg_text,
    _extract_suggestions,
    _flatten_post,
    _footer_note,
    _post_image_keys,
    _suggestion_buttons,
)
from ._card import build_card

__all__ = [
    "FeishuChannel",
    "shutdown_event_loops",
    "OWNER_FILE",
    "_load_owner",
    "_save_owner",
    "get_target_chat",
    "FILE_MAX_BYTES",
    "FILE_TEXT_LIMIT",
    "TEXT_EXTS",
    "_card_md",
    "_extract_msg_text",
    "_extract_suggestions",
    "_flatten_post",
    "_footer_note",
    "_post_image_keys",
    "_suggestion_buttons",
    "build_card",
]
