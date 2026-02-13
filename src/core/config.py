"""Configuration Manager for Twitter Bookmark Processor.

Centralized configuration loading from environment variables with sensible defaults.
All configuration is validated at load time to fail fast on invalid values.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from src.core.exceptions import ConfigurationError


@dataclass
class Config:
    """Application configuration loaded from environment variables.

    Required:
        anthropic_api_key: API key for Claude/Anthropic LLM.

    Optional (with defaults):
        twitter_webhook_token: Bearer token for webhook authentication.
        output_dir: Directory for generated Obsidian notes.
        state_file: Path to JSON state persistence file.
        cache_file: Path to link extraction cache file.
        rate_limit_video: Minimum seconds between video API calls.
        rate_limit_thread: Minimum seconds between thread API calls.
        rate_limit_link: Minimum seconds between link fetches.
        log_level: Logging verbosity (DEBUG, INFO, WARNING, ERROR).
        max_concurrent_workers: Maximum parallel processing workers.
        llm_provider: Which LLM provider to use (anthropic, openai, gemini).
        llm_model: Override default model for the selected provider.
        openai_api_key: API key for OpenAI (optional, only if using openai provider).
        gemini_api_key: API key for Google Gemini (optional, only if using gemini provider).
    """

    # Required
    anthropic_api_key: str

    # Optional with defaults
    twitter_webhook_token: str | None = None
    output_dir: Path = field(default_factory=lambda: Path("/workspace/notes/Sources/twitter/"))
    state_file: Path = field(default_factory=lambda: Path("data/state.json"))
    cache_file: Path = field(default_factory=lambda: Path("data/link_cache.json"))
    rate_limit_video: float = 1.0
    rate_limit_thread: float = 0.5
    rate_limit_link: float = 0.2
    log_level: str = "INFO"
    max_concurrent_workers: int = 5

    # Multi-LLM provider settings
    llm_provider: str = "anthropic"
    llm_model: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None

    # X API settings
    x_api_client_id: str | None = None
    x_api_token_file: Path = field(default_factory=lambda: Path("data/x_api_tokens.json"))
    x_api_poll_interval: int = 900  # 15 minutes
    bookmark_source: str = "twillot"  # "twillot", "x_api", or "both"

    def __post_init__(self) -> None:
        """Validate configuration after initialization."""
        # Ensure paths are Path objects
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)
        if isinstance(self.state_file, str):
            self.state_file = Path(self.state_file)
        if isinstance(self.cache_file, str):
            self.cache_file = Path(self.cache_file)

        # Validate log level
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_log_levels:
            raise ConfigurationError(
                f"Invalid LOG_LEVEL '{self.log_level}'. "
                f"Must be one of: {', '.join(sorted(valid_log_levels))}"
            )
        self.log_level = self.log_level.upper()

        # Validate rate limits
        if self.rate_limit_video < 0:
            raise ConfigurationError("TWITTER_RATE_LIMIT_VIDEO must be non-negative")
        if self.rate_limit_thread < 0:
            raise ConfigurationError("TWITTER_RATE_LIMIT_THREAD must be non-negative")
        if self.rate_limit_link < 0:
            raise ConfigurationError("TWITTER_RATE_LIMIT_LINK must be non-negative")

        # Validate workers
        if self.max_concurrent_workers < 1:
            raise ConfigurationError("TWITTER_MAX_WORKERS must be at least 1")

        # Validate LLM provider
        valid_providers = {"anthropic", "openai", "gemini"}
        if self.llm_provider.lower() not in valid_providers:
            raise ConfigurationError(
                f"Invalid LLM_PROVIDER '{self.llm_provider}'. "
                f"Must be one of: {', '.join(sorted(valid_providers))}"
            )
        self.llm_provider = self.llm_provider.lower()

        # Validate bookmark source
        valid_sources = {"twillot", "x_api", "both"}
        if self.bookmark_source.lower() not in valid_sources:
            raise ConfigurationError(
                f"Invalid BOOKMARK_SOURCE '{self.bookmark_source}'. "
                f"Must be one of: {', '.join(sorted(valid_sources))}"
            )
        self.bookmark_source = self.bookmark_source.lower()

        # Ensure x_api_token_file is a Path
        if isinstance(self.x_api_token_file, str):
            self.x_api_token_file = Path(self.x_api_token_file)

        # Validate poll interval
        if self.x_api_poll_interval < 60:
            raise ConfigurationError("X_API_POLL_INTERVAL must be at least 60 seconds")


def load_config(*, require_api_key: bool = True) -> Config:
    """Load configuration from environment variables.

    Args:
        require_api_key: If True (default), raises ConfigurationError when
            ANTHROPIC_API_KEY is missing. Set to False for testing or
            environments where LLM is not needed.

    Returns:
        Config object with all settings loaded.

    Raises:
        ConfigurationError: If required config is missing or values are invalid.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    if require_api_key and not api_key:
        raise ConfigurationError(
            "ANTHROPIC_API_KEY environment variable is required but not set"
        )

    def get_float(key: str, default: float) -> float:
        """Parse float from env var with default."""
        value = os.environ.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            raise ConfigurationError(f"{key} must be a valid number, got '{value}'")

    def get_int(key: str, default: int) -> int:
        """Parse int from env var with default."""
        value = os.environ.get(key)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            raise ConfigurationError(f"{key} must be a valid integer, got '{value}'")

    return Config(
        anthropic_api_key=api_key,
        twitter_webhook_token=os.environ.get("TWITTER_WEBHOOK_TOKEN"),
        output_dir=Path(
            os.environ.get("TWITTER_OUTPUT_DIR", "/workspace/notes/Sources/twitter/")
        ),
        state_file=Path(os.environ.get("TWITTER_STATE_FILE", "data/state.json")),
        cache_file=Path(os.environ.get("TWITTER_CACHE_FILE", "data/link_cache.json")),
        rate_limit_video=get_float("TWITTER_RATE_LIMIT_VIDEO", 1.0),
        rate_limit_thread=get_float("TWITTER_RATE_LIMIT_THREAD", 0.5),
        rate_limit_link=get_float("TWITTER_RATE_LIMIT_LINK", 0.2),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        max_concurrent_workers=get_int("TWITTER_MAX_WORKERS", 5),
        llm_provider=os.environ.get("LLM_PROVIDER", "anthropic"),
        llm_model=os.environ.get("LLM_MODEL") or None,
        openai_api_key=os.environ.get("OPENAI_API_KEY") or None,
        gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
        x_api_client_id=os.environ.get("X_API_CLIENT_ID") or None,
        x_api_token_file=Path(
            os.environ.get("X_API_TOKEN_FILE", "data/x_api_tokens.json")
        ),
        x_api_poll_interval=get_int("X_API_POLL_INTERVAL", 900),
        bookmark_source=os.environ.get("BOOKMARK_SOURCE", "twillot"),
    )


# Singleton instance for convenience
_config: Config | None = None


def get_config(*, require_api_key: bool = True) -> Config:
    """Get the global configuration instance.

    Loads configuration on first call and caches it for subsequent calls.
    Use reset_config() to force a reload.

    Args:
        require_api_key: If True (default), raises ConfigurationError when
            ANTHROPIC_API_KEY is missing.

    Returns:
        The global Config instance.
    """
    global _config
    if _config is None:
        _config = load_config(require_api_key=require_api_key)
    return _config


def reset_config() -> None:
    """Reset the global configuration instance.

    Forces the next get_config() call to reload from environment variables.
    Useful for testing.
    """
    global _config
    _config = None
