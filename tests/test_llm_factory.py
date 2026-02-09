"""Tests for LLM Factory module."""

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.exceptions import ConfigurationError, ExtractionError
from src.core.llm_factory import (
    AnthropicProvider,
    LLMFactory,
    LLMProvider,
    LLMResponse,
    VisionCapable,
    _parse_json_response,
    encode_image_to_base64,
    get_image_size_mb,
)


# ── LLMResponse dataclass ──────────────────────────────────────────────


class TestLLMResponse:
    """Tests for LLMResponse dataclass."""

    def test_basic_response(self):
        r = LLMResponse(content="hello", model="test-model")
        assert r.content == "hello"
        assert r.model == "test-model"
        assert r.usage is None
        assert r.images_processed == 0

    def test_response_with_usage(self):
        r = LLMResponse(
            content="ok",
            model="m",
            usage={"input_tokens": 10, "output_tokens": 5},
            images_processed=2,
        )
        assert r.usage["input_tokens"] == 10
        assert r.images_processed == 2


# ── JSON parsing ────────────────────────────────────────────────────────


class TestParseJsonResponse:
    """Tests for _parse_json_response helper."""

    def test_parses_plain_json(self):
        result = _parse_json_response('{"title": "Test"}')
        assert result == {"title": "Test"}

    def test_parses_json_in_code_block(self):
        text = '```json\n{"title": "Test"}\n```'
        result = _parse_json_response(text)
        assert result == {"title": "Test"}

    def test_parses_json_in_bare_code_block(self):
        text = '```\n{"key": "val"}\n```'
        result = _parse_json_response(text)
        assert result == {"key": "val"}

    def test_raises_on_malformed_json(self):
        with pytest.raises(ExtractionError, match="not valid JSON"):
            _parse_json_response("not json at all")

    def test_raises_on_non_dict_json(self):
        with pytest.raises(ExtractionError, match="not a JSON object"):
            _parse_json_response('["item1", "item2"]')

    def test_handles_whitespace(self):
        result = _parse_json_response('  \n {"a": 1}  \n')
        assert result == {"a": 1}


# ── Image encoding helpers ──────────────────────────────────────────────


