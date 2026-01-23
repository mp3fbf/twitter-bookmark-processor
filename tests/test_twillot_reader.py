"""Tests for Twillot JSON export parser."""

import tempfile
from pathlib import Path

import pytest

from src.core.bookmark import Bookmark
from src.core.exceptions import ParseError
from src.sources.twillot_reader import (
    _extract_links_from_text,
    _parse_single_bookmark,
    parse_twillot_export,
)


class TestParseSingleBookmark:
    """Test parsing a single Twillot bookmark entry."""

    def test_parse_single_bookmark(self):
        """Parse a simple text-only bookmark."""
        item = {
            "tweet_id": "1000000000000000004",
            "url": "https://x.com/example_user_4/status/1000000000000000004",
            "full_text": "In the era of personal software, Tailscale is essential.",
            "screen_name": "example_user_4",
            "username": "Example User 4",
            "user_id": "123456784",
            "created_at": "2026-01-21T19:17:04.000Z",
            "conversation_id": "1000000000000000004",
            "media_items": [],
            "has_video": False,
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.id == "1000000000000000004"
        assert bookmark.url == "https://x.com/example_user_4/status/1000000000000000004"
        assert "Tailscale is essential" in bookmark.text
        assert bookmark.author_username == "example_user_4"
        assert bookmark.author_name == "Example User 4"
        assert bookmark.author_id == "123456784"
        assert bookmark.created_at == "2026-01-21T19:17:04.000Z"
        assert bookmark.conversation_id == "1000000000000000004"

    def test_parse_bookmark_with_media(self):
        """Parse a bookmark with images."""
        item = {
            "tweet_id": "1000000000000000001",
            "url": "https://x.com/example_user_1/status/1000000000000000001",
            "full_text": "Check this out!",
            "screen_name": "example_user_1",
            "media_items": [
                "https://pbs.twimg.com/media/EXAMPLE_MEDIA_ID.jpg",
                "https://pbs.twimg.com/media/EXAMPLE_MEDIA_ID2.jpg",
            ],
            "has_image": True,
            "has_video": False,
        }

        bookmark = _parse_single_bookmark(item)

        assert len(bookmark.media_urls) == 2
        assert "EXAMPLE_MEDIA_ID.jpg" in bookmark.media_urls[0]
        assert bookmark.video_urls == []

    def test_parse_bookmark_with_video(self):
        """Parse a bookmark with native video (has_video flag)."""
        item = {
            "tweet_id": "1000000000000000002",
            "url": "https://x.com/example_user_2/status/1000000000000000002",
            "full_text": "My Ralph setup has evolved a LOT",
            "screen_name": "example_user_2",
            "media_items": ["https://pbs.twimg.com/media/EXAMPLE_MEDIA_ID.jpg"],
            "has_video": True,
        }

        bookmark = _parse_single_bookmark(item)

        # Video URLs are not directly provided by Twillot,
        # but has_video flag indicates presence
        assert len(bookmark.media_urls) == 1
        # video_urls will be empty since Twillot doesn't provide direct URLs
        assert bookmark.video_urls == []

    def test_parse_bookmark_with_links(self):
        """Parse a bookmark with external URLs in text."""
        item = {
            "tweet_id": "1000000000000000003",
            "url": "https://x.com/example_user_3/status/1000000000000000003",
            "full_text": "Check out our blog: https://www.anthropic.com/engineering/test",
            "screen_name": "example_user_3",
            "media_items": [],
            "has_link": True,
        }

        bookmark = _parse_single_bookmark(item)

        assert len(bookmark.links) == 1
        assert "anthropic.com" in bookmark.links[0]

    def test_parse_empty_export(self):
        """Parse an empty export array."""
        bookmarks = parse_twillot_export([])

        assert bookmarks == []
        assert isinstance(bookmarks, list)

    def test_parse_missing_required_field_raises_error(self):
        """Missing required field should raise ParseError."""
        item = {
            "tweet_id": "123",
            # Missing url, full_text, screen_name
        }

        with pytest.raises(ParseError) as exc_info:
            parse_twillot_export([item])

        assert "Failed to parse bookmark at index 0" in str(exc_info.value)


class TestParseFromFile:
    """Test parsing from file paths."""

    def test_parse_from_fixture_file(self):
        """Parse the sample fixture file."""
        fixture_path = Path("tests/fixtures/twillot_sample.json")

        bookmarks = parse_twillot_export(fixture_path)

        # Fixture has 8 sample bookmarks
        assert len(bookmarks) == 8
        assert all(isinstance(b, Bookmark) for b in bookmarks)

    def test_parse_from_string_path(self):
        """Parse using string path instead of Path object."""
        bookmarks = parse_twillot_export("tests/fixtures/twillot_sample.json")

        assert len(bookmarks) == 8

    def test_parse_file_not_found_raises_error(self):
        """Non-existent file should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError) as exc_info:
            parse_twillot_export("/nonexistent/path/file.json")

        assert "Export file not found" in str(exc_info.value)

    def test_parse_invalid_json_raises_error(self):
        """Invalid JSON content should raise ParseError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json }")
            temp_path = f.name

        try:
            with pytest.raises(ParseError) as exc_info:
                parse_twillot_export(temp_path)

            assert "Invalid JSON" in str(exc_info.value)
        finally:
            Path(temp_path).unlink()

    def test_parse_non_array_json_raises_error(self):
        """JSON that's not an array should raise ParseError."""
        data = {"not": "an array"}

        with pytest.raises(ParseError) as exc_info:
            parse_twillot_export(data)

        assert "must be a JSON array" in str(exc_info.value)


