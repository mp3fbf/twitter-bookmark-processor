"""Tests for Structured JSON Logger."""

import io
import json
import logging

import pytest

from src.core.logger import (
    BookmarkLoggerAdapter,
    ensure_logging_configured,
    get_bookmark_logger,
    get_logger,
    reset_logging,
    setup_logging,
)


@pytest.fixture(autouse=True)
def reset_logging_state():
    """Reset logging state before and after each test."""
    reset_logging()
    yield
    reset_logging()


class TestJSONFormatter:
    """Test JSON log formatting."""

    def test_logger_json_format(self):
        """Output should be valid JSON."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_logger("test")
        logger.info("Test message")

        output = stream.getvalue().strip()
        # Should be valid JSON
        log_entry = json.loads(output)
        assert isinstance(log_entry, dict)

    def test_logger_includes_timestamp(self):
        """Log entry should include 'ts' timestamp field."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_logger("test")
        logger.info("Test message")

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert "ts" in log_entry
        # Should be ISO format with timezone
        assert "T" in log_entry["ts"]
        assert log_entry["ts"].endswith("Z") or "+" in log_entry["ts"]

    def test_logger_includes_level(self):
        """Log entry should include level field."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_logger("test")
        logger.warning("Warning message")

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert log_entry["level"] == "WARNING"

    def test_logger_includes_message(self):
        """Log entry should include msg field."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_logger("test")
        logger.info("Hello, world!")

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert log_entry["msg"] == "Hello, world!"

    def test_logger_includes_logger_name(self):
        """Log entry should include logger name."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_logger("mymodule")
        logger.info("Test")

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert log_entry["logger"] == "mymodule"

    def test_logger_custom_fields(self):
        """Log entry should include extra custom fields."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_logger("test")
        logger.info("Processing", extra={"tweet_id": "12345", "author": "@user"})

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert log_entry["tweet_id"] == "12345"
        assert log_entry["author"] == "@user"


class TestBookmarkLogger:
    """Test bookmark-specific logging with automatic bookmark_id."""

    def test_logger_includes_bookmark_id(self):
        """Bookmark logger should automatically include bookmark_id."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_bookmark_logger("processor", "987654321")
        logger.info("Processing started")

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert "bookmark_id" in log_entry
        assert log_entry["bookmark_id"] == "987654321"

    def test_bookmark_logger_preserves_message(self):
        """Bookmark logger should preserve the original message."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_bookmark_logger("processor", "123")
        logger.error("Something went wrong")

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert log_entry["msg"] == "Something went wrong"
        assert log_entry["level"] == "ERROR"

    def test_bookmark_logger_allows_extra_fields(self):
        """Bookmark logger should allow additional extra fields."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_bookmark_logger("processor", "123")
        logger.info("Classified", extra={"content_type": "VIDEO"})

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert log_entry["bookmark_id"] == "123"
        assert log_entry["content_type"] == "VIDEO"


class TestLogLevels:
    """Test log level filtering."""

    def test_respects_log_level(self):
        """Logger should filter messages below configured level."""
        stream = io.StringIO()
        setup_logging(level="WARNING", stream=stream)

        logger = get_logger("test")
        logger.debug("Debug message")
        logger.info("Info message")
        logger.warning("Warning message")

        output = stream.getvalue().strip()
        # Only WARNING should appear
        assert "Debug message" not in output
        assert "Info message" not in output
        assert "Warning message" in output

    def test_debug_level_shows_all(self):
        """DEBUG level should show all messages."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_logger("test")
        logger.debug("Debug")
        logger.info("Info")

        output = stream.getvalue()
        assert "Debug" in output
        assert "Info" in output


class TestExceptionLogging:
    """Test exception formatting in logs."""

    def test_exception_included_in_log(self):
        """Exception info should be included in log entry."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        logger = get_logger("test")
        try:
            raise ValueError("Test error")
        except ValueError:
            logger.exception("An error occurred")

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert "exception" in log_entry
        assert "ValueError: Test error" in log_entry["exception"]


class TestSetupLogging:
    """Test logging setup and configuration."""

    def test_setup_removes_duplicate_handlers(self):
        """Multiple setup calls should not create duplicate handlers."""
        stream = io.StringIO()
        setup_logging(level="INFO", stream=stream)
        setup_logging(level="INFO", stream=stream)

        logger = get_logger("test")
        logger.info("Single message")

        output = stream.getvalue()
        # Count occurrences of message - should appear only once
        assert output.count("Single message") == 1

    def test_ensure_logging_configured_only_once(self):
        """ensure_logging_configured should only configure once."""
        # First call configures
        ensure_logging_configured(level="DEBUG")
        # Second call with different level is ignored
        ensure_logging_configured(level="ERROR")

        root = logging.getLogger()
        # Should be DEBUG from first call
        assert root.level == logging.DEBUG


class TestBookmarkLoggerAdapter:
    """Test BookmarkLoggerAdapter class directly."""

    def test_adapter_wraps_logger(self):
        """Adapter should wrap an existing logger."""
        stream = io.StringIO()
        setup_logging(level="DEBUG", stream=stream)

        base_logger = get_logger("base")
        adapter = BookmarkLoggerAdapter(base_logger, "bm_456")
        adapter.info("Adapted message")

        output = stream.getvalue().strip()
        log_entry = json.loads(output)

        assert log_entry["bookmark_id"] == "bm_456"
        assert log_entry["msg"] == "Adapted message"
