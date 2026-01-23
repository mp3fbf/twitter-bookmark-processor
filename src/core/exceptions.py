"""Custom exceptions for Twitter Bookmark Processor.

This module defines the exception hierarchy used throughout the processor.
All processor-related exceptions inherit from ProcessorError, which allows
for unified error handling in the pipeline.
"""


class ProcessorError(Exception):
    """Base class for processor errors.

    All recoverable errors during bookmark processing should inherit from this class.
    """

    retryable: bool = False

    def __init__(self, message: str, *, retryable: bool | None = None):
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class RateLimitError(ProcessorError):
    """API rate limit hit.

    This error indicates the external API has rate-limited our requests.
    The caller should wait before retrying.
    """

    retryable: bool = True


class ContentDeletedError(ProcessorError):
    """Tweet/content was deleted.

    The referenced content no longer exists. Retrying will not help.
    """

    retryable: bool = False


class SkillError(ProcessorError):
    """External skill failed.

    An external skill (e.g., /youtube-video, /twitter) returned an error.
    May be retryable depending on the underlying cause.
    """

    retryable: bool = True


class ParseError(ProcessorError):
    """Failed to parse content.

    The content could not be parsed into the expected format.
    Usually not retryable unless the content source is updated.
    """

    retryable: bool = False


class ConfigurationError(Exception):
    """Invalid configuration.

    This is NOT a ProcessorError - configuration issues should be fixed
    before the processor runs, not retried automatically.
    """

    pass
