"""Tests for ThreadProcessor (X API v2 based)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.bookmark import Bookmark, ContentType
from src.core.exceptions import ExtractionError
from src.processors.thread_processor import ThreadProcessor


@pytest.fixture
def mock_auth():
    """Create a mock XApiAuth instance."""
    auth = AsyncMock()
    auth.get_valid_token = AsyncMock(return_value="test-token")
    return auth


@pytest.fixture
def processor(mock_auth):
    """Create a ThreadProcessor with mock auth."""
    return ThreadProcessor(x_api_auth=mock_auth)


@pytest.fixture
def thread_bookmark():
    """Create a bookmark with Twitter URL that's a thread."""
    return Bookmark(
        id="1002103360646823936",
        url="https://x.com/naval/status/1002103360646823936",
        text="Thread about startups and life advice",
        author_username="naval",
        content_type=ContentType.THREAD,
        conversation_id="1002103360646823936",
    )


@pytest.fixture
def webhook_bookmark():
    """Create a minimal bookmark from webhook (no text, no author)."""
    return Bookmark(
        id="1002103360646823936",
        url="https://x.com/unknown/status/1002103360646823936",
        text="",
        author_username="",
        content_type=ContentType.THREAD,
    )


@pytest.fixture
def mock_search_response():
    """Mock X API search/recent response with 3 thread tweets.

    Note: API returns tweets in reverse-chronological order.
    ThreadProcessor sorts by ID ascending to reconstruct order.
    """
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "data": [
            # Returned in reverse order (API default)
            {
                "id": "1002103360646823938",
                "text": "Money is how we transfer time and wealth. #wealth #money",
                "conversation_id": "1002103360646823936",
                "author_id": "uid1",
            },
            {
                "id": "1002103360646823937",
                "text": "Wealth is having assets that earn while you sleep.",
                "conversation_id": "1002103360646823936",
                "author_id": "uid1",
            },
            {
                "id": "1002103360646823936",
                "text": "How to Get Rich (without getting lucky):\n\nSeek wealth, not money or status.",
                "conversation_id": "1002103360646823936",
                "author_id": "uid1",
            },
        ],
        "includes": {
            "users": [{"id": "uid1", "username": "naval", "name": "Naval"}],
        },
    }
    return response


@pytest.fixture
def mock_single_tweet_response():
    """Mock X API single tweet lookup response."""
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "data": {
            "id": "1002103360646823936",
            "text": "How to Get Rich (without getting lucky):\n\nSeek wealth, not money or status.",
            "conversation_id": "1002103360646823936",
            "author_id": "uid1",
        },
        "includes": {
            "users": [{"id": "uid1", "username": "naval", "name": "Naval"}],
        },
    }
    return response


def _make_httpx_mock(search_response=None, tweet_response=None):
    """Create a mock httpx.AsyncClient that handles different API endpoints."""

    async def mock_get(url, **kwargs):
        url_str = str(url)
        if "tweets/search/recent" in url_str:
            return search_response
        elif "/tweets/" in url_str:
            return tweet_response
        # Default: 404
        resp = MagicMock()
        resp.status_code = 404
        resp.text = "Not found"
        return resp

    client = MagicMock()
    client.get = AsyncMock(side_effect=mock_get)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestThreadProcessorFetchesThread:
    """Tests for fetching threads via X API."""

    @pytest.mark.asyncio
    async def test_fetches_thread_via_search(
        self, processor, thread_bookmark, mock_search_response
    ):
        """Uses conversation_id to search for all thread tweets."""
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert result.success is True
        assert result.content is not None
        # Should have called search with conversation_id
        mock_client.get.assert_called()

    @pytest.mark.asyncio
    async def test_tweets_sorted_chronologically(
        self, processor, thread_bookmark, mock_search_response
    ):
        """Thread tweets are sorted by ID (chronological order)."""
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        # First tweet should be the root (lowest ID)
        assert "Seek wealth, not money or status" in result.content
        # Verify ordering: Tweet 1 appears before Tweet 2 before Tweet 3
        content = result.content
        idx1 = content.index("Seek wealth")
        idx2 = content.index("assets that earn")
        idx3 = content.index("how we transfer time")
        assert idx1 < idx2 < idx3

    @pytest.mark.asyncio
    async def test_fetches_single_tweet_when_missing_info(
        self, processor, webhook_bookmark, mock_single_tweet_response, mock_search_response
    ):
        """Fetches single tweet first when conversation_id or author missing."""
        mock_client = _make_httpx_mock(
            search_response=mock_search_response,
            tweet_response=mock_single_tweet_response,
        )

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(webhook_bookmark)

        assert result.success is True


