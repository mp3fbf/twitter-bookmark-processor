"""Tests for rate limiter module."""

import asyncio
import time

import pytest

from src.core.bookmark import ContentType
from src.core.rate_limiter import (
    DEFAULT_RATES,
    RateConfig,
    RateLimiter,
    RateType,
    content_type_to_rate_type,
    get_rate_limiter,
    reset_rate_limiter,
)


class TestRateType:
    """Tests for RateType enum."""

    def test_rate_type_values(self):
        """RateType has expected values."""
        assert RateType.VIDEO.value == "video"
        assert RateType.THREAD.value == "thread"
        assert RateType.LINK.value == "link"
        assert RateType.LLM.value == "llm"

    def test_rate_type_is_string_enum(self):
        """RateType values are strings."""
        for rate_type in RateType:
            assert isinstance(rate_type.value, str)


class TestRateConfig:
    """Tests for RateConfig dataclass."""

    def test_rate_config_creation(self):
        """RateConfig can be created with values."""
        config = RateConfig(requests_per_second=5.0, max_concurrent=3)

        assert config.requests_per_second == 5.0
        assert config.max_concurrent == 3

    def test_rate_config_default_concurrent(self):
        """RateConfig has default max_concurrent of 1."""
        config = RateConfig(requests_per_second=1.0)

        assert config.max_concurrent == 1


class TestDefaultRates:
    """Tests for default rate configurations."""

    def test_default_rates_exist_for_all_types(self):
        """Default rates exist for all RateTypes."""
        for rate_type in RateType:
            assert rate_type in DEFAULT_RATES

    def test_video_rate_is_slowest(self):
        """VIDEO has the slowest rate (1/s) - external API calls."""
        assert DEFAULT_RATES[RateType.VIDEO].requests_per_second == 1.0

    def test_link_rate_is_fastest(self):
        """LINK has a faster rate (5/s) - simple HTTP fetches."""
        assert DEFAULT_RATES[RateType.LINK].requests_per_second == 5.0

    def test_different_rates_per_type(self):
        """Different types have different rates."""
        video_rate = DEFAULT_RATES[RateType.VIDEO].requests_per_second
        link_rate = DEFAULT_RATES[RateType.LINK].requests_per_second

        assert video_rate < link_rate


class TestContentTypeMapping:
    """Tests for content_type_to_rate_type mapping."""

    def test_video_maps_to_video(self):
        """ContentType.VIDEO maps to RateType.VIDEO."""
        assert content_type_to_rate_type(ContentType.VIDEO) == RateType.VIDEO

    def test_thread_maps_to_thread(self):
        """ContentType.THREAD maps to RateType.THREAD."""
        assert content_type_to_rate_type(ContentType.THREAD) == RateType.THREAD

    def test_link_maps_to_link(self):
        """ContentType.LINK maps to RateType.LINK."""
        assert content_type_to_rate_type(ContentType.LINK) == RateType.LINK

    def test_tweet_maps_to_link(self):
        """ContentType.TWEET maps to RateType.LINK (same rate)."""
        assert content_type_to_rate_type(ContentType.TWEET) == RateType.LINK


class TestRateLimiterInit:
    """Tests for RateLimiter initialization."""

    def test_init_with_defaults(self):
        """RateLimiter initializes with default rates."""
        limiter = RateLimiter()

        stats = limiter.get_stats()
        assert "video" in stats
        assert "link" in stats

    def test_init_with_custom_rates(self):
        """RateLimiter can use custom rates."""
        custom_rates = {
            RateType.VIDEO: RateConfig(requests_per_second=0.5, max_concurrent=1),
        }
        limiter = RateLimiter(rates=custom_rates)

        stats = limiter.get_stats()
        assert stats["video"]["interval"] == 2.0  # 1/0.5 = 2


class TestRateLimiterInterval:
    """Tests for rate limiter interval calculation."""

    @pytest.mark.asyncio
    async def test_respects_interval(self):
        """Rate limiter enforces minimum interval between requests."""
        # Use fast rate for quick test
        custom_rates = {
            RateType.LINK: RateConfig(requests_per_second=10.0, max_concurrent=1),
        }
        limiter = RateLimiter(rates=custom_rates)

        # First request - should be immediate
        start = time.monotonic()
        await limiter.acquire(RateType.LINK)
        limiter.release(RateType.LINK)

        # Second request - should wait for interval
        await limiter.acquire(RateType.LINK)
        elapsed = time.monotonic() - start
        limiter.release(RateType.LINK)

        # Should have waited at least the interval (0.1s for 10/s)
        assert elapsed >= 0.09  # Allow small tolerance

    @pytest.mark.asyncio
    async def test_no_wait_if_interval_passed(self):
        """No wait needed if enough time has passed."""
        custom_rates = {
            RateType.LINK: RateConfig(requests_per_second=100.0, max_concurrent=1),
        }
        limiter = RateLimiter(rates=custom_rates)

        await limiter.acquire(RateType.LINK)
        limiter.release(RateType.LINK)

        # Wait longer than interval
        await asyncio.sleep(0.02)

        # This should be immediate
        start = time.monotonic()
        await limiter.acquire(RateType.LINK)
        elapsed = time.monotonic() - start
        limiter.release(RateType.LINK)

        assert elapsed < 0.01  # Should be nearly instant


