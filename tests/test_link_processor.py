"""Tests for LinkProcessor."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.bookmark import Bookmark, ContentType
from src.processors.link_processor import HTMLTextExtractor, LinkProcessor


@pytest.fixture
def processor():
    """Create a LinkProcessor instance with short timeout for tests."""
    return LinkProcessor(timeout=10)


@pytest.fixture
def link_bookmark():
    """Create a bookmark with an external link."""
    return Bookmark(
        id="123456",
        url="https://x.com/user/status/123",
        text="Check out this article about Python",
        author_username="testuser",
        content_type=ContentType.LINK,
        links=["https://example.com/article"],
    )


@pytest.fixture
def link_bookmark_main_url():
    """Create a bookmark where the main URL is the external link."""
    return Bookmark(
        id="234567",
        url="https://example.com/blog/post",
        text="Great read",
        author_username="testuser",
        content_type=ContentType.LINK,
    )


@pytest.fixture
def bookmark_no_external_link():
    """Create a bookmark without external links (only Twitter URLs)."""
    return Bookmark(
        id="345678",
        url="https://x.com/user/status/789",
        text="Just a tweet",
        author_username="testuser",
        content_type=ContentType.LINK,
        links=["https://twitter.com/other/status/456"],
    )


@pytest.fixture
def sample_html():
    """Sample HTML for testing extraction."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Article - Example Site</title>
        <meta property="og:title" content="Test Article">
        <script>console.log('test');</script>
        <style>body { color: black; }</style>
    </head>
    <body>
        <h1>Test Article</h1>
        <p>This is the first paragraph with some content.</p>
        <p>This is the second paragraph with more information.</p>
        <script>alert('ignored');</script>
    </body>
    </html>
    """


class TestFetchHtmlExtractsText:
    """Tests for HTML text extraction."""

    @pytest.mark.asyncio
    async def test_fetch_html_extracts_text(self, processor, link_bookmark, sample_html):
        """HTML → texto limpo."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is True
            assert result.content is not None
            # Text should be extracted
            assert "first paragraph" in result.content.lower()
            assert "second paragraph" in result.content.lower()

    @pytest.mark.asyncio
    async def test_fetch_excludes_script_tags(self, processor, link_bookmark, sample_html):
        """Script and style content is excluded from extracted text."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is True
            # Scripts should not be included
            assert "console.log" not in result.content
            assert "alert" not in result.content


class TestFetchHandlesTimeout:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_fetch_handles_timeout(self, processor, link_bookmark):
        """Timeout → erro."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is False
            assert "timeout" in result.error.lower()


class TestFetchHandles404:
    """Tests for HTTP error handling."""

    @pytest.mark.asyncio
    async def test_fetch_handles_404(self, processor, link_bookmark):
        """404 → erro."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.reason_phrase = "Not Found"
        http_error = httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_response.raise_for_status = MagicMock(side_effect=http_error)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is False
            assert "404" in result.error

    @pytest.mark.asyncio
    async def test_fetch_handles_500(self, processor, link_bookmark):
        """500 server error → erro."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.reason_phrase = "Internal Server Error"
        http_error = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_response.raise_for_status = MagicMock(side_effect=http_error)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is False
            assert "500" in result.error


class TestFetchRespectsRobotsTxt:
    """Tests for robots.txt handling (optional, skip if blocked)."""

    @pytest.mark.asyncio
    async def test_fetch_skips_blocked_urls(self, processor, link_bookmark):
        """Blocked by robots.txt is handled gracefully.

        Note: Current implementation doesn't check robots.txt.
        This test documents expected behavior if implemented later.
        For now, we test that connection errors are handled gracefully.
        """
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is False
            # Should fail gracefully, not crash


class TestURLExtraction:
    """Tests for URL extraction from bookmarks."""

    @pytest.mark.asyncio
    async def test_uses_external_link(self, processor, link_bookmark, sample_html):
        """External link from links list is used."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            await processor.process(link_bookmark)

            # Should have fetched the external link, not the tweet URL
            call_args = mock_client.get.call_args
            assert "example.com" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_falls_back_to_main_url(self, processor, link_bookmark_main_url, sample_html):
        """Falls back to main URL if no links."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            await processor.process(link_bookmark_main_url)

            call_args = mock_client.get.call_args
            assert "example.com/blog/post" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_handles_no_external_url(self, processor, bookmark_no_external_link):
        """Bookmark with only Twitter URLs results in error."""
        result = await processor.process(bookmark_no_external_link)

        assert result.success is False
        assert "no external url" in result.error.lower()


class TestTitleExtraction:
    """Tests for title extraction."""

    @pytest.mark.asyncio
    async def test_extracts_title_from_title_tag(self, processor, link_bookmark):
        """Title is extracted from <title> tag."""
        html = "<html><head><title>My Article Title</title></head><body>Content</body></html>"

        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.title == "My Article Title"

    @pytest.mark.asyncio
    async def test_strips_site_name_from_title(self, processor, link_bookmark):
        """Site name after separator is removed from title."""
        html = "<html><head><title>Article - Site Name</title></head><body>Content</body></html>"

        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.title == "Article"

    @pytest.mark.asyncio
    async def test_generates_title_from_content(self, processor, link_bookmark):
        """Title is generated from content if no title tag."""
        html = "<html><body>This is some interesting content to read</body></html>"

        mock_response = MagicMock()
        mock_response.text = html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.title is not None
            assert "interesting" in result.title.lower() or "some" in result.title.lower()


class TestHTMLTextExtractor:
    """Tests for HTML text extraction helper."""

    def test_extracts_text_from_paragraphs(self):
        """Text from paragraphs is extracted."""
        parser = HTMLTextExtractor()
        parser.feed("<p>First</p><p>Second</p>")

        assert "First" in parser.get_text()
        assert "Second" in parser.get_text()

    def test_ignores_script_content(self):
        """Script content is ignored."""
        parser = HTMLTextExtractor()
        parser.feed("<p>Visible</p><script>hidden();</script><p>Also visible</p>")

        text = parser.get_text()
        assert "Visible" in text
        assert "Also visible" in text
        assert "hidden" not in text

    def test_ignores_style_content(self):
        """Style content is ignored."""
        parser = HTMLTextExtractor()
        parser.feed("<p>Text</p><style>.class { color: red; }</style>")

        text = parser.get_text()
        assert "Text" in text
        assert "color" not in text

    def test_ignores_noscript_content(self):
        """Noscript content is ignored."""
        parser = HTMLTextExtractor()
        parser.feed("<p>Text</p><noscript>Enable JavaScript</noscript>")

        text = parser.get_text()
        assert "Text" in text
        assert "Enable" not in text


class TestDurationTracking:
    """Tests for duration tracking."""

    @pytest.mark.asyncio
    async def test_tracks_duration_on_success(self, processor, link_bookmark, sample_html):
        """Duration is tracked on success."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_tracks_duration_on_error(self, processor, bookmark_no_external_link):
        """Duration is tracked even on error."""
        result = await processor.process(bookmark_no_external_link)

        assert result.duration_ms >= 0


class TestMetadata:
    """Tests for metadata in result."""

    @pytest.mark.asyncio
    async def test_includes_source_url(self, processor, link_bookmark, sample_html):
        """Metadata includes source URL."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert "source_url" in result.metadata
            assert result.metadata["source_url"] == "https://example.com/article"

    @pytest.mark.asyncio
    async def test_includes_raw_text(self, processor, link_bookmark, sample_html):
        """Metadata includes raw extracted text."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert "raw_text" in result.metadata
            assert len(result.metadata["raw_text"]) > 0
