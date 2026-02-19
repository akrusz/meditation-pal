"""Cross-platform audio file playback.

Uses sounddevice (already a dependency) for playback and scipy for
reading WAV files. No additional dependencies needed.
"""

import asyncio

import numpy as np
import sounddevice as sd
from scipy.io import wavfile


async def play_audio_file(file_path: str) -> None:
    """Play a WAV audio file.

    Blocks until playback completes or stop_playback() is called.

    Args:
        file_path: Path to a WAV file
    """
    rate, data = wavfile.read(file_path)

    # Convert integer formats to float32 for sounddevice
    if data.dtype == np.int16:
        data = data.astype(np.float32) / 32768.0
    elif data.dtype == np.int32:
        data = data.astype(np.float32) / 2147483648.0

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: sd.play(data, rate, blocking=True)
    )


async def play_audio_bytes(
    pcm_data: bytes, sample_rate: int = 22050
) -> None:
    """Play raw 16-bit PCM audio bytes.

    Args:
        pcm_data: Raw 16-bit little-endian PCM audio data
        sample_rate: Sample rate in Hz
    """
    data = np.frombuffer(pcm_data, dtype=np.int16).astype(np.float32) / 32768.0
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, lambda: sd.play(data, sample_rate, blocking=True)
    )


def stop_playback() -> None:
    """Stop any current audio playback."""
    sd.stop()
