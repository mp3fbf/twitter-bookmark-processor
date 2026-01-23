"""LLM Client for content extraction using Anthropic Claude.

Provides a simple interface for calling Claude Haiku to extract
structured data from text content (articles, threads, etc).
"""

import json
from typing import Any

import anthropic

from src.core.config import get_config
from src.core.exceptions import ConfigurationError, ExtractionError

# Default model for content extraction (fast and cheap)
DEFAULT_MODEL = "claude-3-haiku-20240307"

# Default max tokens for extraction responses
DEFAULT_MAX_TOKENS = 1024


class LLMClient:
    """Client for interacting with Anthropic Claude API.

    Uses Claude Haiku for cost-effective content extraction.
    Returns structured JSON output.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        """Initialize LLM client.

        Args:
            api_key: Anthropic API key. If not provided, reads from config.
            model: Model to use (default: claude-3-haiku-20240307).
            max_tokens: Maximum tokens in response (default: 1024).

        Raises:
            ConfigurationError: If API key is not provided and not in config.
        """
        if api_key:
            self._api_key = api_key
        else:
            config = get_config(require_api_key=True)
            self._api_key = config.anthropic_api_key

        if not self._api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is required for LLM client"
            )

        self._model = model
        self._max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=self._api_key)

    @property
    def model(self) -> str:
        """Return the model being used."""
        return self._model

    def extract_structured(
        self,
        content: str,
        system_prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Extract structured data from content using LLM.

        Args:
            content: The text content to analyze.
            system_prompt: Instructions for extraction (should request JSON output).
            max_tokens: Override default max tokens for this request.

        Returns:
            Parsed JSON dict from LLM response.

        Raises:
            ExtractionError: If LLM call fails or response is not valid JSON.
        """
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens or self._max_tokens,
                system=system_prompt,
                messages=[
                    {"role": "user", "content": content}
                ],
            )

            # Extract text from response
            if not response.content:
                raise ExtractionError("LLM returned empty response")

            response_text = response.content[0].text

            # Parse JSON from response
            return self._parse_json_response(response_text)

        except anthropic.APIConnectionError as e:
            raise ExtractionError(f"Failed to connect to Anthropic API: {e}")
        except anthropic.RateLimitError as e:
            raise ExtractionError(f"Anthropic API rate limit exceeded: {e}")
        except anthropic.APIStatusError as e:
            raise ExtractionError(f"Anthropic API error: {e.status_code} - {e.message}")

    def _parse_json_response(self, response_text: str) -> dict[str, Any]:
        """Parse JSON from LLM response text.

        Handles cases where JSON is wrapped in markdown code blocks.

        Args:
            response_text: Raw text response from LLM.

        Returns:
            Parsed JSON dict.

        Raises:
            ExtractionError: If response is not valid JSON.
        """
        text = response_text.strip()

        # Try to extract JSON from markdown code block
        if text.startswith("```"):
            # Remove ```json or ``` prefix
            lines = text.split("\n")
            # Find start of JSON (skip first line with ```)
            start_idx = 1
            # Find end (line with just ```)
            end_idx = len(lines)
            for i, line in enumerate(lines[1:], 1):
                if line.strip() == "```":
                    end_idx = i
                    break
            text = "\n".join(lines[start_idx:end_idx])

        try:
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ExtractionError(
                    f"LLM response is not a JSON object: {type(result).__name__}"
                )
            return result
        except json.JSONDecodeError as e:
            raise ExtractionError(
                f"LLM response is not valid JSON: {e.msg}"
            )


# Singleton instance
_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Get the global LLM client instance.

    Creates client on first call and caches for subsequent calls.

    Returns:
        The global LLMClient instance.

    Raises:
        ConfigurationError: If ANTHROPIC_API_KEY is not set.
    """
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm_client() -> None:
    """Reset the global LLM client instance.

    Forces next get_llm_client() call to create a new client.
    Useful for testing.
    """
    global _client
    _client = None
