"""Whisper speech-to-text engine."""

from typing import Literal

import numpy as np

from .base import STTEngine, TranscriptionResult


WhisperModel = Literal["tiny", "base", "small", "medium", "large", "large-v2", "large-v3"]


class WhisperSTT:
    """Speech-to-text using OpenAI's Whisper model.

    Supports both the official whisper package and mlx-whisper
    for Apple Silicon optimization.
    """

    def __init__(
        self,
        model: WhisperModel = "small",
        language: str | None = "en",
        device: str = "auto",
        use_mlx: bool = False,
    ):
        """Initialize Whisper STT.

        Args:
            model: Whisper model size
            language: Language code (e.g., 'en') or None for auto-detect
            device: Device to use ('auto', 'cpu', 'cuda', 'mps')
            use_mlx: Use mlx-whisper for Apple Silicon (requires separate install)
        """
        self.model_name = model
        self.language = language
        self.device = device
        self.use_mlx = use_mlx

        self._model = None
        self._loaded = False

    def _load_model(self) -> None:
        """Lazy load the Whisper model."""
        if self._loaded:
            return

        if self.use_mlx:
            self._load_mlx_model()
        else:
            self._load_whisper_model()

        self._loaded = True

    def _load_whisper_model(self) -> None:
        """Load standard Whisper model."""
        try:
            import whisper
        except ImportError:
            raise ImportError(
                "whisper not installed. Run: pip install openai-whisper"
            )

        # Determine device
        device = self.device
        if device == "auto":
            import torch

            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                device = "cpu"  # MPS has issues with whisper, use CPU
            else:
                device = "cpu"

        print(f"Loading Whisper model '{self.model_name}' on {device}...")
        self._model = whisper.load_model(self.model_name, device=device)
        self._whisper_module = whisper

    def _load_mlx_model(self) -> None:
        """Load MLX-optimized Whisper model."""
        try:
            import mlx_whisper
        except ImportError:
            raise ImportError(
                "mlx-whisper not installed. Run: pip install mlx-whisper"
            )

        print(f"Loading MLX Whisper model '{self.model_name}'...")
        # mlx-whisper uses different model loading
        self._mlx_whisper = mlx_whisper
        self._model = self.model_name  # mlx-whisper loads on demand

    def transcribe(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
    ) -> TranscriptionResult:
        """Transcribe audio to text.

        Args:
            audio: Audio data as numpy array
            sample_rate: Sample rate of the audio (should be 16000 for Whisper)

        Returns:
            TranscriptionResult with transcribed text
        """
        self._load_model()

        # Convert to float32 if needed
        if audio.dtype == np.int16:
            audio = audio.astype(np.float32) / 32768.0
        elif audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        # Resample if needed (Whisper expects 16kHz)
        if sample_rate != 16000:
            audio = self._resample(audio, sample_rate, 16000)

        duration = len(audio) / 16000.0

        if self.use_mlx:
            return self._transcribe_mlx(audio, duration)
        else:
            return self._transcribe_whisper(audio, duration)

    def _transcribe_whisper(
        self,
        audio: np.ndarray,
        duration: float,
    ) -> TranscriptionResult:
        """Transcribe using standard Whisper."""
        # Pad/trim to 30 seconds as Whisper expects
        audio = self._whisper_module.pad_or_trim(audio)

        # Make log-Mel spectrogram
        mel = self._whisper_module.log_mel_spectrogram(audio).to(self._model.device)

        # Detect language if not specified
        language = self.language
        if language is None:
            _, probs = self._model.detect_language(mel)
            language = max(probs, key=probs.get)

        # Decode
        options = self._whisper_module.DecodingOptions(
            language=language,
            fp16=False,  # Use fp32 for stability
        )
        result = self._whisper_module.decode(self._model, mel, options)

        return TranscriptionResult(
            text=result.text.strip(),
            language=language,
            confidence=None,  # Whisper doesn't provide confidence directly
            duration=duration,
        )

    def _transcribe_mlx(
        self,
        audio: np.ndarray,
        duration: float,
    ) -> TranscriptionResult:
        """Transcribe using MLX Whisper."""
        result = self._mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=f"mlx-community/whisper-{self.model_name}-mlx",
            language=self.language,
        )

        return TranscriptionResult(
            text=result["text"].strip(),
            language=result.get("language"),
            confidence=None,
            duration=duration,
        )

    def transcribe_file(self, path: str) -> TranscriptionResult:
        """Transcribe audio from a file.

        Args:
            path: Path to audio file

        Returns:
            TranscriptionResult with transcribed text
        """
        self._load_model()

        if self.use_mlx:
            result = self._mlx_whisper.transcribe(
                path,
                path_or_hf_repo=f"mlx-community/whisper-{self.model_name}-mlx",
                language=self.language,
            )
            return TranscriptionResult(
                text=result["text"].strip(),
                language=result.get("language"),
                confidence=None,
                duration=None,
            )
        else:
            result = self._model.transcribe(
                path,
                language=self.language,
                fp16=False,
            )
            return TranscriptionResult(
                text=result["text"].strip(),
                language=result.get("language"),
                confidence=None,
                duration=None,
            )

    def _resample(
        self,
        audio: np.ndarray,
        orig_sr: int,
        target_sr: int,
    ) -> np.ndarray:
        """Resample audio to target sample rate."""
        try:
            import librosa

            return librosa.resample(audio, orig_sr=orig_sr, target_sr=target_sr)
        except ImportError:
            # Simple linear interpolation fallback
            ratio = target_sr / orig_sr
            new_length = int(len(audio) * ratio)
            indices = np.linspace(0, len(audio) - 1, new_length)
            return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)


def create_stt(
    engine: str = "whisper",
    model: str = "small",
    language: str = "en",
    device: str = "auto",
) -> WhisperSTT:
    """Factory function to create STT engine."""
    if engine == "whisper":
        return WhisperSTT(
            model=model,  # type: ignore
            language=language,
            device=device,
            use_mlx=False,
        )
    elif engine == "mlx-whisper":
        return WhisperSTT(
            model=model,  # type: ignore
            language=language,
            device=device,
            use_mlx=True,
        )
    else:
        raise ValueError(f"Unknown STT engine: {engine}")
