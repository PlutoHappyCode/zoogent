"""
core/models.py — 模型注册表
============================

agent.json 的 "models" 节定义命名模型，agents.members 里按名引用。
当前仅支持 provider = openai-compatible（OpenAI SDK 直连）。
"""

from openai import OpenAI

from .config import get

_clients: dict[str, OpenAI] = {}


def default_model_name() -> str:
    """兜底模型名：优先 "default"，否则第一个非 embed 模型"""
    models = get().get("models", {})
    if "default" in models:
        return "default"
    for name in models:
        if name != "embed":
            return name
    raise ValueError("agent.json 的 models 节没有可用模型")


def model_config(name: str | None = None) -> dict:
    """取某个命名模型的配置（base_url / api_key / model），None = 兜底模型"""
    name = name or default_model_name()
    models = get().get("models", {})
    if name not in models:
        raise ValueError(f"未知模型「{name}」，可用：{sorted(models)}")
    return models[name]


def model_id(name: str | None = None) -> str:
    """模型配置里实际发给 API 的模型名"""
    return model_config(name)["model"]


def get_client(name: str | None = None) -> OpenAI:
    """按模型名取 OpenAI 客户端（每个模型名缓存一个）"""
    name = name or default_model_name()
    if name not in _clients:
        cfg = model_config(name)
        _clients[name] = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"])
    return _clients[name]


def agent_model_name(agent: str) -> str:
    """某人格用的注册表模型名：members 里指定的优先，否则兜底模型"""
    members = get().get("agents", {}).get("members", {})
    return members.get(agent, {}).get("model") or default_model_name()


def get_embeddings(texts: list[str], model_name: str = "embed") -> list[list[float]]:
    """批量 embedding（agent.json 的 models.embed 配置，RAG 知识库用）"""
    cfg = model_config(model_name)
    resp = get_client(model_name).embeddings.create(
        model=cfg["model"], input=texts)
    return [d.embedding for d in resp.data]
