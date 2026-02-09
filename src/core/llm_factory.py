"""Multi-LLM Provider Factory for Twitter Bookmark Processor.

Supports Anthropic (Claude), OpenAI (GPT), and Google Gemini providers
with a unified async interface. Includes vision capabilities for
providers that support image analysis.

Ported from twitter-bookmarks-app/llm_providers.py, rewritten as async.
"""

import base64
import json
import logging
import mimetypes
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

import anthropic

from src.core.exceptions import ConfigurationError, ExtractionError

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    """Standardized response from any LLM provider."""

    content: str
    model: str
    usage: Optional[dict[str, Any]] = None
    images_processed: int = 0


@runtime_checkable
class VisionCapable(Protocol):
    """Protocol for providers that support image analysis."""

    async def generate_with_vision(
        self,
        prompt: str,
        images: list[str],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse: ...


def encode_image_to_base64(image_path: str) -> tuple[str, str]:
    """Encode an image file to base64.

    Args:
        image_path: Path to the image file

    Returns:
        Tuple of (base64_data, media_type)

    Raises:
        FileNotFoundError: If the image file doesn't exist
        ValueError: If the file type is not supported
    """
    path = Path(image_path)

    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")

    mime_type, _ = mimetypes.guess_type(str(path))
    if mime_type is None:
        ext = path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        mime_type = mime_map.get(ext)

    if mime_type is None or not mime_type.startswith("image/"):
        raise ValueError(f"Unsupported image type: {path.suffix}")

    with open(path, "rb") as f:
        image_data = base64.standard_b64encode(f.read()).decode("utf-8")

    return image_data, mime_type


def get_image_size_mb(image_path: str) -> float:
    """Get image file size in MB."""
    return Path(image_path).stat().st_size / (1024 * 1024)


class LLMProvider(ABC):
    """Abstract base class for LLM providers.

    All providers must implement async generate() and extract_structured().
    Vision-capable providers also implement generate_with_vision().
    """

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        """Generate a text response from the LLM.

        Args:
            prompt: User prompt text
            system_prompt: Optional system instructions
            max_tokens: Optional max output tokens override

        Returns:
            LLMResponse with content and metadata
        """

    async def extract_structured(
        self,
        content: str,
        system_prompt: str,
        *,
        max_tokens: Optional[int] = None,
    ) -> dict[str, Any]:
        """Extract structured JSON data from content.

        Args:
            content: The text content to analyze
            system_prompt: Instructions requesting JSON output
            max_tokens: Optional max output tokens override

        Returns:
            Parsed JSON dict from LLM response

        Raises:
            ExtractionError: If LLM call fails or response is not valid JSON
        """
        response = await self.generate(
            content, system_prompt, max_tokens=max_tokens
        )
        return _parse_json_response(response.content)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider with vision support."""

    DEFAULT_MODEL = "claude-3-haiku-20240307"
    DEFAULT_MAX_TOKENS = 1024

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is required for Anthropic provider"
            )

        self._model = model or self.DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

    @property
    def model(self) -> str:
        return self._model

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens or self._max_tokens,
                system=system_prompt or "You are a helpful assistant.",
                messages=[{"role": "user", "content": prompt}],
            )

            if not response.content:
                raise ExtractionError("LLM returned empty response")

            return LLMResponse(
                content=response.content[0].text,
                model=self._model,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            )
        except anthropic.APIConnectionError as e:
            raise ExtractionError(f"Failed to connect to Anthropic API: {e}")
        except anthropic.RateLimitError as e:
            raise ExtractionError(f"Anthropic API rate limit exceeded: {e}")
        except anthropic.APIStatusError as e:
            raise ExtractionError(
                f"Anthropic API error: {e.status_code} - {e.message}"
            )

    async def generate_with_vision(
        self,
        prompt: str,
        images: list[str],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Generate response with image analysis.

        Args:
            prompt: Text prompt
            images: List of image file paths or URLs
            system_prompt: Optional system instructions

        Returns:
            LLMResponse with vision analysis
        """
        # Build content blocks
        content: list[dict[str, Any]] = []
        images_processed = 0

        for image_path in images[:20]:  # Claude limit: 20 images
            try:
                if image_path.startswith(("http://", "https://")):
                    content.append(
                        {
                            "type": "image",
                            "source": {"type": "url", "url": image_path},
                        }
                    )
                    images_processed += 1
                else:
                    size_mb = get_image_size_mb(image_path)
                    if size_mb > 5:
                        logger.warning(
                            "Skipping %s - exceeds 5MB limit (%.1fMB)",
                            image_path,
                            size_mb,
                        )
                        continue
                    base64_data, media_type = encode_image_to_base64(image_path)
                    content.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": base64_data,
                            },
                        }
                    )
                    images_processed += 1
            except (FileNotFoundError, ValueError) as e:
                logger.warning("Skipping image %s: %s", image_path, e)
                continue

        content.append({"type": "text", "text": prompt})

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system_prompt
                or "You are a helpful assistant that analyzes images.",
                messages=[{"role": "user", "content": content}],
            )

            return LLMResponse(
                content=response.content[0].text,
                model=self._model,
                usage={
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
                images_processed=images_processed,
            )
        except anthropic.APIConnectionError as e:
            raise ExtractionError(f"Failed to connect to Anthropic API: {e}")
        except anthropic.RateLimitError as e:
            raise ExtractionError(f"Anthropic API rate limit exceeded: {e}")
        except anthropic.APIStatusError as e:
            raise ExtractionError(
                f"Anthropic API error: {e.status_code} - {e.message}"
            )


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider with vision support."""

    DEFAULT_MODEL = "gpt-4o-mini"
    DEFAULT_MAX_TOKENS = 1024

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ConfigurationError(
                "openai package required: pip install openai"
            )

        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise ConfigurationError(
                "OPENAI_API_KEY is required for OpenAI provider"
            )

        self._model = model or self.DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._client = AsyncOpenAI(api_key=self._api_key)

    @property
    def model(self) -> str:
        return self._model

    def _is_reasoning_model(self) -> bool:
        """Check if model uses max_completion_tokens (o1/o3/gpt-5.x)."""
        return (
            self._model.startswith("o1")
            or self._model.startswith("o3")
            or self._model.startswith("gpt-5")
        )

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        tokens = max_tokens or self._max_tokens
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }

        if self._is_reasoning_model():
            kwargs["max_completion_tokens"] = tokens
            kwargs["timeout"] = 120.0
        else:
            kwargs["max_tokens"] = tokens
            kwargs["temperature"] = 0.7
            kwargs["timeout"] = 60.0

        try:
            response = await self._client.chat.completions.create(**kwargs)
            content = response.choices[0].message.content or ""

            return LLMResponse(
                content=content,
                model=self._model,
                usage=response.usage.model_dump() if response.usage else None,
            )
        except Exception as e:
            if "Invalid API key" in str(e) or "Incorrect API key" in str(e):
                raise ConfigurationError(f"OpenAI API key error: {e}")
            raise ExtractionError(f"OpenAI API error: {e}")

    async def generate_with_vision(
        self,
        prompt: str,
        images: list[str],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Generate response with image analysis using GPT vision."""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        images_processed = 0

        for image_path in images:
            try:
                if image_path.startswith(("http://", "https://")):
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": image_path, "detail": "high"},
                        }
                    )
                    images_processed += 1
                else:
                    base64_data, media_type = encode_image_to_base64(image_path)
                    content.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media_type};base64,{base64_data}",
                                "detail": "high",
                            },
                        }
                    )
                    images_processed += 1
            except (FileNotFoundError, ValueError) as e:
                logger.warning("Skipping image %s: %s", image_path, e)
                continue

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "timeout": 180.0,
        }

        if self._is_reasoning_model():
            kwargs["max_completion_tokens"] = 4096
        else:
            kwargs["max_tokens"] = 4096

        try:
            response = await self._client.chat.completions.create(**kwargs)
            return LLMResponse(
                content=response.choices[0].message.content or "",
                model=self._model,
                usage=response.usage.model_dump() if response.usage else None,
                images_processed=images_processed,
            )
        except Exception as e:
            raise ExtractionError(f"OpenAI vision error: {e}")


