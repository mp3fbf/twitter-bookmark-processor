"""Tests for Async Content Fetcher module."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.content_fetcher import (
    PAYWALL_SITES,
    AsyncContentFetcher,
    FetchedContent,
)


class TestFetchedContent:
    """Tests for FetchedContent dataclass."""

    def test_defaults(self):
        fc = FetchedContent(url="https://example.com", expanded_url="https://example.com")
        assert fc.content_type == "unknown"
        assert fc.paywall_detected is False
        assert fc.cached is False
        assert fc.main_content is None
        assert fc.lists_extracted == []
        assert fc.code_blocks == []


class TestExtractUrls:
    """Tests for URL extraction from text."""

    def test_extracts_simple_url(self):
        urls = AsyncContentFetcher.extract_urls("Check https://example.com/article")
        assert "https://example.com/article" in urls

    def test_extracts_multiple_urls(self):
        text = "See https://foo.com and https://bar.com"
        urls = AsyncContentFetcher.extract_urls(text)
        assert len(urls) == 2

    def test_filters_twitter_media(self):
        text = "Image: https://pbs.twimg.com/media/abc.jpg See https://example.com"
        urls = AsyncContentFetcher.extract_urls(text)
        assert len(urls) == 1
        assert "example.com" in urls[0]

    def test_filters_twitter_photo_links(self):
        text = "https://twitter.com/user/status/123/photo/1 and https://example.com"
        urls = AsyncContentFetcher.extract_urls(text)
        assert len(urls) == 1
        assert "example.com" in urls[0]

    def test_no_urls(self):
        urls = AsyncContentFetcher.extract_urls("Just a regular tweet")
        assert urls == []


class TestExpandUrl:
    """Tests for URL expansion."""

    @pytest.mark.asyncio
    async def test_returns_twitter_url_unchanged(self):
        fetcher = AsyncContentFetcher()
        result = await fetcher._expand_url("https://twitter.com/user/status/123")
        assert result == "https://twitter.com/user/status/123"

    @pytest.mark.asyncio
    async def test_expands_shortened_url(self):
        fetcher = AsyncContentFetcher()

        mock_response = MagicMock()
        mock_response.url = "https://example.com/full-article"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head.return_value = mock_response

        with patch("src.core.content_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetcher._expand_url("https://t.co/abc123")

        assert result == "https://example.com/full-article"

    @pytest.mark.asyncio
    async def test_returns_original_on_error(self):
        fetcher = AsyncContentFetcher()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.head.side_effect = httpx.ConnectError("Connection failed")

        with patch("src.core.content_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetcher._expand_url("https://bit.ly/broken")

        assert result == "https://bit.ly/broken"


class TestPaywallDetection:
    """Tests for paywall site detection."""

    def test_known_paywall_sites(self):
        fetcher = AsyncContentFetcher()
        assert fetcher._is_paywall_site("https://www.nytimes.com/article") is True
        assert fetcher._is_paywall_site("https://medium.com/post") is True
        assert fetcher._is_paywall_site("https://www.ft.com/content/abc") is True

    def test_non_paywall_sites(self):
        fetcher = AsyncContentFetcher()
        assert fetcher._is_paywall_site("https://github.com/repo") is False
        assert fetcher._is_paywall_site("https://example.com") is False


class TestExtractArticleContent:
    """Tests for HTML article content extraction."""

    def test_extracts_from_article_tag(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = "<html><body><article><p>Article content here</p></article></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        content, lists, code = fetcher._extract_article_content(soup)
        assert "Article content here" in content

    def test_extracts_lists(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = """
        <html><body><article>
            <ul>
                <li>Item one</li>
                <li>Item two</li>
                <li>Item three</li>
            </ul>
        </article></body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        content, lists, code = fetcher._extract_article_content(soup)
        assert len(lists) == 1
        assert "Item one" in lists[0]

    def test_ignores_short_lists(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = "<html><body><article><ul><li>A</li><li>B</li></ul></article></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        _, lists, _ = fetcher._extract_article_content(soup)
        assert len(lists) == 0

    def test_extracts_code_blocks(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = '<html><body><article><pre><code>def hello():\n    print("world")\n    return True</code></pre></article></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        _, _, code = fetcher._extract_article_content(soup)
        assert len(code) >= 1
        assert "hello" in code[0]

    def test_removes_nav_and_footer(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = """
        <html><body><article>
            <nav>Navigation</nav>
            <p>Main content</p>
            <footer>Footer content</footer>
        </article></body></html>
        """
        soup = BeautifulSoup(html, "html.parser")
        content, _, _ = fetcher._extract_article_content(soup)
        assert "Navigation" not in content
        assert "Footer" not in content
        assert "Main content" in content

    def test_truncates_long_content(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = f"<html><body><article><p>{'x' * 20000}</p></article></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        content, _, _ = fetcher._extract_article_content(soup)
        assert len(content) <= 15050  # 15000 + "[truncated]"
        assert content.endswith("...[truncated]")


class TestExtractMetadata:
    """Tests for metadata extraction."""

    def test_extracts_og_title(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = '<html><head><meta property="og:title" content="Test Title"></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        content = FetchedContent(url="test", expanded_url="test")
        fetcher._extract_metadata(soup, content)
        assert content.title == "Test Title"

    def test_falls_back_to_title_tag(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = "<html><head><title>Fallback Title</title></head><body></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        content = FetchedContent(url="test", expanded_url="test")
        fetcher._extract_metadata(soup, content)
        assert content.title == "Fallback Title"

    def test_extracts_site_name(self):
        fetcher = AsyncContentFetcher()
        from bs4 import BeautifulSoup

        html = '<html><head><meta property="og:site_name" content="TechBlog"></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        content = FetchedContent(url="test", expanded_url="test")
        fetcher._extract_metadata(soup, content)
        assert content.site_name == "TechBlog"


class TestFetchContent:
    """Tests for the main fetch_content method."""

    @pytest.mark.asyncio
    async def test_github_routing(self):
        """GitHub URLs get routed to the special handler."""
        fetcher = AsyncContentFetcher()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "description": "A test repo",
            "stargazers_count": 100,
            "forks_count": 10,
            "language": "Python",
            "topics": ["testing"],
            "homepage": "",
        }

        mock_readme_response = MagicMock()
        mock_readme_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = [mock_response, mock_readme_response]

        with patch("src.core.content_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetcher.fetch_content("https://github.com/owner/repo")

        assert result.content_type == "github"
        assert result.extra_data.get("stars") == 100

    @pytest.mark.asyncio
    async def test_youtube_routing(self):
        """YouTube URLs get routed to the special handler."""
        fetcher = AsyncContentFetcher()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "title": "Test Video",
            "author_name": "Test Channel",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("src.core.content_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetcher.fetch_content(
                "https://youtube.com/watch?v=abc123"
            )

        assert result.content_type == "youtube"
        assert result.title == "Test Video"
        assert result.extra_data.get("video_id") == "abc123"

    @pytest.mark.asyncio
    async def test_twitter_url_skipped(self):
        """Twitter/X URLs are skipped."""
        fetcher = AsyncContentFetcher()
        result = await fetcher.fetch_content("https://twitter.com/user/status/123")
        assert result.content_type == "twitter"

    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Timeouts produce error in FetchedContent, not an exception."""
        fetcher = AsyncContentFetcher()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.side_effect = httpx.ReadTimeout("timeout")

        with patch("src.core.content_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetcher.fetch_content("https://example.com/article")

        assert result.fetch_error is not None
        assert "Timeout" in result.fetch_error


class TestYouTubeExtraction:
    """Tests for YouTube URL parsing."""

    @pytest.mark.asyncio
    async def test_extracts_video_id_from_watch_url(self):
        fetcher = AsyncContentFetcher()

        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("src.core.content_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetcher._extract_youtube_content(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            )

        assert result.extra_data["video_id"] == "dQw4w9WgXcQ"

    @pytest.mark.asyncio
    async def test_extracts_video_id_from_short_url(self):
        fetcher = AsyncContentFetcher()

        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("src.core.content_fetcher.httpx.AsyncClient", return_value=mock_client):
            result = await fetcher._extract_youtube_content(
                "https://youtu.be/dQw4w9WgXcQ"
            )

        assert result.extra_data["video_id"] == "dQw4w9WgXcQ"
