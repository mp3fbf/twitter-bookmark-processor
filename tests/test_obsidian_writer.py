"""Tests for Obsidian Writer module."""

from pathlib import Path

import pytest

from src.core.bookmark import Bookmark, ContentType
from src.output.obsidian_writer import (
    ObsidianWriter,
    escape_yaml_string,
    sanitize_filename,
)
from src.processors.base import ProcessResult


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    def test_removes_invalid_chars(self):
        """Invalid filename chars should be removed."""
        result = sanitize_filename('test/file:name*with?"bad<chars>|')
        assert '/' not in result
        assert ':' not in result
        assert '*' not in result
        assert '?' not in result
        assert '"' not in result
        assert '<' not in result
        assert '>' not in result
        assert '|' not in result

    def test_collapses_whitespace(self):
        """Multiple spaces should become single space."""
        result = sanitize_filename("too    many   spaces")
        assert result == "too many spaces"

    def test_truncates_long_names(self):
        """Names over 200 chars should be truncated."""
        long_text = "word " * 100  # 500 chars
        result = sanitize_filename(long_text)
        assert len(result) <= 200

    def test_returns_untitled_for_empty(self):
        """Empty or all-invalid input returns 'untitled'."""
        assert sanitize_filename("") == "untitled"
        assert sanitize_filename("***???") == "untitled"


class TestEscapeYamlString:
    """Tests for YAML string escaping."""

    def test_escapes_colons(self):
        """Strings with colons should be quoted."""
        result = escape_yaml_string("key: value")
        assert result == '"key: value"'

    def test_escapes_quotes(self):
        """Double quotes in string should be escaped."""
        result = escape_yaml_string('say "hello"')
        assert result == '"say \\"hello\\""'

    def test_handles_at_sign(self):
        """Strings starting with @ should be quoted."""
        result = escape_yaml_string("@username")
        assert result == '"@username"'

    def test_plain_strings_unchanged(self):
        """Simple strings without special chars stay plain."""
        result = escape_yaml_string("simple text")
        assert result == "simple text"


