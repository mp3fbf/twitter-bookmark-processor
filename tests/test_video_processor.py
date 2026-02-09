"""Tests for VideoProcessor."""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from src.core.bookmark import Bookmark, ContentType
from src.processors.video_processor import VideoProcessor


@pytest.fixture
def processor():
    """Create a VideoProcessor instance with short timeout for tests."""
    return VideoProcessor(timeout=10)


@pytest.fixture
def youtube_bookmark():
    """Create a bookmark with YouTube URL in video_urls."""
    return Bookmark(
        id="123456",
        url="https://twitter.com/user/status/123456",
        text="Check out this great video! https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        author_username="testuser",
        content_type=ContentType.VIDEO,
        video_urls=["https://www.youtube.com/watch?v=dQw4w9WgXcQ"],
    )


@pytest.fixture
def youtube_bookmark_in_links():
    """Create a bookmark with YouTube URL in links (not video_urls)."""
    return Bookmark(
        id="234567",
        url="https://twitter.com/user/status/234567",
        text="This is interesting https://youtu.be/abc123",
        author_username="testuser",
        content_type=ContentType.VIDEO,
        links=["https://youtu.be/abc123"],
    )


@pytest.fixture
def bookmark_no_youtube():
    """Create a bookmark without YouTube URL."""
    return Bookmark(
        id="345678",
        url="https://twitter.com/user/status/345678",
        text="No video here",
        author_username="testuser",
        content_type=ContentType.VIDEO,
    )


@pytest.fixture
def mock_skill_output():
    """Sample successful skill output."""
    return {
        "title": "Test Video Title",
        "channel": "Test Channel",
        "duration": "10:30",
        "tldr": "This is a summary of the video content.",
        "key_points": [
            "First key point",
            "Second key point",
            "Third key point",
        ],
        "tags": ["topic/python", "topic/testing", "person/guido"],
        "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "processed_at": "2026-01-23T12:00:00",
        "mode": "note",
    }


@pytest.fixture
def mock_transcript_output():
    """Sample skill output in transcript mode."""
    return {
        "title": "Transcript Video",
        "channel": "Some Channel",
        "duration": "5:00",
        "summary": "A short video about something.",
        "key_points": [
            {"timestamp": "0:30", "content": "First point"},
            {"timestamp": "2:00", "content": "Second point"},
        ],
        "source_url": "https://www.youtube.com/watch?v=xyz789",
    }


class TestVideoProcessorCallsSkill:
    """Tests for skill invocation."""

    @pytest.mark.asyncio
    async def test_video_processor_calls_skill(self, processor, youtube_bookmark, mock_skill_output):
        """subprocess.run is called with correct arguments."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            await processor.process(youtube_bookmark)

            # Verify subprocess.run was called
            mock_run.assert_called_once()
            call_args = mock_run.call_args

            # Check command structure
            cmd = call_args[0][0]
            assert cmd[0] == "python3"
            assert "youtube_processor.py" in cmd[1]
            assert "youtube.com" in cmd[2]
            assert "--json" in cmd

    @pytest.mark.asyncio
    async def test_video_processor_passes_youtube_url(
        self, processor, youtube_bookmark, mock_skill_output
    ):
        """Correct YouTube URL is passed to skill."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            await processor.process(youtube_bookmark)

            cmd = mock_run.call_args[0][0]
            assert youtube_bookmark.video_urls[0] in cmd

    @pytest.mark.asyncio
    async def test_video_processor_uses_links_fallback(
        self, processor, youtube_bookmark_in_links, mock_skill_output
    ):
        """Falls back to links if video_urls empty."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            await processor.process(youtube_bookmark_in_links)

            cmd = mock_run.call_args[0][0]
            assert youtube_bookmark_in_links.links[0] in cmd


class TestVideoProcessorParsesOutput:
    """Tests for parsing skill output."""

    @pytest.mark.asyncio
    async def test_video_processor_parses_output(
        self, processor, youtube_bookmark, mock_skill_output
    ):
        """Skill output is correctly parsed into ProcessResult."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.success is True
            assert result.title == "Test Video Title"
            assert result.content is not None
            assert "Test Channel" in result.content

    @pytest.mark.asyncio
    async def test_video_processor_extracts_tags(
        self, processor, youtube_bookmark, mock_skill_output
    ):
        """Tags are extracted from hierarchical format."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            # Tags should have hierarchy stripped
            assert "python" in result.tags
            assert "testing" in result.tags
            assert "guido" in result.tags

    @pytest.mark.asyncio
    async def test_video_processor_includes_tldr(
        self, processor, youtube_bookmark, mock_skill_output
    ):
        """TL;DR is included in content."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert "TL;DR" in result.content
            assert mock_skill_output["tldr"] in result.content

    @pytest.mark.asyncio
    async def test_video_processor_includes_key_points(
        self, processor, youtube_bookmark, mock_skill_output
    ):
        """Key points are included in content."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert "Key Points" in result.content
            for point in mock_skill_output["key_points"]:
                assert point in result.content

    @pytest.mark.asyncio
    async def test_video_processor_handles_transcript_format(
        self, processor, youtube_bookmark, mock_transcript_output
    ):
        """Handles transcript mode output with timestamps."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_transcript_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.success is True
            # Check timestamp format
            assert "[0:30]" in result.content
            assert "First point" in result.content


