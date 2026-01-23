"""Rate limiter for controlling request frequency.

Token bucket rate limiter with per-content-type configuration.
Prevents overwhelming external services (Twitter API, LLM, HTTP fetches).
"""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import AsyncIterator

from .bookmark import ContentType


class RateType(str, Enum):
    """Types of rate-limited operations."""

    VIDEO = "video"  # YouTube API / skill calls
    THREAD = "thread"  # Twitter skill calls
    LINK = "link"  # HTTP fetches + LLM extraction
    LLM = "llm"  # Direct LLM API calls


@dataclass
class RateConfig:
    """Configuration for a rate limit.

    Attributes:
        requests_per_second: Maximum requests allowed per second.
        max_concurrent: Maximum concurrent requests (semaphore limit).
    """

    requests_per_second: float
    max_concurrent: int = 1


# Default rate limits per type
DEFAULT_RATES: dict[RateType, RateConfig] = {
    RateType.VIDEO: RateConfig(requests_per_second=1.0, max_concurrent=1),
    RateType.THREAD: RateConfig(requests_per_second=2.0, max_concurrent=2),
    RateType.LINK: RateConfig(requests_per_second=5.0, max_concurrent=3),
    RateType.LLM: RateConfig(requests_per_second=2.0, max_concurrent=2),
}


def content_type_to_rate_type(content_type: ContentType) -> RateType:
    """Map ContentType to RateType.

    Args:
        content_type: The content type of a bookmark.

    Returns:
        Corresponding RateType for rate limiting.
    """
    mapping = {
        ContentType.VIDEO: RateType.VIDEO,
        ContentType.THREAD: RateType.THREAD,
        ContentType.LINK: RateType.LINK,
        ContentType.TWEET: RateType.LINK,  # Tweets use same rate as links
    }
    return mapping.get(content_type, RateType.LINK)


class RateLimiter:
    """Token bucket rate limiter with per-type configuration.

    Supports different rates for different content types and limits
    concurrent requests via semaphores.

    Example:
        limiter = RateLimiter()

        # Wait for rate limit slot
        await limiter.acquire(RateType.VIDEO)

        # Or use as context manager
        async with limiter.acquire_context(RateType.LINK):
            await fetch_url(url)
    """

    def __init__(
        self,
        rates: dict[RateType, RateConfig] | None = None,
    ) -> None:
        """Initialize rate limiter.

        Args:
            rates: Custom rate configurations. Uses defaults if not provided.
        """
        self._rates = rates or DEFAULT_RATES.copy()
        self._last_request: dict[RateType, float] = {}
        self._semaphores: dict[RateType, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    def _get_semaphore(self, rate_type: RateType) -> asyncio.Semaphore:
        """Get or create semaphore for rate type.

        Args:
            rate_type: The type to get semaphore for.

        Returns:
            Semaphore limiting concurrent requests.
        """
        if rate_type not in self._semaphores:
            config = self._rates.get(rate_type, DEFAULT_RATES[RateType.LINK])
            self._semaphores[rate_type] = asyncio.Semaphore(config.max_concurrent)
        return self._semaphores[rate_type]

    def _get_interval(self, rate_type: RateType) -> float:
        """Get minimum interval between requests for rate type.

        Args:
            rate_type: The type to get interval for.

        Returns:
            Minimum seconds between requests.
        """
        config = self._rates.get(rate_type, DEFAULT_RATES[RateType.LINK])
        return 1.0 / config.requests_per_second

    async def acquire(self, rate_type: RateType) -> None:
        """Acquire rate limit slot, waiting if necessary.

        Waits for both:
        1. Semaphore slot (limits concurrent requests)
        2. Minimum interval since last request

        Args:
            rate_type: Type of rate limit to acquire.
        """
        semaphore = self._get_semaphore(rate_type)
        await semaphore.acquire()

        async with self._lock:
            now = time.monotonic()
            last = self._last_request.get(rate_type, 0)
            interval = self._get_interval(rate_type)

            wait_time = interval - (now - last)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

            self._last_request[rate_type] = time.monotonic()

    def release(self, rate_type: RateType) -> None:
        """Release rate limit slot.

        Args:
            rate_type: Type of rate limit to release.
        """
        semaphore = self._get_semaphore(rate_type)
        semaphore.release()

    async def acquire_for_content(self, content_type: ContentType) -> None:
        """Acquire rate limit for a content type.

        Convenience method that maps ContentType to RateType.

        Args:
            content_type: The content type being processed.
        """
        rate_type = content_type_to_rate_type(content_type)
        await self.acquire(rate_type)

    def release_for_content(self, content_type: ContentType) -> None:
        """Release rate limit for a content type.

        Args:
            content_type: The content type being processed.
        """
        rate_type = content_type_to_rate_type(content_type)
        self.release(rate_type)

    @asynccontextmanager
    async def acquire_context(self, rate_type: RateType) -> AsyncIterator[None]:
        """Async context manager for acquiring/releasing rate limit.

        Automatically releases the slot when exiting the context,
        even if an exception occurs.

        Args:
            rate_type: Type of rate limit to acquire.

        Yields:
            None after acquiring the rate limit slot.

        Example:
            async with limiter.acquire_context(RateType.LINK):
                await fetch_url(url)
        """
        await self.acquire(rate_type)
        try:
            yield
        finally:
            self.release(rate_type)

    @asynccontextmanager
    async def acquire_context_for_content(
        self, content_type: ContentType
    ) -> AsyncIterator[None]:
        """Async context manager for content-type based rate limiting.

        Convenience method that maps ContentType to RateType.

        Args:
            content_type: The content type being processed.

        Yields:
            None after acquiring the rate limit slot.

        Example:
            async with limiter.acquire_context_for_content(ContentType.VIDEO):
                await process_video(bookmark)
        """
        rate_type = content_type_to_rate_type(content_type)
        async with self.acquire_context(rate_type):
            yield

    def get_stats(self) -> dict[str, dict[str, float]]:
        """Get statistics about rate limiter state.

        Returns:
            Dict with per-type stats (last_request timestamp, interval).
        """
        return {
            rate_type.value: {
                "last_request": self._last_request.get(rate_type, 0),
                "interval": self._get_interval(rate_type),
                "max_concurrent": self._rates.get(
                    rate_type, DEFAULT_RATES[RateType.LINK]
                ).max_concurrent,
            }
            for rate_type in RateType
        }


# Singleton instance
_rate_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the shared rate limiter instance.

    Returns:
        Shared RateLimiter instance.
    """
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def reset_rate_limiter() -> None:
    """Reset the shared rate limiter instance.

    Useful for testing or reconfiguration.
    """
    global _rate_limiter
    _rate_limiter = None
