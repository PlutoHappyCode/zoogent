"""
channels — 渠道包
=================

按配置实例化渠道（惰性 import：只用终端时不需要 lark_oapi）。
飞书是多账号：build_feishu_channels 返回 {account_name: FeishuChannel}。
"""

from core.log import get_logger

from .base import Channel, IncomingMessage

log = get_logger("channels")


def build_channel(name: str, cfg: dict) -> Channel:
    """单渠道构造（terminal 等无账号概念的渠道）"""
    if name == "terminal":
        from .terminal import TerminalChannel
        return TerminalChannel()
    raise ValueError(f"未知渠道「{name}」（飞书请用 build_feishu_channels）")


def build_feishu_channels(feishu_cfg: dict) -> dict[str, Channel]:
    """按 channels.feishu.accounts 逐账号实例化。
    跳过：enabled=false、凭证不完整（default 账号 app_id 为空时也跳过）"""
    from .feishu import FeishuChannel

    shared = {"reactions": feishu_cfg.get("reactions", {}),
              "card": feishu_cfg.get("card", {}),
              "whitelist": feishu_cfg.get("whitelist", True)}
    channels: dict[str, Channel] = {}
    for account, acct_cfg in feishu_cfg.get("accounts", {}).items():
        if not acct_cfg.get("enabled", True):
            log.info("⏭️ 账号 %s 已禁用，跳过", account)
            continue
        if not acct_cfg.get("app_id") or not acct_cfg.get("app_secret"):
            hint = "（.env 里配置 FEISHU_APP_ID/FEISHU_APP_SECRET 可启用）" \
                if account == "default" else ""
            log.warning("账号 %s 凭证不完整，跳过%s", account, hint)
            continue
        channels[account] = FeishuChannel(account, acct_cfg, shared)
        mode = f"固定人格 {acct_cfg.get('agent')}" if acct_cfg.get("agent") \
            else "多人格"
        log.info("📡 飞书账号已就绪：%s（%s）", account, mode)
    return channels


__all__ = ["Channel", "IncomingMessage", "build_channel",
           "build_feishu_channels"]
