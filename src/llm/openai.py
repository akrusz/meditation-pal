"""OpenAI API provider."""

import os

from .base import BaseLLMProvider, Message, CompletionResult


class OpenAIProvider(BaseLLMProvider):
    """LLM provider using the OpenAI API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4o",
        max_tokens: int = 300,
        base_url: str | None = None,
        env_key: str = "OPENAI_API_KEY",
    ):
        """Initialize OpenAI provider.

        Args:
            api_key: API key (defaults to env_key env var)
            model: Model to use
            max_tokens: Maximum tokens in response
            base_url: Optional base URL for OpenAI-compatible APIs (e.g. OpenRouter)
            env_key: Environment variable name for the API key
        """
        super().__init__(model=model, max_tokens=max_tokens)
        self.api_key = api_key or os.environ.get(env_key)
        self.base_url = base_url

        if not self.api_key:
            raise ValueError(
                f"API key required. Set {env_key} environment variable "
                "or pass api_key parameter."
            )

        self._client = None

    def _get_client(self):
        """Get or create OpenAI client."""
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise ImportError(
                    "openai package not installed. Run: pip install openai"
                )

            self._client = openai.AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
            )

        return self._client

    async def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Generate a completion using OpenAI API."""
        client = self._get_client()

        # Build messages list
        openai_messages = []

        if system:
            openai_messages.append({
                "role": "system",
                "content": system,
            })

        for msg in messages:
            openai_messages.append({
                "role": msg.role,
                "content": msg.content,
            })

        # Make API call
        response = await client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            max_tokens=max_tokens or self.max_tokens,
        )

        # Extract response
        choice = response.choices[0]
        text = choice.message.content or ""

        tokens_used = None
        if response.usage:
            tokens_used = response.usage.total_tokens

        return CompletionResult(
            text=text,
            finish_reason=choice.finish_reason,
            tokens_used=tokens_used,
        )
