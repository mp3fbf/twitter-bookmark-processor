"""Directory Watcher for Twitter Bookmark Processor.

Monitors the backlog directory for new export files and returns
files that are pending processing (not yet in state).

Strategy:
- Use BacklogManager.get_pending_files() to list JSON files
- Filter out already-processed files using StateManager
- Return new files for processing
"""

from pathlib import Path

from src.core.backlog_manager import BacklogManager
from src.core.state_manager import StateManager


class DirectoryWatcher:
    """Watches backlog directory for new export files.

    Combines BacklogManager for file discovery with StateManager
    for deduplication - only returns files not yet processed.

    Attributes:
        backlog_manager: Manages backlog directory operations.
        state_manager: Tracks processed bookmark IDs.
    """

    def __init__(
        self,
        backlog_manager: BacklogManager,
        state_manager: StateManager,
    ):
        """Initialize DirectoryWatcher.

        Args:
            backlog_manager: BacklogManager instance for file discovery.
            state_manager: StateManager instance for processed tracking.
        """
        self.backlog_manager = backlog_manager
        self.state_manager = state_manager
        self._processed_files: set[str] = set()

    def get_new_files(self, pattern: str = "*.json") -> list[Path]:
        """Get list of new files not yet processed.

        Args:
            pattern: Glob pattern to match files (default: *.json).

        Returns:
            List of file paths that are new (not in processed_files set).
            Sorted by modification time (oldest first).
        """
        pending = self.backlog_manager.get_pending_files(pattern)
        new_files = [f for f in pending if str(f) not in self._processed_files]
        return new_files

    def mark_file_processed(self, file_path: str | Path) -> None:
        """Mark a file as processed so it won't be returned again.

        Args:
            file_path: Path to the file that was processed.
        """
        self._processed_files.add(str(file_path))

    def is_file_processed(self, file_path: str | Path) -> bool:
        """Check if a file has been processed by this watcher.

        Args:
            file_path: Path to check.

        Returns:
            True if file was already processed.
        """
        return str(file_path) in self._processed_files

    def reset(self) -> None:
        """Reset the processed files set.

        Useful for testing or when restarting the watcher.
        """
        self._processed_files.clear()

    def get_stats(self) -> dict[str, int]:
        """Get watcher statistics.

        Returns:
            Dictionary with counts of files tracked.
        """
        pending = self.backlog_manager.get_pending_files()
        new_files = self.get_new_files()

        return {
            "total_in_backlog": len(pending),
            "new_files": len(new_files),
            "processed_this_session": len(self._processed_files),
        }
