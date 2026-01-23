"""Tests for retry module."""

import time

import pytest

from src.core.exceptions import ContentDeletedError, RateLimitError, SkillError
from src.core.retry import retry_async, with_retry


class TestRetrySucceedsFirstTry:
    """Tests for successful first attempts."""

    @pytest.mark.asyncio
    async def test_returns_result_on_success(self):
        """Returns result when function succeeds on first try."""
        call_count = 0

        async def successful_func() -> str:
            nonlocal call_count
            call_count += 1
            return "success"

        result = await retry_async(successful_func)

        assert result == "success"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_no_delay_on_first_success(self):
        """No delay when function succeeds immediately."""

        async def instant_success() -> int:
            return 42

        start = time.monotonic()
        result = await retry_async(instant_success)
        elapsed = time.monotonic() - start

        assert result == 42
        assert elapsed < 0.1  # Should be nearly instant

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        """Correctly passes arguments to function."""

        async def func_with_args(a: int, b: int, multiplier: int = 1) -> int:
            return (a + b) * multiplier

        result = await retry_async(func_with_args, 3, 4, multiplier=2)

        assert result == 14  # (3 + 4) * 2

    @pytest.mark.asyncio
    async def test_decorator_succeeds_first_try(self):
        """Decorator returns result on first success."""
        call_count = 0

        @with_retry(max_attempts=3)
        async def decorated_func() -> str:
            nonlocal call_count
            call_count += 1
            return "decorated success"

        result = await decorated_func()

        assert result == "decorated success"
        assert call_count == 1


class TestRetryWithBackoff:
    """Tests for exponential backoff behavior."""

    @pytest.mark.asyncio
    async def test_retries_on_retryable_error(self):
        """Retries when retryable error is raised."""
        call_count = 0

        async def fail_then_succeed() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RateLimitError("Rate limited")
            return "eventual success"

        result = await retry_async(fail_then_succeed, base_delay=0.01, jitter=False)

        assert result == "eventual success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self):
        """Delay doubles on each retry."""
        call_count = 0
        timestamps: list[float] = []

        async def record_timestamps() -> str:
            nonlocal call_count
            call_count += 1
            timestamps.append(time.monotonic())
            if call_count < 3:
                raise SkillError("Skill failed")
            return "done"

        await retry_async(record_timestamps, base_delay=0.1, jitter=False)

        assert len(timestamps) == 3
        # First retry after ~0.1s (base_delay * 2^0)
        delay1 = timestamps[1] - timestamps[0]
        # Second retry after ~0.2s (base_delay * 2^1)
        delay2 = timestamps[2] - timestamps[1]

        assert delay1 >= 0.08  # Allow tolerance
        assert delay2 >= 0.16  # ~0.2s with tolerance

    @pytest.mark.asyncio
    async def test_max_delay_caps_backoff(self):
        """Backoff is capped at max_delay."""
        call_count = 0
        timestamps: list[float] = []

        async def record_timestamps() -> str:
            nonlocal call_count
            call_count += 1
            timestamps.append(time.monotonic())
            if call_count < 4:
                raise RateLimitError("Rate limited")
            return "done"

        # With base_delay=0.1 and max_delay=0.15:
        # Attempt 1: fail
        # Wait 0.1s (min(0.1 * 2^0, 0.15) = 0.1)
        # Attempt 2: fail
        # Wait 0.15s (min(0.1 * 2^1, 0.15) = 0.15)
        # Attempt 3: fail
        # Wait 0.15s (min(0.1 * 2^2, 0.15) = 0.15, capped)
        # Attempt 4: success
        await retry_async(
            record_timestamps, max_attempts=5, base_delay=0.1, max_delay=0.15, jitter=False
        )

        assert len(timestamps) == 4
        delay2 = timestamps[2] - timestamps[1]
        delay3 = timestamps[3] - timestamps[2]

        # Both should be capped at ~0.15
        assert delay2 < 0.2  # Not full exponential (0.2)
        assert delay3 < 0.2  # Capped at max_delay

    @pytest.mark.asyncio
    async def test_jitter_adds_randomness(self):
        """Jitter makes delays vary."""
        delays: list[float] = []

        for _ in range(5):
            call_count = 0
            timestamps: list[float] = []

            async def record_with_jitter() -> str:
                nonlocal call_count
                call_count += 1
                timestamps.append(time.monotonic())
                if call_count < 2:
                    raise RateLimitError("Rate limited")
                return "done"

            await retry_async(record_with_jitter, base_delay=0.1, jitter=True)
            delays.append(timestamps[1] - timestamps[0])

        # With jitter, not all delays should be exactly the same
        # Jitter is delay * (0.5 + random()) so range is [0.05, 0.15] for base_delay=0.1
        assert not all(abs(d - delays[0]) < 0.001 for d in delays)

    @pytest.mark.asyncio
    async def test_decorator_with_custom_params(self):
        """Decorator respects custom parameters."""
        call_count = 0

        @with_retry(max_attempts=5, base_delay=0.01)
        async def fail_four_times() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 5:
                raise SkillError("Skill failed")
            return "success after 5"

        result = await fail_four_times()

        assert result == "success after 5"
        assert call_count == 5


