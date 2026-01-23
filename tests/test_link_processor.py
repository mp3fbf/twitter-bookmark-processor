"""Tests for LinkProcessor."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.bookmark import Bookmark, ContentType
from src.core.link_cache import LinkCache
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


class TestLLMExtraction:
    """Tests for LLM-based content extraction."""

    @pytest.fixture
    def mock_llm_client(self):
        """Create a mock LLM client."""
        mock = MagicMock()
        mock.extract_structured = MagicMock(return_value={
            "title": "Python Async Deep Dive",
            "tldr": "This article covers async programming in Python. It explains await and event loops.",
            "key_points": [
                "Async functions pause at await",
                "Event loop manages concurrency",
                "Use asyncio.gather for parallel tasks"
            ],
            "tags": ["python", "async", "programming"]
        })
        return mock

    @pytest.fixture
    def processor_with_llm(self, mock_llm_client):
        """Create a LinkProcessor with mocked LLM client."""
        return LinkProcessor(timeout=10, llm_client=mock_llm_client)

    @pytest.mark.asyncio
    async def test_extract_returns_title(self, processor_with_llm, link_bookmark, sample_html):
        """LLM-extracted title is used when available."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor_with_llm.process(link_bookmark)

            assert result.success is True
            assert result.title == "Python Async Deep Dive"

    @pytest.mark.asyncio
    async def test_extract_returns_tldr(self, processor_with_llm, link_bookmark, sample_html):
        """TL;DR is extracted and included in content."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor_with_llm.process(link_bookmark)

            assert result.success is True
            # TL;DR should be in content
            assert "TL;DR" in result.content
            assert "async programming" in result.content.lower()
            # TL;DR should be in metadata
            assert result.metadata.get("tldr")
            assert "async programming" in result.metadata["tldr"].lower()

    @pytest.mark.asyncio
    async def test_extract_returns_key_points(self, processor_with_llm, link_bookmark, sample_html):
        """Key points are extracted and included in content."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor_with_llm.process(link_bookmark)

            assert result.success is True
            # Key points should be in content
            assert "Key Points" in result.content
            assert "Async functions pause at await" in result.content
            # Key points should be in metadata
            assert result.metadata.get("key_points")
            assert len(result.metadata["key_points"]) == 3

    @pytest.mark.asyncio
    async def test_extract_returns_tags(self, processor_with_llm, link_bookmark, sample_html):
        """Tags are extracted from LLM."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor_with_llm.process(link_bookmark)

            assert result.success is True
            assert result.tags == ["python", "async", "programming"]

    @pytest.mark.asyncio
    async def test_extract_handles_malformed_response(self, link_bookmark, sample_html):
        """Graceful fallback when LLM returns non-JSON or invalid structure."""
        from src.core.exceptions import ExtractionError

        mock_llm = MagicMock()
        mock_llm.extract_structured = MagicMock(side_effect=ExtractionError("Invalid JSON"))

        processor = LinkProcessor(timeout=10, llm_client=mock_llm)

        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            # Should still succeed with fallback values
            assert result.success is True
            # Title falls back to HTML extraction
            assert result.title is not None
            # Tags should be empty (no LLM extraction)
            assert result.tags == []

    @pytest.mark.asyncio
    async def test_extract_handles_no_llm_client(self, processor, link_bookmark, sample_html):
        """Graceful fallback when no LLM client is available."""
        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # Mock get_llm_client to raise an error (no API key configured)
        with patch("src.processors.link_processor.create_client", return_value=mock_client), \
             patch("src.processors.link_processor.get_llm_client", side_effect=Exception("No API key")):
            result = await processor.process(link_bookmark)

            # Should still succeed with fallback values
            assert result.success is True
            assert result.title is not None
            assert result.tags == []


class TestLLMResponseValidation:
    """Tests for LLM response validation and sanitization."""

    def test_validates_title_max_length(self):
        """Title is truncated to max 100 characters."""
        processor = LinkProcessor()
        result = processor._validate_llm_response({
            "title": "x" * 150  # Too long
        })
        assert len(result.get("title", "")) <= 100

    def test_validates_tldr_max_length(self):
        """TL;DR is truncated to max 500 characters."""
        processor = LinkProcessor()
        result = processor._validate_llm_response({
            "tldr": "x" * 600  # Too long
        })
        assert len(result.get("tldr", "")) <= 500

    def test_validates_key_points_max_5(self):
        """Key points are limited to 5."""
        processor = LinkProcessor()
        result = processor._validate_llm_response({
            "key_points": ["point"] * 10  # Too many
        })
        assert len(result.get("key_points", [])) <= 5

    def test_validates_tags_lowercase(self):
        """Tags are converted to lowercase."""
        processor = LinkProcessor()
        result = processor._validate_llm_response({
            "tags": ["Python", "ASYNC", "Web"]
        })
        assert result.get("tags") == ["python", "async", "web"]

    def test_validates_tags_strips_hash(self):
        """Tags have # prefix stripped."""
        processor = LinkProcessor()
        result = processor._validate_llm_response({
            "tags": ["#python", "#async", "web"]
        })
        assert result.get("tags") == ["python", "async", "web"]

    def test_handles_invalid_types(self):
        """Invalid types are handled gracefully."""
        processor = LinkProcessor()
        result = processor._validate_llm_response({
            "title": 123,  # Not a string
            "tldr": ["not", "a", "string"],  # Not a string
            "key_points": "not a list",  # Not a list
            "tags": {"invalid": "dict"}  # Not a list
        })
        # Invalid types should not be included
        assert "title" not in result
        assert "tldr" not in result
        assert "key_points" not in result
        assert "tags" not in result


