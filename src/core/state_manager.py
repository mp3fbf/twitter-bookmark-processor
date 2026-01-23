"""State Manager for Twitter Bookmark Processor.

Tracks which bookmarks have been processed to avoid reprocessing.
State is persisted to a JSON file for durability across restarts.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.bookmark import ProcessingStatus


class StateManager:
    """Manages processing state persistence.

    Tracks bookmark IDs and their processing status in a JSON file.
    Creates the state file if it doesn't exist on first use.

    Attributes:
        state_file: Path to the JSON state file.
    """

    def __init__(self, state_file: str | Path):
        """Initialize StateManager with a state file path.

        Args:
            state_file: Path to the JSON file for state persistence.
        """
        self.state_file = Path(state_file)
        self._state: dict[str, Any] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load state from file if not already loaded."""
        if self._loaded:
            return
        self.load()

    def load(self) -> dict[str, Any]:
        """Load state from JSON file.

        Creates the file with empty state if it doesn't exist.

        Returns:
            The loaded state dictionary.
        """
        if not self.state_file.exists():
            self._state = {"processed": {}, "last_updated": None}
            self._loaded = True
            self.save()
            return self._state

        with open(self.state_file, encoding="utf-8") as f:
            self._state = json.load(f)

        # Ensure required keys exist
        if "processed" not in self._state:
            self._state["processed"] = {}
        if "last_updated" not in self._state:
            self._state["last_updated"] = None

        self._loaded = True
        return self._state

    def save(self) -> None:
        """Save current state to JSON file.

        Creates parent directories if they don't exist.
        """
        self._state["last_updated"] = datetime.now().isoformat()

        # Ensure parent directory exists
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self._state, f, indent=2, ensure_ascii=False)

    def is_processed(self, bookmark_id: str) -> bool:
        """Check if a bookmark has been processed.

        Args:
            bookmark_id: The tweet/bookmark ID to check.

        Returns:
            True if the bookmark has been processed (status DONE or ERROR).
        """
        self._ensure_loaded()
        return bookmark_id in self._state["processed"]

    def get_status(self, bookmark_id: str) -> ProcessingStatus | None:
        """Get the processing status of a bookmark.

        Args:
            bookmark_id: The tweet/bookmark ID to check.

        Returns:
            The ProcessingStatus if found, None otherwise.
        """
        self._ensure_loaded()
        entry = self._state["processed"].get(bookmark_id)
        if entry is None:
            return None
        return ProcessingStatus(entry["status"])

    def mark_processed(
        self,
        bookmark_id: str,
        status: ProcessingStatus,
        *,
        output_path: str | None = None,
        error: str | None = None,
    ) -> None:
        """Mark a bookmark as processed with the given status.

        Args:
            bookmark_id: The tweet/bookmark ID to mark.
            status: The processing status to record.
            output_path: Path to the generated output file (for DONE status).
            error: Error message (for ERROR status).
        """
        self._ensure_loaded()

        entry: dict[str, Any] = {
            "status": status.value,
            "processed_at": datetime.now().isoformat(),
        }

        if output_path is not None:
            entry["output_path"] = output_path

        if error is not None:
            entry["error"] = error

        self._state["processed"][bookmark_id] = entry
        self.save()

    def get_all_processed_ids(self) -> list[str]:
        """Get all processed bookmark IDs.

        Returns:
            List of bookmark IDs that have been processed.
        """
        self._ensure_loaded()
        return list(self._state["processed"].keys())

    def get_stats(self) -> dict[str, int]:
        """Get processing statistics.

        Returns:
            Dictionary with counts by status.
        """
        self._ensure_loaded()
        stats: dict[str, int] = {
            "total": 0,
            "done": 0,
            "error": 0,
        }

        for entry in self._state["processed"].values():
            stats["total"] += 1
            status = entry.get("status", "")
            if status == ProcessingStatus.DONE.value:
                stats["done"] += 1
            elif status == ProcessingStatus.ERROR.value:
                stats["error"] += 1

        return stats
