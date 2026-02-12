"""Tests for Stage 1: Content Capture."""

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.bookmark import Bookmark
from src.core.content_fetcher import FetchedContent
from src.insight.capture import (
    ContentCapture,
    MAX_STAGE2_TOKENS,
    estimate_tokens,
)
from src.insight.models import ContentPackage, ResolvedLink, ThreadTweet


@pytest.fixture
def bookmark():
    return Bookmark(
        id="123456",
        url="https://x.com/testuser/status/123456",
        text="Check this out https://t.co/abc123",
        author_username="testuser",
        author_name="Test User",
        created_at="2026-01-15T10:00:00Z",
        links=["https://t.co/abc123"],
    )


@pytest.fixture
def capture(tmp_path):
    """ContentCapture with mocked fetcher, no vision, no X API."""
    fetcher = AsyncMock()
    fetcher.fetch_content = AsyncMock(return_value=FetchedContent(
        url="https://t.co/abc123",
        expanded_url="https://example.com/article",
        title="Example Article",
        main_content="This is the article content.",
        content_type="article",
    ))
    fetcher.extract_urls = MagicMock(return_value=["https://t.co/abc123"])

    with patch("src.insight.capture.PACKAGES_DIR", tmp_path / "packages"):
        cap = ContentCapture(content_fetcher=fetcher)
        yield cap


class TestURLSafety:
    def test_safe_http(self):
        assert ContentCapture._is_safe_url("https://example.com")
        assert ContentCapture._is_safe_url("http://example.com")

    def test_unsafe_protocols(self):
        assert not ContentCapture._is_safe_url("ftp://example.com")
        assert not ContentCapture._is_safe_url("javascript:alert(1)")
        assert not ContentCapture._is_safe_url("file:///etc/passwd")
        assert not ContentCapture._is_safe_url("data:text/html,<h1>hi</h1>")


class TestTokenEstimation:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_short_text(self):
        tokens = estimate_tokens("Hello world")
        assert 1 <= tokens <= 5

    def test_long_text(self):
        text = "word " * 10000
        tokens = estimate_tokens(text)
        assert tokens > 5000


class TestLinkResolution:
    @pytest.mark.asyncio
    async def test_resolves_links(self, capture, bookmark, tmp_path):
        with patch("src.insight.capture.PACKAGES_DIR", tmp_path / "packages"):
            package = await capture.capture(bookmark)

        assert len(package.resolved_links) >= 1
        link = package.resolved_links[0]
        assert link.resolved_url == "https://example.com/article"
        assert link.title == "Example Article"
        assert link.content == "This is the article content."

    @pytest.mark.asyncio
    async def test_partial_failure(self, bookmark, tmp_path):
        """One failed link doesn't kill the whole capture."""
        fetcher = AsyncMock()
        fetcher.fetch_content = AsyncMock(side_effect=Exception("Connection timeout"))
        fetcher.extract_urls = MagicMock(return_value=["https://t.co/abc123"])

        with patch("src.insight.capture.PACKAGES_DIR", tmp_path / "packages"):
            cap = ContentCapture(content_fetcher=fetcher)
            package = await cap.capture(bookmark)

        # Should still have a resolved link entry, just with an error
        assert len(package.resolved_links) >= 1
        assert package.resolved_links[0].fetch_error is not None


class TestContentPackagePersistence:
    @pytest.mark.asyncio
    async def test_persists_to_disk(self, capture, bookmark, tmp_path):
        packages_dir = tmp_path / "packages"
        with patch("src.insight.capture.PACKAGES_DIR", packages_dir):
            package = await capture.capture(bookmark)

        # Check file was created
        pkg_file = packages_dir / f"{bookmark.id}.json"
        assert pkg_file.exists()

        # Verify it can be loaded back
        loaded = ContentPackage.model_validate_json(pkg_file.read_text())
        assert loaded.bookmark_id == bookmark.id

    @pytest.mark.asyncio
    async def test_load_package(self, capture, bookmark, tmp_path):
        packages_dir = tmp_path / "packages"
        with patch("src.insight.capture.PACKAGES_DIR", packages_dir):
            await capture.capture(bookmark)
            loaded = ContentCapture.load_package(bookmark.id)
            assert loaded is not None
            assert loaded.bookmark_id == bookmark.id

    def test_load_missing_package(self, tmp_path):
        with patch("src.insight.capture.PACKAGES_DIR", tmp_path / "empty"):
            assert ContentCapture.load_package("nonexistent") is None


class TestTruncation:
    def test_truncates_long_articles(self):
        """Long linked articles should be truncated when budget exceeded."""
        cap = ContentCapture()
        long_content = "word " * 50000  # ~100K chars, ~50K tokens
        package = ContentPackage(
            bookmark_id="1",
            tweet_text="test",
            author_name="a",
            author_username="a",
            tweet_url="https://x.com/a/status/1",
            created_at=datetime.now(),
            resolved_links=[
                ResolvedLink(
                    original_url="https://example.com",
                    resolved_url="https://example.com",
                    content=long_content,
                ),
            ],
        )

        original_len = len(package.resolved_links[0].content)

        # Mock token estimate to exceed budget so truncation triggers
        with patch.object(cap, "_estimate_package_tokens", side_effect=[
            MAX_STAGE2_TOKENS + 1,  # First check: over budget
            MAX_STAGE2_TOKENS - 1,  # After truncation: under budget
        ]):
            cap._truncate_package(package)

        # After truncation, content should be shorter
        assert len(package.resolved_links[0].content) < original_len

    def test_truncates_threads(self):
        """Threads with >12 tweets should be truncated to first+last 5."""
        cap = ContentCapture()
        package = ContentPackage(
            bookmark_id="1",
            tweet_text="test",
            author_name="a",
            author_username="a",
            tweet_url="https://x.com/a/status/1",
            created_at=datetime.now(),
            thread_tweets=[
                ThreadTweet(order=i, text=f"Tweet {i} " * 500)
                for i in range(20)
            ],
        )

        # Force token estimate to exceed budget
        with patch.object(cap, "_estimate_package_tokens", return_value=MAX_STAGE2_TOKENS + 1):
            cap._truncate_package(package)

        # Should be first 5 + summary + last 5 = 11
        assert len(package.thread_tweets) == 11
        assert "omitted" in package.thread_tweets[5].text


class TestDateParsing:
    def test_iso_format_with_z(self):
        dt = ContentCapture._parse_date("2026-01-15T10:00:00Z")
        assert dt.year == 2026
        assert dt.month == 1

    def test_iso_format_with_microseconds(self):
        dt = ContentCapture._parse_date("2026-01-15T10:00:00.123456Z")
        assert dt.year == 2026

    def test_empty_string(self):
        dt = ContentCapture._parse_date("")
        assert dt.year == datetime.now().year

    def test_invalid_string(self):
        dt = ContentCapture._parse_date("not a date")
        assert dt.year == datetime.now().year