class TestImageHelpers:
    """Tests for image encoding utilities."""

    def test_encode_missing_file(self):
        with pytest.raises(FileNotFoundError):
            encode_image_to_base64("/nonexistent/image.png")

    def test_encode_unsupported_type(self, tmp_path):
        txt = tmp_path / "test.xyz"
        txt.write_bytes(b"\x00" * 10)
        with pytest.raises(ValueError, match="Unsupported image type"):
            encode_image_to_base64(str(txt))

    def test_encode_valid_image(self, tmp_path):
        png = tmp_path / "test.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        data, mime = encode_image_to_base64(str(png))
        assert isinstance(data, str)
        assert mime == "image/png"

    def test_get_image_size(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\x00" * 1024 * 1024)  # 1MB
        size = get_image_size_mb(str(img))
        assert abs(size - 1.0) < 0.01


# ── LLMFactory ──────────────────────────────────────────────────────────


class TestLLMFactory:
    """Tests for LLMFactory."""

    def test_unknown_provider_raises(self):
        with pytest.raises(ConfigurationError, match="Unknown provider"):
            LLMFactory.create("unknown_provider")

    def test_available_providers(self):
        providers = LLMFactory.available_providers()
        assert "anthropic" in providers
        assert "openai" in providers
        assert "gemini" in providers

    def test_creates_anthropic_provider(self):
        with patch("anthropic.AsyncAnthropic"):
            provider = LLMFactory.create("anthropic", api_key="sk-test")
            assert isinstance(provider, AnthropicProvider)

    def test_case_insensitive_provider(self):
        with patch("anthropic.AsyncAnthropic"):
            provider = LLMFactory.create("Anthropic", api_key="sk-test")
            assert isinstance(provider, AnthropicProvider)


# ── AnthropicProvider ───────────────────────────────────────────────────


class TestAnthropicProvider:
    """Tests for AnthropicProvider."""

    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError, match="ANTHROPIC_API_KEY"):
                AnthropicProvider()

    def test_accepts_explicit_key(self):
        with patch("anthropic.AsyncAnthropic"):
            p = AnthropicProvider(api_key="sk-test-123")
            assert p._api_key == "sk-test-123"

    def test_reads_key_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-env"}, clear=True):
            with patch("anthropic.AsyncAnthropic"):
                p = AnthropicProvider()
                assert p._api_key == "sk-env"

    def test_default_model(self):
        with patch("anthropic.AsyncAnthropic"):
            p = AnthropicProvider(api_key="test")
            assert p.model == "claude-3-haiku-20240307"

    def test_custom_model(self):
        with patch("anthropic.AsyncAnthropic"):
            p = AnthropicProvider(api_key="test", model="claude-3-sonnet-20240229")
            assert p.model == "claude-3-sonnet-20240229"

    @pytest.mark.asyncio
    async def test_generate_returns_response(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello world")]
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 5

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            p = AnthropicProvider(api_key="test")
            result = await p.generate("Say hello")

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello world"
        assert result.usage["input_tokens"] == 10

    @pytest.mark.asyncio
    async def test_generate_empty_response(self):
        mock_response = MagicMock()
        mock_response.content = []

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            p = AnthropicProvider(api_key="test")
            with pytest.raises(ExtractionError, match="empty response"):
                await p.generate("Say hello")

    @pytest.mark.asyncio
    async def test_extract_structured(self):
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='{"title": "Test Article", "tags": ["python"]}')
        ]
        mock_response.usage.input_tokens = 50
        mock_response.usage.output_tokens = 20

        mock_client = AsyncMock()
        mock_client.messages.create.return_value = mock_response

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            p = AnthropicProvider(api_key="test")
            result = await p.extract_structured(
                "Article text here", "Extract title and tags as JSON"
            )

        assert result["title"] == "Test Article"
        assert result["tags"] == ["python"]

    @pytest.mark.asyncio
    async def test_connection_error(self):
        import anthropic

        mock_client = AsyncMock()
        mock_client.messages.create.side_effect = anthropic.APIConnectionError(
            request=MagicMock()
        )

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            p = AnthropicProvider(api_key="test")
            with pytest.raises(ExtractionError, match="Failed to connect"):
                await p.generate("test")

    @pytest.mark.asyncio
    async def test_rate_limit_error(self):
        import anthropic

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="Rate limited", response=mock_resp, body={}
        )

        with patch("anthropic.AsyncAnthropic", return_value=mock_client):
            p = AnthropicProvider(api_key="test")
            with pytest.raises(ExtractionError, match="rate limit"):
                await p.generate("test")

    def test_is_vision_capable(self):
        with patch("anthropic.AsyncAnthropic"):
            p = AnthropicProvider(api_key="test")
            assert isinstance(p, VisionCapable)


# ── OpenAIProvider ──────────────────────────────────────────────────────


