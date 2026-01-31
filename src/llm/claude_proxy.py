"""Claude provider using CLIProxyAPI for Max subscription routing.

CLIProxyAPI exposes your Claude Max subscription as an OpenAI-compatible endpoint.
https://github.com/AntranCorp/CLIProxyAPI
"""

import httpx

from .base import BaseLLMProvider, Message, CompletionResult


class ClaudeProxyProvider(BaseLLMProvider):
    """LLM provider using CLIProxyAPI to route through Claude Max subscription.

    This allows using Claude without additional API costs by leveraging
    an existing Max subscription.
    """

    def __init__(
        self,
        proxy_url: str = "http://127.0.0.1:8317",
        model: str = "claude-sonnet-4-5-20250929",
        max_tokens: int = 300,
        timeout: float = 60.0,
    ):
        """Initialize Claude proxy provider.

        Args:
            proxy_url: URL of the CLIProxyAPI server
            model: Model to use
            max_tokens: Maximum tokens in response
            timeout: Request timeout in seconds
        """
        super().__init__(model=model, max_tokens=max_tokens)
        self.proxy_url = proxy_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Generate a completion using CLIProxyAPI.

        The proxy exposes an OpenAI-compatible endpoint.
        """
        client = await self._get_client()

        # Build OpenAI-format messages
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

        # Make request to proxy
        response = await client.post(
            f"{self.proxy_url}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": openai_messages,
                "max_tokens": max_tokens or self.max_tokens,
            },
        )
        response.raise_for_status()

        data = response.json()

        # Extract response
        choice = data["choices"][0]
        text = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")

        tokens_used = None
        if "usage" in data:
            tokens_used = data["usage"].get("total_tokens")

        return CompletionResult(
            text=text,
            finish_reason=finish_reason,
            tokens_used=tokens_used,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