class TestVideoProcessorHandlesTimeout:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_video_processor_handles_timeout(self, youtube_bookmark):
        """Timeout results in graceful error."""
        processor = VideoProcessor(timeout=1)  # Very short timeout

        def slow_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="test", timeout=1)

        with patch("subprocess.run", side_effect=slow_run):
            result = await processor.process(youtube_bookmark)

            assert result.success is False
            assert "timed out" in result.error.lower()


class TestVideoProcessorHandlesSkillError:
    """Tests for skill error handling."""

    @pytest.mark.asyncio
    async def test_video_processor_handles_skill_error(self, processor, youtube_bookmark):
        """Non-zero exit code results in error."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "GOOGLE_API_KEY not found"

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.success is False
            assert "skill failed" in result.error.lower()
            assert "GOOGLE_API_KEY" in result.error

    @pytest.mark.asyncio
    async def test_video_processor_handles_malformed_json(self, processor, youtube_bookmark):
        """Malformed JSON output results in error."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not valid json {"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.success is False
            assert "parse" in result.error.lower()

    @pytest.mark.asyncio
    async def test_video_processor_handles_no_youtube_url(self, processor, bookmark_no_youtube):
        """Bookmark without YouTube URL falls back to Twitter video download."""
        result = await processor.process(bookmark_no_youtube)

        # Now attempts yt-dlp download instead of immediately failing
        assert result.success is False
        assert "video" in result.error.lower()


class TestVideoProcessorDuration:
    """Tests for duration tracking."""

    @pytest.mark.asyncio
    async def test_video_processor_tracks_duration(
        self, processor, youtube_bookmark, mock_skill_output
    ):
        """Duration is tracked in result."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.duration_ms >= 0

    @pytest.mark.asyncio
    async def test_video_processor_duration_on_error(self, processor, bookmark_no_youtube):
        """Duration is tracked even on error."""
        result = await processor.process(bookmark_no_youtube)

        assert result.duration_ms >= 0


class TestVideoOutputHandling:
    """Tests for video output handling (Issue #20)."""

    @pytest.mark.asyncio
    async def test_video_output_extracts_title(self, processor, youtube_bookmark, mock_skill_output):
        """Title is extracted from video skill output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.success is True
            assert result.title == "Test Video Title"
            assert result.title == mock_skill_output["title"]

    @pytest.mark.asyncio
    async def test_video_output_extracts_content(
        self, processor, youtube_bookmark, mock_skill_output
    ):
        """Content (transcription/summary) is extracted from skill output."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.success is True
            assert result.content is not None
            # Should include TL;DR
            assert mock_skill_output["tldr"] in result.content
            # Should include key points
            for point in mock_skill_output["key_points"]:
                assert point in result.content
            # Should include channel info
            assert mock_skill_output["channel"] in result.content

    @pytest.mark.asyncio
    async def test_video_output_finds_generated_file(
        self, youtube_bookmark, mock_skill_output, tmp_path
    ):
        """Locates .md file generated by skill when output_dir is specified."""
        # Create a processor with output_dir
        processor = VideoProcessor(timeout=10, output_dir=tmp_path)

        # Create a fake generated file
        generated_file = tmp_path / "test-video-title.md"
        generated_file.write_text("# Test Video\n\nContent here")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = f"Processing video in 'note' mode...\nSaved: {generated_file}"

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.success is True
            assert result.output_file is not None
            assert result.output_file == generated_file
            assert result.output_file.exists()

    @pytest.mark.asyncio
    async def test_video_output_file_none_when_no_output_dir(
        self, processor, youtube_bookmark, mock_skill_output
    ):
        """output_file is None when no output_dir specified."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = "Processing video in 'note' mode..."

        with patch("subprocess.run", return_value=mock_result):
            result = await processor.process(youtube_bookmark)

            assert result.success is True
            assert result.output_file is None

    @pytest.mark.asyncio
    async def test_video_output_passes_output_dir_to_skill(
        self, youtube_bookmark, mock_skill_output, tmp_path
    ):
        """output_dir is passed to skill as -o argument."""
        processor = VideoProcessor(timeout=10, output_dir=tmp_path)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(mock_skill_output)
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            await processor.process(youtube_bookmark)

            cmd = mock_run.call_args[0][0]
            assert "-o" in cmd
            assert str(tmp_path) in cmd