class TestOpenAIProvider:
    """Tests for OpenAIProvider."""

    def _make_provider(self, **kwargs):
        """Helper to create OpenAIProvider with mocked import."""
        mock_client = AsyncMock()
        mock_openai = MagicMock()
        mock_openai.AsyncOpenAI.return_value = mock_client

        with patch.dict(sys.modules, {"openai": mock_openai}):
            # Need to reimport to pick up the mock
            from src.core.llm_factory import OpenAIProvider

            return OpenAIProvider(**kwargs), mock_client

    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError, match="OPENAI_API_KEY"):
                self._make_provider()

    def test_default_model(self):
        p, _ = self._make_provider(api_key="sk-test")
        assert p.model == "gpt-4o-mini"

    def test_reasoning_model_detection(self):
        p1, _ = self._make_provider(api_key="sk-test", model="gpt-5.2")
        assert p1._is_reasoning_model() is True

        p2, _ = self._make_provider(api_key="sk-test", model="gpt-4o")
        assert p2._is_reasoning_model() is False

        p3, _ = self._make_provider(api_key="sk-test", model="o3-mini")
        assert p3._is_reasoning_model() is True

    @pytest.mark.asyncio
    async def test_generate_returns_response(self):
        p, mock_client = self._make_provider(api_key="sk-test")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Response text"
        mock_response.usage.model_dump.return_value = {"total_tokens": 15}
        mock_client.chat.completions.create.return_value = mock_response

        result = await p.generate("Hello")
        assert result.content == "Response text"
        assert result.usage["total_tokens"] == 15

    def test_is_vision_capable(self):
        p, _ = self._make_provider(api_key="sk-test")
        assert isinstance(p, VisionCapable)


# ── GeminiProvider ──────────────────────────────────────────────────────


class TestGeminiProvider:
    """Tests for GeminiProvider."""

    def _make_provider(self, **kwargs):
        """Helper to create GeminiProvider with mocked import."""
        mock_client = MagicMock()
        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client

        with patch.dict(sys.modules, {"google": MagicMock(), "google.genai": mock_genai}):
            from src.core.llm_factory import GeminiProvider

            return GeminiProvider(**kwargs), mock_client

    def test_requires_api_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError, match="GEMINI_API_KEY"):
                self._make_provider()

    def test_default_model(self):
        p, _ = self._make_provider(api_key="test-key")
        assert p.model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_generate_returns_response(self):
        p, mock_client = self._make_provider(api_key="test-key")

        mock_response = MagicMock()
        mock_response.text = "Gemini response"
        mock_response.usage_metadata.prompt_token_count = 10
        mock_response.usage_metadata.candidates_token_count = 20

        # Patch the generate_content on the actual stored client
        p._genai_client.aio.models.generate_content = AsyncMock(
            return_value=mock_response
        )

        result = await p.generate("Hello")
        assert result.content == "Gemini response"
        assert result.usage["input_tokens"] == 10

    def test_is_vision_capable(self):
        p, _ = self._make_provider(api_key="test-key")
        assert isinstance(p, VisionCapable)


# ── VisionCapable protocol ─────────────────────────────────────────────


class TestVisionProtocol:
    """Tests for VisionCapable protocol."""

    def test_anthropic_is_vision_capable(self):
        with patch("anthropic.AsyncAnthropic"):
            a = AnthropicProvider(api_key="test")
            assert isinstance(a, VisionCapable)

    def test_non_vision_class_is_not_capable(self):
        class PlainProvider(LLMProvider):
            async def generate(self, prompt, system_prompt=None, *, max_tokens=None):
                return LLMResponse(content="", model="test")

        p = PlainProvider()
        assert not isinstance(p, VisionCapable)


# ── Config integration ──────────────────────────────────────────────────


class TestConfigIntegration:
    """Tests for config + factory integration."""

    def test_config_llm_provider_default(self):
        from src.core.config import load_config, reset_config

        reset_config()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}, clear=True):
            config = load_config()
            assert config.llm_provider == "anthropic"
            assert config.llm_model is None

    def test_config_llm_provider_custom(self):
        from src.core.config import load_config, reset_config

        reset_config()
        env = {
            "ANTHROPIC_API_KEY": "test",
            "LLM_PROVIDER": "openai",
            "LLM_MODEL": "gpt-4o",
            "OPENAI_API_KEY": "sk-test",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
            assert config.llm_provider == "openai"
            assert config.llm_model == "gpt-4o"
            assert config.openai_api_key == "sk-test"

    def test_config_invalid_provider(self):
        from src.core.config import load_config, reset_config

        reset_config()
        env = {"ANTHROPIC_API_KEY": "test", "LLM_PROVIDER": "invalid"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="LLM_PROVIDER"):
                load_config()
