
from typing import Any

import httpx

from issuehunter.config import get_settings


class OllamaError(Exception):
    """Raised when an Ollama request fails."""


class OllamaClient:
    """Client for generating responses with a local Ollama model."""

    def __init__(self) -> None:
        self.settings = get_settings()

        self.model = self.settings.ollama_model
        self.client = httpx.Client(
            base_url=self.settings.ollama_base_url.rstrip("/"),
            timeout=self.settings.request_timeout,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.client.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def generate(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.2,
    ) -> str:
        """
        Generate a response using Ollama's chat API.

        Args:
            prompt: User prompt sent to the model.
            system_prompt: Optional instructions for the model.
            temperature: Controls response randomness.

        Returns:
            The generated response text.
        """
        if not prompt.strip():
            raise ValueError("Prompt cannot be empty.")

        if not 0 <= temperature <= 2:
            raise ValueError("Temperature must be between 0 and 2.")

        messages = []

        if system_prompt:
            messages.append({
                "role": "system",
                "content": system_prompt,
            })

        messages.append({
            "role": "user",
            "content": prompt,
        })

        try:
            response = self.client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "options": {
                        "temperature": temperature,
                        "num_ctx": self.settings.ollama_num_ctx,
                        "num_predict": 2048,
                    },
                },
            )
            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise OllamaError(
                f"Ollama returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:500]}"
            ) from exc

        except httpx.RequestError as exc:
            raise OllamaError(
                f"Could not connect to Ollama: {exc}"
            ) from exc

        try:
            payload = response.json()
            message = payload.get("message", {})
            content = message.get("content", "")

            if not content.strip():
                raise OllamaError(
                    "Ollama returned empty content. "
                    f"Done reason: {payload.get('done_reason')}; "
                    f"Thinking: {message.get('thinking', '')[:500]!r}; "
                    f"Generated tokens: {payload.get('eval_count')}"
                )
            #content = payload["message"]["content"]

            if not isinstance(content, str):
                raise TypeError("Response content is not text.")

            return content.strip()

        except (ValueError, KeyError, TypeError) as exc:
            raise OllamaError(
                "Ollama returned an unexpected response."
            ) from exc
