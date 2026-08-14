"""
channels/feishu/_owner.py — 主人档案持久化
============================================

主人档案 memory/owner.json 按账号命名空间：
  {"<account>": {"<open_id>": "<chat_id>"}}，重启不丢。
调度器靠它找到每个机器人"往哪推"。
多账号各跑一个线程，_load/_save 的读改写用一把可重入锁保护。
旧的平铺格式启动时自动迁移到 "default" 命名空间。
"""

import json
import threading

from core.config import BASE_DIR
from core.log import get_logger

log = get_logger("feishu")

OWNER_FILE = BASE_DIR / "memory" / "owner.json"

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
