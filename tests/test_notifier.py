"""Tests for the notification module."""

import os
import subprocess
from unittest.mock import MagicMock, patch

from src.core.notifier import (
    get_notify_command,
    notify,
    notify_error,
    notify_processing,
    notify_success,
)


class TestGetNotifyCommand:
    """Tests for get_notify_command."""

    def test_returns_default_path(self):
        """Returns default path when env var not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove NOTIFY_CMD if present
            os.environ.pop("NOTIFY_CMD", None)
            result = get_notify_command()
            assert "notify" in result
            assert "projects/_scripts" in result

    def test_returns_env_var_when_set(self):
        """Returns NOTIFY_CMD env var when set."""
        with patch.dict(os.environ, {"NOTIFY_CMD": "/custom/notify"}):
            assert get_notify_command() == "/custom/notify"


class TestNotify:
    """Tests for notify function."""

    def test_notify_calls_command(self, tmp_path):
        """subprocess.run called with correct args."""
        # Create a fake notify script
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = notify("Test message", "info")

                assert result is True
                mock_run.assert_called_once()
                args = mock_run.call_args[0][0]
                assert args[0] == str(fake_notify)
                assert args[1] == "Test message"
                assert args[2] == "info"

    def test_notify_formats_success(self, tmp_path):
        """Success notification uses 'done' type."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = notify_success("123456", "TWEET")

                assert result is True
                args = mock_run.call_args[0][0]
                assert "123456" in args[1]
                assert "TWEET" in args[1]
                assert args[2] == "done"

    def test_notify_formats_error(self, tmp_path):
        """Error notification uses 'error' type."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = notify_error("123456", "Connection timeout")

                assert result is True
                args = mock_run.call_args[0][0]
                assert "123456" in args[1]
                assert "Connection timeout" in args[1]
                assert args[2] == "error"

    def test_notify_handles_missing_command(self):
        """Gracefully handles missing notify command."""
        with patch.dict(os.environ, {"NOTIFY_CMD": "/nonexistent/notify"}):
            result = notify("Test message", "info")
            assert result is False

    def test_notify_handles_command_failure(self, tmp_path):
        """Gracefully handles command returning non-zero."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 1")
        fake_notify.chmod(0o755)

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stderr="Error")
                result = notify("Test message", "info")
                assert result is False

    def test_notify_handles_timeout(self, tmp_path):
        """Gracefully handles command timeout."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired(cmd="notify", timeout=10)
                result = notify("Test message", "info")
                assert result is False

    def test_notify_handles_exception(self, tmp_path):
        """Gracefully handles unexpected exceptions."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.side_effect = OSError("Permission denied")
                result = notify("Test message", "info")
                assert result is False


class TestNotifyHelpers:
    """Tests for helper notification functions."""

    def test_notify_processing_uses_wait(self, tmp_path):
        """notify_processing uses 'wait' message type."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                result = notify_processing("123456")

                assert result is True
                args = mock_run.call_args[0][0]
                assert "123456" in args[1]
                assert args[2] == "wait"

    def test_notify_error_truncates_long_messages(self, tmp_path):
        """Error notification truncates messages over 100 chars."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        long_error = "A" * 200

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)
                notify_error("123456", long_error)

                args = mock_run.call_args[0][0]
                message = args[1]
                # Message should be truncated
                assert "..." in message
                # Full long_error should NOT be in message
                assert long_error not in message

    def test_notify_types(self, tmp_path):
        """All notification types are accepted."""
        fake_notify = tmp_path / "notify"
        fake_notify.write_text("#!/bin/bash\nexit 0")
        fake_notify.chmod(0o755)

        with patch.dict(os.environ, {"NOTIFY_CMD": str(fake_notify)}):
            with patch("src.core.notifier.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                for msg_type in ["info", "done", "error", "wait"]:
                    notify("Test", msg_type)
                    args = mock_run.call_args[0][0]
                    assert args[2] == msg_type
