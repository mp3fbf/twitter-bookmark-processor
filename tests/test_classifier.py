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
