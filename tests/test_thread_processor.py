"""Tests for ThreadProcessor."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.core.bookmark import Bookmark, ContentType
from src.core.exceptions import ExtractionError
from src.processors.thread_processor import ThreadProcessor


@pytest.fixture
def processor():
    """Create a ThreadProcessor instance with short timeout for tests."""
    return ThreadProcessor(timeout=10)


@pytest.fixture
def thread_bookmark():
    """Create a bookmark with Twitter URL that's a thread."""
    return Bookmark(
        id="123456",
        url="https://x.com/naval/status/1002103360646823936",
        text="Thread about startups and life advice",
        author_username="naval",
        content_type=ContentType.THREAD,
    )


@pytest.fixture
def thread_bookmark_in_links():
    """Create a bookmark with Twitter URL in links (not main url)."""
    return Bookmark(
        id="234567",
        url="https://example.com",
        text="Check out this thread",
        author_username="testuser",
        content_type=ContentType.THREAD,
        links=["https://twitter.com/user/status/789"],
    )


@pytest.fixture
def bookmark_no_twitter():
    """Create a bookmark without Twitter/X URL."""
    return Bookmark(
        id="345678",
        url="https://example.com/article",
        text="No thread here",
        author_username="testuser",
        content_type=ContentType.THREAD,
    )


@pytest.fixture
def mock_skill_output():
    """Sample successful skill output for a thread."""
    return {
        "success": True,
        "source": "bird",
        "author": "naval",
        "tweet_count": 3,
        "tweets": [
            {
                "id": "1002103360646823936",
                "text": "How to Get Rich (without getting lucky):\n\nSeek wealth, not money or status.",
                "author_username": "naval",
                "author_name": "Naval",
                "created_at": "2018-05-31T15:00:00Z",
                "likes": 50000,
                "retweets": 20000,
                "media_urls": [],
                "links": [],
                "is_thread": True,
                "thread_position": 1,
                "url": "https://x.com/naval/status/1002103360646823936",
            },
            {
                "id": "1002103361",
                "text": "Wealth is having assets that earn while you sleep.",
                "author_username": "naval",
                "is_thread": True,
                "thread_position": 2,
                "media_urls": [],
                "links": [],
            },
            {
                "id": "1002103362",
                "text": "Money is how we transfer time and wealth. #wealth #money",
                "author_username": "naval",
                "is_thread": True,
                "thread_position": 3,
                "media_urls": [],
                "links": ["https://nav.al/wealth"],
            },
        ],
        "error": None,
    }


@pytest.fixture
def mock_deleted_thread_output():
    """Sample skill output for deleted thread."""
    return {
        "success": False,
        "tweets": [],
        "error": "Thread not found on ThreadReaderApp",
    }


