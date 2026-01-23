"""Retry logic with exponential backoff.

Decorator and function for retrying operations that may fail transiently.
Respects the `retryable` attribute of ProcessorError subclasses.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from .exceptions import ProcessorError

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


async def retry_async(
    func: Callable[P, Awaitable[T]],
    *args: P.args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    **kwargs: P.kwargs,
) -> T:
    """Retry an async function with exponential backoff.

    Respects ProcessorError.retryable - non-retryable errors are raised immediately.

    Args:
        func: Async function to call.
        *args: Positional arguments for func.
        max_attempts: Maximum number of attempts (default 3).
        base_delay: Initial delay between retries in seconds (default 1.0).
        max_delay: Maximum delay cap in seconds (default 60.0).
        jitter: Add random jitter to delays (default True).
        **kwargs: Keyword arguments for func.

    Returns:
        Result from successful func call.

    Raises:
        Exception: The last exception if all retries fail, or non-retryable error.
    """
    last_exception: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await func(*args, **kwargs)
        except ProcessorError as e:
            if not e.retryable:
                # Non-retryable error - raise immediately
                logger.debug(
                    "Non-retryable error on attempt %d/%d: %s",
                    attempt,
                    max_attempts,
                    e,
                )
                raise

            last_exception = e

            if attempt == max_attempts:
                # Last attempt failed
                logger.warning(
                    "All %d attempts failed for %s: %s",
                    max_attempts,
                    func.__name__,
                    e,
                )
                raise

            # Calculate backoff delay
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay = delay * (0.5 + random.random())

            logger.info(
                "Attempt %d/%d failed for %s, retrying in %.2fs: %s",
                attempt,
                max_attempts,
                func.__name__,
                delay,
                e,
            )
            await asyncio.sleep(delay)

        except Exception as e:
            # Non-ProcessorError - check if it looks retryable
            last_exception = e

            if attempt == max_attempts:
                logger.warning(
                    "All %d attempts failed for %s: %s",
                    max_attempts,
                    func.__name__,
                    e,
                )
                raise

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay = delay * (0.5 + random.random())

            logger.info(
                "Attempt %d/%d failed for %s, retrying in %.2fs: %s",
                attempt,
                max_attempts,
                func.__name__,
                delay,
                e,
            )
            await asyncio.sleep(delay)

    # Should not reach here, but satisfy type checker
    if last_exception:
        raise last_exception
    raise RuntimeError("Retry logic error: no attempts made")


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorator to add retry logic to async functions.

    Respects ProcessorError.retryable - non-retryable errors are raised immediately.

    Args:
        max_attempts: Maximum number of attempts (default 3).
        base_delay: Initial delay between retries in seconds (default 1.0).
        max_delay: Maximum delay cap in seconds (default 60.0).
        jitter: Add random jitter to delays (default True).

    Returns:
        Decorator function.

    Example:
        @with_retry(max_attempts=5, base_delay=0.5)
        async def fetch_data(url: str) -> dict:
            ...
    """

    def decorator(func: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            return await retry_async(
                func,
                *args,
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
                **kwargs,
            )

        return wrapper

    return decorator
