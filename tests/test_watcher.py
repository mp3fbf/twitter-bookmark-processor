"""Tests for DirectoryWatcher."""

import time
from pathlib import Path

import pytest

from src.core.backlog_manager import BacklogManager
from src.core.state_manager import StateManager
from src.core.watcher import DirectoryWatcher


@pytest.fixture
def temp_dirs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create temporary directories for testing."""
    backlog = tmp_path / "backlog"
    backlog.mkdir()
    processed = tmp_path / "processed"
    state_file = tmp_path / "state.json"
    return backlog, processed, state_file


@pytest.fixture
def watcher(temp_dirs: tuple[Path, Path, Path]) -> DirectoryWatcher:
    """Create a DirectoryWatcher with temp directories."""
    backlog, processed, state_file = temp_dirs
    backlog_mgr = BacklogManager(backlog, processed)
    state_mgr = StateManager(state_file)
    return DirectoryWatcher(backlog_mgr, state_mgr)


class TestGetNewFiles:
    """Tests for get_new_files method."""

    def test_watcher_detects_new_file(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """New file in backlog is detected."""
        backlog, _, _ = temp_dirs

        # Add a new file
        new_file = backlog / "export.json"
        new_file.write_text('{"bookmarks": []}')

        files = watcher.get_new_files()

        assert len(files) == 1
        assert files[0].name == "export.json"

    def test_watcher_detects_multiple_files(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """Multiple new files are detected."""
        backlog, _, _ = temp_dirs

        (backlog / "a.json").write_text("{}")
        time.sleep(0.01)
        (backlog / "b.json").write_text("{}")
        time.sleep(0.01)
        (backlog / "c.json").write_text("{}")

        files = watcher.get_new_files()

        assert len(files) == 3
        # Should be sorted by mtime (oldest first)
        names = [f.name for f in files]
        assert names == ["a.json", "b.json", "c.json"]

    def test_watcher_ignores_processed(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """Already processed files are not returned."""
        backlog, _, _ = temp_dirs

        file1 = backlog / "old.json"
        file1.write_text("{}")
        file2 = backlog / "new.json"
        file2.write_text("{}")

        # Mark first file as processed
        watcher.mark_file_processed(file1)

        files = watcher.get_new_files()

        assert len(files) == 1
        assert files[0].name == "new.json"

    def test_watcher_handles_empty_dir(
        self, watcher: DirectoryWatcher
    ) -> None:
        """Empty directory returns empty list."""
        files = watcher.get_new_files()
        assert files == []

    def test_watcher_custom_pattern(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """Custom glob pattern is respected."""
        backlog, _, _ = temp_dirs

        (backlog / "data.json").write_text("{}")
        (backlog / "data.csv").write_text("")

        json_files = watcher.get_new_files("*.json")
        csv_files = watcher.get_new_files("*.csv")

        assert len(json_files) == 1
        assert len(csv_files) == 1
        assert json_files[0].name == "data.json"
        assert csv_files[0].name == "data.csv"

    def test_watcher_excludes_non_json_by_default(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """Default pattern only matches *.json files."""
        backlog, _, _ = temp_dirs

        (backlog / "export.json").write_text("{}")
        (backlog / "readme.txt").write_text("")
        (backlog / "data.csv").write_text("")

        files = watcher.get_new_files()

        assert len(files) == 1
        assert files[0].name == "export.json"


class TestMarkFileProcessed:
    """Tests for mark_file_processed method."""

    def test_marks_file_processed(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """File is marked as processed."""
        backlog, _, _ = temp_dirs
        file_path = backlog / "test.json"
        file_path.write_text("{}")

        assert not watcher.is_file_processed(file_path)

        watcher.mark_file_processed(file_path)

        assert watcher.is_file_processed(file_path)

    def test_marks_string_path(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """Works with string paths."""
        backlog, _, _ = temp_dirs
        file_path = str(backlog / "test.json")

        watcher.mark_file_processed(file_path)

        assert watcher.is_file_processed(file_path)

    def test_is_file_processed_with_path_object(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """is_file_processed works with Path objects too."""
        backlog, _, _ = temp_dirs
        file_path = backlog / "test.json"

        watcher.mark_file_processed(str(file_path))

        # Check with Path object
        assert watcher.is_file_processed(file_path)


class TestReset:
    """Tests for reset method."""

    def test_reset_clears_processed(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """Reset clears all processed files."""
        backlog, _, _ = temp_dirs
        file_path = backlog / "test.json"
        file_path.write_text("{}")

        watcher.mark_file_processed(file_path)
        assert watcher.is_file_processed(file_path)

        watcher.reset()

        assert not watcher.is_file_processed(file_path)
        # File should now appear in get_new_files again
        files = watcher.get_new_files()
        assert len(files) == 1


class TestGetStats:
    """Tests for get_stats method."""

    def test_stats_empty(self, watcher: DirectoryWatcher) -> None:
        """Stats for empty watcher."""
        stats = watcher.get_stats()

        assert stats["total_in_backlog"] == 0
        assert stats["new_files"] == 0
        assert stats["processed_this_session"] == 0

    def test_stats_with_files(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """Stats reflect current state."""
        backlog, _, _ = temp_dirs

        (backlog / "a.json").write_text("{}")
        (backlog / "b.json").write_text("{}")
        (backlog / "c.json").write_text("{}")

        stats = watcher.get_stats()

        assert stats["total_in_backlog"] == 3
        assert stats["new_files"] == 3
        assert stats["processed_this_session"] == 0

    def test_stats_after_processing(
        self, watcher: DirectoryWatcher, temp_dirs: tuple[Path, Path, Path]
    ) -> None:
        """Stats update after processing files."""
        backlog, _, _ = temp_dirs

        file1 = backlog / "a.json"
        file1.write_text("{}")
        file2 = backlog / "b.json"
        file2.write_text("{}")

        watcher.mark_file_processed(file1)

        stats = watcher.get_stats()

        assert stats["total_in_backlog"] == 2
        assert stats["new_files"] == 1  # Only file2 is new
        assert stats["processed_this_session"] == 1