class TestThreadProcessorCallsSkill:
    """Tests for skill invocation."""

    @pytest.mark.asyncio
    async def test_thread_processor_calls_skill(self, processor, thread_bookmark, mock_skill_output):
        """subprocess.run is called with --thread --json arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            await processor.process(thread_bookmark)

            # Verify subprocess.run was called
            mock_run.assert_called_once()
            call_args = mock_run.call_args

            # Check command structure
            cmd = call_args[0][0]
            assert cmd[0] == "python3"
            assert "twitter_reader.py" in cmd[1]
            assert "--thread" in cmd
            assert "--json" in cmd

    @pytest.mark.asyncio
    async def test_thread_processor_passes_twitter_url(
        self, processor, thread_bookmark, mock_skill_output
    ):
        """Correct Twitter URL is passed to skill."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            await processor.process(thread_bookmark)

            cmd = mock_run.call_args[0][0]
            assert thread_bookmark.url in cmd

    @pytest.mark.asyncio
    async def test_thread_processor_uses_links_fallback(
        self, processor, thread_bookmark_in_links, mock_skill_output
    ):
        """Falls back to links if main URL is not Twitter."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            await processor.process(thread_bookmark_in_links)

            cmd = mock_run.call_args[0][0]
            assert thread_bookmark_in_links.links[0] in cmd


class TestThreadProcessorParsesOutput:
    """Tests for parsing skill output."""

    @pytest.mark.asyncio
    async def test_thread_processor_parses_json(
        self, processor, thread_bookmark, mock_skill_output
    ):
        """Output JSON is correctly parsed into ProcessResult."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            assert result.success is True
            assert result.content is not None
            assert "naval" in result.content.lower()

    @pytest.mark.asyncio
    async def test_thread_processor_extracts_title(
        self, processor, thread_bookmark, mock_skill_output
    ):
        """Title is extracted from first tweet (max 8 words)."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            assert result.title is not None
            # Title should be from first tweet, not too long
            assert len(result.title.split()) <= 9  # 8 words + possible "..."

    @pytest.mark.asyncio
    async def test_thread_processor_extracts_hashtags_as_tags(
        self, processor, thread_bookmark, mock_skill_output
    ):
        """Hashtags from tweets are extracted as tags."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            # Third tweet has #wealth #money
            assert "wealth" in result.tags
            assert "money" in result.tags

    @pytest.mark.asyncio
    async def test_thread_processor_includes_all_tweets(
        self, processor, thread_bookmark, mock_skill_output
    ):
        """All tweets from thread are included in content."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            # Content should have all tweet texts
            assert "Seek wealth, not money or status" in result.content
            assert "assets that earn while you sleep" in result.content
            assert "how we transfer time and wealth" in result.content


class TestThreadProcessorHandlesDeletedThread:
    """Tests for deleted thread handling."""

    @pytest.mark.asyncio
    async def test_thread_processor_handles_deleted_thread(
        self, processor, thread_bookmark, mock_deleted_thread_output
    ):
        """Deleted thread results in graceful error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = json.dumps(mock_deleted_thread_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            assert result.success is False
            assert "not found" in result.error.lower() or "thread" in result.error.lower()

    @pytest.mark.asyncio
    async def test_thread_processor_handles_success_false(
        self, processor, thread_bookmark, mock_deleted_thread_output
    ):
        """success=False in output is handled even with returncode=0."""
        mock_result = MagicMock()
        mock_result.returncode = 0  # returncode OK but success=False
        mock_result.stdout = json.dumps(mock_deleted_thread_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            assert result.success is False


class TestThreadProcessorHandlesTimeout:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_thread_processor_handles_timeout(self, thread_bookmark):
        """Timeout results in graceful error."""
        processor = ThreadProcessor(timeout=1)  # Very short timeout

        def slow_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="test", timeout=1)

        with patch("subprocess.run", side_effect=slow_run):
            result = await processor.process(thread_bookmark)

            assert result.success is False
            assert "timeout" in result.error.lower()


class TestThreadProcessorHandlesErrors:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_thread_processor_handles_no_twitter_url(self, processor, bookmark_no_twitter):
        """Bookmark without Twitter URL results in error."""
        result = await processor.process(bookmark_no_twitter)

        assert result.success is False
        assert "no twitter" in result.error.lower() or "url" in result.error.lower()

    @pytest.mark.asyncio
    async def test_thread_processor_handles_malformed_json(self, processor, thread_bookmark):
        """Malformed JSON output results in error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = "not valid json {"
        mock_result.stderr = "Error: something went wrong"

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            assert result.success is False

    @pytest.mark.asyncio
    async def test_thread_processor_handles_skill_error(self, processor, thread_bookmark):
        """Non-zero exit code with error message results in error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "bird CLI not found"

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            assert result.success is False
            assert "bird" in result.error.lower() or "skill" in result.error.lower()


class TestThreadProcessorDuration:
    """Tests for duration tracking."""

    @pytest.mark.asyncio
    async def test_thread_processor_tracks_duration(
        self, processor, thread_bookmark, mock_skill_output
    ):
        """Duration is tracked in result."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_thread_processor_duration_on_error(self, processor, bookmark_no_twitter):
        """Duration is tracked even on error."""
        result = await processor.process(bookmark_no_twitter)

        assert result.duration_ms >= 0


class TestThreadProcessorContentFormatting:
    """Tests for content formatting (Issue #22)."""

    @pytest.mark.asyncio
    async def test_thread_formats_multiple_tweets(
        self, processor, thread_bookmark, mock_skill_output
    ):
        """Each tweet is numbered in formatted content."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            # Each tweet should be numbered
            assert "### Tweet 1" in result.content
            assert "### Tweet 2" in result.content
            assert "### Tweet 3" in result.content

    @pytest.mark.asyncio
    async def test_thread_includes_media(self, thread_bookmark):
        """Media URLs (images) are included in formatted content."""
        processor = ThreadProcessor(timeout=10)

        # Skill output with media
        skill_output = {
            "success": True,
            "author": "testuser",
            "tweets": [
                {
                    "id": "1",
                    "text": "Check out this image",
                    "media_urls": ["https://pbs.twimg.com/media/image1.jpg"],
                    "links": [],
                },
                {
                    "id": "2",
                    "text": "And this link",
                    "media_urls": [],
                    "links": ["https://example.com/article"],
                },
            ],
        }

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            # Media should be included as markdown images
            assert "![image](https://pbs.twimg.com/media/image1.jpg)" in result.content
            # External links should be included
            assert "https://example.com/article" in result.content

    @pytest.mark.asyncio
    async def test_thread_extracts_key_points(self, thread_bookmark, mock_skill_output):
        """Key points are extracted via LLM when available."""
        # Create mock LLM client
        mock_llm = MagicMock()
        mock_llm.extract_structured.return_value = {
            "key_points": [
                "Seek wealth, not money or status",
                "Wealth is assets that earn while you sleep",
                "Money transfers time and wealth",
            ]
        }

        processor = ThreadProcessor(timeout=10, llm_client=mock_llm)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            # LLM should have been called
            mock_llm.extract_structured.assert_called_once()

            # Key points should be in metadata
            assert "key_points" in result.metadata
            assert len(result.metadata["key_points"]) == 3
            assert "Seek wealth" in result.metadata["key_points"][0]


class TestThreadProcessorKeyPointsEdgeCases:
    """Tests for key points extraction edge cases."""

    @pytest.mark.asyncio
    async def test_key_points_graceful_without_llm(self, thread_bookmark, mock_skill_output):
        """Key points are empty when LLM is not available."""
        processor = ThreadProcessor(timeout=10, llm_client=None)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        # Also patch get_llm_client to raise (no API key)
        with patch("subprocess.run", return_value=mock_result):
            with patch(
                "src.processors.thread_processor.get_llm_client",
                side_effect=Exception("No API key"),
            ):
                result = await processor.process(thread_bookmark)

                # Should succeed but with empty key_points
                assert result.success is True
                assert result.metadata["key_points"] == []

    @pytest.mark.asyncio
    async def test_key_points_graceful_on_llm_error(self, thread_bookmark, mock_skill_output):
        """Key points are empty when LLM extraction fails."""
        # Create mock LLM client that raises ExtractionError
        mock_llm = MagicMock()
        mock_llm.extract_structured.side_effect = ExtractionError("API error")

        processor = ThreadProcessor(timeout=10, llm_client=mock_llm)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            # Should succeed but with empty key_points
            assert result.success is True
            assert result.metadata["key_points"] == []

    @pytest.mark.asyncio
    async def test_key_points_limits_to_five(self, thread_bookmark, mock_skill_output):
        """Key points are limited to max 5 items."""
        # Create mock LLM client that returns too many points
        mock_llm = MagicMock()
        mock_llm.extract_structured.return_value = {
            "key_points": [
                "Point 1", "Point 2", "Point 3",
                "Point 4", "Point 5", "Point 6", "Point 7",
            ]
        }

        processor = ThreadProcessor(timeout=10, llm_client=mock_llm)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            # Should be limited to 5
            assert len(result.metadata["key_points"]) == 5

    @pytest.mark.asyncio
    async def test_key_points_handles_invalid_response(self, thread_bookmark, mock_skill_output):
        """Key points are empty when LLM returns invalid format."""
        # Create mock LLM client that returns wrong format
        mock_llm = MagicMock()
        mock_llm.extract_structured.return_value = {
            "key_points": "not a list"  # Should be a list
        }

        processor = ThreadProcessor(timeout=10, llm_client=mock_llm)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(thread_bookmark)

            # Should succeed but with empty key_points
            assert result.success is True
            assert result.metadata["key_points"] == []
