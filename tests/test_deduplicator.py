"""Tests for Deduplicator."""

import logging
from pathlib import Path

from src.core.bookmark import Bookmark, ProcessingStatus
from src.core.deduplicator import DeduplicationStats, Deduplicator
from src.core.state_manager import StateManager


def make_bookmark(id: str, username: str = "testuser") -> Bookmark:
    """Create a test bookmark with minimal required fields."""
    return Bookmark(
        id=id,
        url=f"https://twitter.com/{username}/status/{id}",
        text=f"Test tweet {id}",
        author_username=username,
    )


class TestDeduplicationStats:
    """Test DeduplicationStats dataclass."""

    def test_stats_default_values(self):
        """Stats should have zero defaults."""
        stats = DeduplicationStats()
        assert stats.total_checked == 0
        assert stats.duplicates_found == 0
        assert stats.unique_bookmarks == 0

    def test_stats_duplicate_rate_calculation(self):
        """duplicate_rate should calculate percentage correctly."""
        stats = DeduplicationStats(
            total_checked=100,
            duplicates_found=25,
            unique_bookmarks=75,
        )
        assert stats.duplicate_rate == 25.0

    def test_stats_duplicate_rate_zero_checked(self):
        """duplicate_rate should return 0.0 when nothing checked."""
        stats = DeduplicationStats()
        assert stats.duplicate_rate == 0.0


