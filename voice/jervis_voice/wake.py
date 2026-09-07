"""Wake-word detection with openwakeword."""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

import numpy as np

from .audio import FRAME_SAMPLES, WAKE_CHUNK_FRAMES

log = logging.getLogger("jervis.voice.wake")

# Models live here rather than in site-packages, which `uv sync --reinstall` wipes.
MODEL_DIR = Path("~/.jervis/models/openwakeword").expanduser()

# openwakeword ships this one. A custom "jervis" model is the eventual goal - see
# voice/jervis_voice/models/README.md - but Phase 3 must not be blocked on training.
BUILTIN_MODELS = ("hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy")

# tflite-runtime has no cp312 wheels, so ONNX is the only option on this Python.
INFERENCE_FRAMEWORK = "onnx"


def ensure_models(model: str = "hey_jarvis", target: Path | None = None) -> Path:
    """Download the wake-word models into a directory that survives a venv rebuild."""
    directory = target or MODEL_DIR
    directory.mkdir(parents=True, exist_ok=True)

    import openwakeword
    import openwakeword.utils as utils

    packaged = Path(openwakeword.__file__).parent / "resources" / "models"
    names = [model] if model in BUILTIN_MODELS else []
    if not _have_models(directory, model):
        utils.download_models(model_names=names)
        for source in packaged.glob("*.onnx"):
            destination = directory / source.name
            if not destination.exists():
                shutil.copy2(source, destination)
    return directory


def _have_models(directory: Path, model: str) -> bool:
    needed = ["melspectrogram.onnx", "embedding_model.onnx"]
    if not all((directory / name).exists() for name in needed):
        return False
    return bool(list(directory.glob(f"{model}*.onnx")) or (directory / f"{model}.onnx").exists())


def resolve_model_path(model: str, directory: Path) -> str:
    """A custom model is a file here; a built-in one is named."""
    explicit = directory / f"{model}.onnx"
    if explicit.exists():
        return str(explicit)
    matches = sorted(directory.glob(f"{model}_v*.onnx"))
    if matches:
        return str(matches[0])
    if model in BUILTIN_MODELS:
        return model
    raise FileNotFoundError(
        f"No wake-word model {model!r} in {directory}. Train one (see "
        "voice/jervis_voice/models/README.md) or set voice.wake_model to one of "
        f"{', '.join(BUILTIN_MODELS)}."
    )


class WakeListener:
    """Streams 20ms frames in, reports when the wake word is heard.

    A single detection fires a burst of high scores, so a cooldown stops one spoken
    "Jervis" from triggering three times.
    """

    def __init__(
        self,
        model: str = "hey_jarvis",
        threshold: float = 0.6,
        cooldown_seconds: float = 2.0,
        model_dir: Path | None = None,
    ) -> None:
        self.model_name = model
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds
        directory = ensure_models(model, model_dir)

        from openwakeword.model import Model

        self._model = Model(
            wakeword_models=[resolve_model_path(model, directory)],
            inference_framework=INFERENCE_FRAMEWORK,
        )
        self._buffer: list[np.ndarray] = []
        self._last_fired = 0.0
        self.last_score = 0.0

    def feed(self, frame: np.ndarray, *, now: float | None = None) -> bool:
        """Add one 20ms frame. True when the wake word has just been heard."""
        self._buffer.append(frame)
        if len(self._buffer) < WAKE_CHUNK_FRAMES:
            return False

        chunk = np.concatenate(self._buffer)
        self._buffer.clear()
        if chunk.size != FRAME_SAMPLES * WAKE_CHUNK_FRAMES:
            return False

        scores = self._model.predict(chunk.astype(np.int16))
        self.last_score = max(scores.values()) if scores else 0.0

        moment = time.monotonic() if now is None else now
        if self.last_score < self.threshold:
            return False
        if moment - self._last_fired < self.cooldown_seconds:
            return False
        self._last_fired = moment
        log.info("wake word heard (score %.2f)", self.last_score)
        self.reset()
        return True

    def reset(self) -> None:
        """Clear the model's internal audio history.

        Without this the frames that triggered a detection stay in the buffer and
        immediately re-trigger it after the cooldown.
        """
        self._buffer.clear()
        if hasattr(self._model, "reset"):
            self._model.reset()
