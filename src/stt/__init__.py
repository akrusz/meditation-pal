"""Speech-to-text engines."""

from .base import STTEngine, TranscriptionResult
from .whisper import WhisperSTT

__all__ = ["STTEngine", "TranscriptionResult", "WhisperSTT"]