class TestDeduplicatorDetection:
    """Test duplicate detection by ID."""

    def test_detects_duplicate_by_id(self, tmp_path: Path):
        """Same tweet ID should be detected as duplicate."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("12345", ProcessingStatus.DONE)

        dedup = Deduplicator(state_manager)
        bookmark = make_bookmark("12345")

        assert dedup.is_duplicate(bookmark) is True

    def test_allows_different_ids(self, tmp_path: Path):
        """Different IDs should not be duplicates."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("12345", ProcessingStatus.DONE)

        dedup = Deduplicator(state_manager)
        bookmark1 = make_bookmark("12345")
        bookmark2 = make_bookmark("67890")

        assert dedup.is_duplicate(bookmark1) is True
        assert dedup.is_duplicate(bookmark2) is False

    def test_error_status_also_duplicate(self, tmp_path: Path):
        """Bookmarks marked as ERROR should also be considered duplicates."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("12345", ProcessingStatus.ERROR)

        dedup = Deduplicator(state_manager)
        bookmark = make_bookmark("12345")

        assert dedup.is_duplicate(bookmark) is True


class TestDeduplicatorFilter:
    """Test batch filtering of duplicates."""

    def test_filter_removes_duplicates(self, tmp_path: Path):
        """filter_duplicates should remove already processed bookmarks."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("111", ProcessingStatus.DONE)
        state_manager.mark_processed("333", ProcessingStatus.DONE)

        dedup = Deduplicator(state_manager)
        bookmarks = [
            make_bookmark("111"),
            make_bookmark("222"),
            make_bookmark("333"),
            make_bookmark("444"),
        ]

        unique = dedup.filter_duplicates(bookmarks)

        assert len(unique) == 2
        assert unique[0].id == "222"
        assert unique[1].id == "444"

    def test_filter_all_unique(self, tmp_path: Path):
        """filter_duplicates should return all when none are duplicates."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.load()

        dedup = Deduplicator(state_manager)
        bookmarks = [
            make_bookmark("111"),
            make_bookmark("222"),
            make_bookmark("333"),
        ]

        unique = dedup.filter_duplicates(bookmarks)

        assert len(unique) == 3
        assert [b.id for b in unique] == ["111", "222", "333"]

    def test_filter_all_duplicates(self, tmp_path: Path):
        """filter_duplicates should return empty when all are duplicates."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("111", ProcessingStatus.DONE)
        state_manager.mark_processed("222", ProcessingStatus.DONE)

        dedup = Deduplicator(state_manager)
        bookmarks = [
            make_bookmark("111"),
            make_bookmark("222"),
        ]

        unique = dedup.filter_duplicates(bookmarks)

        assert len(unique) == 0

    def test_filter_empty_list(self, tmp_path: Path):
        """filter_duplicates should handle empty input gracefully."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.load()

        dedup = Deduplicator(state_manager)
        unique = dedup.filter_duplicates([])

        assert len(unique) == 0


class TestDeduplicatorLogging:
    """Test logging for skipped duplicates."""

    def test_logs_skipped_duplicates(self, tmp_path: Path, caplog):
        """Duplicate detection should log at INFO level."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("12345", ProcessingStatus.DONE)

        dedup = Deduplicator(state_manager)
        bookmark = make_bookmark("12345", username="johndoe")

        with caplog.at_level(logging.INFO):
            dedup.is_duplicate(bookmark)

        assert "Duplicate detected" in caplog.text
        assert "12345" in caplog.text

    def test_filter_logs_summary(self, tmp_path: Path, caplog):
        """filter_duplicates should log summary at INFO level."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("111", ProcessingStatus.DONE)
        state_manager.load()

        dedup = Deduplicator(state_manager)
        bookmarks = [
            make_bookmark("111"),
            make_bookmark("222"),
            make_bookmark("333"),
        ]

        with caplog.at_level(logging.INFO):
            dedup.filter_duplicates(bookmarks)

        assert "Deduplication complete" in caplog.text
        assert "3 total" in caplog.text
        assert "1 duplicates" in caplog.text
        assert "2 unique" in caplog.text


class TestDeduplicatorStats:
    """Test statistics tracking."""

    def test_counts_duplicates_in_stats(self, tmp_path: Path):
        """Stats should accurately count duplicates."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("111", ProcessingStatus.DONE)
        state_manager.mark_processed("222", ProcessingStatus.DONE)

        dedup = Deduplicator(state_manager)
        bookmarks = [
            make_bookmark("111"),
            make_bookmark("222"),
            make_bookmark("333"),
            make_bookmark("444"),
            make_bookmark("555"),
        ]

        dedup.filter_duplicates(bookmarks)
        stats = dedup.get_stats()

        assert stats.total_checked == 5
        assert stats.duplicates_found == 2
        assert stats.unique_bookmarks == 3
        assert stats.duplicate_rate == 40.0

    def test_stats_accumulate_across_calls(self, tmp_path: Path):
        """Stats should accumulate across multiple filter calls."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("111", ProcessingStatus.DONE)
        state_manager.load()

        dedup = Deduplicator(state_manager)

        # First batch
        dedup.filter_duplicates([make_bookmark("111"), make_bookmark("222")])
        # Second batch
        dedup.filter_duplicates([make_bookmark("333")])

        stats = dedup.get_stats()
        assert stats.total_checked == 3
        assert stats.duplicates_found == 1
        assert stats.unique_bookmarks == 2

    def test_reset_stats(self, tmp_path: Path):
        """reset_stats should clear all counters."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("111", ProcessingStatus.DONE)

        dedup = Deduplicator(state_manager)
        dedup.filter_duplicates([make_bookmark("111"), make_bookmark("222")])

        dedup.reset_stats()
        stats = dedup.get_stats()

        assert stats.total_checked == 0
        assert stats.duplicates_found == 0
        assert stats.unique_bookmarks == 0

    def test_stats_with_is_duplicate(self, tmp_path: Path):
        """is_duplicate alone should not affect stats (only filter does)."""
        state_file = tmp_path / "state.json"
        state_manager = StateManager(state_file)
        state_manager.mark_processed("111", ProcessingStatus.DONE)

        dedup = Deduplicator(state_manager)
        dedup.is_duplicate(make_bookmark("111"))

        stats = dedup.get_stats()
        # is_duplicate doesn't update stats, only filter_duplicates does
        assert stats.total_checked == 0