class GeminiProvider(LLMProvider):
    """Google Gemini provider with vision support."""

    DEFAULT_MODEL = "gemini-2.5-flash"
    DEFAULT_MAX_TOKENS = 1024

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        try:
            from google import genai
        except ImportError:
            raise ConfigurationError(
                "google-genai package required: pip install google-genai"
            )

        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self._api_key:
            raise ConfigurationError(
                "GEMINI_API_KEY is required for Gemini provider"
            )

        self._model_name = model or self.DEFAULT_MODEL
        self._max_tokens = max_tokens
        self._genai_client = genai.Client(api_key=self._api_key)

    @property
    def model(self) -> str:
        return self._model_name

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        max_tokens: Optional[int] = None,
    ) -> LLMResponse:
        from google.genai import types

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        try:
            response = await self._genai_client.aio.models.generate_content(
                model=self._model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=max_tokens or self._max_tokens,
                ),
            )

            return LLMResponse(
                content=response.text or "",
                model=self._model_name,
                usage=(
                    {
                        "input_tokens": response.usage_metadata.prompt_token_count,
                        "output_tokens": response.usage_metadata.candidates_token_count,
                    }
                    if response.usage_metadata
                    else None
                ),
            )
        except Exception as e:
            raise ExtractionError(f"Gemini API error: {e}")

    async def generate_with_vision(
        self,
        prompt: str,
        images: list[str],
        system_prompt: Optional[str] = None,
    ) -> LLMResponse:
        """Generate response with image analysis using Gemini."""
        from google.genai import types

        content_parts: list[Any] = []
        images_processed = 0

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"{system_prompt}\n\n{prompt}"

        for image_path in images:
            try:
                base64_data, media_type = encode_image_to_base64(image_path)
                content_parts.append(
                    types.Part.from_bytes(
                        data=base64.b64decode(base64_data),
                        mime_type=media_type,
                    )
                )
                images_processed += 1
            except (FileNotFoundError, ValueError) as e:
                logger.warning("Skipping image %s: %s", image_path, e)
                continue

        content_parts.append(full_prompt)

        try:
            response = await self._genai_client.aio.models.generate_content(
                model=self._model_name,
                contents=content_parts,
                config=types.GenerateContentConfig(
                    max_output_tokens=4096,
                ),
            )

            return LLMResponse(
                content=response.text or "",
                model=self._model_name,
                usage=(
                    {
                        "input_tokens": response.usage_metadata.prompt_token_count,
                        "output_tokens": response.usage_metadata.candidates_token_count,
                    }
                    if response.usage_metadata
                    else None
                ),
                images_processed=images_processed,
            )
        except Exception as e:
            raise ExtractionError(f"Gemini vision error: {e}")


