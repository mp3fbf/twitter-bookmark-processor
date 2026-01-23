"""Tests for TweetProcessor."""

import pytest

from src.core.bookmark import Bookmark, ContentType
from src.processors.tweet_processor import TweetProcessor


@pytest.fixture
def processor():
    """Create a TweetProcessor instance."""
    return TweetProcessor()


@pytest.fixture
def simple_bookmark():
    """Create a simple text-only bookmark."""
    return Bookmark(
        id="123456",
        url="https://twitter.com/user/status/123456",
        text="This is a simple tweet with some interesting content about programming.",
        author_username="testuser",
        author_name="Test User",
        content_type=ContentType.TWEET,
    )


@pytest.fixture
def bookmark_with_images():
    """Create a bookmark with images."""
    return Bookmark(
        id="234567",
        url="https://twitter.com/user/status/234567",
        text="Check out this cool image!",
        author_username="photouser",
        author_name="Photo User",
        content_type=ContentType.TWEET,
        media_urls=[
            "https://pbs.twimg.com/media/abc123.jpg",
            "https://pbs.twimg.com/media/def456.jpg",
        ],
    )


@pytest.fixture
def bookmark_with_hashtags():
    """Create a bookmark with hashtags."""
    return Bookmark(
        id="345678",
        url="https://twitter.com/user/status/345678",
        text="Learning #Python and #MachineLearning today! #Python is great.",
        author_username="learner",
        content_type=ContentType.TWEET,
    )


class TestProcessSimpleTweet:
    """Tests for processing simple text-only tweets."""

    @pytest.mark.asyncio
    async def test_process_simple_tweet_success(self, processor, simple_bookmark):
        """Process a simple text tweet returns success."""
        result = await processor.process(simple_bookmark)

        assert result.success is True
        assert result.error is None

    @pytest.mark.asyncio
    async def test_process_simple_tweet_has_content(self, processor, simple_bookmark):
        """Process returns non-empty content."""
        result = await processor.process(simple_bookmark)

        assert result.content is not None
        assert len(result.content) > 0

    @pytest.mark.asyncio
    async def test_process_simple_tweet_content_includes_text(
        self, processor, simple_bookmark
    ):
        """Content includes the original tweet text."""
        result = await processor.process(simple_bookmark)

        assert simple_bookmark.text in result.content

    @pytest.mark.asyncio
    async def test_process_simple_tweet_content_includes_author(
        self, processor, simple_bookmark
    ):
        """Content includes author information."""
        result = await processor.process(simple_bookmark)

        assert simple_bookmark.author_username in result.content
        assert simple_bookmark.author_name in result.content

    @pytest.mark.asyncio
    async def test_process_simple_tweet_content_includes_link(
        self, processor, simple_bookmark
    ):
        """Content includes link to original tweet."""
        result = await processor.process(simple_bookmark)

        assert simple_bookmark.url in result.content


class TestProcessTweetWithImages:
    """Tests for processing tweets with images."""

    @pytest.mark.asyncio
    async def test_process_tweet_with_images_success(
        self, processor, bookmark_with_images
    ):
        """Process a tweet with images returns success."""
        result = await processor.process(bookmark_with_images)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_process_tweet_with_images_includes_media(
        self, processor, bookmark_with_images
    ):
        """Content includes image URLs."""
        result = await processor.process(bookmark_with_images)

        for url in bookmark_with_images.media_urls:
            assert url in result.content

    @pytest.mark.asyncio
    async def test_process_tweet_with_images_markdown_format(
        self, processor, bookmark_with_images
    ):
        """Images are formatted as markdown."""
        result = await processor.process(bookmark_with_images)

        # Check for markdown image syntax
        assert "![image](" in result.content


class TestExtractTitle:
    """Tests for title extraction from tweet text."""

    @pytest.mark.asyncio
    async def test_extracts_title_from_beginning(self, processor, simple_bookmark):
        """Title comes from beginning of tweet."""
        result = await processor.process(simple_bookmark)

        assert result.title is not None
        assert result.title.startswith("This is a simple tweet")

    @pytest.mark.asyncio
    async def test_title_truncates_long_text(self, processor):
        """Title truncates very long tweets with ellipsis."""
        bookmark = Bookmark(
            id="1",
            url="https://twitter.com/u/status/1",
            text="Word1 word2 word3 word4 word5 word6 word7 word8 word9 word10 word11",
            author_username="user",
        )
        result = await processor.process(bookmark)

        assert "..." in result.title
        # Should have max 8 words + ellipsis
        words_in_title = result.title.replace("...", "").split()
        assert len(words_in_title) <= 8

    @pytest.mark.asyncio
    async def test_title_removes_urls(self, processor):
        """Title does not include URLs."""
        bookmark = Bookmark(
            id="1",
            url="https://twitter.com/u/status/1",
            text="Check this out https://example.com great stuff",
            author_username="user",
        )
        result = await processor.process(bookmark)

        assert "https://" not in result.title
        assert "example.com" not in result.title

    @pytest.mark.asyncio
    async def test_title_removes_hashtags(self, processor, bookmark_with_hashtags):
        """Title does not include hashtags."""
        result = await processor.process(bookmark_with_hashtags)

        assert "#" not in result.title

    @pytest.mark.asyncio
    async def test_empty_text_gives_default_title(self, processor):
        """Empty or whitespace-only text gives default title."""
        bookmark = Bookmark(
            id="1",
            url="https://twitter.com/u/status/1",
            text="#OnlyHashtags",
            author_username="user",
        )
        result = await processor.process(bookmark)

        assert result.title == "Untitled Tweet"


class TestExtractHashtagsAsTags:
    """Tests for extracting hashtags as tags."""

    @pytest.mark.asyncio
    async def test_extracts_hashtags_as_tags(self, processor, bookmark_with_hashtags):
        """Hashtags are extracted as tags without # prefix."""
        result = await processor.process(bookmark_with_hashtags)

        assert "Python" in result.tags
        assert "MachineLearning" in result.tags

    @pytest.mark.asyncio
    async def test_tags_are_unique(self, processor, bookmark_with_hashtags):
        """Duplicate hashtags only appear once in tags."""
        # The fixture has #Python twice
        result = await processor.process(bookmark_with_hashtags)

        python_count = sum(1 for t in result.tags if t.lower() == "python")
        assert python_count == 1

    @pytest.mark.asyncio
    async def test_no_hashtags_gives_empty_tags(self, processor, simple_bookmark):
        """Tweet without hashtags has empty tags list."""
        result = await processor.process(simple_bookmark)

        assert result.tags == []


class TestProcessDuration:
    """Tests for processing duration measurement."""

    @pytest.mark.asyncio
    async def test_returns_duration_ms(self, processor, simple_bookmark):
        """Result includes duration_ms field."""
        result = await processor.process(simple_bookmark)

        assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_duration_is_reasonable(self, processor, simple_bookmark):
        """Duration is in reasonable range (< 1 second for simple processing)."""
        result = await processor.process(simple_bookmark)

        # Simple text processing should be very fast
        assert result.duration_ms < 1000
