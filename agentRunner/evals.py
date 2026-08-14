"""
evals.py — 回归测试：改 prompt / 结构后跑一遍，防止 agent 悄悄变笨
=================================================================

用例写在 AgentsHome/<agent_id>/evals.md，格式：

  ## 用例 标题
  Q: 你是谁？
  A: 程序猴, coder2        ← 回答应包含的关键词，逗号分隔
                             「a|b」表示命中任一即可（应对措辞随机性）

用法：
  python evals.py              # 测所有 agent
  python evals.py coder2       # 只测一个

每条用例用独立临时会话跑，不污染真实对话。
"""

import re
import sys

from core import home, list_agents, run_agent

HOME = home()


def parse_evals(agent: str) -> list[dict]:
    """解析 evals.md → [{title, q, keywords}]"""
    path = HOME / agent / "evals.md"
    if not path.exists():
        return []
    cases = []
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("## "):
            current = {"title": line[3:].strip(), "q": "", "keywords": []}
            cases.append(current)
        elif line.startswith("Q:") and current is not None:
            current["q"] = line[2:].strip()
        elif line.startswith("A:") and current is not None:
            current["keywords"] = [k.strip() for k in line[2:].split(",") if k.strip()]
    return [c for c in cases if c["q"] and c["keywords"]]


def run_evals(agent: str) -> tuple[int, int]:
    """跑一个 agent 的全部用例，返回 (通过数, 总数)"""
    cases = parse_evals(agent)
    if not cases:
        print(f"  ⏭️  [{agent}] 没有用例（{agent}/evals.md 为空）")
        return 0, 0
    passed = 0
    for i, case in enumerate(cases):
        # 每条用例独立会话（chat_id 用 eval 前缀，和真实会话隔离）
        answer = run_agent(f"eval-{agent}-{i}-{time_salt()}", case["q"], agent=agent)
        # 关键词支持「a|b」或语法：命中任一即算通过（模型措辞有随机性）
        missing = [k for k in case["keywords"]
                   if not any(alt in answer for alt in k.split("|"))]
        if missing:
            print(f"  ❌ [{agent}] {case['title']}：缺少关键词 {missing}")
            print(f"     回答：{answer[:100]}…")
        else:
            passed += 1
            print(f"  ✅ [{agent}] {case['title']}")
    return passed, len(cases)


def time_salt() -> str:
    import time
    return str(int(time.time()))


def main() -> None:
    targets = [sys.argv[1]] if len(sys.argv) > 1 else list_agents()
    total_pass = total = 0
    for agent in targets:
        p, t = run_evals(agent)
        total_pass += p
        total += t
    print(f"\n结果：{total_pass}/{total} 通过")
    sys.exit(0 if total_pass == total else 1)


if __name__ == "__main__":
    main()
