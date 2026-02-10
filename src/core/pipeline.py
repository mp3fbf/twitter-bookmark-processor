"""Processing pipeline for Twitter Bookmark Processor.

Integrates all components to process bookmarks end-to-end:
twillot_reader → classifier → processor → obsidian_writer

This is the main orchestrator that takes Twillot exports and produces
Obsidian notes while tracking state to avoid reprocessing.

Supports concurrent processing with rate limiting to avoid overwhelming
external services while maximizing throughput.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx

from src.core.bookmark import ContentType, ProcessingStatus
from src.core.classifier import classify
from src.core.rate_limiter import RateLimiter
from src.core.retry import retry_async
from src.core.state_manager import StateManager
from src.output.obsidian_writer import ObsidianWriter
from src.processors.link_processor import LinkProcessor
from src.processors.thread_processor import ThreadProcessor
from src.processors.tweet_processor import TweetProcessor
from src.processors.video_processor import VideoProcessor
from src.sources.twillot_reader import parse_twillot_export

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark
    from src.processors.base import ProcessResult
    from src.sources.x_api_auth import XApiAuth

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of processing a batch of bookmarks.

    Attributes:
        processed: Number of bookmarks successfully processed
        skipped: Number of bookmarks skipped (already processed)
        failed: Number of bookmarks that failed processing
        errors: List of error messages for failed items
    """

    processed: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class Pipeline:
    """Main processing pipeline for Twitter bookmarks.

    Orchestrates the flow from Twillot export to Obsidian notes:
    1. Parse export file
    2. Filter already-processed bookmarks (via StateManager)
    3. Classify each bookmark's content type
    4. Route to appropriate processor
    5. Write output via ObsidianWriter
    6. Update state tracking

    Supports TWEET, VIDEO, THREAD, and LINK content types.
    Processes bookmarks concurrently with per-content-type rate limiting.
    """

    def __init__(
        self,
        output_dir: Path,
        state_file: Path,
        rate_limiter: RateLimiter | None = None,
        max_concurrency: int = 10,
        x_api_auth: Optional["XApiAuth"] = None,
    ):
        """Initialize pipeline with output and state paths.

        Args:
            output_dir: Directory where Obsidian notes will be written
            state_file: Path to JSON file for state persistence
            rate_limiter: Optional rate limiter instance (creates new if not provided)
            max_concurrency: Maximum concurrent bookmark processing tasks (default 10)
            x_api_auth: Optional XApiAuth for thread processing via X API
        """
        self.output_dir = output_dir
        self.state_manager = StateManager(state_file)
        self.writer = ObsidianWriter(output_dir)
        self._rate_limiter = rate_limiter or RateLimiter()
        self._max_concurrency = max_concurrency
        self._concurrency_semaphore = asyncio.Semaphore(max_concurrency)

        # Processors by content type
        self._processors = {
            ContentType.TWEET: TweetProcessor(),
            ContentType.VIDEO: VideoProcessor(output_dir=output_dir),
            ContentType.THREAD: ThreadProcessor(
                output_dir=output_dir, x_api_auth=x_api_auth
            ),
            ContentType.LINK: LinkProcessor(),
        }

    async def process_bookmarks(
        self,
        bookmarks: list["Bookmark"],
    ) -> PipelineResult:
        """Process a list of bookmarks concurrently.

        Classifies each bookmark, routes to appropriate processor, and writes
        output. This is the core method used by both Twillot and X API sources.

        Args:
            bookmarks: List of Bookmark instances to process

        Returns:
            PipelineResult with processing statistics
        """
        result = PipelineResult()
        logger.info("Processing %d bookmarks", len(bookmarks))

        # Create tasks for all bookmarks
        tasks = [
            self._process_single_with_result(bookmark)
            for bookmark in bookmarks
        ]

        # Process concurrently, collecting results (including exceptions)
        task_results = await asyncio.gather(*tasks, return_exceptions=True)

        # Aggregate results
        for i, task_result in enumerate(task_results):
            bookmark = bookmarks[i]

            if isinstance(task_result, Exception):
                # Task raised an exception
                result.failed += 1
                error_msg = f"Bookmark {bookmark.id}: {task_result}"
                result.errors.append(error_msg)
                logger.error("Failed to process %s: %s", bookmark.id, task_result)

                # Mark as error in state
                self.state_manager.mark_processed(
                    bookmark.id,
                    ProcessingStatus.ERROR,
                    error=str(task_result),
                )
            elif task_result is None:
                # Skipped (already processed or unsupported)
                result.skipped += 1
            else:
                # Successfully processed
                result.processed += 1

        logger.info(
            "Pipeline complete: processed=%d, skipped=%d, failed=%d",
            result.processed,
            result.skipped,
            result.failed,
        )

        return result

    async def process_export(
        self,
        export_path: Path,
    ) -> PipelineResult:
        """Process a Twillot export file concurrently.

        Convenience wrapper around process_bookmarks() for Twillot exports.

        Args:
            export_path: Path to Twillot JSON export file

        Returns:
            PipelineResult with processing statistics
        """
        bookmarks = parse_twillot_export(export_path)
        logger.info("Parsed %d bookmarks from %s", len(bookmarks), export_path)
        return await self.process_bookmarks(bookmarks)

    async def _process_single_with_result(
        self,
        bookmark: "Bookmark",
    ) -> Path | None:
        """Process a single bookmark with concurrency and rate limiting.

        Wrapper around _process_single that:
        1. Limits overall concurrency via semaphore
        2. Applies rate limiting based on content type

        Args:
            bookmark: The bookmark to process

        Returns:
            Path to generated note, or None if skipped
        """
        # Limit overall concurrency
        async with self._concurrency_semaphore:
            # Check if already processed (before classification to save work)
            if self.state_manager.is_processed(bookmark.id):
                logger.debug("Skipping already processed bookmark: %s", bookmark.id)
                return None

            # Resolve t.co links before classification so URL-only tweets
            # get classified as LINK instead of TWEET
            await self._resolve_tco_links(bookmark)

            # Classify content type
            bookmark.content_type = classify(bookmark)
            logger.debug("Classified %s as %s", bookmark.id, bookmark.content_type.value)

            # Apply rate limiting based on content type
            async with self._rate_limiter.acquire_context_for_content(
                bookmark.content_type
            ):
                return await self._process_single(bookmark)

    async def process_bookmark(
        self,
        bookmark: "Bookmark",
    ) -> Path | None:
        """Process a single bookmark.

        Public method for processing individual bookmarks (e.g., from webhook).
        Applies rate limiting based on content type.

        Args:
            bookmark: The bookmark to process

        Returns:
            Path to generated note, or None if skipped/failed
        """
        try:
            return await self._process_single_with_result(bookmark)
        except Exception as e:
            logger.error("Failed to process %s: %s", bookmark.id, e)
            self.state_manager.mark_processed(
                bookmark.id,
                ProcessingStatus.ERROR,
                error=str(e),
            )
            return None

    @staticmethod
    async def _resolve_tco_links(bookmark: "Bookmark") -> None:
        """Resolve t.co URLs in tweet text and add them to bookmark.links.

        Many tweets contain only a t.co shortened URL. Without resolving it,
        the classifier sees no external links and falls through to TWEET,
        producing a useless "Untitled Tweet" note. This method expands t.co
        URLs so the classifier can properly route them as LINK or VIDEO.

        Only runs when bookmark.links is empty and text contains t.co URLs.
        Modifies bookmark.links in place.
        """
        if bookmark.links:
            return

        # Extract t.co URLs from text
        tco_urls = re.findall(r"https?://t\.co/\w+", bookmark.text)
        if not tco_urls:
            return

        for tco_url in tco_urls:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0),
                    follow_redirects=True,
                ) as client:
                    response = await client.head(tco_url)
                    resolved = str(response.url)

                    # Skip if it resolved to another Twitter/X URL
                    if re.match(r"https?://(www\.)?(twitter\.com|x\.com)/", resolved):
                        continue
                    # Skip t.co that didn't resolve
                    if "t.co/" in resolved:
                        continue

                    bookmark.links.append(resolved)
                    logger.debug(
                        "Resolved t.co link for %s: %s -> %s",
                        bookmark.id,
                        tco_url,
                        resolved,
                    )
            except Exception as e:
                logger.warning(
                    "Failed to resolve t.co URL %s for bookmark %s: %s",
                    tco_url,
                    bookmark.id,
                    e,
                )

    async def _process_single(
        self,
        bookmark: "Bookmark",
    ) -> Path | None:
        """Process a single bookmark internally.

        Note: State check and classification should be done before calling this.
        This method assumes bookmark.content_type is already set.

        Args:
            bookmark: The bookmark to process (with content_type already set)

        Returns:
            Path to generated note, or None if skipped
        """
        # Get processor for content type
        processor = self._processors.get(bookmark.content_type)
        if processor is None:
            # Unsupported content type - log and skip for now
            logger.warning(
                "No processor for content type %s (bookmark %s) - skipping",
                bookmark.content_type.value,
                bookmark.id,
            )
            return None

        # Process the bookmark with retry for transient failures
        async def _do_process() -> "ProcessResult":
            r = await processor.process(bookmark)
            if not r.success:
                raise RuntimeError(
                    f"Processing failed: {r.error or 'Unknown error'}"
                )
            return r

        process_result = await retry_async(
            _do_process, max_attempts=3, base_delay=2.0, max_delay=30.0
        )

        # Write to Obsidian
        output_path = self.writer.write(bookmark, process_result)
        logger.info("Wrote note: %s", output_path)

        # Update state
        self.state_manager.mark_processed(
            bookmark.id,
            ProcessingStatus.DONE,
            output_path=str(output_path),
        )

        return output_path
