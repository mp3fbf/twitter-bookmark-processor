"""Tests for LLM client module."""

import os
from unittest.mock import MagicMock, patch

import pytest

from src.core.config import reset_config
from src.core.exceptions import ConfigurationError, ExtractionError
from src.core.llm_client import (
    DEFAULT_MODEL,
    LLMClient,
    get_llm_client,
    reset_llm_client,
)


class TestLLMClientRequiresApiKey:
    """Tests for API key requirement."""

    def setup_method(self):
        """Reset singletons before each test."""
        reset_config()
        reset_llm_client()

    def teardown_method(self):
        """Reset singletons after each test."""
        reset_config()
        reset_llm_client()

    def test_llm_client_requires_api_key(self):
        """Erro se ANTHROPIC_API_KEY não set."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                LLMClient()

            assert "ANTHROPIC_API_KEY" in str(exc_info.value)

    def test_llm_client_accepts_explicit_api_key(self):
        """Client accepts API key passed directly."""
        with patch("src.core.llm_client.anthropic.Anthropic"):
            client = LLMClient(api_key="sk-test-key-123")
            assert client._api_key == "sk-test-key-123"

    def test_llm_client_reads_api_key_from_config(self):
        """Client reads API key from config when not provided."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-from-env"}, clear=True):
            with patch("src.core.llm_client.anthropic.Anthropic"):
                client = LLMClient()
                assert client._api_key == "sk-from-env"


class TestLLMClientUsesHaiku:
    """Tests for model selection."""

    def test_llm_client_uses_haiku(self):
        """Model é claude-3-haiku."""
        with patch("src.core.llm_client.anthropic.Anthropic"):
            client = LLMClient(api_key="test-key")

            assert "haiku" in client.model.lower()
            assert client.model == DEFAULT_MODEL

    def test_llm_client_allows_custom_model(self):
        """Client allows custom model override."""
        with patch("src.core.llm_client.anthropic.Anthropic"):
            client = LLMClient(api_key="test-key", model="claude-3-sonnet-20240229")

            assert client.model == "claude-3-sonnet-20240229"


class TestLLMClientStructuredOutput:
    """Tests for structured JSON output."""

    def test_llm_client_structured_output(self):
        """Retorna JSON parseável."""
        mock_anthropic = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='{"title": "Test", "summary": "A summary"}')
        ]
        mock_anthropic.return_value.messages.create.return_value = mock_response

        with patch("src.core.llm_client.anthropic.Anthropic", mock_anthropic):
            client = LLMClient(api_key="test-key")
            result = client.extract_structured(
                content="Some article text",
                system_prompt="Extract title and summary as JSON",
            )

        assert isinstance(result, dict)
        assert result["title"] == "Test"
        assert result["summary"] == "A summary"

    def test_llm_client_handles_json_in_code_block(self):
        """Client handles JSON wrapped in markdown code block."""
        mock_anthropic = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='```json\n{"title": "Test"}\n```')
        ]
        mock_anthropic.return_value.messages.create.return_value = mock_response

        with patch("src.core.llm_client.anthropic.Anthropic", mock_anthropic):
            client = LLMClient(api_key="test-key")
            result = client.extract_structured(
                content="Some text",
                system_prompt="Extract as JSON",
            )

        assert result["title"] == "Test"

    def test_llm_client_handles_malformed_json(self):
        """Client raises ExtractionError for malformed JSON."""
        mock_anthropic = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text="This is not JSON at all")
        ]
        mock_anthropic.return_value.messages.create.return_value = mock_response

        with patch("src.core.llm_client.anthropic.Anthropic", mock_anthropic):
            client = LLMClient(api_key="test-key")

            with pytest.raises(ExtractionError) as exc_info:
                client.extract_structured(
                    content="Some text",
                    system_prompt="Extract as JSON",
                )

            assert "not valid JSON" in str(exc_info.value)

    def test_llm_client_handles_empty_response(self):
        """Client raises ExtractionError for empty response."""
        mock_anthropic = MagicMock()
        mock_response = MagicMock()
        mock_response.content = []
        mock_anthropic.return_value.messages.create.return_value = mock_response

        with patch("src.core.llm_client.anthropic.Anthropic", mock_anthropic):
            client = LLMClient(api_key="test-key")

            with pytest.raises(ExtractionError) as exc_info:
                client.extract_structured(
                    content="Some text",
                    system_prompt="Extract as JSON",
                )

            assert "empty response" in str(exc_info.value)

    def test_llm_client_handles_non_dict_json(self):
        """Client raises ExtractionError when JSON is not an object."""
        mock_anthropic = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='["item1", "item2"]')
        ]
        mock_anthropic.return_value.messages.create.return_value = mock_response

        with patch("src.core.llm_client.anthropic.Anthropic", mock_anthropic):
            client = LLMClient(api_key="test-key")

            with pytest.raises(ExtractionError) as exc_info:
                client.extract_structured(
                    content="Some text",
                    system_prompt="Extract as JSON",
                )

            assert "not a JSON object" in str(exc_info.value)