class TestThreadProcessorFallback:
    """Tests for fallback when search is unavailable."""

    @pytest.mark.asyncio
    async def test_falls_back_on_search_403(
        self, processor, thread_bookmark
    ):
        """Falls back to single tweet when search returns 403."""
        # Search returns 403 (insufficient API tier)
        mock_403 = MagicMock()
        mock_403.status_code = 403
        mock_403.text = "Forbidden"

        mock_client = _make_httpx_mock(search_response=mock_403)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        # Should still succeed with the bookmark's own text as fallback
        assert result.success is True
        assert "Thread about startups" in result.content

    @pytest.mark.asyncio
    async def test_falls_back_on_empty_search_results(
        self, processor, thread_bookmark
    ):
        """Falls back when search returns no data (thread >7 days old)."""
        mock_empty = MagicMock()
        mock_empty.status_code = 200
        mock_empty.json.return_value = {"data": [], "meta": {"result_count": 0}}

        mock_client = _make_httpx_mock(search_response=mock_empty)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_falls_back_on_rate_limit(
        self, processor, thread_bookmark
    ):
        """Falls back on 429 rate limit response."""
        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.text = "Too Many Requests"

        mock_client = _make_httpx_mock(search_response=mock_429)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert result.success is True


class TestThreadProcessorErrors:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_no_auth_returns_error(self, thread_bookmark):
        """Processor without auth returns error."""
        processor = ThreadProcessor(x_api_auth=None)
        result = await processor.process(thread_bookmark)

        assert result.success is False
        assert "auth not configured" in result.error.lower()

    @pytest.mark.asyncio
    async def test_api_error_on_fetch(self, processor, webhook_bookmark):
        """API error on single tweet fetch raises SkillError."""
        mock_error = MagicMock()
        mock_error.status_code = 500
        mock_error.text = "Internal Server Error"

        mock_client = _make_httpx_mock(tweet_response=mock_error)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(webhook_bookmark)

        assert result.success is False

    @pytest.mark.asyncio
    async def test_tracks_duration_on_error(self, thread_bookmark):
        """Duration is tracked even on error."""
        processor = ThreadProcessor(x_api_auth=None)
        result = await processor.process(thread_bookmark)

        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_tracks_duration_on_success(
        self, processor, thread_bookmark, mock_search_response
    ):
        """Duration is tracked on success."""
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert result.duration_ms >= 0


class TestThreadProcessorParsesOutput:
    """Tests for parsing thread data."""

    @pytest.mark.asyncio
    async def test_extracts_title(
        self, processor, thread_bookmark, mock_search_response
    ):
        """Title is extracted from first tweet (max 8 words)."""
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert result.title is not None
        assert len(result.title.split()) <= 9  # 8 words + possible "..."

    @pytest.mark.asyncio
    async def test_extracts_hashtags_as_tags(
        self, processor, thread_bookmark, mock_search_response
    ):
        """Hashtags from tweets are extracted as tags."""
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        # Third tweet has #wealth #money
        assert "wealth" in result.tags
        assert "money" in result.tags

    @pytest.mark.asyncio
    async def test_includes_all_tweets(
        self, processor, thread_bookmark, mock_search_response
    ):
        """All tweets from thread are included in content."""
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert "Seek wealth, not money or status" in result.content
        assert "assets that earn while you sleep" in result.content
        assert "how we transfer time and wealth" in result.content

    @pytest.mark.asyncio
    async def test_metadata_includes_author_and_source(
        self, processor, thread_bookmark, mock_search_response
    ):
        """Metadata includes author, source, and tweet_count."""
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert result.metadata["author"] == "naval"
        assert result.metadata["source"] == "X API v2"
        assert result.metadata["tweet_count"] == 3


