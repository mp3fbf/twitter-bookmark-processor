"""Tests for main entry point module."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.pipeline import PipelineResult
from src.main import (
    DEFAULT_POLL_INTERVAL,
    create_argument_parser,
    main,
    print_stats,
    run_daemon,
    run_once,
)


@pytest.fixture
def temp_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a temporary workspace with backlog, output, and state dirs."""
    backlog = tmp_path / "data" / "backlog"
    backlog.mkdir(parents=True)
    output = tmp_path / "notes"
    output.mkdir()
    state_file = tmp_path / "data" / "state.json"
    return backlog, output, state_file


@pytest.fixture
def sample_export(temp_workspace: tuple[Path, Path, Path]) -> Path:
    """Create a sample Twillot export file."""
    backlog, _, _ = temp_workspace
    export_data = [
        {
            "tweet_id": "111111111",
            "url": "https://twitter.com/user1/status/111111111",
            "full_text": "First tweet content here",
            "screen_name": "user1",
            "username": "User One",
            "user_id": "100",
            "created_at": "2024-01-15T10:00:00Z",
        },
        {
            "tweet_id": "222222222",
            "url": "https://twitter.com/user2/status/222222222",
            "full_text": "Second tweet content here",
            "screen_name": "user2",
            "username": "User Two",
            "user_id": "200",
            "created_at": "2024-01-15T11:00:00Z",
        },
    ]
    export_path = backlog / "export.json"
    export_path.write_text(json.dumps(export_data))
    return export_path


class TestRunOnce:
    """Tests for run_once function."""

    @pytest.mark.asyncio
    async def test_once_processes_backlog(
        self, temp_workspace: tuple[Path, Path, Path], sample_export: Path
    ) -> None:
        """run_once processes files in backlog."""
        backlog, output, state_file = temp_workspace

        result = await run_once(
            backlog_dir=backlog,
            output_dir=output,
            state_file=state_file,
        )

        assert result.processed == 2
        assert result.skipped == 0
        assert result.failed == 0

    @pytest.mark.asyncio
    async def test_once_exits_after_processing(
        self, temp_workspace: tuple[Path, Path, Path], sample_export: Path
    ) -> None:
        """run_once returns after processing all files."""
        backlog, output, state_file = temp_workspace

        # First run
        result1 = await run_once(
            backlog_dir=backlog,
            output_dir=output,
            state_file=state_file,
        )
        assert result1.processed == 2

        # Second run should find no new files (original was archived)
        result2 = await run_once(
            backlog_dir=backlog,
            output_dir=output,
            state_file=state_file,
        )
        assert result2.processed == 0
        assert result2.skipped == 0

    @pytest.mark.asyncio
    async def test_once_reports_stats(
        self, temp_workspace: tuple[Path, Path, Path], sample_export: Path
    ) -> None:
        """run_once returns accurate statistics."""
        backlog, output, state_file = temp_workspace

        result = await run_once(
            backlog_dir=backlog,
            output_dir=output,
            state_file=state_file,
        )

        assert isinstance(result, PipelineResult)
        assert result.processed >= 0
        assert result.skipped >= 0
        assert result.failed >= 0
        assert isinstance(result.errors, list)

    @pytest.mark.asyncio
    async def test_once_creates_notes(
        self, temp_workspace: tuple[Path, Path, Path], sample_export: Path
    ) -> None:
        """run_once generates Obsidian notes."""
        backlog, output, state_file = temp_workspace

        await run_once(
            backlog_dir=backlog,
            output_dir=output,
            state_file=state_file,
        )

        # Check that notes were created
        notes = list(output.glob("*.md"))
        assert len(notes) == 2

    @pytest.mark.asyncio
    async def test_once_archives_processed_files(
        self, temp_workspace: tuple[Path, Path, Path], sample_export: Path
    ) -> None:
        """run_once archives processed export files."""
        backlog, output, state_file = temp_workspace

        await run_once(
            backlog_dir=backlog,
            output_dir=output,
            state_file=state_file,
        )

        # Original file should be gone
        assert not sample_export.exists()

        # Should be in processed dir
        processed_dir = backlog / "processed"
        assert processed_dir.exists()
        archived = list(processed_dir.glob("*export.json"))
        assert len(archived) == 1

    @pytest.mark.asyncio
    async def test_once_handles_empty_backlog(
        self, temp_workspace: tuple[Path, Path, Path]
    ) -> None:
        """run_once handles empty backlog gracefully."""
        backlog, output, state_file = temp_workspace

        result = await run_once(
            backlog_dir=backlog,
            output_dir=output,
            state_file=state_file,
        )

        assert result.processed == 0
        assert result.skipped == 0
        assert result.failed == 0