class TestLLMClientApiErrors:
    """Tests for API error handling."""

    def test_llm_client_handles_connection_error(self):
        """Client raises ExtractionError on connection failure."""
        import anthropic

        mock_anthropic_class = MagicMock()
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = anthropic.APIConnectionError(
            request=MagicMock()
        )
        mock_anthropic_class.return_value = mock_client

        with patch("src.core.llm_client.anthropic.Anthropic", mock_anthropic_class):
            client = LLMClient(api_key="test-key")

            with pytest.raises(ExtractionError) as exc_info:
                client.extract_structured("content", "prompt")

            assert "Failed to connect" in str(exc_info.value)

    def test_llm_client_handles_rate_limit(self):
        """Client raises ExtractionError on rate limit."""
        import anthropic

        mock_anthropic_class = MagicMock()
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_client.messages.create.side_effect = anthropic.RateLimitError(
            message="Rate limited",
            response=mock_response,
            body={}
        )
        mock_anthropic_class.return_value = mock_client

        with patch("src.core.llm_client.anthropic.Anthropic", mock_anthropic_class):
            client = LLMClient(api_key="test-key")

            with pytest.raises(ExtractionError) as exc_info:
                client.extract_structured("content", "prompt")

            assert "rate limit" in str(exc_info.value)


class TestLLMClientSingleton:
    """Tests for singleton functionality."""

    def setup_method(self):
        """Reset singletons before each test."""
        reset_config()
        reset_llm_client()

    def teardown_method(self):
        """Reset singletons after each test."""
        reset_config()
        reset_llm_client()

    def test_get_llm_client_returns_same_instance(self):
        """get_llm_client returns same instance on repeated calls."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            with patch("src.core.llm_client.anthropic.Anthropic"):
                client1 = get_llm_client()
                client2 = get_llm_client()

                assert client1 is client2

    def test_reset_llm_client_clears_cache(self):
        """reset_llm_client forces new instance on next call."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            with patch("src.core.llm_client.anthropic.Anthropic"):
                client1 = get_llm_client()
                reset_llm_client()
                client2 = get_llm_client()

                assert client1 is not client2

    def test_get_llm_client_requires_api_key(self):
        """get_llm_client raises ConfigurationError without API key."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError):
                get_llm_client()


class TestLLMClientMaxTokens:
    """Tests for max tokens configuration."""

    def test_llm_client_default_max_tokens(self):
        """Client uses default max tokens."""
        with patch("src.core.llm_client.anthropic.Anthropic"):
            client = LLMClient(api_key="test-key")
            assert client._max_tokens == 1024

    def test_llm_client_custom_max_tokens(self):
        """Client allows custom max tokens."""
        with patch("src.core.llm_client.anthropic.Anthropic"):
            client = LLMClient(api_key="test-key", max_tokens=2048)
            assert client._max_tokens == 2048

    def test_extract_respects_max_tokens_override(self):
        """extract_structured respects max_tokens parameter."""
        mock_anthropic = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"key": "value"}')]
        mock_create = mock_anthropic.return_value.messages.create
        mock_create.return_value = mock_response

        with patch("src.core.llm_client.anthropic.Anthropic", mock_anthropic):
            client = LLMClient(api_key="test-key", max_tokens=1024)
            client.extract_structured("content", "prompt", max_tokens=512)

            # Verify max_tokens was passed correctly
            call_kwargs = mock_create.call_args.kwargs
            assert call_kwargs["max_tokens"] == 512
