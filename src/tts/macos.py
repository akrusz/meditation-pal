"""macOS native text-to-speech using the 'say' command."""

import asyncio
import subprocess
from typing import Literal


class MacOSTTS:
    """Text-to-speech using macOS 'say' command.

    Zero latency, no API cost, decent quality.
    Available voices include: Samantha, Ava, Alex, Allison, Susan, Tom, etc.
    Enhanced voices (e.g., "Ava (Enhanced)") have better quality.
    """

    def __init__(
        self,
        voice: str = "Samantha",
        rate: int = 180,
    ):
        """Initialize macOS TTS.

        Args:
            voice: Voice name (e.g., "Samantha", "Ava", "Alex")
            rate: Speaking rate in words per minute
        """
        self.voice = voice
        self.rate = rate
        self._process: asyncio.subprocess.Process | None = None
        self._speaking = False

    async def speak(self, text: str) -> None:
        """Speak the given text.

        Args:
            text: Text to speak
        """
        # Stop any current speech
        self.stop()

        if not text.strip():
            return

        self._speaking = True
        try:
            cmd = ["say", "-v", self.voice, "-r", str(self.rate), text]

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await self._process.wait()
        finally:
            self._speaking = False
            self._process = None

    def speak_sync(self, text: str) -> None:
        """Speak text synchronously (blocking).

        Args:
            text: Text to speak
        """
        if not text.strip():
            return

        cmd = ["say", "-v", self.voice, "-r", str(self.rate), text]
        subprocess.run(cmd, check=True)

    def stop(self) -> None:
        """Stop any current speech."""
        if self._process:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
            self._process = None
        self._speaking = False

        # Also kill any running say processes
        try:
            subprocess.run(
                ["pkill", "-9", "say"],
                capture_output=True,
            )
        except Exception:
            pass

    def is_speaking(self) -> bool:
        """Check if currently speaking.

        Returns:
            True if currently speaking
        """
        return self._speaking

    def set_voice(self, voice: str) -> None:
        """Set the voice to use.

        Args:
            voice: Voice name
        """
        self.voice = voice

    def set_rate(self, rate: int) -> None:
        """Set the speaking rate.

        Args:
            rate: Words per minute
        """
        self.rate = rate

    @staticmethod
    def list_voices() -> list[str]:
        """List available voices.

        Returns:
            List of voice names
        """
        result = subprocess.run(
            ["say", "-v", "?"],
            capture_output=True,
            text=True,
        )

        voices = []
        for line in result.stdout.strip().split("\n"):
            if line:
                # Format: "Voice Name    lang  # description"
                voice_name = line.split()[0]
                voices.append(voice_name)

        return voices
