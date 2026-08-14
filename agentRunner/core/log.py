"""
core/log.py — 统一日志：控制台 + 轮转文件
===========================================

用法：各模块顶部 `log = get_logger("engine")`，然后
`log.info(...) / log.warning(...)` 替代 print。

- 控制台：保持原来的 emoji 风格输出（docker logs 里看的一样）
- 文件：agentRunner/logs/agent.log，单文件 5MB、保留 3 个备份，
  带时间戳和级别，排障不用盯着实时日志
- 可在 agent.json 加 "logging" 节覆盖：
  {"level": "INFO", "max_mb": 5, "backups": 3}

terminal.py / evals.py 是人看的交互界面，保留 print，不走本模块。
"""

import logging
from logging.handlers import RotatingFileHandler

from .config import BASE_DIR, get

LOG_DIR = BASE_DIR / "logs"
_configured = False


def _setup() -> None:
    global _configured
    if _configured:
        return
    cfg = get().get("logging", {})
    level = getattr(logging, str(cfg.get("level", "INFO")).upper(), logging.INFO)
    max_bytes = int(cfg.get("max_mb", 5)) * 1024 * 1024
    backups = int(cfg.get("backups", 3))

    root = logging.getLogger("agent")
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler()
    # 控制台只显示消息本体（和原来 print 的观感一致），时间戳留给文件
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)

    try:
        LOG_DIR.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_DIR / "agent.log", maxBytes=max_bytes,
            backupCount=backups, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as e:
        root.warning("日志文件初始化失败（只输出到控制台）：%s", e)

    root.propagate = False
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """取一个子 logger（名字会出现在日志文件的 [name] 字段里）"""
    _setup()
    return logging.getLogger(f"agent.{name}")
