"""local_stt.py — 本機語音轉文字（faster-whisper，預設引擎）。

不需要任何雲端 API Key；模型第一次使用時會從 Hugging Face 下載到本機快取
（~/.cache/huggingface），之後完全離線可用。用 large-v3-turbo：中英文夾雜
（這個 app 的典型使用情境——邊講話邊夾雜程式術語）辨識效果比純中文微調模型
好，速度也比 large-v3 快得多，CPU 就能跑。

模型物件很重（載入要花幾秒、佔用數百 MB～GB 記憶體），所以用 process 內的
單例，只載入一次、重複使用；真正的推論（CPU-bound）丟到 executor 跑，避免
卡住 event loop。
"""

from __future__ import annotations

import asyncio
import io
import threading

MODEL_SIZE = "large-v3-turbo"

_model = None
_model_lock = threading.Lock()


class LocalSttUnavailable(RuntimeError):
    """faster-whisper 沒安裝，或模型載入失敗（例如第一次使用但沒有網路可下載）。"""


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise LocalSttUnavailable(
                "faster-whisper 未安裝；請執行 pip install -r requirements.txt"
            ) from e
        try:
            _model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
        except Exception as e:
            raise LocalSttUnavailable(f"本機語音模型載入失敗：{e}") from e
        return _model


def _transcribe_sync(audio_bytes: bytes, language: str) -> str:
    model = _get_model()
    segments, _info = model.transcribe(
        io.BytesIO(audio_bytes),
        language=language or None,
        vad_filter=True,
    )
    return "".join(seg.text for seg in segments).strip()


async def transcribe_local(audio_bytes: bytes, language: str = "") -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _transcribe_sync, audio_bytes, language)