class TestThreadProcessorContentFormatting:
    """Tests for content formatting."""

    @pytest.mark.asyncio
    async def test_thread_formats_numbered_tweets(
        self, processor, thread_bookmark, mock_search_response
    ):
        """Each tweet is numbered in formatted content."""
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert "### Tweet 1" in result.content
        assert "### Tweet 2" in result.content
        assert "### Tweet 3" in result.content

    @pytest.mark.asyncio
    async def test_thread_includes_media(self, mock_auth):
        """Media URLs (images) are included in formatted content."""
        processor = ThreadProcessor(x_api_auth=mock_auth)

        bookmark = Bookmark(
            id="100",
            url="https://x.com/user/status/100",
            text="thread text",
            author_username="user",
            content_type=ContentType.THREAD,
            conversation_id="100",
        )

        # Search response with media
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "100",
                    "text": "Check out this image",
                    "conversation_id": "100",
                    "author_id": "uid1",
                    "attachments": {"media_keys": ["m1"]},
                },
                {
                    "id": "101",
                    "text": "And this link",
                    "conversation_id": "100",
                    "author_id": "uid1",
                    "entities": {
                        "urls": [
                            {"expanded_url": "https://example.com/article"}
                        ]
                    },
                },
            ],
            "includes": {
                "users": [{"id": "uid1", "username": "user"}],
                "media": [
                    {
                        "media_key": "m1",
                        "type": "photo",
                        "url": "https://pbs.twimg.com/media/image1.jpg",
                    }
                ],
            },
        }

        mock_client = _make_httpx_mock(search_response=mock_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(bookmark)

        assert "![image](https://pbs.twimg.com/media/image1.jpg)" in result.content
        assert "https://example.com/article" in result.content


class TestThreadProcessorKeyPoints:
    """Tests for key points extraction."""

    @pytest.mark.asyncio
    async def test_extracts_key_points(
        self, mock_auth, thread_bookmark, mock_search_response
    ):
        """Key points are extracted via LLM when available."""
        mock_llm = MagicMock()
        mock_llm.extract_structured.return_value = {
            "key_points": [
                "Seek wealth, not money or status",
                "Wealth is assets that earn while you sleep",
                "Money transfers time and wealth",
            ]
        }

        processor = ThreadProcessor(x_api_auth=mock_auth, llm_client=mock_llm)
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        mock_llm.extract_structured.assert_called_once()
        assert "key_points" in result.metadata
        assert len(result.metadata["key_points"]) == 3

    @pytest.mark.asyncio
    async def test_key_points_graceful_without_llm(
        self, mock_auth, thread_bookmark, mock_search_response
    ):
        """Key points are empty when LLM is not available."""
        processor = ThreadProcessor(x_api_auth=mock_auth, llm_client=None)
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with (
            patch(
                "src.processors.thread_processor.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch(
                "src.processors.thread_processor.get_llm_client",
                side_effect=Exception("No API key"),
            ),
        ):
            result = await processor.process(thread_bookmark)

        assert result.success is True
        assert result.metadata["key_points"] == []

    @pytest.mark.asyncio
    async def test_key_points_graceful_on_llm_error(
        self, mock_auth, thread_bookmark, mock_search_response
    ):
        """Key points are empty when LLM extraction fails."""
        mock_llm = MagicMock()
        mock_llm.extract_structured.side_effect = ExtractionError("API error")

        processor = ThreadProcessor(x_api_auth=mock_auth, llm_client=mock_llm)
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert result.success is True
        assert result.metadata["key_points"] == []

    @pytest.mark.asyncio
    async def test_key_points_limits_to_five(
        self, mock_auth, thread_bookmark, mock_search_response
    ):
        """Key points are limited to max 5 items."""
        mock_llm = MagicMock()
        mock_llm.extract_structured.return_value = {
            "key_points": [f"Point {i}" for i in range(7)]
        }

        processor = ThreadProcessor(x_api_auth=mock_auth, llm_client=mock_llm)
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert len(result.metadata["key_points"]) == 5

    @pytest.mark.asyncio
    async def test_key_points_handles_invalid_response(
        self, mock_auth, thread_bookmark, mock_search_response
    ):
        """Key points are empty when LLM returns invalid format."""
        mock_llm = MagicMock()
        mock_llm.extract_structured.return_value = {
            "key_points": "not a list"
        }

        processor = ThreadProcessor(x_api_auth=mock_auth, llm_client=mock_llm)
        mock_client = _make_httpx_mock(search_response=mock_search_response)

        with patch(
            "src.processors.thread_processor.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await processor.process(thread_bookmark)

        assert result.success is True
        assert result.metadata["key_points"] == []


class TestApiTweetToDict:
    """Tests for _api_tweet_to_dict static method."""

    def test_basic_conversion(self):
        """Converts API tweet to simple dict format."""
        raw = {
            "id": "123",
            "text": "Hello world",
        }
        result = ThreadProcessor._api_tweet_to_dict(raw, "testuser", {})

        assert result["id"] == "123"
        assert result["text"] == "Hello world"
        assert result["url"] == "https://twitter.com/testuser/status/123"
        assert result["media_urls"] == []
        assert result["links"] == []

    def test_uses_note_tweet_for_long_text(self):
        """Uses note_tweet text when available (long tweets)."""
        raw = {
            "id": "123",
            "text": "Short preview...",
            "note_tweet": {"text": "Full long tweet text here"},
        }
        result = ThreadProcessor._api_tweet_to_dict(raw, "user", {})

        assert result["text"] == "Full long tweet text here"

    def test_extracts_media(self):
        """Extracts photo URLs from media attachments."""
        raw = {
            "id": "123",
            "text": "pic",
            "attachments": {"media_keys": ["m1"]},
        }
        media_map = {
            "m1": {
                "media_key": "m1",
                "type": "photo",
                "url": "https://pbs.twimg.com/photo.jpg",
            }
        }
        result = ThreadProcessor._api_tweet_to_dict(raw, "user", media_map)

        assert "https://pbs.twimg.com/photo.jpg" in result["media_urls"]

    def test_extracts_external_links(self):
        """Extracts external links from entities, skipping media URLs."""
        raw = {
            "id": "123",
            "text": "link",
            "entities": {
                "urls": [
                    {"expanded_url": "https://example.com/article"},
                    {"expanded_url": "https://twitter.com/user/status/123/photo/1"},
                    {"expanded_url": "https://pbs.twimg.com/media/img.jpg"},
                ]
            },
        }
        result = ThreadProcessor._api_tweet_to_dict(raw, "user", {})

        assert result["links"] == ["https://example.com/article"]
