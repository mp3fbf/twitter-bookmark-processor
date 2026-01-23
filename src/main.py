"""Main Entry Point for Twitter Bookmark Processor.

Processes Twitter/X bookmarks from Twillot exports into Obsidian notes.

Usage:
    python -m src.main --once     # Process backlog once and exit
    python -m src.main            # Run as daemon (polling mode)
"""

import asyncio
import sys
from pathlib import Path

from src.core.backlog_manager import BacklogManager
from src.core.config import get_config
from src.core.logger import get_logger, setup_logging
from src.core.pipeline import Pipeline, PipelineResult
from src.core.state_manager import StateManager
from src.core.watcher import DirectoryWatcher

logger = get_logger(__name__)


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
        # Daemon mode will be implemented in #49
        print("Daemon mode not yet implemented. Use --once for now.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
