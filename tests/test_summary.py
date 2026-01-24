"""Tests for the summary module."""

import os
from unittest.mock import patch

from src.core.bookmark import ContentType
from src.core.summary import (
    ProcessingSummary,
    create_summary,
    format_summary,
    send_daily_summary,
)


class TestProcessingSummary:
    """Tests for ProcessingSummary dataclass."""

    def test_summary_counts_by_type(self):
        """Counts by type are tracked correctly."""
        summary = ProcessingSummary(
            counts_by_type={
                ContentType.TWEET: 10,
                ContentType.VIDEO: 5,
                ContentType.THREAD: 3,
                ContentType.LINK: 2,
            }
        )

        assert summary.counts_by_type[ContentType.TWEET] == 10
        assert summary.counts_by_type[ContentType.VIDEO] == 5
        assert summary.counts_by_type[ContentType.THREAD] == 3
        assert summary.counts_by_type[ContentType.LINK] == 2
        assert summary.total_processed == 20

    def test_summary_includes_errors(self):
        """Errors are listed in summary."""
        errors = [
            "Bookmark 123: Connection timeout",
            "Bookmark 456: Content deleted",
            "Bookmark 789: Rate limited",
        ]
        summary = ProcessingSummary(
            errors=errors,
            failed_count=3,
        )

        assert len(summary.errors) == 3
        assert "Connection timeout" in summary.errors[0]
        assert "Content deleted" in summary.errors[1]
        assert "Rate limited" in summary.errors[2]
        assert summary.failed_count == 3

    def test_summary_average_duration(self):
        """Average duration is calculated correctly."""
        summary = ProcessingSummary(
            counts_by_type={
                ContentType.TWEET: 5,
                ContentType.VIDEO: 5,
            },
            total_duration_seconds=100.0,
        )

        # 10 items, 100 seconds = 10 seconds average
        assert summary.total_processed == 10
        assert summary.average_duration == 10.0

    def test_average_duration_zero_processed(self):
        """Average duration is 0 when nothing processed."""
        summary = ProcessingSummary()
        assert summary.average_duration == 0.0

    def test_total_processed_empty(self):
        """Total processed is 0 when no counts."""
        summary = ProcessingSummary()
        assert summary.total_processed == 0

    def test_defaults(self):
        """Default values are correct."""
        summary = ProcessingSummary()
        assert summary.counts_by_type == {}
        assert summary.errors == []
        assert summary.failed_count == 0
        assert summary.total_duration_seconds == 0.0
        assert summary.start_time is None
        assert summary.end_time is None


class TestCreateSummary:
    """Tests for create_summary function."""

    def test_create_summary_with_all_params(self):
        """Creates summary with all parameters."""
        summary = create_summary(
            processed_by_type={
                ContentType.TWEET: 10,
                ContentType.VIDEO: 5,
            },
            errors=["Error 1", "Error 2"],
            duration_seconds=120.0,
        )

        assert summary.counts_by_type[ContentType.TWEET] == 10
        assert summary.counts_by_type[ContentType.VIDEO] == 5
        assert len(summary.errors) == 2
        assert summary.failed_count == 2
        assert summary.total_duration_seconds == 120.0

    def test_create_summary_defaults(self):
        """Creates summary with default values."""
        summary = create_summary()

        assert summary.counts_by_type == {}
        assert summary.errors == []
        assert summary.failed_count == 0
        assert summary.total_duration_seconds == 0.0

    def test_create_summary_none_params(self):
        """Handles None parameters gracefully."""
        summary = create_summary(
            processed_by_type=None,
            errors=None,
        )

        assert summary.counts_by_type == {}
        assert summary.errors == []
        assert summary.failed_count == 0


class TestFormatSummary:
    """Tests for format_summary function."""

    def test_format_basic_summary(self):
        """Formats basic summary with counts."""
        summary = ProcessingSummary(
            counts_by_type={
                ContentType.TWEET: 10,
                ContentType.VIDEO: 5,
            },
        )

        formatted = format_summary(summary)

        assert "Daily Summary" in formatted
        assert "Total: 15 processed" in formatted
        assert "tweet: 10" in formatted
        assert "video: 5" in formatted

    def test_format_summary_with_errors(self):
        """Formats summary showing error count."""
        summary = ProcessingSummary(
            counts_by_type={ContentType.TWEET: 5},
            errors=["Error 1", "Error 2"],
            failed_count=2,
        )

        formatted = format_summary(summary)

        assert "2 errors" in formatted

    def test_format_summary_with_duration(self):
        """Formats summary showing average duration."""
        summary = ProcessingSummary(
            counts_by_type={ContentType.TWEET: 10},
            total_duration_seconds=50.0,
        )

        formatted = format_summary(summary)

        assert "Avg: 5.0s/item" in formatted

    def test_format_summary_empty(self):
        """Formats empty summary."""
        summary = ProcessingSummary()
        formatted = format_summary(summary)

        assert "Daily Summary" in formatted
        assert "Total: 0 processed" in formatted

    def test_format_orders_by_count(self):
        """Types are ordered by count descending."""
        summary = ProcessingSummary(
            counts_by_type={
                ContentType.LINK: 1,
                ContentType.TWEET: 100,
                ContentType.VIDEO: 10,
            },
        )

        formatted = format_summary(summary)
        lines = formatted.split("\n")

        # Find the line with type breakdown
        type_line = None
        for line in lines:
            if "tweet" in line:
                type_line = line
                break

        assert type_line is not None
        # tweet should come before video, video before link
        assert type_line.index("tweet") < type_line.index("video")
        assert type_line.index("video") < type_line.index("link")


class TestSendDailySummary:
    """Tests for send_daily_summary function."""

    def test_send_success_summary(self, tmp_path):
        """Sends success notification for no-error summary."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        summary = ProcessingSummary(
            counts_by_type={ContentType.TWEET: 10},
        )

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.summary.notify") as mock_notify:
                mock_notify.return_value = True
                result = send_daily_summary(summary)

                assert result is True
                mock_notify.assert_called_once()
                args, kwargs = mock_notify.call_args
                assert "Daily Summary" in args[0]
                assert args[1] == "done"

    def test_send_error_summary(self, tmp_path):
        """Sends error notification for summary with failures."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        summary = ProcessingSummary(
            counts_by_type={ContentType.TWEET: 5},
            errors=["Error 1"],
            failed_count=1,
        )

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.summary.notify") as mock_notify:
                mock_notify.return_value = True
                result = send_daily_summary(summary)

                assert result is True
                mock_notify.assert_called_once()
                args, kwargs = mock_notify.call_args
                assert args[1] == "error"

    def test_send_returns_notify_result(self, tmp_path):
        """Returns result from notify function."""
        summary = ProcessingSummary()

        with patch("src.core.summary.notify") as mock_notify:
            mock_notify.return_value = False
            result = send_daily_summary(summary)
            assert result is False

            mock_notify.return_value = True
            result = send_daily_summary(summary)
            assert result is True
