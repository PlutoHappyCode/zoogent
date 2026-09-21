"""
channels/terminal.py — 终端调试渠道
====================================

不连飞书、不调外部服务，直接在终端里和 Agent 对话，
用于本地调试 core 逻辑。斜杠命令同样可用（由 core 路由）。
"""

from core import agent_display, get_chat_agent, handle_command, run_agent_meta

from .base import Channel


class TerminalChannel(Channel):
    name = "terminal"
    CHAT_ID = "terminal"
    fixed_agent = None  # 多人格模式，支持 /agent 切换

    def get_target_chat(self) -> str:
        """调度器降级推送目标：终端会话"""
        return self.CHAT_ID

    def start(self) -> None:
        print("🖥️  终端调试模式，输入 /agents 查看人格，Ctrl+C 退出")
        while True:
            try:
                text = input("\n你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 再见")
                return
            if not text:
                continue

            reply = handle_command(self.CHAT_ID, text)
            if reply is not None:
                print(reply)
                continue

            try:
                answer, meta = run_agent_meta(self.CHAT_ID, text,
                                              account="terminal")
            except Exception as e:
                answer, meta = f"出错了：{e} 😵", None
            print(f"{agent_display(get_chat_agent(self.CHAT_ID))} > {answer}")
            if meta:
                parts = [meta["model"]]
                if meta.get("rounds"):
                    parts.append(f"{meta['rounds']}轮·{meta.get('steps', 0)}步")
                if meta.get("llm_s") is not None:
                    parts.append(f"LLM {meta['llm_s']}s·工具 {meta.get('tool_s', 0)}s")
                if meta.get("out_tokens") and meta.get("llm_s"):
                    parts.append(f"{meta['out_tokens'] / meta['llm_s']:.0f} tok/s")
                if meta.get("in_tokens") and meta.get("cached_tokens"):
                    parts.append(f"缓存命中 {meta['cached_tokens'] * 100 // meta['in_tokens']}%")
                parts.append(f"用时 {meta['elapsed']}s")
                print(f"  ({' · '.join(parts)})")

    def send(self, chat_id: str, text: str, meta: dict | None = None) -> None:
        """主动推送 = 直接打印（调度器输出会落到终端）"""
        print(f"\n📮 [主动推送 → {chat_id}]\n{text}")