class TestRetryMaxAttempts:
    """Tests for max attempts behavior."""

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self):
        """Raises last exception after max attempts exhausted."""
        call_count = 0

        async def always_fail() -> str:
            nonlocal call_count
            call_count += 1
            raise RateLimitError(f"Attempt {call_count}")

        with pytest.raises(RateLimitError) as exc_info:
            await retry_async(always_fail, max_attempts=3, base_delay=0.01, jitter=False)

        assert "Attempt 3" in str(exc_info.value)
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_respects_max_attempts_parameter(self):
        """Stops after specified max_attempts."""
        call_count = 0

        async def always_fail() -> str:
            nonlocal call_count
            call_count += 1
            raise SkillError("Fail")

        with pytest.raises(SkillError):
            await retry_async(always_fail, max_attempts=5, base_delay=0.01, jitter=False)

        assert call_count == 5

    @pytest.mark.asyncio
    async def test_single_attempt_no_retry(self):
        """With max_attempts=1, no retry occurs."""
        call_count = 0

        async def fail_once() -> str:
            nonlocal call_count
            call_count += 1
            raise RateLimitError("Single fail")

        with pytest.raises(RateLimitError):
            await retry_async(fail_once, max_attempts=1)

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_decorator_max_attempts(self):
        """Decorator respects max_attempts."""
        call_count = 0

        @with_retry(max_attempts=2, base_delay=0.01)
        async def fail_always() -> str:
            nonlocal call_count
            call_count += 1
            raise RateLimitError("Always fail")

        with pytest.raises(RateLimitError):
            await fail_always()

        assert call_count == 2


class TestRetryNonRetryableErrors:
    """Tests for non-retryable error handling."""

    @pytest.mark.asyncio
    async def test_raises_immediately_on_content_deleted(self):
        """ContentDeletedError is raised immediately without retry."""
        call_count = 0

        async def deleted_content() -> str:
            nonlocal call_count
            call_count += 1
            raise ContentDeletedError("Tweet was deleted")

        with pytest.raises(ContentDeletedError):
            await retry_async(deleted_content, max_attempts=5, base_delay=0.01)

        assert call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_non_retryable_processor_error(self):
        """ProcessorError with retryable=False is not retried."""
        call_count = 0

        from src.core.exceptions import ParseError

        async def parse_fails() -> str:
            nonlocal call_count
            call_count += 1
            raise ParseError("Cannot parse")

        with pytest.raises(ParseError):
            await retry_async(parse_fails, max_attempts=5, base_delay=0.01)

        assert call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_retryable_overridden_to_false(self):
        """Error with retryable explicitly set to False is not retried."""
        call_count = 0

        async def no_retry() -> str:
            nonlocal call_count
            call_count += 1
            # SkillError is normally retryable, but we override
            raise SkillError("Do not retry", retryable=False)

        with pytest.raises(SkillError):
            await retry_async(no_retry, max_attempts=5, base_delay=0.01)

        assert call_count == 1  # No retry due to override

    @pytest.mark.asyncio
    async def test_non_processor_error_is_retried(self):
        """Non-ProcessorError exceptions are retried."""
        call_count = 0

        async def generic_error() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("Generic error")
            return "success"

        result = await retry_async(generic_error, base_delay=0.01, jitter=False)

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_decorator_respects_non_retryable(self):
        """Decorator does not retry non-retryable errors."""
        call_count = 0

        @with_retry(max_attempts=5, base_delay=0.01)
        async def deleted() -> str:
            nonlocal call_count
            call_count += 1
            raise ContentDeletedError("Deleted")

        with pytest.raises(ContentDeletedError):
            await deleted()

        assert call_count == 1


class TestRetryFunctionPreservation:
    """Tests for function metadata preservation."""

    @pytest.mark.asyncio
    async def test_decorator_preserves_name(self):
        """Decorator preserves function name."""

        @with_retry()
        async def my_special_function() -> str:
            return "hi"

        assert my_special_function.__name__ == "my_special_function"

    @pytest.mark.asyncio
    async def test_decorator_preserves_docstring(self):
        """Decorator preserves function docstring."""

        @with_retry()
        async def documented_function() -> str:
            """This is my docstring."""
            return "hi"

        assert documented_function.__doc__ == "This is my docstring."

    @pytest.mark.asyncio
    async def test_decorator_works_with_args_and_kwargs(self):
        """Decorated function handles args and kwargs correctly."""

        @with_retry(max_attempts=3, base_delay=0.01)
        async def complex_func(a: int, b: int, *, c: int = 0, d: int = 0) -> int:
            return a + b + c + d

        result = await complex_func(1, 2, c=3, d=4)

        assert result == 10
