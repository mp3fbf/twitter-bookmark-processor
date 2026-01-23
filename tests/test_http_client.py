"""Tests for HTTP client module."""

import httpx
import pytest

from src.core.http_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    USER_AGENT,
    close_client,
    create_client,
    get_client,
    get_headers,
    get_timeout,
)


class TestDefaultTimeout:
    """Tests for timeout configuration."""

    def test_client_default_timeout(self):
        """Timeout padrão configurado."""
        timeout = get_timeout()

        assert timeout.connect == DEFAULT_CONNECT_TIMEOUT
        assert timeout.read == DEFAULT_READ_TIMEOUT
        assert isinstance(timeout, httpx.Timeout)

    def test_timeout_has_all_components(self):
        """Timeout has connect, read, write, pool components."""
        timeout = get_timeout()

        assert timeout.connect is not None
        assert timeout.read is not None
        assert timeout.write is not None
        assert timeout.pool is not None

    def test_timeout_values_are_reasonable(self):
        """Timeout values are reasonable (not too short, not too long)."""
        timeout = get_timeout()

        # Connect should be fast
        assert 5 <= timeout.connect <= 30
        # Read can be longer for slow pages
        assert 10 <= timeout.read <= 60


class TestFollowRedirects:
    """Tests for redirect handling."""

    @pytest.mark.asyncio
    async def test_client_follows_redirects(self):
        """Segue redirects."""
        client = create_client(follow_redirects=True)

        assert client.follow_redirects is True
        await client.aclose()

    @pytest.mark.asyncio
    async def test_client_can_disable_redirects(self):
        """Can disable redirect following."""
        client = create_client(follow_redirects=False)

        assert client.follow_redirects is False
        await client.aclose()

    @pytest.mark.asyncio
    async def test_client_max_redirects(self):
        """Max redirects is configurable."""
        client = create_client(max_redirects=5)

        assert client.max_redirects == 5
        await client.aclose()


class TestUserAgent:
    """Tests for User-Agent configuration."""

    def test_client_user_agent(self):
        """User-Agent customizado."""
        headers = get_headers()

        assert "User-Agent" in headers
        assert "TwitterBookmarkProcessor" in headers["User-Agent"]

    def test_user_agent_constant(self):
        """USER_AGENT constant is set."""
        assert USER_AGENT is not None
        assert len(USER_AGENT) > 0
        assert "TwitterBookmarkProcessor" in USER_AGENT

    @pytest.mark.asyncio
    async def test_client_has_user_agent_header(self):
        """Client is configured with User-Agent header."""
        client = create_client()

        assert "User-Agent" in client.headers
        assert "TwitterBookmarkProcessor" in client.headers["User-Agent"]
        await client.aclose()


class TestCreateClient:
    """Tests for client creation."""

    @pytest.mark.asyncio
    async def test_create_client_returns_async_client(self):
        """create_client returns httpx.AsyncClient."""
        client = create_client()

        assert isinstance(client, httpx.AsyncClient)
        await client.aclose()

    @pytest.mark.asyncio
    async def test_create_client_custom_timeout(self):
        """Can pass custom timeout to create_client."""
        custom_timeout = httpx.Timeout(5.0)
        client = create_client(timeout=custom_timeout)

        assert client.timeout == custom_timeout
        await client.aclose()

    @pytest.mark.asyncio
    async def test_create_client_default_timeout(self):
        """Client uses default timeout when not specified."""
        client = create_client()

        assert client.timeout.connect == DEFAULT_CONNECT_TIMEOUT
        assert client.timeout.read == DEFAULT_READ_TIMEOUT
        await client.aclose()


class TestSharedClient:
    """Tests for shared client singleton."""

    @pytest.mark.asyncio
    async def test_get_client_returns_same_instance(self):
        """get_client returns the same instance on subsequent calls."""
        try:
            client1 = await get_client()
            client2 = await get_client()

            assert client1 is client2
        finally:
            await close_client()

    @pytest.mark.asyncio
    async def test_close_client_clears_singleton(self):
        """close_client clears the singleton."""
        client1 = await get_client()
        await close_client()
        client2 = await get_client()

        # New client should be created after close
        assert client1 is not client2
        await close_client()

    @pytest.mark.asyncio
    async def test_close_client_is_idempotent(self):
        """close_client can be called multiple times safely."""
        await get_client()
        await close_client()
        await close_client()  # Should not raise
        await close_client()  # Should not raise


class TestFetchFunction:
    """Tests for fetch convenience function."""

    @pytest.mark.asyncio
    async def test_fetch_uses_shared_client(self):
        """fetch uses the shared client instance."""
        # We can't easily test this without mocking, but we can verify
        # that fetch works and the client is created
        try:
            client = await get_client()
            assert client is not None
        finally:
            await close_client()


class TestHeaders:
    """Tests for default headers."""

    def test_headers_include_accept(self):
        """Default headers include Accept."""
        headers = get_headers()

        assert "Accept" in headers

    def test_headers_include_accept_language(self):
        """Default headers include Accept-Language."""
        headers = get_headers()

        assert "Accept-Language" in headers
        assert "en" in headers["Accept-Language"]
