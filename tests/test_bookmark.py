"""Tests for Bookmark data model."""

from datetime import datetime

from src.core.bookmark import Bookmark, ContentType, ProcessingStatus


class TestContentTypeEnum:
    """Test ContentType enum values and behavior."""

    def test_content_type_video_value(self):
        assert ContentType.VIDEO.value == "video"

    def test_content_type_thread_value(self):
        assert ContentType.THREAD.value == "thread"

    def test_content_type_link_value(self):
        assert ContentType.LINK.value == "link"

    def test_content_type_tweet_value(self):
        assert ContentType.TWEET.value == "tweet"

    def test_content_type_is_string_comparable(self):
        """ContentType values should be comparable to strings for JSON compatibility."""
        assert ContentType.VIDEO == "video"
        assert ContentType.THREAD == "thread"
        assert ContentType.VIDEO.value == "video"

    def test_content_type_from_string(self):
        """Should be able to create ContentType from string value."""
        assert ContentType("video") == ContentType.VIDEO
        assert ContentType("thread") == ContentType.THREAD


class TestProcessingStatusEnum:
    """Test ProcessingStatus enum values and behavior."""

    def test_processing_status_pending_value(self):
        assert ProcessingStatus.PENDING.value == "pending"

    def test_processing_status_processing_value(self):
        assert ProcessingStatus.PROCESSING.value == "processing"

    def test_processing_status_done_value(self):
        assert ProcessingStatus.DONE.value == "done"

    def test_processing_status_error_value(self):
        assert ProcessingStatus.ERROR.value == "error"

    def test_processing_status_is_string_comparable(self):
        """ProcessingStatus values should be comparable to strings for JSON compatibility."""
        assert ProcessingStatus.DONE == "done"
        assert ProcessingStatus.ERROR == "error"
        assert ProcessingStatus.PENDING.value == "pending"


class TestBookmarkCreation:
    """Test Bookmark dataclass creation."""

    def test_bookmark_creation_with_required_fields(self):
        """Create bookmark with only required fields."""
        bookmark = Bookmark(
            id="123456789",
            url="https://x.com/user/status/123456789",
            text="Hello world",
            author_username="testuser",
        )

        assert bookmark.id == "123456789"
        assert bookmark.url == "https://x.com/user/status/123456789"
        assert bookmark.text == "Hello world"
        assert bookmark.author_username == "testuser"

    def test_bookmark_with_all_fields(self):
        """Create bookmark with all optional fields populated."""
        now = datetime.now()
        bookmark = Bookmark(
            id="987654321",
            url="https://x.com/author/status/987654321",
            text="Full featured tweet",
            author_username="fulluser",
            author_name="Full User",
            author_id="111222333",
            created_at="2026-01-20T10:00:00.000Z",
            conversation_id="987654321",
            in_reply_to_user_id=None,
            content_type=ContentType.VIDEO,
            media_urls=["https://pbs.twimg.com/media/image.jpg"],
            video_urls=["https://video.twimg.com/ext_tw_video/123/video.mp4"],
            links=["https://youtube.com/watch?v=abc123"],
            is_thread=False,
            bookmarked_at=now,
            processed_at=now,
            status=ProcessingStatus.DONE,
            error_count=0,
            last_error=None,
            output_path="/workspace/notes/twitter/tweet-987654321.md",
        )

        assert bookmark.author_name == "Full User"
        assert bookmark.author_id == "111222333"
        assert bookmark.content_type == ContentType.VIDEO
        assert len(bookmark.media_urls) == 1
        assert len(bookmark.video_urls) == 1
        assert bookmark.status == ProcessingStatus.DONE
        assert bookmark.output_path is not None


class TestBookmarkDefaultValues:
    """Test that default values are correctly applied."""

    def test_default_content_type_is_tweet(self):
        bookmark = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        assert bookmark.content_type == ContentType.TWEET

    def test_default_status_is_pending(self):
        bookmark = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        assert bookmark.status == ProcessingStatus.PENDING

    def test_default_media_urls_is_empty_list(self):
        bookmark = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        assert bookmark.media_urls == []
        assert isinstance(bookmark.media_urls, list)

    def test_default_video_urls_is_empty_list(self):
        bookmark = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        assert bookmark.video_urls == []

    def test_default_links_is_empty_list(self):
        bookmark = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        assert bookmark.links == []

    def test_default_is_thread_is_false(self):
        bookmark = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        assert bookmark.is_thread is False

    def test_default_error_count_is_zero(self):
        bookmark = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        assert bookmark.error_count == 0

    def test_default_optional_fields_are_none(self):
        bookmark = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        assert bookmark.author_id is None
        assert bookmark.conversation_id is None
        assert bookmark.in_reply_to_user_id is None
        assert bookmark.bookmarked_at is None
        assert bookmark.processed_at is None
        assert bookmark.last_error is None
        assert bookmark.output_path is None


class TestBookmarkListFieldIsolation:
    """Test that list fields are properly isolated between instances."""

    def test_media_urls_not_shared_between_instances(self):
        """Each bookmark should have its own media_urls list."""
        b1 = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        b2 = Bookmark(
            id="2", url="https://x.com/u/status/2", text="test", author_username="u"
        )

        b1.media_urls.append("https://example.com/image.jpg")

        assert len(b1.media_urls) == 1
        assert len(b2.media_urls) == 0

    def test_video_urls_not_shared_between_instances(self):
        """Each bookmark should have its own video_urls list."""
        b1 = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        b2 = Bookmark(
            id="2", url="https://x.com/u/status/2", text="test", author_username="u"
        )

        b1.video_urls.append("https://video.twimg.com/video.mp4")

        assert len(b1.video_urls) == 1
        assert len(b2.video_urls) == 0

    def test_links_not_shared_between_instances(self):
        """Each bookmark should have its own links list."""
        b1 = Bookmark(
            id="1", url="https://x.com/u/status/1", text="test", author_username="u"
        )
        b2 = Bookmark(
            id="2", url="https://x.com/u/status/2", text="test", author_username="u"
        )

        b1.links.append("https://example.com")

        assert len(b1.links) == 1
        assert len(b2.links) == 0


class TestBookmarkEquality:
    """Test bookmark comparison behavior."""

    def test_bookmarks_with_same_values_are_equal(self):
        """Dataclass equality compares all fields."""
        b1 = Bookmark(
            id="123", url="https://x.com/u/status/123", text="Hello", author_username="u"
        )
        b2 = Bookmark(
            id="123", url="https://x.com/u/status/123", text="Hello", author_username="u"
        )
        assert b1 == b2

    def test_bookmarks_with_different_ids_are_not_equal(self):
        b1 = Bookmark(
            id="123", url="https://x.com/u/status/123", text="Hello", author_username="u"
        )
        b2 = Bookmark(
            id="456", url="https://x.com/u/status/456", text="Hello", author_username="u"
        )
        assert b1 != b2
