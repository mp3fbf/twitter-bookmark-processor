"""Deduplicator for Twitter Bookmark Processor.

Detects and tracks duplicate bookmarks across multiple exports.
Uses StateManager as the source of truth for processed bookmark IDs.

Primary key: Tweet ID - same tweet ID in different exports = duplicate.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.core.state_manager import StateManager

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark

logger = logging.getLogger(__name__)


@dataclass
class DeduplicationStats:
    """Statistics from deduplication operations.

    Attributes:
        total_checked: Total number of bookmarks checked
        duplicates_found: Number of duplicates detected and skipped
        unique_bookmarks: Number of unique (non-duplicate) bookmarks
    """

    total_checked: int = 0
    duplicates_found: int = 0
    unique_bookmarks: int = 0

    @property
    def duplicate_rate(self) -> float:
        """Calculate duplicate rate as a percentage.

        Returns:
            Percentage of duplicates (0.0 to 100.0), or 0.0 if nothing checked.
        """
        if self.total_checked == 0:
            return 0.0
        return (self.duplicates_found / self.total_checked) * 100.0


class Deduplicator:
    """Detects and filters duplicate bookmarks.

    Uses StateManager to check if bookmark IDs have already been processed.
    Tracks statistics for visibility into deduplication effectiveness.

    Example:
        >>> dedup = Deduplicator(state_manager)
        >>> unique = dedup.filter_duplicates(bookmarks)
        >>> stats = dedup.get_stats()
        >>> print(f"Skipped {stats.duplicates_found} duplicates")
    """

    def __init__(self, state_manager: StateManager):
        """Initialize Deduplicator with a StateManager.

        Args:
            state_manager: StateManager instance for checking processed IDs.
        """
        self._state_manager = state_manager
        self._stats = DeduplicationStats()

    def is_duplicate(self, bookmark: "Bookmark") -> bool:
        """Check if a bookmark is a duplicate (already processed).

        Args:
            bookmark: The bookmark to check.

        Returns:
            True if the bookmark has already been processed.
        """
        is_dup = self._state_manager.is_processed(bookmark.id)
        if is_dup:
            logger.info(
                "Duplicate detected: bookmark %s already processed",
                bookmark.id,
            )
        return is_dup

    def filter_duplicates(
        self,
        bookmarks: list["Bookmark"],
    ) -> list["Bookmark"]:
        """Filter out duplicate bookmarks from a list.

        Logs each skipped duplicate and updates internal statistics.

        Args:
            bookmarks: List of bookmarks to filter.

        Returns:
            List of unique (non-duplicate) bookmarks.
        """
        unique: list["Bookmark"] = []

        for bookmark in bookmarks:
            self._stats.total_checked += 1

            if self.is_duplicate(bookmark):
                self._stats.duplicates_found += 1
                logger.debug(
                    "Skipping duplicate bookmark: %s (@%s)",
                    bookmark.id,
                    bookmark.author_username,
                )
            else:
                self._stats.unique_bookmarks += 1
                unique.append(bookmark)

        logger.info(
            "Deduplication complete: %d total, %d duplicates, %d unique",
            self._stats.total_checked,
            self._stats.duplicates_found,
            self._stats.unique_bookmarks,
        )

        return unique

    def get_stats(self) -> DeduplicationStats:
        """Get deduplication statistics.

        Returns:
            DeduplicationStats with counts and rates.
        """
        return self._stats

    def reset_stats(self) -> None:
        """Reset statistics to zero.

        Call this between batches if you want per-batch stats.
        """
        self._stats = DeduplicationStats()
