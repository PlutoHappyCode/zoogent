"""
core/scheduler.py — 调度器：让 Agent 主动找你说话（多账号版）
==============================================================

扫描 AgentsHome 每个已启用 agent 目录下的 cron.md（声明式）：

  # cron.md 格式：每行一个任务，HH:MM 空格 指令内容，# 开头为注释
  08:50 现在是早盘前，请为用户生成今日自选股简报……

到点 → 以该 agent 身份执行指令 → 通过**绑了该 agent 的飞书账号**推送
（finance 的简报由 finance 机器人发）；没有绑定账号或该账号还没有
推送目标时，降级到 default 账号；再不行打印警告、下一轮重试。

实现：一个守护线程，每 scheduler.tick_seconds 秒醒来一次看表。
轻量设计，不引第三方依赖。
"""

import hashlib
import json
import threading
import time
from datetime import datetime

from .config import get
from .log import get_logger
from .memory import MEMORY_DIR
from .personas import home, list_agents

log = get_logger("scheduler")


def load_jobs() -> list[dict]:
    """扫描所有已启用 agent 的 cron.md，返回任务列表
    [{agent, time, instruction}]，每次循环重新读 —— 改文件即生效"""
    jobs = []
    root = home()
    if not root.exists():
        return jobs
    for agent in list_agents():
        cron_file = root / agent / "cron.md"
        if not cron_file.exists():
            continue
        for line in cron_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=1)
            if len(parts) != 2 or not _valid_time(parts[0]):
                log.warning("%s/cron.md 忽略非法行：%s", agent, line[:40])
                continue
            instruction = parts[1]
            # [quiet] 前缀 = 静默任务：照常执行但不推送给主人（凌晨整理等场景）
            quiet = instruction.startswith("[quiet]")
            if quiet:
                instruction = instruction[len("[quiet]"):].strip()
            jobs.append({"agent": agent,
                         "time": parts[0],
                         "instruction": instruction,
                         "quiet": quiet})
    return jobs


def _valid_time(s: str) -> bool:
    try:
        datetime.strptime(s, "%H:%M")
        return True
    except ValueError:
        return False


def resolve_push_channel(agent: str, channels: dict,
                         default_name: str = "default"):
    """给某个 agent 的定时任务找推送渠道。
    优先绑了该 agent 的固定人格账号；没有或没有推送目标时降级 default。
    返回 (account_name, channel, chat_id) 或 None（双方都无推送目标）"""
    for name, ch in channels.items():
        if getattr(ch, "fixed_agent", None) == agent:
            chat_id = ch.get_target_chat()
            if chat_id:
                return name, ch, chat_id
            break  # 有绑定账号但还没人说过话 → 降级 default
    default = channels.get(default_name)
    if default:
        chat_id = default.get_target_chat()
        if chat_id:
            return default_name, default, chat_id
    return None


# 触发记录落盘：重启后同一分钟内的任务不会重复推送
FIRED_FILE = MEMORY_DIR / "scheduler_fired.json"


def _load_fired() -> dict:
    """读触发记录，只留今天的（隔夜的记录直接丢）"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        data = json.loads(FIRED_FILE.read_text(encoding="utf-8"))
        return {k: v for k, v in data.items() if v == today}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_fired(fired: dict) -> None:
    try:
        FIRED_FILE.write_text(json.dumps(fired, ensure_ascii=False),
                              encoding="utf-8")
    except OSError as e:
        log.warning("触发记录写入失败：%s", e)


def start_scheduler(channels: dict, default_name: str = "default") -> None:
    """启动调度线程。
    channels：{account_name: 渠道实例} 注册表（main.py 装配时传入），
    渠道需提供 fixed_agent / get_target_chat() / send()。
    """
    tick = int(get().get("scheduler", {}).get("tick_seconds", 30))
    fired: dict[str, str] = _load_fired()  # "agent|time|指令hash" → 上次触发日期，防重复

    def loop() -> None:
        while True:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            today = now.strftime("%Y-%m-%d")

            for job in load_jobs():
                key = f"{job['agent']}|{job['time']}|{hashlib.md5(job['instruction'].encode()).hexdigest()[:8]}"
                if current_time != job["time"] or fired.get(key) == today:
                    continue
                target = resolve_push_channel(job["agent"], channels,
                                              default_name)
                if not target:
                    log.warning("⏰ [%s] 任务到点，但还没有推送目标"
                                "（先用对应机器人说句话）", job["agent"])
                    continue
                account, channel, chat_id = target
                fired[key] = today
                _save_fired(fired)
                log.info("⏰ 触发任务：[%s → %s] %s…",
                         job["agent"], account, job["instruction"][:30])
                # 导入放这里，避免和渠道层循环依赖
                from .engine import run_proactive
                from .personas import agent_display
                try:
                    result = run_proactive(chat_id, job["instruction"],
                                           agent=job["agent"])
                    if job.get("quiet"):
                        log.info("🤫 [%s] 静默任务完成，不推送", job["agent"])
                    else:
                        channel.send(chat_id,
                                     f"📮 {agent_display(job['agent'])} 的定时任务"
                                     f"\n\n{result}")
                except Exception as e:
                    log.warning("[%s] 任务执行失败：%s", job["agent"], e)

            time.sleep(tick)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    log.info("⏰ 调度器已启动（扫描 %s/*/cron.md，每 %ds 一轮，推送渠道：%s）",
             home(), tick, sorted(channels))


# ===============================================================
# 哨兵：欠条队列的自动补发
# ===============================================================
# 定时扫每个 agent 的 pending.json，尝试重放；模型还没恢复就留着
# 下轮再试，恢复了就通过原账号把答案推回去并核销欠条。

def start_sentinel(channels: dict, default_name: str = "default") -> None:
    """启动欠条哨兵线程。channels：{account_name: 渠道实例} 注册表"""
    from .memory import load_pending  # 放这里，避免循环依赖

    tick = int(get().get("scheduler", {}).get("sentinel_seconds", 120))

    def _sweep() -> None:
        from .engine import run_agent_meta
        from .personas import agent_display
        for agent in list_agents():
            for task in load_pending(agent):
                chat_id = task.get("chat_id")
                if not chat_id:
                    continue
                answer, meta = run_agent_meta(
                    chat_id, task.get("text", ""), agent=agent,
                    account=task.get("account", "sentinel"))
                if meta.get("pending"):
                    continue  # 模型还没恢复，欠条已被重新落盘，下轮再试
                # 推送回原渠道；账号已下线时降级到该 agent 的推送渠道
                channel = channels.get(task.get("account"))
                if channel is None:
                    target = resolve_push_channel(agent, channels, default_name)
                    channel = target[1] if target else None
                if channel is None:
                    log.info("📮 [%s] 欠条已补做但无推送渠道，先留账", agent)
                    continue
                try:
                    channel.send(
                        chat_id,
                        f"📮 {agent_display(agent)} 的补发"
                        f"（之前模型故障的任务）\n\n{answer}")
                except Exception as e:
                    log.warning("[%s] 欠条补发失败：%s，先留账", agent, e)
                    continue
                from .memory import pop_pending
                pop_pending(agent, chat_id)
                log.info("📮 [%s] 欠条已补发并核销（%s…）", agent, chat_id[:6])

    def loop() -> None:
        while True:
            time.sleep(tick)
            try:
                _sweep()
            except Exception as e:
                log.warning("哨兵扫描出错：%s", e)

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    log.info("📮 欠条哨兵已启动（每 %ds 扫一次 pending.json）", tick)