class TestPrintStats:
    """Tests for print_stats function."""

    def test_prints_basic_stats(self, capsys) -> None:
        """print_stats outputs processing counts."""
        result = PipelineResult(processed=5, skipped=2, failed=1)

        print_stats(result)

        captured = capsys.readouterr()
        assert "Processed: 5" in captured.out
        assert "Skipped:   2" in captured.out
        assert "Failed:    1" in captured.out

    def test_prints_errors(self, capsys) -> None:
        """print_stats shows error messages."""
        result = PipelineResult(
            processed=0,
            failed=2,
            errors=["Error 1: something went wrong", "Error 2: another issue"],
        )

        print_stats(result)

        captured = capsys.readouterr()
        assert "Errors (2):" in captured.out
        assert "Error 1: something went wrong" in captured.out
        assert "Error 2: another issue" in captured.out

    def test_truncates_many_errors(self, capsys) -> None:
        """print_stats limits error display to 10."""
        errors = [f"Error {i}" for i in range(15)]
        result = PipelineResult(failed=15, errors=errors)

        print_stats(result)

        captured = capsys.readouterr()
        assert "Error 0" in captured.out
        assert "Error 9" in captured.out
        assert "Error 10" not in captured.out
        assert "... and 5 more" in captured.out


class TestMain:
    """Tests for main entry point."""

    @pytest.fixture
    def mock_env(self, tmp_path: Path) -> dict:
        """Set up environment for main."""
        backlog = tmp_path / "data" / "backlog"
        backlog.mkdir(parents=True)
        output = tmp_path / "notes"
        output.mkdir()

        return {
            "ANTHROPIC_API_KEY": "test-key",
            "TWITTER_OUTPUT_DIR": str(output),
            "TWITTER_STATE_FILE": str(tmp_path / "data" / "state.json"),
        }

    def test_once_flag_recognized(self, mock_env: dict, tmp_path: Path) -> None:
        """--once flag triggers once mode."""
        with patch.dict(os.environ, mock_env, clear=False):
            with patch("src.main.run_once") as mock_run:
                mock_run.return_value = PipelineResult()
                with patch("src.main.Path") as mock_path:
                    mock_path.return_value = tmp_path / "data" / "backlog"

                    exit_code = main(["--once"])

                assert exit_code == 0
                mock_run.assert_called_once()

    def test_missing_api_key_fails(self) -> None:
        """Missing API key returns error."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove ANTHROPIC_API_KEY if present
            os.environ.pop("ANTHROPIC_API_KEY", None)

            from src.core.config import reset_config
            reset_config()

            exit_code = main(["--once"])

            assert exit_code == 1

    def test_daemon_mode_starts_daemon(self, mock_env: dict) -> None:
        """Running without --once starts daemon mode."""
        with patch.dict(os.environ, mock_env, clear=False):
            with patch("src.main.run_daemon") as mock_daemon:
                # Make daemon exit immediately
                mock_daemon.return_value = None

                exit_code = main([])

                # Daemon mode returns 0 on clean exit
                assert exit_code == 0
                mock_daemon.assert_called_once()

    def test_returns_zero_on_success(
        self, mock_env: dict, tmp_path: Path
    ) -> None:
        """Returns 0 when processing succeeds with no failures."""
        with patch.dict(os.environ, mock_env, clear=False):
            with patch("src.main.run_once") as mock_run:
                mock_run.return_value = PipelineResult(processed=2, failed=0)

                exit_code = main(["--once"])

                assert exit_code == 0

    def test_returns_one_on_failures(
        self, mock_env: dict, tmp_path: Path
    ) -> None:
        """Returns 1 when there are processing failures."""
        with patch.dict(os.environ, mock_env, clear=False):
            with patch("src.main.run_once") as mock_run:
                mock_run.return_value = PipelineResult(processed=1, failed=1)

                exit_code = main(["--once"])

                assert exit_code == 1


class TestRunDaemon:
    """Tests for run_daemon function."""

    @pytest.fixture
    def daemon_workspace(self, tmp_path: Path) -> tuple[Path, Path, Path]:
        """Create workspace for daemon tests."""
        backlog = tmp_path / "backlog"
        backlog.mkdir()
        output = tmp_path / "output"
        output.mkdir()
        state_file = tmp_path / "state.json"
        return backlog, output, state_file

    @pytest.mark.asyncio
    async def test_daemon_runs_loop(
        self, daemon_workspace: tuple[Path, Path, Path]
    ) -> None:
        """run_daemon polls repeatedly until shutdown."""
        backlog, output, state_file = daemon_workspace
        call_count = 0

        async def mock_run_once(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # Stop after 2 iterations
            if call_count >= 2:
                # Trigger shutdown
                import src.main as main_module
                if main_module._shutdown_event:
                    main_module._shutdown_event.set()
            return PipelineResult(processed=1)

        with patch("src.main.run_once", side_effect=mock_run_once):
            await run_daemon(
                backlog_dir=backlog,
                output_dir=output,
                state_file=state_file,
                poll_interval=1,  # Short interval for testing
            )

        assert call_count >= 2  # Loop ran multiple times

    @pytest.mark.asyncio
    async def test_daemon_interval_2min_default(self) -> None:
        """Default poll interval is 2 minutes (120 seconds)."""
        assert DEFAULT_POLL_INTERVAL == 120

    @pytest.mark.asyncio
    async def test_daemon_graceful_shutdown(
        self, daemon_workspace: tuple[Path, Path, Path]
    ) -> None:
        """Daemon handles SIGTERM gracefully."""
        backlog, output, state_file = daemon_workspace
        shutdown_completed = False

        async def slow_run_once(*args, **kwargs):
            # Simulate a slow processing job
            await asyncio.sleep(0.5)
            return PipelineResult(processed=1)

        async def trigger_shutdown():
            # Wait a bit then trigger shutdown
            await asyncio.sleep(0.1)
            import src.main as main_module
            if main_module._shutdown_event:
                main_module._shutdown_event.set()

        with patch("src.main.run_once", side_effect=slow_run_once):
            # Run daemon with shutdown trigger
            daemon_task = asyncio.create_task(
                run_daemon(
                    backlog_dir=backlog,
                    output_dir=output,
                    state_file=state_file,
                    poll_interval=60,  # Won't reach this
                )
            )
            shutdown_task = asyncio.create_task(trigger_shutdown())

            # Wait for both
            await asyncio.gather(daemon_task, shutdown_task)
            shutdown_completed = True

        assert shutdown_completed  # Daemon shut down cleanly

    @pytest.mark.asyncio
    async def test_daemon_waits_for_in_progress_jobs(
        self, daemon_workspace: tuple[Path, Path, Path]
    ) -> None:
        """Daemon waits for in-progress jobs on shutdown."""
        backlog, output, state_file = daemon_workspace
        job_completed = False

        async def slow_run_once(*args, **kwargs):
            nonlocal job_completed
            await asyncio.sleep(0.3)
            job_completed = True
            return PipelineResult(processed=1)

        async def trigger_shutdown():
            # Trigger shutdown while job is running
            await asyncio.sleep(0.1)
            import src.main as main_module
            if main_module._shutdown_event:
                main_module._shutdown_event.set()

        with patch("src.main.run_once", side_effect=slow_run_once):
            daemon_task = asyncio.create_task(
                run_daemon(
                    backlog_dir=backlog,
                    output_dir=output,
                    state_file=state_file,
                    poll_interval=60,
                )
            )
            shutdown_task = asyncio.create_task(trigger_shutdown())

            await asyncio.gather(daemon_task, shutdown_task)

        # Job should have completed despite shutdown request
        assert job_completed


class TestCLIArguments:
    """Tests for CLI argument parsing."""

    def test_cli_once_flag(self) -> None:
        """--once flag is recognized and stored."""
        parser = create_argument_parser()
        args = parser.parse_args(["--once"])
        assert args.once is True

    def test_cli_once_flag_default(self) -> None:
        """--once flag defaults to False."""
        parser = create_argument_parser()
        args = parser.parse_args([])
        assert args.once is False

    def test_cli_port_flag(self) -> None:
        """--port flag accepts custom port."""
        parser = create_argument_parser()
        args = parser.parse_args(["--port", "8766"])
        assert args.port == 8766

    def test_cli_port_flag_custom(self) -> None:
        """--port flag with custom value."""
        parser = create_argument_parser()
        args = parser.parse_args(["--port", "9000"])
        assert args.port == 9000

    def test_cli_port_default(self) -> None:
        """--port flag defaults to 8766."""
        parser = create_argument_parser()
        args = parser.parse_args([])
        assert args.port == 8766

    def test_cli_verbose_flag(self) -> None:
        """--verbose flag is recognized."""
        parser = create_argument_parser()
        args = parser.parse_args(["--verbose"])
        assert args.verbose is True

    def test_cli_verbose_flag_short(self) -> None:
        """-v is alias for --verbose."""
        parser = create_argument_parser()
        args = parser.parse_args(["-v"])
        assert args.verbose is True

    def test_cli_verbose_default(self) -> None:
        """--verbose defaults to False."""
        parser = create_argument_parser()
        args = parser.parse_args([])
        assert args.verbose is False

    def test_cli_help(self, capsys) -> None:
        """--help shows usage information."""
        parser = create_argument_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "twitter-bookmark-processor" in captured.out
        assert "--once" in captured.out
        assert "--port" in captured.out
        assert "--verbose" in captured.out

    def test_cli_combined_flags(self) -> None:
        """Multiple flags can be combined."""
        parser = create_argument_parser()
        args = parser.parse_args(["--once", "--verbose", "--port", "9999"])
        assert args.once is True
        assert args.verbose is True
        assert args.port == 9999

    def test_verbose_flag_increases_log_level(self, tmp_path: Path) -> None:
        """--verbose flag causes DEBUG log level to be used."""
        mock_env = {
            "ANTHROPIC_API_KEY": "test-key",
            "TWITTER_OUTPUT_DIR": str(tmp_path / "notes"),
            "TWITTER_STATE_FILE": str(tmp_path / "state.json"),
        }
        (tmp_path / "notes").mkdir()

        with patch.dict(os.environ, mock_env, clear=False):
            with patch("src.main.run_once") as mock_run:
                mock_run.return_value = PipelineResult()
                with patch("src.main.setup_logging") as mock_setup:
                    main(["--once", "--verbose"])

                    # setup_logging should have been called with DEBUG
                    mock_setup.assert_called_once_with("DEBUG")
