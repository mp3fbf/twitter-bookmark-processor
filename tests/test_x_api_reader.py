"""Tests for X API Bookmark Reader."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.bookmark import Bookmark
from src.sources.x_api_reader import XApiReader


def _make_auth_mock(token: str = "test_token") -> MagicMock:
    """Create a mock XApiAuth that returns a valid token."""
    auth = MagicMock()
    auth.get_valid_token = AsyncMock(return_value=token)
    return auth


def _make_state_manager(processed_ids: set | None = None) -> MagicMock:
    """Create a mock StateManager."""
    sm = MagicMock()
    processed = processed_ids or set()
    sm.is_processed.side_effect = lambda id_: id_ in processed
    return sm


def _make_api_response(tweets: list[dict], users: list[dict] = None,
                       media: list[dict] = None, next_token: str = None) -> dict:
    """Build a mock X API response."""
    result = {"data": tweets, "includes": {}}
    if users:
        result["includes"]["users"] = users
    if media:
        result["includes"]["media"] = media
    if next_token:
        result["meta"] = {"next_token": next_token}
    return result


SAMPLE_TWEET = {
    "id": "123456",
    "text": "Hello world! Check https://example.com",
    "author_id": "user1",
    "created_at": "2025-01-15T10:30:00Z",
    "conversation_id": "123456",
    "entities": {
        "urls": [
            {
                "expanded_url": "https://example.com/article",
                "url": "https://t.co/abc",
            }
        ]
    },
}

SAMPLE_USER = {"id": "user1", "username": "testuser", "name": "Test User"}


class TestTweetToBookmark:
    """Tests for converting X API tweet to Bookmark."""

    def test_basic_conversion(self):
        reader = XApiReader(auth=_make_auth_mock())
        users = {"user1": SAMPLE_USER}
        bookmark = reader._tweet_to_bookmark(SAMPLE_TWEET, users, {})

        assert bookmark.id == "123456"
        assert bookmark.author_username == "testuser"
        assert bookmark.author_name == "Test User"
        assert "Hello world" in bookmark.text

    def test_extracts_links_from_entities(self):
        reader = XApiReader(auth=_make_auth_mock())
        users = {"user1": SAMPLE_USER}
        bookmark = reader._tweet_to_bookmark(SAMPLE_TWEET, users, {})
        assert "https://example.com/article" in bookmark.links

    def test_builds_tweet_url(self):
        reader = XApiReader(auth=_make_auth_mock())
        users = {"user1": SAMPLE_USER}
        bookmark = reader._tweet_to_bookmark(SAMPLE_TWEET, users, {})
        assert bookmark.url == "https://twitter.com/testuser/status/123456"

    def test_handles_missing_author(self):
        reader = XApiReader(auth=_make_auth_mock())
        bookmark = reader._tweet_to_bookmark(SAMPLE_TWEET, {}, {})
        assert bookmark.author_username == ""

    def test_uses_note_tweet_for_long_text(self):
        reader = XApiReader(auth=_make_auth_mock())
        tweet = {
            **SAMPLE_TWEET,
            "note_tweet": {"text": "This is the full long tweet content..."},
        }
        users = {"user1": SAMPLE_USER}
        bookmark = reader._tweet_to_bookmark(tweet, users, {})
        assert "This is the full long tweet content" in bookmark.text

    def test_extracts_photo_media(self):
        reader = XApiReader(auth=_make_auth_mock())
        tweet = {
            **SAMPLE_TWEET,
            "attachments": {"media_keys": ["m1"]},
        }
        media = {
            "m1": {"media_key": "m1", "type": "photo", "url": "https://pbs.twimg.com/photo1.jpg"},
        }
        users = {"user1": SAMPLE_USER}
        bookmark = reader._tweet_to_bookmark(tweet, users, media)
        assert "https://pbs.twimg.com/photo1.jpg" in bookmark.media_urls

    def test_extracts_video_media(self):
        reader = XApiReader(auth=_make_auth_mock())
        tweet = {
            **SAMPLE_TWEET,
            "attachments": {"media_keys": ["m1"]},
        }
        media = {
            "m1": {
                "media_key": "m1",
                "type": "video",
                "preview_image_url": "https://pbs.twimg.com/preview.jpg",
                "variants": [
                    {"content_type": "video/mp4", "bit_rate": 256000, "url": "https://video.twimg.com/low.mp4"},
                    {"content_type": "video/mp4", "bit_rate": 2176000, "url": "https://video.twimg.com/high.mp4"},
                    {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/stream.m3u8"},
                ],
            },
        }
        users = {"user1": SAMPLE_USER}
        bookmark = reader._tweet_to_bookmark(tweet, users, media)
        assert "https://video.twimg.com/high.mp4" in bookmark.video_urls
        assert "https://pbs.twimg.com/preview.jpg" in bookmark.media_urls


class TestExtractLinks:
    """Tests for URL extraction from tweet entities."""

    def test_extracts_expanded_urls(self):
        tweet = {
            "entities": {
                "urls": [{"expanded_url": "https://example.com/article"}]
            }
        }
        links = XApiReader._extract_links(tweet)
        assert links == ["https://example.com/article"]

    def test_filters_twitter_media_urls(self):
        tweet = {
            "entities": {
                "urls": [
                    {"expanded_url": "https://twitter.com/user/status/123/photo/1"},
                    {"expanded_url": "https://example.com/real"},
                ]
            }
        }
        links = XApiReader._extract_links(tweet)
        assert len(links) == 1
        assert "example.com" in links[0]

    def test_handles_no_entities(self):
        links = XApiReader._extract_links({})
        assert links == []


class TestGetBestVideoVariant:
    """Tests for video variant selection."""

    def test_picks_highest_bitrate(self):
        variants = [
            {"content_type": "video/mp4", "bit_rate": 100, "url": "low.mp4"},
            {"content_type": "video/mp4", "bit_rate": 2000, "url": "high.mp4"},
        ]
        assert XApiReader._get_best_video_variant(variants) == "high.mp4"

    def test_ignores_non_mp4(self):
        variants = [
            {"content_type": "application/x-mpegURL", "url": "stream.m3u8"},
        ]
        assert XApiReader._get_best_video_variant(variants) is None

    def test_handles_empty_variants(self):
        assert XApiReader._get_best_video_variant([]) is None


class TestFetchNewBookmarks:
    """Tests for the main fetch_new_bookmarks method."""

    @pytest.mark.asyncio
    async def test_fetches_bookmarks(self):
        auth = _make_auth_mock()
        reader = XApiReader(auth=auth)

        # Mock _get_user_id
        reader._get_user_id = AsyncMock(return_value="uid123")

        # Mock _fetch_page
        bookmark = Bookmark(
            id="1", url="https://twitter.com/u/status/1",
            text="test", author_username="u",
        )
        reader._fetch_page = AsyncMock(return_value=([bookmark], None))

        result = await reader.fetch_new_bookmarks()
        assert len(result) == 1
        assert result[0].id == "1"

    @pytest.mark.asyncio
    async def test_skips_already_processed(self):
        auth = _make_auth_mock()
        state = _make_state_manager(processed_ids={"1"})
        reader = XApiReader(auth=auth, state_manager=state)

        reader._get_user_id = AsyncMock(return_value="uid123")

        bm1 = Bookmark(id="1", url="u", text="t", author_username="a")
        bm2 = Bookmark(id="2", url="u", text="t", author_username="a")
        reader._fetch_page = AsyncMock(return_value=([bm1, bm2], None))

        result = await reader.fetch_new_bookmarks()
        assert len(result) == 1
        assert result[0].id == "2"

    @pytest.mark.asyncio
    async def test_stops_on_empty_page(self):
        auth = _make_auth_mock()
        reader = XApiReader(auth=auth)

        reader._get_user_id = AsyncMock(return_value="uid123")
        reader._fetch_page = AsyncMock(return_value=([], None))

        result = await reader.fetch_new_bookmarks()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_no_user_id(self):
        auth = _make_auth_mock()
        reader = XApiReader(auth=auth)

        reader._get_user_id = AsyncMock(return_value=None)

        result = await reader.fetch_new_bookmarks()
        assert result == []

    @pytest.mark.asyncio
    async def test_respects_max_bookmarks(self):
        auth = _make_auth_mock()
        reader = XApiReader(auth=auth)
        reader._get_user_id = AsyncMock(return_value="uid123")

        bookmarks = [
            Bookmark(id=str(i), url="u", text="t", author_username="a")
            for i in range(10)
        ]
        reader._fetch_page = AsyncMock(return_value=(bookmarks, "next"))

        result = await reader.fetch_new_bookmarks(max_bookmarks=5)
        assert len(result) == 5


class TestFetchPage:
    """Tests for single page fetching."""

    @pytest.mark.asyncio
    async def test_handles_429_rate_limit(self):
        auth = _make_auth_mock()
        reader = XApiReader(auth=auth)

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.text = "Rate limited"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("src.sources.x_api_reader.httpx.AsyncClient", return_value=mock_client):
            bookmarks, next_token = await reader._fetch_page("uid", "token")

        assert bookmarks == []
        assert next_token is None

    @pytest.mark.asyncio
    async def test_handles_error_response(self):
        auth = _make_auth_mock()
        reader = XApiReader(auth=auth)

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Server error"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("src.sources.x_api_reader.httpx.AsyncClient", return_value=mock_client):
            bookmarks, next_token = await reader._fetch_page("uid", "token")

        assert bookmarks == []

    @pytest.mark.asyncio
    async def test_parses_api_response(self):
        auth = _make_auth_mock()
        reader = XApiReader(auth=auth)

        api_data = _make_api_response(
            tweets=[SAMPLE_TWEET],
            users=[SAMPLE_USER],
            next_token="cursor123",
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = api_data

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get.return_value = mock_response

        with patch("src.sources.x_api_reader.httpx.AsyncClient", return_value=mock_client):
            bookmarks, next_token = await reader._fetch_page("uid", "token")

        assert len(bookmarks) == 1
        assert bookmarks[0].id == "123456"
        assert next_token == "cursor123"


class TestConfigIntegration:
    """Tests for X API config settings."""

    def test_config_bookmark_source_default(self):
        import os
        from src.core.config import load_config

        env = {"ANTHROPIC_API_KEY": "test"}
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        assert config.bookmark_source == "twillot"

    def test_config_bookmark_source_x_api(self):
        import os
        from src.core.config import load_config

        env = {"ANTHROPIC_API_KEY": "test", "BOOKMARK_SOURCE": "x_api"}
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        assert config.bookmark_source == "x_api"

    def test_config_invalid_source_raises(self):
        import os
        from src.core.config import load_config
        from src.core.exceptions import ConfigurationError

        env = {"ANTHROPIC_API_KEY": "test", "BOOKMARK_SOURCE": "invalid"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="BOOKMARK_SOURCE"):
                load_config()

    def test_config_x_api_poll_interval(self):
        import os
        from src.core.config import load_config

        env = {"ANTHROPIC_API_KEY": "test", "X_API_POLL_INTERVAL": "600"}
        with patch.dict(os.environ, env, clear=True):
            config = load_config()
        assert config.x_api_poll_interval == 600

    def test_config_x_api_poll_interval_min(self):
        import os
        from src.core.config import load_config
        from src.core.exceptions import ConfigurationError

        env = {"ANTHROPIC_API_KEY": "test", "X_API_POLL_INTERVAL": "30"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="at least 60"):
                load_config()
