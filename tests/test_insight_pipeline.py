"""Tests for Insight Pipeline — orchestrator and state management."""

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.bookmark import Bookmark
from src.insight.models import ContentPackage, InsightNote, Section, ValueType
from src.insight.pipeline import InsightPipeline, InsightState


@pytest.fixture
def state_file(tmp_path):
    return tmp_path / "insight_state.json"


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "notes"
    d.mkdir()
    return d


@pytest.fixture
def bookmark():
    return Bookmark(
        id="test123",
        url="https://x.com/testuser/status/test123",
        text="A great technique for testing",
        author_username="testuser",
        author_name="Test User",
        created_at="2026-01-15T10:00:00Z",
    )


class TestInsightState:
    def test_initial_state(self, state_file):
        state = InsightState(state_file)
        assert not state.is_done("nonexistent")
        assert not state.is_capture_done("nonexistent")
        assert state.get("nonexistent") is None

    def test_mark_capture_done(self, state_file):
        state = InsightState(state_file)
        state.mark_capture_done("bid1")
        assert state.is_capture_done("bid1")
        assert not state.is_done("bid1")  # distill not done yet

    def test_mark_distill_done(self, state_file):
        state = InsightState(state_file)
        state.mark_capture_done("bid1")
        state.mark_distill_done("bid1", "technique", "/path/to/note.md")
        assert state.is_done("bid1")

    def test_mark_error(self, state_file):
        state = InsightState(state_file)
        state.mark_error("bid1", "Something failed")
        entry = state.get("bid1")
        assert entry["error"] == "Something failed"
        assert entry["needs_review"] is True
        assert not state.is_done("bid1")

    def test_get_review_ids(self, state_file):
        state = InsightState(state_file)
        state.mark_error("bid1", "err1")
        state.mark_error("bid2", "err2")
        state.mark_capture_done("bid3")
        state.mark_distill_done("bid3", "tip", "/path")
        reviews = state.get_review_ids()
        assert set(reviews) == {"bid1", "bid2"}

    def test_get_stats(self, state_file):
        state = InsightState(state_file)
        state.mark_capture_done("bid1")
        state.mark_distill_done("bid1", "technique", "/path")
        state.mark_error("bid2", "err")
        stats = state.get_stats()
        assert stats["done"] == 1
        assert stats["review"] == 1
        assert stats["total"] == 2

    def test_state_persists_across_instances(self, state_file):
        state1 = InsightState(state_file)
        state1.mark_capture_done("bid1")
        state1.mark_distill_done("bid1", "tool", "/path")

        # New instance should load persisted state
        state2 = InsightState(state_file)
        assert state2.is_done("bid1")


class TestInsightPipeline:
    @pytest.mark.asyncio
    async def test_skip_already_processed(self, state_file, output_dir, bookmark):
        """Already-processed bookmarks should be skipped."""
        state = InsightState(state_file)
        state.mark_capture_done("test123")
        state.mark_distill_done("test123", "technique", "/path")

        pipeline = InsightPipeline(
            output_dir=output_dir,
            state_file=state_file,
        )
        result = await pipeline.process_bookmark(bookmark)
        assert result is None  # Skipped

    @pytest.mark.asyncio
    async def test_crash_recovery_resume_stage2(self, state_file, output_dir, bookmark, tmp_path):
        """If capture is done but distill isn't, resume from Stage 2."""
        # Pre-mark capture as done and persist a content package
        state = InsightState(state_file)
        state.mark_capture_done("test123")

        package = ContentPackage(
            bookmark_id="test123",
            tweet_text="A great technique for testing",
            author_name="Test User",
            author_username="testuser",
            tweet_url="https://x.com/testuser/status/test123",
            created_at=datetime(2026, 1, 15),
        )
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        (packages_dir / "test123.json").write_text(
            package.model_dump_json(indent=2)
        )

        mock_note = InsightNote(
            value_type=ValueType.TECHNIQUE,
            title="Testing is a technique",
            sections=[Section(heading="The Knowledge", content="Test content")],
            tags=["testing"],
            original_content="A great technique for testing",
        )

        with patch("src.insight.capture.PACKAGES_DIR", packages_dir):
            pipeline = InsightPipeline(
                output_dir=output_dir,
                state_file=state_file,
            )
            # Mock the distiller to avoid API call
            pipeline._distill.distill = AsyncMock(return_value=mock_note)

            result = await pipeline.process_bookmark(bookmark)

        assert result is not None
        assert result.value_type == ValueType.TECHNIQUE
        assert pipeline.state.is_done("test123")

    @pytest.mark.asyncio
    async def test_error_marks_needs_review(self, state_file, output_dir, bookmark, tmp_path):
        """Pipeline failures should mark the bookmark for review."""
        with patch("src.insight.capture.PACKAGES_DIR", tmp_path / "packages"):
            pipeline = InsightPipeline(
                output_dir=output_dir,
                state_file=state_file,
            )
            # Mock capture to succeed, distill to fail
            pipeline._capture.capture = AsyncMock(return_value=ContentPackage(
                bookmark_id="test123",
                tweet_text="test",
                author_name="a",
                author_username="a",
                tweet_url="https://x.com/a/status/test123",
                created_at=datetime.now(),
            ))
            pipeline._distill.distill = AsyncMock(side_effect=Exception("API error"))

            result = await pipeline.process_bookmark(bookmark)

        assert result is None
        assert pipeline.state.needs_review("test123")


class TestInsightWriter:
    def test_write_note(self, output_dir):
        from src.insight.writer import InsightWriter
        writer = InsightWriter(output_dir)

        note = InsightNote(
            value_type=ValueType.TECHNIQUE,
            title="How to test effectively",
            sections=[
                Section(heading="The Knowledge", content="Testing is important"),
                Section(heading="The Technique", content="Step 1: Write tests\nStep 2: Run them"),
                Section(heading="The Insight", content="Tests catch bugs early"),
            ],
            tags=["testing", "techniques"],
            original_content="Original tweet about testing",
        )
        package = ContentPackage(
            bookmark_id="123",
            tweet_text="Original tweet about testing",
            author_name="Test User",
            author_username="testuser",
            tweet_url="https://x.com/testuser/status/123",
            created_at=datetime(2026, 1, 15),
        )

        path = writer.write(note, package)
        assert path.exists()
        content = path.read_text()
        assert "How to test effectively" in content
        assert "value_type: technique" in content
        assert "testing" in content
        assert "The Technique" in content

    def test_filename_collision(self, output_dir):
        from src.insight.writer import InsightWriter
        writer = InsightWriter(output_dir)

        note = InsightNote(
            value_type=ValueType.TIP,
            title="Same Title",
            sections=[Section(heading="The Knowledge", content="...")],
            tags=["test"],
            original_content="test",
        )
        pkg1 = ContentPackage(
            bookmark_id="111",
            tweet_text="t",
            author_name="a",
            author_username="a",
            tweet_url="https://x.com/a/status/111",
            created_at=datetime.now(),
        )
        pkg2 = ContentPackage(
            bookmark_id="222",
            tweet_text="t",
            author_name="a",
            author_username="a",
            tweet_url="https://x.com/a/status/222",
            created_at=datetime.now(),
        )

        path1 = writer.write(note, pkg1)
        path2 = writer.write(note, pkg2)
        assert path1 != path2
        assert path1.exists()
        assert path2.exists()
        assert "222" in path2.name  # Collision resolved with bookmark ID
