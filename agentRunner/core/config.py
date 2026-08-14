"""
core/config.py — 中央配置加载（agent.json + .env）
==================================================

agent.json 支持 JSONC 风格注释（// 行注释、/* */ 块注释），
字符串里的 ${VAR} 从环境变量（含 .env）替换。

配置文件位置：默认项目根目录 agent.json（agentRunner 的上一级）；
.env 或环境变量里设 AGENT_CONFIG 可指向别处（NAS 上指向 SSD 数据目录
/data/zoogent/agent.json，人格/记忆/配置统一管理）。

语义约定：
  - agents.home 等支持相对路径（相对配置文件所在目录解析）
  - channels.feishu.owner_open_ids：每个元素做 ${VAR} 替换，
    单个元素内含逗号则拆分，替换后为空字符串的项剔除
  - agents.members 未列出的 agent = 默认启用、default 模型、全部技能；
    "enabled": false 的人格运行时不加载（磁盘目录不动）
"""

import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent   # agentRunner/
CONFIG_FILE = BASE_DIR.parent / "agent.json"        # 默认位置：项目根目录（AGENT_CONFIG 可覆盖）
ENV_FILE = BASE_DIR / ".env"

_config: dict | None = None


def _config_file() -> Path:
    """实际生效的配置文件路径：.env / 环境变量里的 AGENT_CONFIG 优先。
    注意调用时机：必须在 _load_env() 之后（.env 里的值才读得到）"""
    return Path(os.environ.get("AGENT_CONFIG") or CONFIG_FILE)


def _load_env() -> None:
    """极简 .env 解析：KEY=VALUE，# 开头为注释；不覆盖已有环境变量"""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _strip_comments(text: str) -> str:
    """剥掉 // 和 /* */ 注释，但不动字符串字面量内部
    （例如 "https://..." 里的 // 必须保留）"""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _substitute(node):
    """递归替换所有字符串值里的 ${VAR} / ${VAR:-默认值}；
    未定义（或空）变量：无默认值替换为空串，有默认值用默认值"""
    if isinstance(node, str):
        return _VAR_RE.sub(
            lambda m: os.environ.get(m.group(1)) or (m.group(2) or ""), node)
    if isinstance(node, list):
        return [_substitute(x) for x in node]
    if isinstance(node, dict):
        return {k: _substitute(v) for k, v in node.items()}
    return node


def _normalize(cfg: dict, base_dir: Path) -> dict:
    # agents.home / agents.workspace 相对路径 → 相对配置文件所在目录解析
    # 绝对路径（以 / 开头）直接用
    agents = cfg.setdefault("agents", {})
    home = agents.get("home", "AgentsHome")
    agents["home"] = str(Path(home) if Path(home).is_absolute() else (base_dir / home).resolve())
    workspace = agents.get("workspace", "Zootopia/homework")
    agents["workspace"] = str(Path(workspace) if Path(workspace).is_absolute() else (base_dir / workspace).resolve())

    # 每个账号的 owner_open_ids：元素内逗号拆分 + 剔除空串
    feishu = cfg.get("channels", {}).get("feishu", {})
    for acct in feishu.get("accounts", {}).values():
        ids = acct.get("owner_open_ids", []) or []
        acct["owner_open_ids"] = [
            s.strip() for item in ids for s in str(item).split(",") if s.strip()
        ]

    # skills_dir / asr.model_dir 同样相对配置文件所在目录解析
    # 绝对路径直接用
    tools = cfg.setdefault("tools", {})
    skills_dir = tools.get("skills_dir", "AgentsHome/skills")
    tools["skills_dir"] = str(Path(skills_dir) if Path(skills_dir).is_absolute() else (base_dir / skills_dir).resolve())
    asr = tools.get("asr")
    if asr and asr.get("model_dir"):
        md = asr["model_dir"]
        asr["model_dir"] = str(Path(md) if Path(md).is_absolute() else (base_dir / md).resolve())
    return cfg


def _check_file_permissions() -> list[str]:
    """检查 .env 和 agent.json 等敏感文件的权限是否过宽。"""
    issues = []
    for label, path in [(".env", ENV_FILE), ("agent.json", CONFIG_FILE)]:
        if not path.exists():
            continue
        mode = path.stat().st_mode
        world_readable = mode & 0o004
        group_readable = mode & 0o040
        if world_readable:
            issues.append(f"{label} ({path}) 对所有人可读，建议 chmod 600")
        elif group_readable:
            issues.append(f"{label} ({path}) 对同组用户可读，建议 chmod 600")
    return issues


def load() -> dict:
    """加载并返回配置（幂等，重复调用直接返回缓存）"""
    global _config
    if _config is not None:
        return _config
    _load_env()
    cfg_file = _config_file()
    raw = cfg_file.read_text(encoding="utf-8")
    plaintext_issues = _check_plaintext_keys(raw)
    perm_issues = _check_file_permissions()
    all_issues = plaintext_issues + perm_issues
    if all_issues:
        import sys
        for issue in all_issues:
            print(f"[config] ⚠️  {issue}", file=sys.stderr)
    _config = _normalize(_substitute(json.loads(_strip_comments(raw))),
                         cfg_file.parent)
    return _config


def get() -> dict:
    """取配置（未加载则自动加载，evals 等直接 import core 的场景可用）"""
    return load()


def _check_plaintext_keys(raw_cfg_text: str) -> list[str]:
    """检测 agent.json 中是否还有明文 sk- 前缀的 API Key，返回警告列表。
    只检查 models.*.api_key 字段，且仅在 ${VAR} 未替换的情况下触发。"""
    issues = []
    try:
        raw = json.loads(_strip_comments(raw_cfg_text))
    except Exception:
        return issues
    models = raw.get("models", {})
    for name, model_cfg in models.items():
        api_key = model_cfg.get("api_key", "") if isinstance(model_cfg, dict) else ""
        if api_key and not api_key.startswith("${") and api_key.startswith("sk-"):
            issues.append(f"models.{name}.api_key 含明文 key（应以 ${{VAR}} 引用环境变量）")
    return issues


def check() -> list[str]:
    """校验必填项，返回缺失项描述列表（空 = 全部就绪）。
    default 账号允许留空（运行时跳过）；固定人格账号缺凭证会在启动时
    逐个告警跳过，但一个可用账号都没有则视为配置缺失。"""
    cfg = load()
    missing = []
    default_model = cfg.get("models", {}).get("default", {})
    if not default_model.get("api_key"):
        missing.append("models.default.api_key（.env 里配置 AGENT_API_KEY）")
    feishu = cfg.get("channels", {}).get("feishu", {})
    if feishu.get("enabled"):
        usable = [
            name for name, acct in feishu.get("accounts", {}).items()
            if acct.get("enabled", True)
            and acct.get("app_id") and acct.get("app_secret")
        ]
        if not usable:
            missing.append("channels.feishu.accounts：没有任何凭证完整的账号"
                           "（.env 里配置 FEISHU_*_APP_ID / FEISHU_*_APP_SECRET）")
    return missing