class TestExtractLinksFromText:
    """Test URL extraction from tweet text."""

    def test_extract_single_link(self):
        """Extract a single URL from text."""
        text = "Check this out: https://example.com/page"

        links = _extract_links_from_text(text)

        assert len(links) == 1
        assert links[0] == "https://example.com/page"

    def test_extract_multiple_links(self):
        """Extract multiple URLs from text."""
        text = "Links: https://first.com and https://second.com/path"

        links = _extract_links_from_text(text)

        assert len(links) == 2

    def test_extract_filters_tco_links(self):
        """t.co links should be filtered out."""
        text = "Original: https://t.co/abc123 Real: https://example.com"

        links = _extract_links_from_text(text)

        assert len(links) == 1
        assert "example.com" in links[0]
        assert "t.co" not in links[0]

    def test_extract_no_links(self):
        """Text without links returns empty list."""
        text = "Just a regular tweet with no URLs"

        links = _extract_links_from_text(text)

        assert links == []

    def test_extract_youtube_link(self):
        """YouTube links should be extracted."""
        text = "Watch this: https://www.youtube.com/watch?v=abc123"

        links = _extract_links_from_text(text)

        assert len(links) == 1
        assert "youtube.com" in links[0]


class TestBookmarkFieldMapping:
    """Test that Twillot fields map correctly to Bookmark fields."""

    def test_tweet_id_to_id(self):
        """tweet_id maps to id."""
        item = self._minimal_item(tweet_id="999888777")

        bookmark = _parse_single_bookmark(item)

        assert bookmark.id == "999888777"

    def test_full_text_to_text(self):
        """full_text maps to text."""
        item = self._minimal_item(full_text="Hello world!")

        bookmark = _parse_single_bookmark(item)

        assert bookmark.text == "Hello world!"

    def test_screen_name_to_author_username(self):
        """screen_name maps to author_username."""
        item = self._minimal_item(screen_name="testuser")

        bookmark = _parse_single_bookmark(item)

        assert bookmark.author_username == "testuser"

    def test_username_to_author_name(self):
        """username (display name) maps to author_name."""
        item = self._minimal_item()
        item["username"] = "Test User Display"

        bookmark = _parse_single_bookmark(item)

        assert bookmark.author_name == "Test User Display"

    def test_user_id_to_author_id(self):
        """user_id maps to author_id (as string)."""
        item = self._minimal_item()
        item["user_id"] = 12345

        bookmark = _parse_single_bookmark(item)

        assert bookmark.author_id == "12345"

    def test_missing_optional_fields_use_defaults(self):
        """Missing optional fields should use Bookmark defaults."""
        item = self._minimal_item()

        bookmark = _parse_single_bookmark(item)

        assert bookmark.author_name == ""
        assert bookmark.author_id is None
        assert bookmark.created_at == ""
        assert bookmark.media_urls == []
        assert bookmark.video_urls == []
        assert bookmark.links == []

    def _minimal_item(self, **overrides):
        """Create a minimal valid Twillot item."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
        }
        item.update(overrides)
        return item


class TestThreadDetectionFields:
    """Test extraction of thread detection fields.

    These fields are used by the classifier to detect threads:
    - conversation_id: Groups tweets in the same conversation
    - in_reply_to_user_id: ID of user being replied to
    - author_id: ID of the tweet author
    """

    def test_parse_conversation_id(self):
        """Extract conversation_id if present in export."""
        item = {
            "tweet_id": "123456789",
            "url": "https://x.com/user/status/123456789",
            "full_text": "Test tweet",
            "screen_name": "testuser",
            "conversation_id": "987654321",
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.conversation_id == "987654321"

    def test_parse_conversation_id_converts_to_string(self):
        """conversation_id should be converted to string if numeric."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
            "conversation_id": 987654321,  # numeric
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.conversation_id == "987654321"
        assert isinstance(bookmark.conversation_id, str)

    def test_parse_conversation_id_missing(self):
        """conversation_id should be None if not in export."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.conversation_id is None

    def test_parse_in_reply_to_user_id(self):
        """Extract in_reply_to_user_id if present in export."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
            "in_reply_to_user_id": "555555",
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.in_reply_to_user_id == "555555"

    def test_parse_in_reply_to_user_id_converts_to_string(self):
        """in_reply_to_user_id should be converted to string if numeric."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
            "in_reply_to_user_id": 555555,  # numeric
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.in_reply_to_user_id == "555555"
        assert isinstance(bookmark.in_reply_to_user_id, str)

    def test_parse_in_reply_to_user_id_missing(self):
        """in_reply_to_user_id should be None if not in export."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
            "is_reply": True,  # Reply flag but no user_id
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.in_reply_to_user_id is None

    def test_parse_author_id(self):
        """Extract author_id (user_id) from export."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
            "user_id": "999888777",
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.author_id == "999888777"

    def test_parse_author_id_converts_to_string(self):
        """author_id should be converted to string if numeric."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
            "user_id": 999888777,  # numeric
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.author_id == "999888777"
        assert isinstance(bookmark.author_id, str)

    def test_parse_author_id_missing(self):
        """author_id should be None if user_id not in export."""
        item = {
            "tweet_id": "123",
            "url": "https://x.com/u/status/123",
            "full_text": "test",
            "screen_name": "user",
        }

        bookmark = _parse_single_bookmark(item)

        assert bookmark.author_id is None


class TestMultipleBookmarks:
    """Test parsing multiple bookmarks."""

    def test_parse_preserves_order(self):
        """Bookmarks should be returned in the same order as the input."""
        items = [
            self._item("1", "First"),
            self._item("2", "Second"),
            self._item("3", "Third"),
        ]

        bookmarks = parse_twillot_export(items)

        assert [b.id for b in bookmarks] == ["1", "2", "3"]

    def test_parse_all_types_from_fixture(self):
        """Fixture contains various bookmark types."""
        bookmarks = parse_twillot_export("tests/fixtures/twillot_sample.json")

        # Check we got diverse content
        texts = [b.text for b in bookmarks]

        # Verify different types exist
        assert any("Tailscale" in t for t in texts)  # text_only
        assert any("anthropic.com" in t for t in texts)  # with_link
        assert any("Ralph" in t for t in texts)  # with_video

    def _item(self, tweet_id, text):
        """Create a minimal item with given id and text."""
        return {
            "tweet_id": tweet_id,
            "url": f"https://x.com/u/status/{tweet_id}",
            "full_text": text,
            "screen_name": "user",
        }
