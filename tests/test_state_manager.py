"""Tests for StateManager."""

import json
from pathlib import Path

from src.core.bookmark import ProcessingStatus
from src.core.state_manager import StateManager


class TestStateManagerCreation:
    """Test StateManager initialization and file creation."""

    def test_state_manager_creates_file_if_missing(self, tmp_path: Path):
        """StateManager should create state file on first use if it doesn't exist."""
        state_file = tmp_path / "state.json"
        assert not state_file.exists()

        manager = StateManager(state_file)
        manager.load()

        assert state_file.exists()
        with open(state_file) as f:
            data = json.load(f)
        assert "processed" in data
        assert data["processed"] == {}

    def test_state_manager_creates_parent_directories(self, tmp_path: Path):
        """StateManager should create parent directories if they don't exist."""
        state_file = tmp_path / "deep" / "nested" / "state.json"
        assert not state_file.parent.exists()

        manager = StateManager(state_file)
        manager.load()

        assert state_file.exists()
        assert state_file.parent.exists()

    def test_state_manager_accepts_string_path(self, tmp_path: Path):
        """StateManager should accept both str and Path for state_file."""
        state_file = str(tmp_path / "state.json")
        manager = StateManager(state_file)
        manager.load()

        assert Path(state_file).exists()


class TestStateManagerLoad:
    """Test loading existing state files."""

    def test_state_manager_load_existing(self, tmp_path: Path):
        """StateManager should load existing state from file."""
        state_file = tmp_path / "state.json"
        existing_state = {
            "processed": {
                "123": {"status": "done", "processed_at": "2026-01-20T10:00:00"},
                "456": {"status": "error", "processed_at": "2026-01-20T11:00:00"},
            },
            "last_updated": "2026-01-20T11:00:00",
        }
        with open(state_file, "w") as f:
            json.dump(existing_state, f)

        manager = StateManager(state_file)
        state = manager.load()

        assert "123" in state["processed"]
        assert "456" in state["processed"]
        assert state["processed"]["123"]["status"] == "done"

    def test_state_manager_load_handles_missing_keys(self, tmp_path: Path):
        """StateManager should handle state files with missing keys gracefully."""
        state_file = tmp_path / "state.json"
        # Malformed state file without required keys
        with open(state_file, "w") as f:
            json.dump({}, f)

        manager = StateManager(state_file)
        state = manager.load()

        assert "processed" in state
        assert "last_updated" in state


class TestStateManagerIsProcessed:
    """Test checking if bookmarks have been processed."""

    def test_state_manager_is_processed_returns_true(self, tmp_path: Path):
        """is_processed should return True for processed bookmarks."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.mark_processed("123", ProcessingStatus.DONE)

        assert manager.is_processed("123") is True

    def test_state_manager_is_processed_returns_false(self, tmp_path: Path):
        """is_processed should return False for unprocessed bookmarks."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.load()

        assert manager.is_processed("unknown_id") is False

    def test_state_manager_is_processed_auto_loads(self, tmp_path: Path):
        """is_processed should auto-load state if not already loaded."""
        state_file = tmp_path / "state.json"
        existing_state = {
            "processed": {"123": {"status": "done", "processed_at": "2026-01-20"}},
            "last_updated": "2026-01-20",
        }
        with open(state_file, "w") as f:
            json.dump(existing_state, f)

        manager = StateManager(state_file)
        # Don't call load() explicitly
        assert manager.is_processed("123") is True


class TestStateManagerMarkProcessed:
    """Test marking bookmarks as processed."""

    def test_state_manager_mark_processed_with_status(self, tmp_path: Path):
        """mark_processed should save bookmark with correct status."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)

        manager.mark_processed("123", ProcessingStatus.DONE)

        assert manager.is_processed("123") is True
        assert manager.get_status("123") == ProcessingStatus.DONE

    def test_state_manager_mark_processed_with_output_path(self, tmp_path: Path):
        """mark_processed should save output_path when provided."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)

        manager.mark_processed(
            "123", ProcessingStatus.DONE, output_path="/notes/tweet-123.md"
        )

        # Reload to verify persistence
        with open(state_file) as f:
            data = json.load(f)
        assert data["processed"]["123"]["output_path"] == "/notes/tweet-123.md"

    def test_state_manager_mark_processed_with_error(self, tmp_path: Path):
        """mark_processed should save error message when provided."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)

        manager.mark_processed(
            "123", ProcessingStatus.ERROR, error="Content was deleted"
        )

        with open(state_file) as f:
            data = json.load(f)
        assert data["processed"]["123"]["status"] == "error"
        assert data["processed"]["123"]["error"] == "Content was deleted"

    def test_state_manager_mark_processed_persists_immediately(self, tmp_path: Path):
        """mark_processed should persist to file immediately."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.mark_processed("123", ProcessingStatus.DONE)

        # Create new manager to verify persistence
        manager2 = StateManager(state_file)
        assert manager2.is_processed("123") is True

    def test_state_manager_mark_processed_updates_timestamp(self, tmp_path: Path):
        """mark_processed should include processed_at timestamp."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.mark_processed("123", ProcessingStatus.DONE)

        with open(state_file) as f:
            data = json.load(f)
        assert "processed_at" in data["processed"]["123"]
        assert data["last_updated"] is not None


class TestStateManagerGetStatus:
    """Test getting bookmark status."""

    def test_get_status_returns_correct_status(self, tmp_path: Path):
        """get_status should return the correct ProcessingStatus."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.mark_processed("done_id", ProcessingStatus.DONE)
        manager.mark_processed("error_id", ProcessingStatus.ERROR)

        assert manager.get_status("done_id") == ProcessingStatus.DONE
        assert manager.get_status("error_id") == ProcessingStatus.ERROR

    def test_get_status_returns_none_for_unknown(self, tmp_path: Path):
        """get_status should return None for unknown bookmark IDs."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.load()

        assert manager.get_status("unknown") is None


class TestStateManagerStats:
    """Test statistics functionality."""

    def test_get_all_processed_ids(self, tmp_path: Path):
        """get_all_processed_ids should return all processed bookmark IDs."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.mark_processed("111", ProcessingStatus.DONE)
        manager.mark_processed("222", ProcessingStatus.DONE)
        manager.mark_processed("333", ProcessingStatus.ERROR)

        ids = manager.get_all_processed_ids()
        assert sorted(ids) == ["111", "222", "333"]

    def test_get_stats(self, tmp_path: Path):
        """get_stats should return correct counts by status."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.mark_processed("1", ProcessingStatus.DONE)
        manager.mark_processed("2", ProcessingStatus.DONE)
        manager.mark_processed("3", ProcessingStatus.DONE)
        manager.mark_processed("4", ProcessingStatus.ERROR)

        stats = manager.get_stats()
        assert stats["total"] == 4
        assert stats["done"] == 3
        assert stats["error"] == 1

    def test_get_stats_empty_state(self, tmp_path: Path):
        """get_stats should return zeros for empty state."""
        state_file = tmp_path / "state.json"
        manager = StateManager(state_file)
        manager.load()

        stats = manager.get_stats()
        assert stats["total"] == 0
        assert stats["done"] == 0
        assert stats["error"] == 0
