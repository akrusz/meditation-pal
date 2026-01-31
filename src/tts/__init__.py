"""Text-to-speech engines."""

from .base import TTSEngine
from .macos import MacOSTTS
from .piper import PiperTTS
from .parakeet import ParakeetTTS
from .elevenlabs import ElevenLabsTTS

__all__ = [
    "TTSEngine",
    "MacOSTTS",
    "PiperTTS",
    "ParakeetTTS",
    "ElevenLabsTTS",
    "create_tts",
]


def create_tts(
    engine: str = "macos",
    voice: str | None = None,
    rate: int = 180,
    **kwargs,
) -> "MacOSTTS | PiperTTS | ParakeetTTS | ElevenLabsTTS":
    """Factory function to create TTS engine.

    Args:
        engine: TTS engine name:
            - "macos": macOS native 'say' command (zero latency, decent quality)
            - "piper": Piper TTS (fast local neural TTS)
            - "parakeet": NVIDIA Parakeet (high quality neural TTS)
            - "elevenlabs": ElevenLabs API (highest quality, requires API key)
        voice: Voice name/model (engine-specific)
        rate: Speaking rate in WPM (mainly for macos)
        **kwargs: Additional engine-specific arguments

    Returns:
        TTS engine instance
    """
    if engine == "macos":
        return MacOSTTS(
            voice=voice or "Samantha",
            rate=rate,
        )

    elif engine == "piper":
        return PiperTTS(
            voice=voice or "en_US-lessac-medium",
            rate=float(rate) / 180.0,  # Convert WPM to rate multiplier
            model_path=kwargs.get("model_path"),
        )

    elif engine == "parakeet":
        return ParakeetTTS(
            model_name=kwargs.get("model_name", "nvidia/parakeet-tts-1.1b"),
            device=kwargs.get("device", "auto"),
            backend=kwargs.get("backend", "transformers"),
        )

    elif engine == "elevenlabs":
        return ElevenLabsTTS(
            api_key=kwargs.get("api_key"),
            voice_name=voice,
            voice_id=kwargs.get("voice_id"),
            model_id=kwargs.get("model_id", "eleven_monolingual_v1"),
            stability=kwargs.get("stability", 0.75),
            similarity_boost=kwargs.get("similarity_boost", 0.75),
        )

    else:
        raise ValueError(
            f"Unknown TTS engine: {engine}. "
            f"Available: macos, piper, parakeet, elevenlabs"
        )