class TestCacheIntegration:
    """Tests for cache integration in LinkProcessor."""

    @pytest.fixture
    def temp_cache_file(self, tmp_path):
        """Create a temporary cache file path."""
        return tmp_path / "link_cache.json"

    @pytest.fixture
    def link_cache(self, temp_cache_file):
        """Create a LinkCache instance."""
        return LinkCache(temp_cache_file)

    @pytest.fixture
    def mock_llm_client(self):
        """Create a mock LLM client."""
        mock = MagicMock()
        mock.extract_structured = MagicMock(return_value={
            "title": "Cached Article Title",
            "tldr": "This is a cached summary.",
            "key_points": ["Point 1", "Point 2"],
            "tags": ["cached", "test"]
        })
        return mock

    @pytest.mark.asyncio
    async def test_processor_checks_cache_first(self, link_bookmark, sample_html, link_cache):
        """Cache hit → LLM not called."""
        # Pre-populate cache
        cached_data = {
            "title": "Pre-cached Title",
            "tldr": "Pre-cached summary.",
            "key_points": ["Pre-cached point"],
            "tags": ["precached"]
        }
        link_cache.set("https://example.com/article", cached_data)

        # Create processor with cache but also with LLM client to verify it's NOT called
        mock_llm = MagicMock()
        mock_llm.extract_structured = MagicMock(return_value={
            "title": "LLM Title",
            "tldr": "LLM summary.",
            "key_points": ["LLM point"],
            "tags": ["llm"]
        })

        processor = LinkProcessor(timeout=10, llm_client=mock_llm, cache=link_cache)

        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is True
            # Should use cached title, not LLM title
            assert result.title == "Pre-cached Title"
            assert result.tags == ["precached"]
            # LLM should NOT have been called
            mock_llm.extract_structured.assert_not_called()

    @pytest.mark.asyncio
    async def test_processor_caches_result(self, link_bookmark, sample_html, link_cache, mock_llm_client):
        """Successful LLM extraction result is saved to cache."""
        processor = LinkProcessor(timeout=10, llm_client=mock_llm_client, cache=link_cache)

        # Verify cache is empty initially
        assert not link_cache.has("https://example.com/article")

        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is True
            # Result should now be cached
            assert link_cache.has("https://example.com/article")

            # Verify cached data matches what LLM returned
            cached = link_cache.get("https://example.com/article")
            assert cached["title"] == "Cached Article Title"
            assert cached["tldr"] == "This is a cached summary."

    @pytest.mark.asyncio
    async def test_processor_llm_on_cache_miss(self, link_bookmark, sample_html, link_cache, mock_llm_client):
        """Cache miss → LLM is called."""
        processor = LinkProcessor(timeout=10, llm_client=mock_llm_client, cache=link_cache)

        # Cache is empty (miss)
        assert not link_cache.has("https://example.com/article")

        mock_response = MagicMock()
        mock_response.text = sample_html
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("src.processors.link_processor.create_client", return_value=mock_client):
            result = await processor.process(link_bookmark)

            assert result.success is True
            # LLM should have been called due to cache miss
            mock_llm_client.extract_structured.assert_called_once()
            # Result should use LLM data
            assert result.title == "Cached Article Title"
            assert result.tags == ["cached", "test"]
