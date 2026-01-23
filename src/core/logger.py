"""Structured JSON Logger for Twitter Bookmark Processor.

Provides JSON-formatted logging for observability and debugging.
Each log entry includes timestamp, level, message, and optional context fields
like bookmark_id for tracing processing of individual bookmarks.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class JSONFormatter(logging.Formatter):
    """Formats log records as JSON objects.

    Output format:
        {
            "ts": "2026-01-23T10:30:00.123456Z",
            "level": "INFO",
            "msg": "Processing bookmark",
            "bookmark_id": "123456789",
            ...extra fields...
        }
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as JSON.

        Args:
            record: The log record to format.

        Returns:
            JSON string representation of the log record.
        """
        # Build the base log entry
        log_entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }

        # Add logger name if not root
        if record.name and record.name != "root":
            log_entry["logger"] = record.name

        # Add extra fields from the record
        # These are set via logger.info("msg", extra={"key": "value"})
        for key, value in record.__dict__.items():
            # Skip standard LogRecord attributes
            if key in {
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "exc_info",
                "exc_text",
                "thread",
                "threadName",
                "taskName",
                "message",
            }:
                continue
            # Include user-provided extra fields
            log_entry[key] = value

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


class BookmarkLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that automatically includes bookmark_id in all log messages.

    Usage:
        logger = get_logger("processor")
        bookmark_logger = BookmarkLoggerAdapter(logger, bookmark_id="123")
        bookmark_logger.info("Processing")  # Includes bookmark_id automatically
    """

    def __init__(self, logger: logging.Logger, bookmark_id: str):
        """Initialize the adapter with a bookmark ID.

        Args:
            logger: The underlying logger to adapt.
            bookmark_id: The bookmark ID to include in all log messages.
        """
        super().__init__(logger, {"bookmark_id": bookmark_id})

    def process(
        self, msg: str, kwargs: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Process the logging call to add bookmark_id.

        Args:
            msg: The log message.
            kwargs: Keyword arguments passed to the logging call.

        Returns:
            Tuple of (message, modified kwargs with extra fields).
        """
        extra = kwargs.get("extra", {})
        extra["bookmark_id"] = self.extra["bookmark_id"]
        kwargs["extra"] = extra
        return msg, kwargs


def setup_logging(level: str = "INFO", stream: Any = None) -> None:
    """Configure the root logger with JSON formatting.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        stream: Output stream (defaults to sys.stderr).
    """
    if stream is None:
        stream = sys.stderr

    # Get the root logger
    root_logger = logging.getLogger()

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Create and configure the JSON handler
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, level.upper()))


def get_logger(name: str | None = None) -> logging.Logger:
    """Get a logger instance.

    Args:
        name: Logger name (typically __name__). If None, returns root logger.

    Returns:
        A configured Logger instance.
    """
    return logging.getLogger(name)


def get_bookmark_logger(name: str, bookmark_id: str) -> BookmarkLoggerAdapter:
    """Get a logger adapter that includes bookmark_id in all messages.

    This is the recommended way to log during bookmark processing,
    as it automatically includes the bookmark_id for tracing.

    Args:
        name: Logger name (typically __name__).
        bookmark_id: The bookmark ID being processed.

    Returns:
        A BookmarkLoggerAdapter that includes bookmark_id.

    Example:
        logger = get_bookmark_logger(__name__, bookmark.id)
        logger.info("Processing started")
        # Output: {"ts": "...", "level": "INFO", "msg": "Processing started", "bookmark_id": "123"}
    """
    return BookmarkLoggerAdapter(get_logger(name), bookmark_id)


# Module-level singleton state
_logging_configured: bool = False


def ensure_logging_configured(level: str = "INFO") -> None:
    """Ensure logging is configured exactly once.

    Safe to call multiple times; only the first call takes effect.

    Args:
        level: Log level string (only used on first call).
    """
    global _logging_configured
    if not _logging_configured:
        setup_logging(level)
        _logging_configured = True


def reset_logging() -> None:
    """Reset logging configuration.

    Useful for testing to ensure clean state between tests.
    """
    global _logging_configured
    _logging_configured = False

    # Clear all handlers from root logger
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
