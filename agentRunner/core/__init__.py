"""
core — Agent 引擎包
===================

对外暴露原 core.py 的全部接口，evals.py 等调用方 import 路径不变：
  from core import list_agents, run_agent, run_agent_meta, agent_display, ...
"""

from .config import BASE_DIR, check, get, load
from .engine import (
    TOOL_FUNCTIONS,
    TOOL_SCHEMAS,
    chat_with_retry,
    handle_command,
    load_skills,
    run_agent,
    run_agent_meta,
    run_proactive,
    write_tools_md,
)
from .memory import (
    MEMORY_DIR,
    get_chat_agent,
    load_pending,
    pop_pending,
    save_pending,
    set_chat_agent,
)
from .models import agent_model_name, get_client, model_id
from .personas import (
    agent_display,
    agent_workspace,
    build_system_prompt,
    default_agent,
    home,
    list_agents,
    workspace_root,
)
from .scheduler import (
    load_jobs,
    resolve_push_channel,
    start_scheduler,
    start_sentinel,
)

__all__ = [
    # config
    "BASE_DIR", "check", "get", "load",
    # engine
    "TOOL_FUNCTIONS", "TOOL_SCHEMAS", "chat_with_retry", "handle_command",
    "load_skills", "run_agent", "run_agent_meta", "run_proactive",
    "write_tools_md",
    # memory / sessions
    "MEMORY_DIR", "get_chat_agent", "load_pending", "pop_pending",
    "save_pending", "set_chat_agent",
    # models
    "agent_model_name", "get_client", "model_id",
    # personas
    "agent_display", "agent_workspace", "build_system_prompt", "default_agent",
    "home", "list_agents", "workspace_root",
    # scheduler
    "load_jobs", "resolve_push_channel", "start_scheduler", "start_sentinel",
]
