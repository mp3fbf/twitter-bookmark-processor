"""HTTP client for external requests.

Configures httpx client with sensible defaults for timeouts, retries, and user agent.
Used by link processor and other components that need to fetch external content.
"""

import httpx

# Default timeout configuration (in seconds)
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 30.0
DEFAULT_WRITE_TIMEOUT = 10.0
DEFAULT_POOL_TIMEOUT = 10.0

# User agent to identify our requests
USER_AGENT = "TwitterBookmarkProcessor/1.0 (+https://github.com/mp3fbf/twitter-bookmark-processor)"


def get_timeout() -> httpx.Timeout:
    """Get default timeout configuration.

    Returns:
        httpx.Timeout with configured connect/read/write/pool timeouts.
    """
    return httpx.Timeout(
        connect=DEFAULT_CONNECT_TIMEOUT,
        read=DEFAULT_READ_TIMEOUT,
        write=DEFAULT_WRITE_TIMEOUT,
        pool=DEFAULT_POOL_TIMEOUT,
    )


def get_headers() -> dict[str, str]:
    """Get default headers for requests.

    Returns:
        Dict with User-Agent and other standard headers.
    """
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }


def create_client(
    *,
    timeout: httpx.Timeout | None = None,
    follow_redirects: bool = True,
    max_redirects: int = 10,
) -> httpx.AsyncClient:
    """Create an async HTTP client with configured defaults.

    Args:
        timeout: Custom timeout configuration. Uses defaults if not provided.
        follow_redirects: Whether to follow redirects (default: True).
        max_redirects: Maximum number of redirects to follow (default: 10).

    Returns:
        Configured httpx.AsyncClient ready for use.

    Example:
        async with create_client() as client:
            response = await client.get("https://example.com")
    """
    return httpx.AsyncClient(
        timeout=timeout or get_timeout(),
        headers=get_headers(),
        follow_redirects=follow_redirects,
        max_redirects=max_redirects,
    )


# Singleton client for reuse across requests
_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    """Get or create a shared HTTP client instance.

    The client is created on first call and reused for subsequent calls.
    This enables connection pooling across multiple requests.

    Returns:
        Shared httpx.AsyncClient instance.

    Note:
        The caller should NOT close this client - it's managed globally.
        Use close_client() at application shutdown.
    """
    global _client
    if _client is None:
        _client = create_client()
    return _client


async def close_client() -> None:
    """Close the shared HTTP client.

    Should be called at application shutdown to cleanly close connections.
    """
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def fetch(url: str, *, timeout: httpx.Timeout | None = None) -> httpx.Response:
    """Fetch a URL using the shared client.

    Convenience function for simple GET requests.

    Args:
        url: URL to fetch.
        timeout: Optional custom timeout for this request.

    Returns:
        httpx.Response object.

    Raises:
        httpx.HTTPStatusError: For 4xx/5xx responses (if raise_for_status called).
        httpx.TimeoutException: On timeout.
        httpx.RequestError: For connection errors.
    """
    client = await get_client()
    return await client.get(url, timeout=timeout)