class TestRateLimiterConcurrency:
    """Tests for concurrent request limiting."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent(self):
        """Semaphore limits concurrent acquisitions."""
        custom_rates = {
            RateType.LINK: RateConfig(requests_per_second=100.0, max_concurrent=2),
        }
        limiter = RateLimiter(rates=custom_rates)

        # Acquire 2 slots (max_concurrent)
        await limiter.acquire(RateType.LINK)
        await limiter.acquire(RateType.LINK)

        # Third acquisition should block
        blocked = True

        async def try_acquire():
            nonlocal blocked
            await limiter.acquire(RateType.LINK)
            blocked = False

        task = asyncio.create_task(try_acquire())
        await asyncio.sleep(0.05)

        assert blocked is True  # Still blocked

        # Release one slot
        limiter.release(RateType.LINK)
        await asyncio.sleep(0.05)

        assert blocked is False  # Now acquired

        # Cleanup
        limiter.release(RateType.LINK)
        limiter.release(RateType.LINK)
        await task

    @pytest.mark.asyncio
    async def test_different_types_have_separate_semaphores(self):
        """Different rate types have independent semaphores."""
        custom_rates = {
            RateType.VIDEO: RateConfig(requests_per_second=100.0, max_concurrent=1),
            RateType.LINK: RateConfig(requests_per_second=100.0, max_concurrent=1),
        }
        limiter = RateLimiter(rates=custom_rates)

        # Acquire VIDEO slot
        await limiter.acquire(RateType.VIDEO)

        # LINK should still be available (different semaphore)
        start = time.monotonic()
        await limiter.acquire(RateType.LINK)
        elapsed = time.monotonic() - start

        assert elapsed < 0.01  # Should be immediate

        limiter.release(RateType.VIDEO)
        limiter.release(RateType.LINK)


class TestRateLimiterContentType:
    """Tests for content-type based methods."""

    @pytest.mark.asyncio
    async def test_acquire_for_content(self):
        """acquire_for_content maps ContentType correctly."""
        limiter = RateLimiter()

        await limiter.acquire_for_content(ContentType.VIDEO)
        limiter.release_for_content(ContentType.VIDEO)

        # Should not raise

    @pytest.mark.asyncio
    async def test_release_for_content(self):
        """release_for_content maps ContentType correctly."""
        limiter = RateLimiter()

        await limiter.acquire_for_content(ContentType.LINK)
        limiter.release_for_content(ContentType.LINK)

        # Second acquire should work since we released
        await limiter.acquire_for_content(ContentType.LINK)
        limiter.release_for_content(ContentType.LINK)


class TestRateLimiterStats:
    """Tests for get_stats method."""

    def test_get_stats_returns_all_types(self):
        """get_stats returns info for all rate types."""
        limiter = RateLimiter()

        stats = limiter.get_stats()

        for rate_type in RateType:
            assert rate_type.value in stats

    def test_get_stats_includes_interval(self):
        """Stats include interval for each type."""
        limiter = RateLimiter()

        stats = limiter.get_stats()

        assert "interval" in stats["video"]
        assert stats["video"]["interval"] == 1.0  # 1/1.0

    def test_get_stats_includes_max_concurrent(self):
        """Stats include max_concurrent for each type."""
        limiter = RateLimiter()

        stats = limiter.get_stats()

        assert "max_concurrent" in stats["video"]

    @pytest.mark.asyncio
    async def test_get_stats_tracks_last_request(self):
        """Stats track last_request timestamp."""
        limiter = RateLimiter()

        # Initially no requests
        stats_before = limiter.get_stats()
        assert stats_before["video"]["last_request"] == 0

        # After acquire
        await limiter.acquire(RateType.VIDEO)
        limiter.release(RateType.VIDEO)

        stats_after = limiter.get_stats()
        assert stats_after["video"]["last_request"] > 0


class TestRateLimiterSingleton:
    """Tests for singleton pattern."""

    def test_get_rate_limiter_returns_same_instance(self):
        """get_rate_limiter returns the same instance."""
        reset_rate_limiter()

        limiter1 = get_rate_limiter()
        limiter2 = get_rate_limiter()

        assert limiter1 is limiter2

        reset_rate_limiter()

    def test_reset_rate_limiter_clears_instance(self):
        """reset_rate_limiter creates new instance on next get."""
        limiter1 = get_rate_limiter()
        reset_rate_limiter()
        limiter2 = get_rate_limiter()

        assert limiter1 is not limiter2

        reset_rate_limiter()

    def test_reset_rate_limiter_is_idempotent(self):
        """reset_rate_limiter can be called multiple times."""
        reset_rate_limiter()
        reset_rate_limiter()
        reset_rate_limiter()

        # Should not raise
