"""Tests for custom exceptions."""

import pytest

from src.core.exceptions import (
    ConfigurationError,
    ContentDeletedError,
    ParseError,
    ProcessorError,
    RateLimitError,
    SkillError,
)


class TestExceptionHierarchy:
    """Test that all processor exceptions inherit from ProcessorError."""

    def test_rate_limit_error_inherits_processor_error(self):
        assert issubclass(RateLimitError, ProcessorError)

    def test_content_deleted_error_inherits_processor_error(self):
        assert issubclass(ContentDeletedError, ProcessorError)

    def test_skill_error_inherits_processor_error(self):
        assert issubclass(SkillError, ProcessorError)

    def test_parse_error_inherits_processor_error(self):
        assert issubclass(ParseError, ProcessorError)

    def test_configuration_error_does_not_inherit_processor_error(self):
        """ConfigurationError is separate - not a processing error."""
        assert not issubclass(ConfigurationError, ProcessorError)
        assert issubclass(ConfigurationError, Exception)


class TestExceptionMessages:
    """Test that custom messages are preserved."""

    def test_processor_error_message(self):
        error = ProcessorError("test message")
        assert str(error) == "test message"

    def test_rate_limit_error_message(self):
        error = RateLimitError("rate limit exceeded")
        assert str(error) == "rate limit exceeded"

    def test_content_deleted_error_message(self):
        error = ContentDeletedError("tweet not found")
        assert str(error) == "tweet not found"

    def test_skill_error_message(self):
        error = SkillError("youtube skill failed")
        assert str(error) == "youtube skill failed"

    def test_parse_error_message(self):
        error = ParseError("invalid JSON")
        assert str(error) == "invalid JSON"

    def test_configuration_error_message(self):
        error = ConfigurationError("missing API key")
        assert str(error) == "missing API key"


class TestRetryableFlag:
    """Test that retryable flag is correctly set for each exception type."""

    def test_rate_limit_error_is_retryable(self):
        """Rate limit errors should be retried after waiting."""
        error = RateLimitError("too many requests")
        assert error.retryable is True

    def test_content_deleted_error_is_not_retryable(self):
        """Deleted content will not magically reappear."""
        error = ContentDeletedError("tweet deleted")
        assert error.retryable is False

    def test_skill_error_is_retryable_by_default(self):
        """Skill errors may be transient."""
        error = SkillError("timeout")
        assert error.retryable is True

    def test_parse_error_is_not_retryable(self):
        """Parsing the same content will fail the same way."""
        error = ParseError("malformed data")
        assert error.retryable is False

    def test_processor_error_retryable_can_be_overridden(self):
        """Base ProcessorError allows custom retryable setting."""
        error_retryable = ProcessorError("retry me", retryable=True)
        error_not_retryable = ProcessorError("don't retry", retryable=False)

        assert error_retryable.retryable is True
        assert error_not_retryable.retryable is False

    def test_processor_error_default_not_retryable(self):
        """By default, ProcessorError is not retryable."""
        error = ProcessorError("generic error")
        assert error.retryable is False


class TestExceptionCanBeRaised:
    """Verify exceptions can be raised and caught properly."""

    def test_catch_processor_error_catches_subclasses(self):
        """Catching ProcessorError should catch all processor exceptions."""
        exceptions_to_test = [
            RateLimitError("rate limited"),
            ContentDeletedError("deleted"),
            SkillError("skill failed"),
            ParseError("parse failed"),
        ]

        for exc in exceptions_to_test:
            with pytest.raises(ProcessorError):
                raise exc

    def test_configuration_error_not_caught_by_processor_error(self):
        """ConfigurationError should not be caught by ProcessorError handler."""
        with pytest.raises(ConfigurationError):
            try:
                raise ConfigurationError("bad config")
            except ProcessorError:
                pytest.fail("ConfigurationError should not be caught by ProcessorError")
