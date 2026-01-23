"""Backlog Manager for Twitter Bookmark Processor.

Manages processed export files by archiving them to a processed directory
and cleaning up old files after a configurable retention period.

Strategy:
- After processing: move files to data/backlog/processed/
- Keep last 30 days of processed files by default
- Clean older files automatically
"""

import shutil
from datetime import datetime, timedelta
from pathlib import Path


class BacklogManager:
    """Manages export file lifecycle after processing.

    Moves processed export files to an archive directory and cleans up
    files older than the retention period.

    Attributes:
        backlog_dir: Directory containing unprocessed export files.
        processed_dir: Directory for archived processed files.
        retention_days: Number of days to keep processed files (default 30).
    """

    def __init__(
        self,
        backlog_dir: str | Path,
        processed_dir: str | Path | None = None,
        retention_days: int = 30,
    ):
        """Initialize BacklogManager.

        Args:
            backlog_dir: Path to the backlog directory with export files.
            processed_dir: Path to archive directory. Defaults to backlog_dir/processed.
            retention_days: Days to retain processed files before cleanup.
        """
        self.backlog_dir = Path(backlog_dir)
        self.processed_dir = (
            Path(processed_dir)
            if processed_dir is not None
            else self.backlog_dir / "processed"
        )
        self.retention_days = retention_days

    def archive_file(self, file_path: str | Path) -> Path | None:
        """Move a processed file to the archive directory.

        Args:
            file_path: Path to the file to archive.

        Returns:
            Path to the archived file, or None if source doesn't exist.
        """
        source = Path(file_path)

        if not source.exists():
            return None

        # Ensure processed directory exists
        self.processed_dir.mkdir(parents=True, exist_ok=True)

        # Generate unique destination name with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_name = f"{timestamp}_{source.name}"
        dest = self.processed_dir / dest_name

        # Handle collision (rare but possible)
        counter = 1
        while dest.exists():
            dest_name = f"{timestamp}_{counter}_{source.name}"
            dest = self.processed_dir / dest_name
            counter += 1

        shutil.move(str(source), str(dest))
        return dest

    def clean_old_files(self) -> list[Path]:
        """Remove processed files older than retention period.

        Returns:
            List of paths that were deleted.
        """
        if not self.processed_dir.exists():
            return []

        cutoff = datetime.now() - timedelta(days=self.retention_days)
        deleted: list[Path] = []

        for file_path in self.processed_dir.iterdir():
            if file_path.is_file() and file_path.name != ".gitkeep":
                mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
                if mtime < cutoff:
                    file_path.unlink()
                    deleted.append(file_path)

        return deleted

    def get_pending_files(self, pattern: str = "*.json") -> list[Path]:
        """Get list of unprocessed export files in backlog.

        Args:
            pattern: Glob pattern to match files (default: *.json).

        Returns:
            List of file paths sorted by modification time (oldest first).
        """
        if not self.backlog_dir.exists():
            return []

        files = [
            f
            for f in self.backlog_dir.glob(pattern)
            if f.is_file() and f.name != ".gitkeep"
        ]

        # Sort by modification time (oldest first for FIFO processing)
        return sorted(files, key=lambda f: f.stat().st_mtime)

    def get_stats(self) -> dict[str, int]:
        """Get backlog statistics.

        Returns:
            Dictionary with counts of pending and processed files.
        """
        pending = len(self.get_pending_files())

        processed = 0
        if self.processed_dir.exists():
            processed = sum(
                1
                for f in self.processed_dir.iterdir()
                if f.is_file() and f.name != ".gitkeep"
            )

        return {
            "pending": pending,
            "processed": processed,
        }
