"""Main Entry Point for Twitter Bookmark Processor.

Processes Twitter/X bookmarks from Twillot exports into Obsidian notes.

Usage:
    python -m src.main --webhook             # Run as HTTP webhook server on port 8766
    python -m src.main --webhook --port 8080 # Webhook server on custom port
    python -m src.main --once                # Process backlog once and exit
    python -m src.main --source x_api --once # Fetch from X API once
    python -m src.main --source both         # Daemon: watch backlog + poll X API
    python -m src.main --authorize           # Run X API OAuth authorization flow
    python -m src.main                       # Run as daemon (polling mode)
    python -m src.main --verbose             # Enable debug logging
"""

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from typing import TYPE_CHECKING

from src.core.backlog_manager import BacklogManager
from src.core.config import get_config
from src.core.logger import get_logger, setup_logging
from src.core.pipeline import Pipeline, PipelineResult
from src.core.state_manager import StateManager
from src.core.watcher import DirectoryWatcher
from src.webhook_server import run_server

if TYPE_CHECKING:
    from src.core.config import Config

logger = get_logger(__name__)

# Default polling interval in seconds (2 minutes)
DEFAULT_POLL_INTERVAL = 120


def sync_brain(output_dir: Path) -> None:
    """Run sync-brain.sh if output dir is inside a Brain vault."""
    output_str = str(output_dir)
    if "/brain/" not in output_str:
        return

    sync_script = Path(__file__).resolve().parent.parent / "deploy" / "sync-brain.sh"
    if not sync_script.exists():
        return

    try:
        subprocess.run(
            ["bash", str(sync_script), output_str],
            timeout=30,
            capture_output=True,
            text=True,
        )
    except Exception as e:
        logger.warning("Brain sync failed: %s", e)

# Shutdown event for graceful termination
_shutdown_event: asyncio.Event | None = None


