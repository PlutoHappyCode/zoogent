"""
core/asr.py — 语音转写（faster-whisper 本地 ASR）
==================================================

飞书语音消息（opus）→ 文字。模型懒加载单例：
  - 模型：agent.json → tools.asr.model（默认 small，中文够用）
  - 模型目录：tools.asr.model_dir（NAS 上 /data/zoogent/models，SSD 挂载内）
  - 依赖 faster-whisper（PyAV 自带 ffmpeg 库，opus 直解，无需系统 ffmpeg）

faster-whisper 未安装时降级：transcribe 返回空串，渠道提示用户改打字。
"""

from .config import BASE_DIR, get
from .log import get_logger

log = get_logger("asr")

import threading

_model = None
_MODEL_LOCK = threading.Lock()  # 防预热和语音消息并发触发双重加载


def _cfg() -> tuple[str, str]:
    c = get().get("tools", {}).get("asr", {})
    return (c.get("model", "small"),
            c.get("model_dir") or str(BASE_DIR.parent / "models"))


def _get_model():
    """懒加载模型单例（首次加载要从磁盘读 464MB 并初始化，较慢）"""
    global _model
    if _model is not None:
        return _model
    with _MODEL_LOCK:
        if _model is None:
            from faster_whisper import WhisperModel
            size, model_dir = _cfg()
            _model = WhisperModel(size, device="cpu", compute_type="int8",
                                  download_root=model_dir)
            log.info("🎤 ASR 模型已加载（%s，%s）", size, model_dir)
    return _model


def warmup() -> None:
    """启动时在后台线程预加载模型：避免第一条语音消息干等模型加载"""
    try:
        _get_model()
    except Exception as e:
        log.warning("ASR 模型预热失败（语音消息暂不可用）：%s", e)


def transcribe(audio_path) -> str:
    """音频文件 → 文字（中文优化）。不可用/失败返回空串"""
    try:
        from faster_whisper import WhisperModel  # noqa: F401
    except ImportError:
        log.warning("faster-whisper 未安装，语音消息无法转写")
        return ""
    try:
        segments, _ = _get_model().transcribe(
            str(audio_path), language="zh", vad_filter=True,
            initial_prompt="以下是一段简体中文语音，请用简体字转写。")
        return " ".join(s.text.strip() for s in segments).strip()
    except Exception as e:
        log.warning("语音转写失败：%s", e)
        return ""