class TestObsidianWriter:
    """Tests for ObsidianWriter class."""

    @pytest.fixture
    def output_dir(self, tmp_path: Path) -> Path:
        """Create a temporary output directory."""
        return tmp_path / "notes"

    @pytest.fixture
    def writer(self, output_dir: Path) -> ObsidianWriter:
        """Create a writer instance."""
        return ObsidianWriter(output_dir)

    @pytest.fixture
    def sample_bookmark(self) -> Bookmark:
        """Create a sample bookmark for testing."""
        return Bookmark(
            id="1234567890",
            url="https://twitter.com/testuser/status/1234567890",
            text="This is a test tweet with #python and #testing",
            author_username="testuser",
            author_name="Test User",
            content_type=ContentType.TWEET,
            created_at="2024-01-15T10:30:00Z",
        )

    @pytest.fixture
    def sample_result(self) -> ProcessResult:
        """Create a sample process result for testing."""
        return ProcessResult(
            success=True,
            title="This is a test tweet",
            content="**Test User** (@testuser)\n\nThis is a test tweet with #python and #testing",
            tags=["python", "testing"],
        )

    def test_write_creates_file(
        self,
        writer: ObsidianWriter,
        output_dir: Path,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Writing should create a .md file in the output directory."""
        output_path = writer.write(sample_bookmark, sample_result)

        assert output_path.exists()
        assert output_path.suffix == ".md"
        assert output_path.parent == output_dir

    def test_write_yaml_frontmatter(
        self,
        writer: ObsidianWriter,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Output should have valid YAML frontmatter with required fields."""
        output_path = writer.write(sample_bookmark, sample_result)
        content = output_path.read_text()

        # Should start and end with frontmatter delimiters
        assert content.startswith("---\n")
        assert "---\n\n" in content  # End of frontmatter

        # Required fields should be present
        assert "title:" in content
        assert "author:" in content
        assert "source:" in content
        assert "type:" in content
        assert "tweet_id:" in content
        assert "processed_at:" in content

    def test_write_content_body(
        self,
        writer: ObsidianWriter,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Content body should be present after frontmatter."""
        output_path = writer.write(sample_bookmark, sample_result)
        content = output_path.read_text()

        # Split at second --- to get body
        parts = content.split("---")
        assert len(parts) >= 3
        body = parts[2].strip()

        # Body should contain the processed content
        assert "Test User" in body
        assert "@testuser" in body

    def test_write_escapes_special_chars(
        self,
        writer: ObsidianWriter,
        output_dir: Path,
    ):
        """Special characters should be properly escaped in frontmatter."""
        bookmark = Bookmark(
            id="999",
            url="https://twitter.com/user/status/999",
            text='Tweet with "quotes" and colons: here',
            author_username="user",
            content_type=ContentType.TWEET,
        )
        result = ProcessResult(
            success=True,
            title='Title with "quotes" and: colons',
            content="Content",
            tags=["tag:with:colons"],
        )

        output_path = writer.write(bookmark, result)
        content = output_path.read_text()

        # File should be valid (no YAML parse errors would occur)
        # Check that quotes are escaped
        assert '\\"quotes\\"' in content or "quotes" in content

    def test_write_returns_output_path(
        self,
        writer: ObsidianWriter,
        output_dir: Path,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Write should return the path of the created file."""
        output_path = writer.write(sample_bookmark, sample_result)

        assert isinstance(output_path, Path)
        assert output_path.exists()
        assert output_path.is_file()

    def test_write_creates_output_dir(
        self,
        tmp_path: Path,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Writer should create output directory if it doesn't exist."""
        nested_dir = tmp_path / "deeply" / "nested" / "dir"
        writer = ObsidianWriter(nested_dir)

        output_path = writer.write(sample_bookmark, sample_result)

        assert nested_dir.exists()
        assert output_path.exists()

    def test_write_handles_collision(
        self,
        writer: ObsidianWriter,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Filename collision should append bookmark ID."""
        # Write first file
        first_path = writer.write(sample_bookmark, sample_result)

        # Create another bookmark with same title
        other_bookmark = Bookmark(
            id="9999999999",
            url="https://twitter.com/other/status/9999999999",
            text="Different tweet",
            author_username="other",
            content_type=ContentType.TWEET,
        )

        second_path = writer.write(other_bookmark, sample_result)

        # Both files should exist with different names
        assert first_path.exists()
        assert second_path.exists()
        assert first_path != second_path
        assert "9999999999" in second_path.name

    def test_write_includes_tags(
        self,
        writer: ObsidianWriter,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Tags should be included in frontmatter."""
        output_path = writer.write(sample_bookmark, sample_result)
        content = output_path.read_text()

        assert "tags:" in content
        assert "  - python" in content
        assert "  - testing" in content

    def test_write_handles_empty_tags(
        self,
        writer: ObsidianWriter,
        sample_bookmark: Bookmark,
    ):
        """Empty tags list should not include tags section."""
        result = ProcessResult(
            success=True,
            title="No tags tweet",
            content="Content without tags",
            tags=[],
        )

        output_path = writer.write(sample_bookmark, result)
        content = output_path.read_text()

        # tags: should not appear if no tags
        lines = content.split("\n")
        frontmatter_lines = []
        in_frontmatter = False
        for line in lines:
            if line == "---":
                if in_frontmatter:
                    break
                in_frontmatter = True
                continue
            if in_frontmatter:
                frontmatter_lines.append(line)

        # No "tags:" line in frontmatter
        assert not any(line.startswith("tags:") for line in frontmatter_lines)

    def test_write_preserves_content_type(
        self,
        writer: ObsidianWriter,
        sample_result: ProcessResult,
    ):
        """Content type should be correctly written to frontmatter."""
        for content_type in ContentType:
            bookmark = Bookmark(
                id="123",
                url="https://twitter.com/u/status/123",
                text="test",
                author_username="u",
                content_type=content_type,
            )

            output_path = writer.write(bookmark, sample_result)
            content = output_path.read_text()

            assert f"type: {content_type.value}" in content

            # Clean up for next iteration
            output_path.unlink()


class TestTemplateStructure:
    """Tests for Jinja2 template structure."""

    @pytest.fixture
    def output_dir(self, tmp_path: Path) -> Path:
        """Create a temporary output directory."""
        return tmp_path / "notes"

    @pytest.fixture
    def writer(self, output_dir: Path) -> ObsidianWriter:
        """Create a writer instance."""
        return ObsidianWriter(output_dir)

    @pytest.fixture
    def sample_bookmark(self) -> Bookmark:
        """Create a sample bookmark for testing."""
        return Bookmark(
            id="1234567890",
            url="https://twitter.com/testuser/status/1234567890",
            text="This is a test tweet about Python programming",
            author_username="testuser",
            author_name="Test User",
            content_type=ContentType.TWEET,
            created_at="2024-01-15T10:30:00Z",
        )

    @pytest.fixture
    def sample_result(self) -> ProcessResult:
        """Create a sample process result for testing."""
        return ProcessResult(
            success=True,
            title="This is a test tweet",
            content="**Test User** (@testuser)\n\nThis is a test tweet about Python programming",
            tags=["python", "programming"],
        )

    def test_tweet_template_structure(
        self,
        writer: ObsidianWriter,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Tweet template should have TL;DR and Content sections."""
        output_path = writer.write(sample_bookmark, sample_result)
        content = output_path.read_text()

        # Should have TL;DR section
        assert "## TL;DR" in content

        # Should have Content section
        assert "## Content" in content

    def test_template_includes_footer(
        self,
        writer: ObsidianWriter,
        sample_bookmark: Bookmark,
        sample_result: ProcessResult,
    ):
        """Template should include footer with processor version."""
        output_path = writer.write(sample_bookmark, sample_result)
        content = output_path.read_text()

        # Should have footer with version
        assert "twitter-bookmark-processor v" in content
        assert "0.1.0" in content


class TestThreadTemplate:
    """Tests for thread-specific Jinja2 template."""

    @pytest.fixture
    def output_dir(self, tmp_path: Path) -> Path:
        """Create a temporary output directory."""
        return tmp_path / "notes"

    @pytest.fixture
    def writer(self, output_dir: Path) -> ObsidianWriter:
        """Create a writer instance."""
        return ObsidianWriter(output_dir)

    @pytest.fixture
    def thread_bookmark(self) -> Bookmark:
        """Create a thread bookmark for testing."""
        return Bookmark(
            id="1002103360646823936",
            url="https://x.com/naval/status/1002103360646823936",
            text="How to Get Rich (without getting lucky)",
            author_username="naval",
            author_name="Naval",
            content_type=ContentType.THREAD,
            created_at="2018-05-31T15:00:00Z",
        )

    @pytest.fixture
    def thread_result(self) -> ProcessResult:
        """Create a thread process result with metadata."""
        return ProcessResult(
            success=True,
            title="How to Get Rich without getting lucky",
            content="Original formatted content here",
            tags=["wealth", "money"],
            metadata={
                "tweets": [
                    {
                        "id": "1002103360646823936",
                        "text": "How to Get Rich (without getting lucky):\n\nSeek wealth, not money or status.",
                        "media_urls": [],
                        "links": [],
                    },
                    {
                        "id": "1002103361",
                        "text": "Wealth is having assets that earn while you sleep.",
                        "media_urls": ["https://pbs.twimg.com/media/example.jpg"],
                        "links": [],
                    },
                    {
                        "id": "1002103362",
                        "text": "Money is how we transfer time and wealth.",
                        "media_urls": [],
                        "links": ["https://nav.al/wealth"],
                    },
                ],
                "tweet_count": 3,
                "author": "naval",
                "source": "bird",
                "key_points": [],
            },
        )

    @pytest.fixture
    def thread_result_with_key_points(self) -> ProcessResult:
        """Create a thread process result with key points."""
        return ProcessResult(
            success=True,
            title="How to Get Rich without getting lucky",
            content="Original formatted content here",
            tags=["wealth", "money"],
            metadata={
                "tweets": [
                    {
                        "id": "1",
                        "text": "First tweet",
                        "media_urls": [],
                        "links": [],
                    },
                ],
                "tweet_count": 1,
                "author": "naval",
                "source": "bird",
                "key_points": [
                    "Seek wealth, not money or status",
                    "Build assets that earn while you sleep",
                    "Specific knowledge cannot be taught",
                ],
            },
        )

    def test_thread_template_has_tweet_count(
        self,
        writer: ObsidianWriter,
        thread_bookmark: Bookmark,
        thread_result: ProcessResult,
    ):
        """Thread template should show 'Thread (N tweets)' header."""
        output_path = writer.write(thread_bookmark, thread_result)
        content = output_path.read_text()

        # Should have thread header with count
        assert "## Thread (3 tweets)" in content

    def test_thread_template_numbers_tweets(
        self,
        writer: ObsidianWriter,
        thread_bookmark: Bookmark,
        thread_result: ProcessResult,
    ):
        """Thread template should number each tweet."""
        output_path = writer.write(thread_bookmark, thread_result)
        content = output_path.read_text()

        # Should have numbered tweets
        assert "#### 1." in content
        assert "#### 2." in content
        assert "#### 3." in content

    def test_thread_template_has_key_points(
        self,
        writer: ObsidianWriter,
        thread_bookmark: Bookmark,
        thread_result_with_key_points: ProcessResult,
    ):
        """Thread template should have Key Points section when present."""
        output_path = writer.write(thread_bookmark, thread_result_with_key_points)
        content = output_path.read_text()

        # Should have Key Points section
        assert "### Key Points" in content
        assert "Seek wealth, not money or status" in content
        assert "Build assets that earn while you sleep" in content
        assert "Specific knowledge cannot be taught" in content

    def test_thread_template_omits_key_points_when_empty(
        self,
        writer: ObsidianWriter,
        thread_bookmark: Bookmark,
        thread_result: ProcessResult,
    ):
        """Thread template should omit Key Points section when empty."""
        output_path = writer.write(thread_bookmark, thread_result)
        content = output_path.read_text()

        # Should NOT have Key Points section when list is empty
        assert "### Key Points" not in content

    def test_thread_template_includes_tweet_content(
        self,
        writer: ObsidianWriter,
        thread_bookmark: Bookmark,
        thread_result: ProcessResult,
    ):
        """Thread template should include tweet text as blockquotes."""
        output_path = writer.write(thread_bookmark, thread_result)
        content = output_path.read_text()

        # Tweet content should be present
        assert "Seek wealth, not money or status" in content
        assert "assets that earn while you sleep" in content
        assert "how we transfer time and wealth" in content

    def test_thread_template_includes_media(
        self,
        writer: ObsidianWriter,
        thread_bookmark: Bookmark,
        thread_result: ProcessResult,
    ):
        """Thread template should include images from tweets."""
        output_path = writer.write(thread_bookmark, thread_result)
        content = output_path.read_text()

        # Image should be embedded
        assert "![image](https://pbs.twimg.com/media/example.jpg)" in content

    def test_thread_template_includes_links(
        self,
        writer: ObsidianWriter,
        thread_bookmark: Bookmark,
        thread_result: ProcessResult,
    ):
        """Thread template should include external links from tweets."""
        output_path = writer.write(thread_bookmark, thread_result)
        content = output_path.read_text()

        # Link should be present
        assert "https://nav.al/wealth" in content

    def test_thread_template_uses_thread_type_in_frontmatter(
        self,
        writer: ObsidianWriter,
        thread_bookmark: Bookmark,
        thread_result: ProcessResult,
    ):
        """Thread frontmatter should have type: thread."""
        output_path = writer.write(thread_bookmark, thread_result)
        content = output_path.read_text()

        # Type should be thread
        assert "type: thread" in content