async def run_x_api_once(
    output_dir: Path,
    state_file: Path,
    config: "Config",
) -> PipelineResult:
    """Fetch bookmarks from X API once and process them.

    Args:
        output_dir: Directory for generated Obsidian notes.
        state_file: Path to JSON state persistence file.
        config: Application configuration with X API settings.

    Returns:
        PipelineResult with processing statistics.
    """
    from src.sources.x_api_auth import XApiAuth
    from src.sources.x_api_reader import XApiReader

    if not config.x_api_client_id:
        logger.error("X_API_CLIENT_ID not set")
        return PipelineResult(failed=1, errors=["X_API_CLIENT_ID not configured"])

    auth = XApiAuth(
        client_id=config.x_api_client_id,
        token_file=config.x_api_token_file,
    )

    if not auth.has_tokens():
        logger.error("No X API tokens found. Run --authorize first.")
        return PipelineResult(
            failed=1, errors=["No X API tokens. Run --authorize first."]
        )

    state_manager = StateManager(state_file)
    reader = XApiReader(auth=auth, state_manager=state_manager)
    pipeline = Pipeline(output_dir, state_file, x_api_auth=auth)

    bookmarks = await reader.fetch_new_bookmarks()
    if not bookmarks:
        logger.info("No new bookmarks from X API")
        return PipelineResult()

    return await pipeline.process_bookmarks(bookmarks)


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
    source: str = "twillot",
    config: "Config | None" = None,
) -> None:
    """Run as a daemon, continuously polling for new files and/or X API.

    Polls the backlog directory and/or X API at regular intervals.
    Handles SIGTERM/SIGINT for graceful shutdown.

    Args:
        backlog_dir: Directory containing Twillot export files.
        output_dir: Directory for generated Obsidian notes.
        state_file: Path to JSON state persistence file.
        poll_interval: Seconds between polling (default: 120).
        source: Bookmark source ("twillot", "x_api", "both").
        config: Application configuration (required for x_api source).
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
            async def _cycle():
                total = PipelineResult()
                if source in ("twillot", "both"):
                    r = await run_once(
                        backlog_dir=backlog_dir,
                        output_dir=output_dir,
                        state_file=state_file,
                    )
                    total.processed += r.processed
                    total.skipped += r.skipped
                    total.failed += r.failed
                    total.errors.extend(r.errors)

                if source in ("x_api", "both") and config:
                    r = await run_x_api_once(
                        output_dir=output_dir,
                        state_file=state_file,
                        config=config,
                    )
                    total.processed += r.processed
                    total.skipped += r.skipped
                    total.failed += r.failed
                    total.errors.extend(r.errors)

                return total

            current_task = asyncio.create_task(_cycle())

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
                    sync_brain(output_dir)
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


async def _run_authorize(config: "Config") -> int:
    """Run the X API OAuth 2.0 authorization flow.

    Starts a callback server on 0.0.0.0:8766 and prints the auth URL.
    The user opens the URL in any browser, approves, and either:
    - The callback server captures the redirect automatically, OR
    - The user pastes the redirect URL if the callback didn't reach us.

    One-time operation. With offline.access, refresh tokens auto-rotate.
    """
    from urllib.parse import parse_qs, urlparse

    from aiohttp import web

    from src.sources.x_api_auth import XApiAuth

    if not config.x_api_client_id:
        print("Error: X_API_CLIENT_ID not set in environment", file=sys.stderr)
        return 1

    auth = XApiAuth(
        client_id=config.x_api_client_id,
        token_file=config.x_api_token_file,
    )

    url, state = auth.get_authorization_url()

    # Shared state between callback server and main flow
    captured_code: dict[str, str | None] = {"code": None, "error": None}
    code_received = asyncio.Event()

    async def callback_handler(request: web.Request) -> web.Response:
        error = request.query.get("error")
        if error:
            captured_code["error"] = request.query.get("error_description", error)
            code_received.set()
            return web.Response(
                text="<h2>Authorization failed</h2><p>You can close this tab.</p>",
                content_type="text/html",
            )
        code = request.query.get("code")
        if code:
            captured_code["code"] = code
            code_received.set()
            return web.Response(
                text="<h2>Authorization successful!</h2>"
                "<p>You can close this tab.</p>",
                content_type="text/html",
            )
        return web.Response(text="Missing code parameter", status=400)

    # Start callback server on 0.0.0.0 (reachable from outside container)
    app = web.Application()
    app.router.add_get("/oauth/callback", callback_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8766)
    await site.start()

    print("\n=== X API Authorization ===\n")
    print("Open this URL in your browser:\n")
    print(f"  {url}\n")
    print("After approving, one of two things will happen:")
    print("  1. The page loads 'Authorization successful' → done automatically")
    print("  2. The page fails to load → copy the URL from your address bar\n")
    print("Waiting for callback (or paste the redirect URL below)...\n")

    # Race: callback server vs manual paste
    async def wait_for_paste():
        """Read pasted URL from stdin in a thread."""
        loop = asyncio.get_event_loop()
        pasted = await loop.run_in_executor(None, sys.stdin.readline)
        pasted = pasted.strip()
        if pasted and "code=" in pasted:
            parsed = parse_qs(urlparse(pasted).query)
            if "code" in parsed:
                captured_code["code"] = parsed["code"][0]
                code_received.set()

    paste_task = asyncio.create_task(wait_for_paste())

    try:
        await asyncio.wait_for(code_received.wait(), timeout=300)
    except asyncio.TimeoutError:
        print("\nTimeout (5 min). Run --authorize again.", file=sys.stderr)
        await runner.cleanup()
        return 1

    paste_task.cancel()
    await runner.cleanup()

    if captured_code["error"]:
        print(f"\nAuthorization denied: {captured_code['error']}", file=sys.stderr)
        return 1

    code = captured_code["code"]
    if not code:
        print("\nNo authorization code received.", file=sys.stderr)
        return 1

    try:
        tokens = await auth.exchange_code(code)
        print("\nAuthorization successful!")
        print(f"Tokens saved to: {auth.token_file}")
        print(f"Scopes: {tokens.scope}")
        print(f"Access token expires in: {int(tokens.expires_at - time.time())}s")
        print("Refresh token will auto-rotate on each use (valid 6 months).")
        return 0
    except Exception as e:
        print(f"\nToken exchange failed: {e}", file=sys.stderr)
        return 1


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


def create_argument_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="twitter-bookmark-processor",
        description="Process Twitter/X bookmarks from Twillot exports into Obsidian notes.",
        epilog="By default, runs as a daemon polling for new exports every 2 minutes.",
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help="Process backlog once and exit (instead of daemon mode)",
    )

    parser.add_argument(
        "--webhook",
        action="store_true",
        help="Run as HTTP webhook server (instead of daemon mode)",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8766,
        metavar="PORT",
        help="Port for webhook server (default: 8766, requires --webhook)",
    )

    parser.add_argument(
        "--source",
        choices=["twillot", "x_api", "both"],
        default=None,
        metavar="SOURCE",
        help="Bookmark source: twillot, x_api, or both (default: from config)",
    )

    parser.add_argument(
        "--authorize",
        action="store_true",
        help="Run X API OAuth 2.0 authorization flow (one-time setup)",
    )

    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Clear ERROR entries from state so they get reprocessed on next run",
    )

    # Insight Engine flags
    parser.add_argument(
        "--insight",
        action="store_true",
        help="Use Insight Engine pipeline (capture → distill → write)",
    )

    parser.add_argument(
        "--reprocess-stage2",
        action="store_true",
        help="Re-run distillation on existing content packages (Insight Engine)",
    )

    parser.add_argument(
        "--retry-reviews",
        action="store_true",
        help="Re-process bookmarks flagged needs_review (Insight Engine)",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit number of bookmarks to process (for testing)",
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    return parser


async def _run_insight(parsed_args: argparse.Namespace, config: "Config") -> int:
    """Run Insight Engine pipeline modes.

    Handles --insight, --reprocess-stage2, and --retry-reviews.
    """
    from src.insight.pipeline import InsightPipeline

    # Optional X API auth
    x_api_auth = None
    if config.x_api_client_id:
        try:
            from src.sources.x_api_auth import XApiAuth
            auth = XApiAuth(
                client_id=config.x_api_client_id,
                token_file=config.x_api_token_file,
            )
            if auth.has_tokens():
                x_api_auth = auth
        except Exception:
            pass

    pipeline = InsightPipeline(
        output_dir=config.output_dir,
        x_api_auth=x_api_auth,
    )

    # --retry-reviews: re-process bookmarks flagged needs_review
    if parsed_args.retry_reviews:
        results = await pipeline.retry_reviews()
        if not results:
            print("No bookmarks flagged for review")
        else:
            for bid, status in results.items():
                print(f"  {bid}: {status}")
            success = sum(1 for s in results.values() if s == "success")
            print(f"\nRetried {len(results)}: {success} success, {len(results) - success} failed")
        return 0

    # --reprocess-stage2: re-run distillation on all content packages
    if parsed_args.reprocess_stage2:
        from src.insight.capture import PACKAGES_DIR
        packages = list(PACKAGES_DIR.glob("*.json"))
        limit = parsed_args.limit or len(packages)
        packages = packages[:limit]
        print(f"Reprocessing Stage 2 for {len(packages)} packages...")

        success = 0
        for pkg_path in packages:
            bid = pkg_path.stem
            note = await pipeline.reprocess_stage2(bid)
            if note:
                success += 1
                print(f"  ✓ {bid} [{note.value_type.value}]")
            else:
                print(f"  ✗ {bid}")

        print(f"\nReprocessed: {success}/{len(packages)}")
        return 0

    # --insight: process bookmarks through insight pipeline
    # Source bookmarks the same way as legacy pipeline
    source = parsed_args.source or config.bookmark_source
    bookmarks: list = []

    if source in ("x_api", "both") and x_api_auth:
        from src.sources.x_api_reader import XApiReader
        reader = XApiReader(auth=x_api_auth, state_manager=StateManager(config.state_file))
        bookmarks.extend(await reader.fetch_new_bookmarks())

    if source in ("twillot", "both"):
        from src.core.backlog_manager import BacklogManager
        from src.core.watcher import DirectoryWatcher
        backlog_dir = Path("data/backlog")
        if backlog_dir.exists():
            bm = BacklogManager(backlog_dir)
            watcher = DirectoryWatcher(bm, StateManager(config.state_file))
            for export_file in watcher.get_new_files():
                from src.sources.twillot_reader import parse_twillot_export
                bookmarks.extend(parse_twillot_export(export_file))

    # Also allow processing bookmarks already in legacy state (for backfill)
    if not bookmarks:
        from src.core.bookmark import Bookmark, ProcessingStatus
        legacy_state = StateManager(config.state_file)
        legacy_state.load()
        all_ids = legacy_state.get_all_processed_ids()
        done_ids = [
            bid for bid in all_ids
            if legacy_state.get_status(bid) == ProcessingStatus.DONE
        ]
        # Filter out already-insight-processed
        done_ids = [bid for bid in done_ids if not pipeline.state.is_done(bid)]

        for bid in done_ids:
            bookmarks.append(Bookmark(
                id=bid,
                url=f"https://x.com/i/status/{bid}",
                text="",
                author_username="unknown",
            ))

    limit = parsed_args.limit or len(bookmarks)
    bookmarks = bookmarks[:limit]

    logger.info("Insight pipeline: %d bookmarks to process", len(bookmarks))
    print(f"Processing {len(bookmarks)} bookmarks through Insight Engine...")

    processed = 0
    skipped = 0
    failed = 0

    for bookmark in bookmarks:
        note = await pipeline.process_bookmark(bookmark)
        if note is None:
            if pipeline.state.is_done(bookmark.id):
                skipped += 1
            else:
                failed += 1
        else:
            processed += 1
            print(f"  [{processed}] {bookmark.id} -> {note.value_type.value}: {note.title[:60]}")

    print(f"\n=== Insight Engine Complete ===")
    print(f"Processed: {processed}")
    print(f"Skipped:   {skipped}")
    print(f"Failed:    {failed}")

    stats = pipeline.state.get_stats()
    print(f"\nState: {stats}")

    return 0 if failed == 0 else 1


def main(args: list[str] | None = None) -> int:
    """Main entry point.

    Args:
        args: Command line arguments (defaults to sys.argv[1:]).

    Returns:
        Exit code (0 for success, 1 for errors).
    """
    parser = create_argument_parser()
    parsed_args = parser.parse_args(args)

    # --authorize doesn't need ANTHROPIC_API_KEY, only X API credentials
    require_api_key = not parsed_args.authorize

    # Load config and setup logging
    try:
        config = get_config(require_api_key=require_api_key)
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    # Override log level if verbose flag is set
    log_level = "DEBUG" if parsed_args.verbose else config.log_level
    setup_logging(log_level)

    if parsed_args.authorize:
        # X API OAuth authorization flow (no LLM needed)
        return asyncio.run(_run_authorize(config))

    # Handle --retry-errors: clear ERROR entries before processing
    if parsed_args.retry_errors:
        state_manager = StateManager(config.state_file)
        cleared = state_manager.reset_errors()
        if cleared:
            logger.info("Cleared %d error entries for retry: %s", len(cleared), cleared)
            print(f"Cleared {len(cleared)} error entries for retry")
        else:
            logger.info("No error entries to clear")
            print("No error entries to clear")

    # ── Insight Engine modes ──────────────────────────────────────
    if parsed_args.insight or parsed_args.reprocess_stage2 or parsed_args.retry_reviews:
        return asyncio.run(_run_insight(parsed_args, config))

    # Default backlog directory (relative to workspace)
    backlog_dir = Path("data/backlog")

    # Determine bookmark source (CLI overrides config)
    source = parsed_args.source or config.bookmark_source

    if parsed_args.webhook:
        # Webhook mode - HTTP server
        logger.info("Running in webhook mode on port %d", parsed_args.port)
        try:
            async def run_webhook_server():
                runner = await run_server(port=parsed_args.port)
                # Keep running until interrupted
                try:
                    while True:
                        await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    await runner.cleanup()

            asyncio.run(run_webhook_server())
            return 0
        except KeyboardInterrupt:
            logger.info("Webhook server stopped")
            return 0
    elif parsed_args.once:
        logger.info("Running in once mode (source: %s)", source)

        async def _once():
            results = []
            if source in ("twillot", "both"):
                results.append(
                    await run_once(
                        backlog_dir=backlog_dir,
                        output_dir=config.output_dir,
                        state_file=config.state_file,
                    )
                )
            if source in ("x_api", "both"):
                results.append(
                    await run_x_api_once(
                        output_dir=config.output_dir,
                        state_file=config.state_file,
                        config=config,
                    )
                )
            # Aggregate results
            total = PipelineResult()
            for r in results:
                total.processed += r.processed
                total.skipped += r.skipped
                total.failed += r.failed
                total.errors.extend(r.errors)
            return total

        result = asyncio.run(_once())
        print_stats(result)
        return 0 if result.failed == 0 else 1
    else:
        # Daemon mode - continuous polling
        logger.info("Running in daemon mode (source: %s)", source)
        try:
            asyncio.run(
                run_daemon(
                    backlog_dir=backlog_dir,
                    output_dir=config.output_dir,
                    state_file=config.state_file,
                    source=source,
                    config=config,
                )
            )
            return 0
        except KeyboardInterrupt:
            # Handle Ctrl+C during startup
            logger.info("Interrupted during startup")
            return 0


if __name__ == "__main__":
    sys.exit(main())
