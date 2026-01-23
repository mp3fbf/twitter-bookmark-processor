"""Tests for content classifier."""

import logging

from src.core.bookmark import Bookmark, ContentType
from src.core.classifier import classify


def _make_bookmark(**kwargs) -> Bookmark:
    """Helper to create a bookmark with defaults."""
    defaults = {
        "id": "123456789",
        "url": "https://x.com/user/status/123456789",
        "text": "Test tweet",
        "author_username": "testuser",
    }
    defaults.update(kwargs)
    return Bookmark(**defaults)


class TestClassifyVideoNative:
    """Test VIDEO classification via native video_urls."""

    def test_classify_video_native(self):
        """video_urls populated → VIDEO."""
        bookmark = _make_bookmark(
            video_urls=["https://video.twimg.com/ext_tw_video/123/video.mp4"]
        )

        result = classify(bookmark)

        assert result == ContentType.VIDEO

    def test_classify_video_native_multiple(self):
        """Multiple video_urls still classifies as VIDEO."""
        bookmark = _make_bookmark(
            video_urls=[
                "https://video.twimg.com/ext_tw_video/123/video.mp4",
                "https://video.twimg.com/ext_tw_video/456/video.mp4",
            ]
        )

        result = classify(bookmark)

        assert result == ContentType.VIDEO


class TestClassifyVideoYouTube:
    """Test VIDEO classification via YouTube links."""

    def test_classify_video_youtube_link(self):
        """youtube.com in links → VIDEO."""
        bookmark = _make_bookmark(
            text="Check this out https://youtube.com/watch?v=dQw4w9WgXcQ",
            links=["https://youtube.com/watch?v=dQw4w9WgXcQ"],
        )

        result = classify(bookmark)

        assert result == ContentType.VIDEO

    def test_classify_video_youtube_www(self):
        """www.youtube.com in links → VIDEO."""
        bookmark = _make_bookmark(
            text="Check this out https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            links=["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
        )

        result = classify(bookmark)

        assert result == ContentType.VIDEO

    def test_classify_video_youtu_be(self):
        """youtu.be in links → VIDEO."""
        bookmark = _make_bookmark(
            text="Short link https://youtu.be/dQw4w9WgXcQ",
            links=["https://youtu.be/dQw4w9WgXcQ"],
        )

        result = classify(bookmark)

        assert result == ContentType.VIDEO


class TestClassifyVideoUnsupported:
    """Test VIDEO classification for unsupported platforms."""

    def test_classify_video_unsupported_logs_warning(self, caplog):
        """vimeo.com → VIDEO but logs 'unsupported platform'."""
        bookmark = _make_bookmark(
            id="999",
            text="Check this Vimeo video https://vimeo.com/123456789",
            links=["https://vimeo.com/123456789"],
        )

        with caplog.at_level(logging.WARNING):
            result = classify(bookmark)

        assert result == ContentType.VIDEO
        assert "unsupported video platform" in caplog.text.lower()
        assert "vimeo.com/123456789" in caplog.text

    def test_classify_video_dailymotion_logs_warning(self, caplog):
        """dailymotion.com → VIDEO with warning."""
        bookmark = _make_bookmark(
            text="Watch on Dailymotion https://dailymotion.com/video/xyz",
            links=["https://dailymotion.com/video/xyz"],
        )

        with caplog.at_level(logging.WARNING):
            result = classify(bookmark)

        assert result == ContentType.VIDEO
        assert "unsupported" in caplog.text.lower()

    def test_classify_video_twitch_logs_warning(self, caplog):
        """twitch.tv → VIDEO with warning."""
        bookmark = _make_bookmark(
            text="Live on Twitch https://twitch.tv/streamer/clip/abc",
            links=["https://twitch.tv/streamer/clip/abc"],
        )

        with caplog.at_level(logging.WARNING):
            result = classify(bookmark)

        assert result == ContentType.VIDEO
        assert "unsupported" in caplog.text.lower()


