"""
main.py — 装配入口（多机器人版）
=================================

  1. core.config.load() 并校验必填密钥
  2. core 引擎初始化（import core 即完成：模型注册表、人格、技能、tools.md）
  3. 遍历 feishu.accounts，每个账号一个 FeishuChannel，
     各自起守护线程跑 ws client（lark-oapi 的 ws 是阻塞式的）
  4. 终端渠道（如启用）也起守护线程
  5. scheduler.enabled 时启动调度线程，推送走 {account: channel} 注册表：
     任务属于哪个 agent 就由哪个机器人发，降级 default
  6. 主线程 park 住

运行：python main.py
"""

import asyncio
import signal
import threading
import time

import core
from channels import build_channel, build_feishu_channels
from core.log import get_logger

log = get_logger("main")


def main() -> None:
    # 信号处理两条都要显式装：
    # - 后台启动（&、nohup、部分守护工具）会把 SIGINT 继承成「忽略」，
    #   Python 不会覆盖它，Ctrl+C / kill -INT 就永远失效 → 显式装回默认
    # - NAS / docker 用 SIGTERM 停机，映射到同一条优雅退出路径
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM,
                  lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    missing = core.check()
    if missing:
        raise SystemExit("缺少配置：\n  - " + "\n  - ".join(missing)
                         + "\n请检查 agent.json 与 .env 文件")

    channel_cfgs = core.get().get("channels", {})
    started: list = []
    push_registry: dict = {}

    # 飞书多账号
    feishu_cfg = channel_cfgs.get("feishu", {})
    if feishu_cfg.get("enabled"):
        feishu_channels = build_feishu_channels(feishu_cfg)
        started.extend(feishu_channels.values())
        push_registry.update(feishu_channels)

    # 终端调试渠道
    if channel_cfgs.get("terminal", {}).get("enabled"):
        terminal = build_channel("terminal", {})
        started.append(terminal)
        if not push_registry:  # 没有飞书账号时调度器降级推终端
            push_registry["default"] = terminal

    if not started:
        raise SystemExit("没有任何启用的渠道，请检查 agent.json 的 channels 配置")

    # 调度器：任务属于哪个 agent 就由绑了它的账号推送；
    # 降级推送账号 = agents.default 人格对应的账号（housekeeper）
    if core.get().get("scheduler", {}).get("enabled", True):
        fallback = core.get().get("agents", {}).get("default", "housekeeper")
        core.start_scheduler(push_registry, fallback)
        core.start_sentinel(push_registry, fallback)  # 欠条哨兵：模型恢复后自动补发

    # 所有渠道各起守护线程（飞书 ws client 是阻塞式的），主线程 park 住
    for ch in started:
        threading.Thread(target=_safe_start, args=(ch,), daemon=True).start()

    # ASR 模型后台预热：464MB 模型加载要两分多钟，
    # 不预热的话第一条语音消息会干等
    threading.Thread(target=_asr_warmup, daemon=True).start()

    log.info("✅ %d 个渠道已启动，Ctrl+C 退出", len(started))
    try:
        while True:
            # 不能用 Event().wait()：Python 3.13+ 的锁实现会让主线程
            # 在无限期等待时收不到 SIGINT；time.sleep 可被信号正常打断
            time.sleep(3600)
    except KeyboardInterrupt:
        log.info("🧹 正在清理后台任务……")
        try:
            from channels.feishu import shutdown_event_loops
            shutdown_event_loops()  # 取消 SDK 缓存清理等挂起任务，退出不刷屏
        except Exception:
            pass
        # lark SDK 的缓存任务取消后仍在解释器收尾时刷几条临终遗言
        # （Task was destroyed / RuntimeWarning），退出前静音这两路输出
        import logging
        import warnings
        logging.getLogger("asyncio").setLevel(logging.CRITICAL)
        warnings.filterwarnings("ignore")
        log.info("👋 再见")


def _asr_warmup() -> None:
    try:
        from core.asr import warmup
        warmup()
    except Exception:
        pass  # 本地 dev 没装 faster-whisper 也无所谓


def _safe_start(ch) -> None:
    try:
        ch.start()
    except asyncio.CancelledError:
        pass  # 优雅退出时任务被取消，正常寿终，不用报
    except Exception as e:
        log.warning("渠道 %s 已退出：%s", ch.name, e)


if __name__ == "__main__":
    main()
