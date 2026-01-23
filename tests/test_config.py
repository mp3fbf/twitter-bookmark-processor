"""Tests for Configuration Manager."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.config import Config, ConfigurationError, get_config, load_config, reset_config


class TestConfigDefaults:
    """Test Config default values."""

    def test_config_has_correct_defaults(self):
        """Config should have sensible defaults for optional fields."""
        config = Config(anthropic_api_key="test-key")

        assert config.output_dir == Path("/workspace/notes/twitter/")
        assert config.state_file == Path("data/state.json")
        assert config.cache_file == Path("data/link_cache.json")
        assert config.rate_limit_video == 1.0
        assert config.rate_limit_thread == 0.5
        assert config.rate_limit_link == 0.2
        assert config.log_level == "INFO"
        assert config.max_concurrent_workers == 5
        assert config.twitter_webhook_token is None

    def test_config_accepts_string_paths(self):
        """Config should convert string paths to Path objects."""
        config = Config(
            anthropic_api_key="test-key",
            output_dir="/custom/output",  # type: ignore[arg-type]
            state_file="/custom/state.json",  # type: ignore[arg-type]
            cache_file="/custom/cache.json",  # type: ignore[arg-type]
        )

        assert isinstance(config.output_dir, Path)
        assert isinstance(config.state_file, Path)
        assert isinstance(config.cache_file, Path)
        assert config.output_dir == Path("/custom/output")


class TestConfigValidation:
    """Test Config validation."""

    def test_config_validates_log_level(self):
        """Config should reject invalid log levels."""
        with pytest.raises(ConfigurationError) as exc_info:
            Config(anthropic_api_key="test-key", log_level="INVALID")

        assert "Invalid LOG_LEVEL" in str(exc_info.value)
        assert "INVALID" in str(exc_info.value)

    def test_config_accepts_valid_log_levels(self):
        """Config should accept all standard log levels."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            config = Config(anthropic_api_key="test-key", log_level=level)
            assert config.log_level == level

    def test_config_normalizes_log_level_case(self):
        """Config should normalize log level to uppercase."""
        config = Config(anthropic_api_key="test-key", log_level="debug")
        assert config.log_level == "DEBUG"

    def test_config_validates_rate_limit_video_non_negative(self):
        """Config should reject negative rate_limit_video."""
        with pytest.raises(ConfigurationError) as exc_info:
            Config(anthropic_api_key="test-key", rate_limit_video=-1.0)

        assert "TWITTER_RATE_LIMIT_VIDEO" in str(exc_info.value)
        assert "non-negative" in str(exc_info.value)

    def test_config_validates_rate_limit_thread_non_negative(self):
        """Config should reject negative rate_limit_thread."""
        with pytest.raises(ConfigurationError) as exc_info:
            Config(anthropic_api_key="test-key", rate_limit_thread=-0.5)

        assert "TWITTER_RATE_LIMIT_THREAD" in str(exc_info.value)

    def test_config_validates_rate_limit_link_non_negative(self):
        """Config should reject negative rate_limit_link."""
        with pytest.raises(ConfigurationError) as exc_info:
            Config(anthropic_api_key="test-key", rate_limit_link=-0.1)

        assert "TWITTER_RATE_LIMIT_LINK" in str(exc_info.value)

    def test_config_validates_max_workers_positive(self):
        """Config should reject non-positive max_concurrent_workers."""
        with pytest.raises(ConfigurationError) as exc_info:
            Config(anthropic_api_key="test-key", max_concurrent_workers=0)

        assert "TWITTER_MAX_WORKERS" in str(exc_info.value)
        assert "at least 1" in str(exc_info.value)

    def test_config_accepts_zero_rate_limits(self):
        """Config should accept zero rate limits (no delay)."""
        config = Config(
            anthropic_api_key="test-key",
            rate_limit_video=0.0,
            rate_limit_thread=0.0,
            rate_limit_link=0.0,
        )
        assert config.rate_limit_video == 0.0
        assert config.rate_limit_thread == 0.0
        assert config.rate_limit_link == 0.0


