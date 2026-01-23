"""Processing pipeline for Twitter Bookmark Processor.

Integrates all components to process bookmarks end-to-end:
twillot_reader → classifier → processor → obsidian_writer

This is the main orchestrator that takes Twillot exports and produces
Obsidian notes while tracking state to avoid reprocessing.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from src.core.bookmark import ContentType, ProcessingStatus
from src.core.classifier import classify
from src.core.state_manager import StateManager
from src.output.obsidian_writer import ObsidianWriter
from src.processors.thread_processor import ThreadProcessor
from src.processors.tweet_processor import TweetProcessor
from src.processors.video_processor import VideoProcessor
from src.sources.twillot_reader import parse_twillot_export

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark
    from src.processors.base import ProcessResult

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

    Supports TWEET, VIDEO, and THREAD content types.
    LINK processor will be added in Sprint 4.
    """

    def __init__(
        self,
        output_dir: Path,
        state_file: Path,
    ):
        """Initialize pipeline with output and state paths.

        Args:
            output_dir: Directory where Obsidian notes will be written
            state_file: Path to JSON file for state persistence
        """
        self.output_dir = output_dir
        self.state_manager = StateManager(state_file)
        self.writer = ObsidianWriter(output_dir)

        # Processors by content type
        self._processors = {
            ContentType.TWEET: TweetProcessor(),
            ContentType.VIDEO: VideoProcessor(output_dir=output_dir),
            ContentType.THREAD: ThreadProcessor(output_dir=output_dir),
        }

    async def process_export(
        self,
        export_path: Path,
    ) -> PipelineResult:
        """Process a Twillot export file.

        Reads bookmarks from the export, classifies them, processes each
        through the appropriate processor, and writes Obsidian notes.

        Args:
            export_path: Path to Twillot JSON export file

        Returns:
            PipelineResult with processing statistics
        """
        result = PipelineResult()

        # Parse export
        bookmarks = parse_twillot_export(export_path)
        logger.info("Parsed %d bookmarks from %s", len(bookmarks), export_path)

        for bookmark in bookmarks:
            try:
                processed = await self._process_single(bookmark)
                if processed:
                    result.processed += 1
                else:
                    result.skipped += 1
            except Exception as e:
                result.failed += 1
                error_msg = f"Bookmark {bookmark.id}: {e}"
                result.errors.append(error_msg)
                logger.error("Failed to process %s: %s", bookmark.id, e)

                # Mark as error in state
                self.state_manager.mark_processed(
                    bookmark.id,
                    ProcessingStatus.ERROR,
                    error=str(e),
                )

        logger.info(
            "Pipeline complete: processed=%d, skipped=%d, failed=%d",
            result.processed,
            result.skipped,
            result.failed,
        )

        return result

    async def process_bookmark(
        self,
        bookmark: "Bookmark",
    ) -> Path | None:
        """Process a single bookmark.

        Public method for processing individual bookmarks (e.g., from webhook).

        Args:
            bookmark: The bookmark to process

        Returns:
            Path to generated note, or None if skipped/failed
        """
        try:
            return await self._process_single(bookmark)
        except Exception as e:
            logger.error("Failed to process %s: %s", bookmark.id, e)
            self.state_manager.mark_processed(
                bookmark.id,
                ProcessingStatus.ERROR,
                error=str(e),
            )
            return None

    async def _process_single(
        self,
        bookmark: "Bookmark",
    ) -> Path | None:
        """Process a single bookmark internally.

        Args:
            bookmark: The bookmark to process

        Returns:
            Path to generated note, or None if skipped
        """
        # Check if already processed
        if self.state_manager.is_processed(bookmark.id):
            logger.debug("Skipping already processed bookmark: %s", bookmark.id)
            return None

        # Classify content type
        bookmark.content_type = classify(bookmark)
        logger.debug("Classified %s as %s", bookmark.id, bookmark.content_type.value)

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

        # Process the bookmark
        process_result: "ProcessResult" = await processor.process(bookmark)

        if not process_result.success:
            raise RuntimeError(
                f"Processing failed: {process_result.error or 'Unknown error'}"
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
