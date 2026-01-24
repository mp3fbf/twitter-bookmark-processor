"""Daily summary module for Twitter Bookmark Processor.

Generates processing summaries with statistics for notification.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

from src.core.bookmark import ContentType
from src.core.notifier import notify

logger = logging.getLogger(__name__)


@dataclass
class ProcessingSummary:
    """Summary of bookmark processing statistics.

    Tracks counts by content type, errors, and timing information
    for generating daily summary notifications.
    """

    # Counts by content type
    counts_by_type: dict[ContentType, int] = field(default_factory=dict)

    # Error tracking
    errors: list[str] = field(default_factory=list)
    failed_count: int = 0

    # Timing
    total_duration_seconds: float = 0.0
    start_time: datetime | None = None
    end_time: datetime | None = None

    @property
    def total_processed(self) -> int:
        """Total bookmarks processed (sum of all content types)."""
        return sum(self.counts_by_type.values())

    @property
    def average_duration(self) -> float:
        """Average processing duration per bookmark in seconds.

        Returns 0.0 if no bookmarks were processed.
        """
        if self.total_processed == 0:
            return 0.0
        return self.total_duration_seconds / self.total_processed


def create_summary(
    processed_by_type: dict[ContentType, int] | None = None,
    errors: list[str] | None = None,
    duration_seconds: float = 0.0,
) -> ProcessingSummary:
    """Create a ProcessingSummary from processing results.

    Args:
        processed_by_type: Dict mapping ContentType to count processed.
        errors: List of error messages from failed processing.
        duration_seconds: Total processing time in seconds.

    Returns:
        ProcessingSummary with all statistics populated.
    """
    summary = ProcessingSummary(
        counts_by_type=processed_by_type or {},
        errors=errors or [],
        failed_count=len(errors) if errors else 0,
        total_duration_seconds=duration_seconds,
    )
    return summary


def format_summary(summary: ProcessingSummary) -> str:
    """Format a ProcessingSummary as a human-readable string.

    Args:
        summary: The summary to format.

    Returns:
        Formatted string suitable for notification.
    """
    lines = ["📊 Daily Summary"]

    # Total counts
    total = summary.total_processed
    lines.append(f"Total: {total} processed")

    # Breakdown by type (only show non-zero)
    if summary.counts_by_type:
        type_parts = []
        for content_type, count in sorted(
            summary.counts_by_type.items(),
            key=lambda x: x[1],
            reverse=True,
        ):
            if count > 0:
                type_parts.append(f"{content_type.value}: {count}")
        if type_parts:
            lines.append(", ".join(type_parts))

    # Errors
    if summary.failed_count > 0:
        lines.append(f"❌ {summary.failed_count} errors")

    # Average duration
    avg_duration = summary.average_duration
    if avg_duration > 0:
        lines.append(f"⏱️ Avg: {avg_duration:.1f}s/item")

    return "\n".join(lines)


def send_daily_summary(summary: ProcessingSummary) -> bool:
    """Send daily summary via notification system.

    Args:
        summary: The processing summary to send.

    Returns:
        True if notification was sent successfully.
    """
    message = format_summary(summary)

    # Use 'done' type if no errors, 'error' type if there were failures
    msg_type = "error" if summary.failed_count > 0 else "done"

    return notify(message, msg_type)