class TestLoadConfig:
    """Test load_config function."""

    def test_load_config_requires_api_key_by_default(self):
        """load_config should raise when ANTHROPIC_API_KEY is missing."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load_config()

            assert "ANTHROPIC_API_KEY" in str(exc_info.value)
            assert "required" in str(exc_info.value)

    def test_load_config_allows_missing_api_key_when_not_required(self):
        """load_config should allow missing API key when require_api_key=False."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config(require_api_key=False)
            assert config.anthropic_api_key == ""

    def test_load_config_reads_all_env_vars(self):
        """load_config should read all configuration from environment."""
        env = {
            "ANTHROPIC_API_KEY": "sk-test-key-123",
            "TWITTER_WEBHOOK_TOKEN": "webhook-secret",
            "TWITTER_OUTPUT_DIR": "/custom/notes/",
            "TWITTER_STATE_FILE": "/custom/state.json",
            "TWITTER_CACHE_FILE": "/custom/cache.json",
            "TWITTER_RATE_LIMIT_VIDEO": "2.5",
            "TWITTER_RATE_LIMIT_THREAD": "1.0",
            "TWITTER_RATE_LIMIT_LINK": "0.5",
            "LOG_LEVEL": "DEBUG",
            "TWITTER_MAX_WORKERS": "10",
        }

        with patch.dict(os.environ, env, clear=True):
            config = load_config()

        assert config.anthropic_api_key == "sk-test-key-123"
        assert config.twitter_webhook_token == "webhook-secret"
        assert config.output_dir == Path("/custom/notes/")
        assert config.state_file == Path("/custom/state.json")
        assert config.cache_file == Path("/custom/cache.json")
        assert config.rate_limit_video == 2.5
        assert config.rate_limit_thread == 1.0
        assert config.rate_limit_link == 0.5
        assert config.log_level == "DEBUG"
        assert config.max_concurrent_workers == 10

    def test_load_config_uses_defaults_for_missing_optional(self):
        """load_config should use defaults when optional env vars are missing."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            config = load_config()

        assert config.output_dir == Path("/workspace/notes/twitter/")
        assert config.state_file == Path("data/state.json")
        assert config.rate_limit_video == 1.0
        assert config.twitter_webhook_token is None

    def test_load_config_handles_invalid_float(self):
        """load_config should raise on invalid float values."""
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "TWITTER_RATE_LIMIT_VIDEO": "not-a-number",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load_config()

            assert "TWITTER_RATE_LIMIT_VIDEO" in str(exc_info.value)
            assert "valid number" in str(exc_info.value)

    def test_load_config_handles_invalid_int(self):
        """load_config should raise on invalid integer values."""
        env = {
            "ANTHROPIC_API_KEY": "test-key",
            "TWITTER_MAX_WORKERS": "five",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError) as exc_info:
                load_config()

            assert "TWITTER_MAX_WORKERS" in str(exc_info.value)
            assert "valid integer" in str(exc_info.value)


class TestGetConfig:
    """Test get_config singleton functionality."""

    def setup_method(self):
        """Reset config before each test."""
        reset_config()

    def teardown_method(self):
        """Reset config after each test."""
        reset_config()

    def test_get_config_returns_same_instance(self):
        """get_config should return the same instance on repeated calls."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}, clear=True):
            config1 = get_config()
            config2 = get_config()

            assert config1 is config2

    def test_get_config_caches_config(self):
        """get_config should cache the config and not reload on env change."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "first-key"}, clear=True):
            config1 = get_config()

        # Change env var
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "second-key"}, clear=True):
            config2 = get_config()

        # Should still have first value (cached)
        assert config1.anthropic_api_key == "first-key"
        assert config2.anthropic_api_key == "first-key"

    def test_reset_config_clears_cache(self):
        """reset_config should force reload on next get_config call."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "first-key"}, clear=True):
            config1 = get_config()

        reset_config()

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "second-key"}, clear=True):
            config2 = get_config()

        assert config1.anthropic_api_key == "first-key"
        assert config2.anthropic_api_key == "second-key"

    def test_get_config_passes_require_api_key(self):
        """get_config should respect require_api_key parameter."""
        with patch.dict(os.environ, {}, clear=True):
            # Should not raise when require_api_key=False
            config = get_config(require_api_key=False)
            assert config.anthropic_api_key == ""