class TestClassifyTweetDefault:
    """Test TWEET as default classification."""

    def test_classify_tweet_default(self):
        """No video/thread/link → TWEET."""
        bookmark = _make_bookmark(
            text="Just a simple tweet with no links",
            video_urls=[],
            links=[],
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET

    def test_classify_tweet_with_images_only(self):
        """Images without links → TWEET."""
        bookmark = _make_bookmark(
            text="Check out these photos",
            media_urls=["https://pbs.twimg.com/media/image1.jpg"],
            video_urls=[],
            links=[],
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET

    def test_classify_tweet_empty_lists(self):
        """Completely empty bookmark → TWEET."""
        bookmark = _make_bookmark()

        result = classify(bookmark)

        assert result == ContentType.TWEET


class TestClassifyThreadByConversation:
    """Test THREAD classification via conversation_id."""

    def test_classify_thread_by_conversation_id(self):
        """conversation_id != id → THREAD."""
        bookmark = _make_bookmark(
            id="123456789",
            conversation_id="987654321",  # Different from id
        )

        result = classify(bookmark)

        assert result == ContentType.THREAD

    def test_classify_not_thread_same_conversation_id(self):
        """conversation_id == id → NOT thread (just a single tweet)."""
        bookmark = _make_bookmark(
            id="123456789",
            conversation_id="123456789",  # Same as id
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET

    def test_classify_not_thread_null_conversation_id(self):
        """conversation_id is None → NOT thread."""
        bookmark = _make_bookmark(
            id="123456789",
            conversation_id=None,
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET


class TestClassifyThreadByReplyChain:
    """Test THREAD classification via reply chain (author replying to self)."""

    def test_classify_thread_by_reply_to_self(self):
        """in_reply_to_user_id == author_id → THREAD (self-reply)."""
        bookmark = _make_bookmark(
            author_id="user123",
            in_reply_to_user_id="user123",  # Same as author_id
        )

        result = classify(bookmark)

        assert result == ContentType.THREAD

    def test_classify_not_thread_reply_to_other(self):
        """in_reply_to_user_id != author_id → NOT thread (reply to someone else)."""
        bookmark = _make_bookmark(
            author_id="user123",
            in_reply_to_user_id="other456",  # Different from author_id
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET

    def test_classify_not_thread_null_reply_to(self):
        """in_reply_to_user_id is None → NOT thread."""
        bookmark = _make_bookmark(
            author_id="user123",
            in_reply_to_user_id=None,
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET

    def test_classify_not_thread_null_author_id(self):
        """author_id is None → NOT thread (cannot determine self-reply)."""
        bookmark = _make_bookmark(
            author_id=None,
            in_reply_to_user_id="user123",
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET


class TestClassifyThreadByHeuristics:
    """Test THREAD classification via text heuristics (needs 2+ signals)."""

    def test_classify_thread_number_and_emoji(self):
        """Number pattern + 🧵 emoji → THREAD (2 signals)."""
        bookmark = _make_bookmark(
            text="1/ 🧵 This is a thread about AI",
        )

        result = classify(bookmark)

        assert result == ContentType.THREAD

    def test_classify_thread_number_and_word(self):
        """Number pattern + (thread) word → THREAD (2 signals)."""
        bookmark = _make_bookmark(
            text="1. Important topic (thread)",
        )

        result = classify(bookmark)

        assert result == ContentType.THREAD

    def test_classify_thread_emoji_and_word(self):
        """🧵 emoji + (thread) word → THREAD (2 signals)."""
        bookmark = _make_bookmark(
            text="🧵 Here's my analysis (thread)",
        )

        result = classify(bookmark)

        assert result == ContentType.THREAD

    def test_classify_thread_all_three_signals(self):
        """All 3 heuristic signals → THREAD."""
        bookmark = _make_bookmark(
            text="1/ 🧵 Complete breakdown (thread)",
        )

        result = classify(bookmark)

        assert result == ContentType.THREAD

    def test_classify_not_thread_single_number(self):
        """Only number pattern → NOT thread (1 signal insufficient)."""
        bookmark = _make_bookmark(
            text="1/ Here's my take on this topic",
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET

    def test_classify_not_thread_single_emoji(self):
        """Only 🧵 emoji → NOT thread (1 signal insufficient)."""
        bookmark = _make_bookmark(
            text="🧵 Interesting read about startups",
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET

    def test_classify_not_thread_single_word(self):
        """Only (thread) word → NOT thread (1 signal insufficient)."""
        bookmark = _make_bookmark(
            text="A fascinating discussion (thread)",
        )

        result = classify(bookmark)

        assert result == ContentType.TWEET

    def test_classify_thread_word_case_insensitive(self):
        """(THREAD) in uppercase still counts as signal."""
        bookmark = _make_bookmark(
            text="1/ Important topic (THREAD)",
        )

        result = classify(bookmark)

        assert result == ContentType.THREAD


class TestClassifyPriority:
    """Test classification priority (VIDEO > THREAD > TWEET)."""

    def test_video_takes_priority_over_thread(self):
        """VIDEO wins even if thread signals present."""
        bookmark = _make_bookmark(
            text="1/ 🧵 Check out this video",
            conversation_id="987654321",  # Thread signal
            video_urls=["https://video.twimg.com/ext_tw_video/123/video.mp4"],
        )

        result = classify(bookmark)

        assert result == ContentType.VIDEO

    def test_thread_takes_priority_over_tweet(self):
        """THREAD wins over default TWEET."""
        bookmark = _make_bookmark(
            text="Just a regular tweet",
            conversation_id="987654321",  # Thread signal
        )

        result = classify(bookmark)

        assert result == ContentType.THREAD
