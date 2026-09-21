"""
core/sessionLog.py — 旁路会话日志（不受会话裁剪影响）
=====================================================

为什么需要它：会话压缩（memory._trim）会把超长的旧对话摘要进 summary.md 后
**永久丢弃原始消息**，_compress_stale_tool_results 还会把旧工具结果截成 200 字。
所以磁盘上的 sessions/*.json 只是"残本"，做不了完整的上下文导出。

本模块在每轮回答结束时追加一行 JSONL，记录该轮的
用户输入 / 工具调用（名称+参数+结果长度+预览）/ 最终回答 / 元信息，
独立于会话历史，永远不会被裁剪，也不参与任何 LLM 上下文。

路径：agentRunner/memory/sessionLog/<agent>--<chat_id>.jsonl
文件超过 LOG_MAX_MB 时轮转为 <name>.1.jsonl（只留一代）。

每行一条 turn 记录：
  {"t": 1789801945.8, "ts": "2026-09-19 15:12", "agent": "analyst",
   "source": "user" | "system",
   "user": "...", "answer": "...",
   "tools": [{"name": "web_search", "args": {...}, "chars": 1234,
              "preview": "..."}],
   "meta": {"model": "...", "rounds": 3, "tokens": 4567, ...}}
"""

import json
import re
import time
from pathlib import Path

from .config import BASE_DIR
from .log import get_logger

log = get_logger("sessionLog")

LOG_DIR = BASE_DIR / "memory" / "sessionLog"

USER_CHARS = 4000        # 用户输入入库上限
ANSWER_CHARS = 6000      # 回答入库上限
PREVIEW_CHARS = 400      # 工具结果预览上限
ARG_CHARS = 300          # 单个工具参数上限
LOG_MAX_MB = 10          # 单文件超过则轮转


def _safe(chat_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", chat_id)


def log_file(agent: str, chat_id: str) -> Path:
    """某个（人格 + 会话）的旁路日志路径"""
    return LOG_DIR / f"{agent}--{_safe(chat_id)}.jsonl"


def _clip(text, limit: int) -> str:
    s = text if isinstance(text, str) else str(text)
    return s if len(s) <= limit else s[:limit] + "…（截断）"


def _clip_args(args) -> dict:
    """工具参数瘦身：只保留可读的标量，长字符串截断，避免日志被大 payload 撑爆"""
    if not isinstance(args, dict):
        return {"_raw": _clip(args, ARG_CHARS)}
    out = {}
    for k, v in args.items():
        if isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        else:
            out[k] = _clip(v, ARG_CHARS)
    return out


def _rotate(path: Path) -> None:
    """日志过大时轮转：当前文件改名 .1.jsonl，旧的一代直接丢掉"""
    try:
        if path.exists() and path.stat().st_size > LOG_MAX_MB * 1024 * 1024:
            path.replace(path.with_suffix(".jsonl.1"))
    except OSError as e:
        log.warning("会话日志轮转失败：%s", e)


def log_turn(agent: str, chat_id: str, user: str, answer: str,
             tools: list[dict] | None = None, meta: dict | None = None,
             source: str = "user") -> None:
    """追加一轮记录。任何失败都只告警，绝不打断主流程。"""
    if not chat_id:
        return
    record = {
        "t": round(time.time(), 3),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "agent": agent,
        "source": source,
        "user": _clip(user, USER_CHARS),
        "answer": _clip(answer, ANSWER_CHARS),
        "tools": [
            {"name": t.get("name", "?"),
             "args": _clip_args(t.get("args", {})),
             "chars": len(str(t.get("result", ""))),
             "preview": _clip(t.get("result", ""), PREVIEW_CHARS)}
            for t in (tools or [])
        ],
        "meta": meta or {},
    }
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = log_file(agent, chat_id)
        _rotate(path)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError) as e:
        log.warning("[%s] 会话旁路日志写入失败：%s", agent, e)


def read_turns(agent: str, chat_id: str) -> list[dict]:
    """读回该会话的全部轮次（按时间正序）。文件不存在返回空列表，
    损坏的行跳过——日志坏了也不能炸掉导出功能。"""
    path = log_file(agent, chat_id)
    if not path.exists():
        return []
    turns = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                turns.append(rec)
    except OSError as e:
        log.warning("[%s] 会话旁路日志读取失败：%s", agent, e)
    return turns