class LLMFactory:
    """Factory for creating LLM provider instances."""

    _PROVIDERS: dict[str, tuple[type[LLMProvider], str]] = {
        "anthropic": (AnthropicProvider, AnthropicProvider.DEFAULT_MODEL),
        "openai": (OpenAIProvider, OpenAIProvider.DEFAULT_MODEL),
        "gemini": (GeminiProvider, GeminiProvider.DEFAULT_MODEL),
    }

    @staticmethod
    def create(
        provider: str,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> LLMProvider:
        """Create an LLM provider instance.

        Args:
            provider: One of 'anthropic', 'openai', 'gemini'
            api_key: API key (optional if set in environment)
            model: Model name (optional, uses provider defaults)
            **kwargs: Additional provider-specific options

        Returns:
            LLMProvider instance

        Raises:
            ConfigurationError: If provider is unknown or API key is missing
        """
        provider_lower = provider.lower()
        if provider_lower not in LLMFactory._PROVIDERS:
            available = ", ".join(sorted(LLMFactory._PROVIDERS.keys()))
            raise ConfigurationError(
                f"Unknown provider: {provider}. Available: {available}"
            )

        provider_class, _ = LLMFactory._PROVIDERS[provider_lower]
        return provider_class(api_key=api_key, model=model, **kwargs)

    @staticmethod
    def available_providers() -> list[str]:
        """Return list of available provider names."""
        return sorted(LLMFactory._PROVIDERS.keys())


def _parse_json_response(response_text: str) -> dict[str, Any]:
    """Parse JSON from LLM response text.

    Handles JSON wrapped in markdown code blocks.

    Args:
        response_text: Raw text response from LLM

    Returns:
        Parsed JSON dict

    Raises:
        ExtractionError: If response is not valid JSON
    """
    text = response_text.strip()

    # Extract from markdown code block
    if text.startswith("```"):
        lines = text.split("\n")
        start_idx = 1
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
        raise ExtractionError(f"LLM response is not valid JSON: {e.msg}")
