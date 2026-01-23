"""Main Entry Point for Twitter Bookmark Processor.

Processes Twitter/X bookmarks from Twillot exports into Obsidian notes.

Usage:
    python -m src.main --once     # Process backlog once and exit
    python -m src.main            # Run as daemon (polling mode)
"""

import asyncio
import signal
import sys
from pathlib import Path

from src.core.backlog_manager import BacklogManager
from src.core.config import get_config
from src.core.logger import get_logger, setup_logging
from src.core.pipeline import Pipeline, PipelineResult
from src.core.state_manager import StateManager
from src.core.watcher import DirectoryWatcher

logger = get_logger(__name__)

# Default polling interval in seconds (2 minutes)
DEFAULT_POLL_INTERVAL = 120

# Shutdown event for graceful termination
_shutdown_event: asyncio.Event | None = None


async def run_once(
    backlog_dir: Path,
    output_dir: Path,
    state_file: Path,
) -> PipelineResult:
    """Process backlog once and return results.

    Finds all pending export files in backlog_dir, processes them
    through the pipeline, and archives processed files.

    Args:
        backlog_dir: Directory containing Twillot export files.
        output_dir: Directory for generated Obsidian notes.
        state_file: Path to JSON state persistence file.

    Returns:
        Aggregated PipelineResult with processing statistics.
    """
    # Initialize components
    backlog_manager = BacklogManager(backlog_dir)
    state_manager = StateManager(state_file)
    watcher = DirectoryWatcher(backlog_manager, state_manager)
    pipeline = Pipeline(output_dir, state_file)

    # Get pending files
    pending_files = watcher.get_new_files()
    logger.info("Found %d pending files", len(pending_files))

    # Aggregate results
    total_result = PipelineResult()

    # Process each file
    for export_file in pending_files:
        logger.info("Processing %s", export_file)
        try:
            result = await pipeline.process_export(export_file)

            # Aggregate stats
            total_result.processed += result.processed
            total_result.skipped += result.skipped
            total_result.failed += result.failed
            total_result.errors.extend(result.errors)

            # Archive processed file
            archived = backlog_manager.archive_file(export_file)
            if archived:
                logger.info("Archived to %s", archived)
                watcher.mark_file_processed(export_file)

        except Exception as e:
            logger.error("Failed to process %s: %s", export_file, e)
            total_result.failed += 1
            total_result.errors.append(f"File {export_file}: {e}")

    return total_result


async def run_daemon(
    backlog_dir: Path,
    output_dir: Path,
    state_file: Path,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
) -> None:
    """Run as a daemon, continuously polling for new files.

    Polls the backlog directory at regular intervals and processes
    any new export files. Handles SIGTERM/SIGINT for graceful shutdown,
    waiting for any in-progress jobs to complete before exiting.

    Args:
        backlog_dir: Directory containing Twillot export files.
        output_dir: Directory for generated Obsidian notes.
        state_file: Path to JSON state persistence file.
        poll_interval: Seconds between polling (default: 120).
    """
    global _shutdown_event
    _shutdown_event = asyncio.Event()

    # Set up signal handlers for graceful shutdown
    loop = asyncio.get_running_loop()

    def signal_handler(sig: int) -> None:
        logger.info("Received signal %s, initiating graceful shutdown...", sig)
        if _shutdown_event:
            _shutdown_event.set()

    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal.signal(sig, lambda s, f: signal_handler(s))

    logger.info("Starting daemon mode (poll interval: %ds)", poll_interval)

    # Track current processing task for graceful shutdown
    current_task: asyncio.Task | None = None

    try:
        while not _shutdown_event.is_set():
            # Run one processing cycle
            current_task = asyncio.create_task(
                run_once(
                    backlog_dir=backlog_dir,
                    output_dir=output_dir,
                    state_file=state_file,
                )
            )

            try:
                result = await current_task
                current_task = None

                if result.processed > 0:
                    logger.info(
                        "Cycle complete: processed %d, skipped %d, failed %d",
                        result.processed,
                        result.skipped,
                        result.failed,
                    )
            except asyncio.CancelledError:
                logger.info("Processing cycle cancelled")
                break

            # Wait for next poll interval or shutdown signal
            try:
                await asyncio.wait_for(
                    _shutdown_event.wait(),
                    timeout=poll_interval,
                )
                # Shutdown requested during wait
                break
            except asyncio.TimeoutError:
                # Normal timeout - continue to next poll
                pass

    finally:
        # Handle any in-progress job on shutdown
        if current_task and not current_task.done():
            logger.info("Waiting for in-progress job to complete...")
            try:
                # Give it some time to finish gracefully
                await asyncio.wait_for(current_task, timeout=30)
                logger.info("In-progress job completed")
            except asyncio.TimeoutError:
                logger.warning("In-progress job timed out, cancelling...")
                current_task.cancel()
                try:
                    await current_task
                except asyncio.CancelledError:
                    pass

        logger.info("Daemon shutdown complete")


def print_stats(result: PipelineResult) -> None:
    """Print processing statistics to stdout.

    Args:
        result: The PipelineResult to summarize.
    """
    print("\n=== Processing Complete ===")
    print(f"Processed: {result.processed}")
    print(f"Skipped:   {result.skipped}")
    print(f"Failed:    {result.failed}")

    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for error in result.errors[:10]:  # Limit to first 10
            print(f"  - {error}")
        if len(result.errors) > 10:
            print(f"  ... and {len(result.errors) - 10} more")


def main(args: list[str] | None = None) -> int:
    """Main entry point.

    Args:
        args: Command line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for errors).
    """
    if args is None:
        args = sys.argv[1:]

    # Simple arg parsing for now (will be extended in #50)
    once_mode = "--once" in args

    # Load config and setup logging
    try:
        config = get_config(require_api_key=True)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    setup_logging(config.log_level)

    # Default backlog directory (relative to workspace)
    backlog_dir = Path("data/backlog")

    if once_mode:
        logger.info("Running in once mode")
        result = asyncio.run(
            run_once(
                backlog_dir=backlog_dir,
                output_dir=config.output_dir,
                state_file=config.state_file,
            )
        )
        print_stats(result)
        return 0 if result.failed == 0 else 1
    else:
        # Daemon mode - continuous polling
        logger.info("Running in daemon mode")
        try:
            asyncio.run(
                run_daemon(
                    backlog_dir=backlog_dir,
                    output_dir=config.output_dir,
                    state_file=config.state_file,
                )
            )
            return 0
        except KeyboardInterrupt:
            # Handle Ctrl+C during startup
            logger.info("Interrupted during startup")
            return 0


if __name__ == "__main__":
    sys.exit(main())
