"""Tests for BacklogManager."""

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.core.backlog_manager import BacklogManager


@pytest.fixture
def temp_dirs(tmp_path: Path) -> tuple[Path, Path]:
    """Create temporary backlog and processed directories."""
    backlog = tmp_path / "backlog"
    backlog.mkdir()
    processed = tmp_path / "processed"
    return backlog, processed


@pytest.fixture
def manager(temp_dirs: tuple[Path, Path]) -> BacklogManager:
    """Create a BacklogManager with temp directories."""
    backlog, processed = temp_dirs
    return BacklogManager(backlog, processed, retention_days=30)


class TestArchiveFile:
    """Tests for archive_file method."""

    def test_moves_processed_file(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """File is moved to processed directory."""
        backlog, processed = temp_dirs
        source = backlog / "export.json"
        source.write_text('{"test": true}')

        result = manager.archive_file(source)

        assert result is not None
        assert result.parent == processed
        assert result.exists()
        assert not source.exists()
        assert "export.json" in result.name

    def test_handles_missing_file(self, manager: BacklogManager) -> None:
        """Returns None for non-existent file."""
        result = manager.archive_file("/nonexistent/file.json")
        assert result is None

    def test_creates_processed_dir(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Creates processed directory if it doesn't exist."""
        backlog, processed = temp_dirs
        source = backlog / "test.json"
        source.write_text("{}")

        assert not processed.exists()
        manager.archive_file(source)
        assert processed.exists()

    def test_adds_timestamp_to_filename(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Archived file includes timestamp prefix."""
        backlog, _ = temp_dirs
        source = backlog / "myexport.json"
        source.write_text("{}")

        result = manager.archive_file(source)

        assert result is not None
        # Format: YYYYMMDD_HHMMSS_originalname.json
        name = result.name
        assert name.endswith("_myexport.json")
        # Check timestamp format
        timestamp_part = name.split("_myexport.json")[0]
        assert len(timestamp_part) >= 15  # YYYYMMDD_HHMMSS

    def test_handles_filename_collision(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Handles collision when same file archived multiple times."""
        backlog, processed = temp_dirs
        processed.mkdir()

        # Create first file
        source1 = backlog / "test.json"
        source1.write_text('{"v": 1}')
        result1 = manager.archive_file(source1)

        # Create second file with same name immediately (same second)
        source2 = backlog / "test.json"
        source2.write_text('{"v": 2}')
        result2 = manager.archive_file(source2)

        assert result1 is not None
        assert result2 is not None
        assert result1 != result2
        assert result1.exists()
        assert result2.exists()


class TestCleanOldFiles:
    """Tests for clean_old_files method."""

    def test_preserves_recent_files(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Files younger than retention period are kept."""
        _, processed = temp_dirs
        processed.mkdir()

        recent = processed / "recent.json"
        recent.write_text("{}")

        deleted = manager.clean_old_files()

        assert len(deleted) == 0
        assert recent.exists()

    def test_cleans_old_files(self, tmp_path: Path) -> None:
        """Files older than retention period are deleted."""
        backlog = tmp_path / "backlog"
        backlog.mkdir()
        processed = tmp_path / "processed"
        processed.mkdir()

        # Create manager with 1 day retention
        manager = BacklogManager(backlog, processed, retention_days=1)

        old = processed / "old.json"
        old.write_text("{}")

        # Set mtime to 2 days ago
        old_time = (datetime.now() - timedelta(days=2)).timestamp()
        import os

        os.utime(old, (old_time, old_time))

        deleted = manager.clean_old_files()

        assert len(deleted) == 1
        assert not old.exists()
        assert deleted[0].name == "old.json"

    def test_handles_empty_processed_dir(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Returns empty list when processed dir is empty."""
        _, processed = temp_dirs
        processed.mkdir()

        deleted = manager.clean_old_files()

        assert deleted == []

    def test_handles_missing_processed_dir(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Returns empty list when processed dir doesn't exist."""
        _, processed = temp_dirs
        assert not processed.exists()

        deleted = manager.clean_old_files()

        assert deleted == []

    def test_preserves_gitkeep(self, tmp_path: Path) -> None:
        """Never deletes .gitkeep file."""
        backlog = tmp_path / "backlog"
        backlog.mkdir()
        processed = tmp_path / "processed"
        processed.mkdir()

        manager = BacklogManager(backlog, processed, retention_days=0)

        gitkeep = processed / ".gitkeep"
        gitkeep.write_text("")

        deleted = manager.clean_old_files()

        assert len(deleted) == 0
        assert gitkeep.exists()


class TestGetPendingFiles:
    """Tests for get_pending_files method."""

    def test_returns_json_files(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Returns .json files from backlog directory."""
        backlog, _ = temp_dirs

        (backlog / "a.json").write_text("{}")
        (backlog / "b.json").write_text("{}")
        (backlog / "c.txt").write_text("")

        files = manager.get_pending_files()

        names = [f.name for f in files]
        assert "a.json" in names
        assert "b.json" in names
        assert "c.txt" not in names

    def test_sorts_by_mtime(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Files are sorted by modification time (oldest first)."""
        backlog, _ = temp_dirs

        # Create files with different mtimes
        old = backlog / "old.json"
        old.write_text("{}")
        time.sleep(0.01)

        new = backlog / "new.json"
        new.write_text("{}")

        files = manager.get_pending_files()

        assert len(files) == 2
        assert files[0].name == "old.json"
        assert files[1].name == "new.json"

    def test_excludes_gitkeep(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Excludes .gitkeep from pending files."""
        backlog, _ = temp_dirs

        (backlog / ".gitkeep").write_text("")
        (backlog / "export.json").write_text("{}")

        files = manager.get_pending_files()

        names = [f.name for f in files]
        assert ".gitkeep" not in names
        assert "export.json" in names

    def test_handles_missing_dir(self, tmp_path: Path) -> None:
        """Returns empty list when backlog dir doesn't exist."""
        manager = BacklogManager(tmp_path / "nonexistent")
        files = manager.get_pending_files()
        assert files == []

    def test_custom_pattern(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Supports custom glob patterns."""
        backlog, _ = temp_dirs

        (backlog / "a.json").write_text("{}")
        (backlog / "b.csv").write_text("")

        json_files = manager.get_pending_files("*.json")
        csv_files = manager.get_pending_files("*.csv")

        assert len(json_files) == 1
        assert len(csv_files) == 1
        assert json_files[0].name == "a.json"
        assert csv_files[0].name == "b.csv"


class TestGetStats:
    """Tests for get_stats method."""

    def test_counts_pending_and_processed(
        self, manager: BacklogManager, temp_dirs: tuple[Path, Path]
    ) -> None:
        """Returns correct counts for pending and processed."""
        backlog, processed = temp_dirs
        processed.mkdir()

        (backlog / "pending1.json").write_text("{}")
        (backlog / "pending2.json").write_text("{}")
        (processed / "done1.json").write_text("{}")

        stats = manager.get_stats()

        assert stats["pending"] == 2
        assert stats["processed"] == 1

    def test_empty_directories(self, manager: BacklogManager) -> None:
        """Returns zeros when directories are empty."""
        stats = manager.get_stats()

        assert stats["pending"] == 0
        assert stats["processed"] == 0


class TestDefaultProcessedDir:
    """Tests for default processed directory behavior."""

    def test_uses_subdirectory_by_default(self, tmp_path: Path) -> None:
        """Default processed dir is backlog/processed."""
        backlog = tmp_path / "backlog"
        backlog.mkdir()

        manager = BacklogManager(backlog)

        assert manager.processed_dir == backlog / "processed"

    def test_custom_processed_dir(self, tmp_path: Path) -> None:
        """Accepts custom processed directory."""
        backlog = tmp_path / "backlog"
        custom = tmp_path / "archive"

        manager = BacklogManager(backlog, custom)

        assert manager.processed_dir == custom
