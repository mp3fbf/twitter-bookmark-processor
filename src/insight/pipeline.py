"""Insight Pipeline — orchestrates capture → distill → write.

Two function calls. If a validator stage is ever needed, add a third line.

State management uses a thin wrapper around the existing StateManager to
get atomic writes and file locking for free, with an insight-specific schema.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.core.content_fetcher import AsyncContentFetcher
from src.core.llm_factory import AnthropicProvider
from src.core.rate_limiter import RateConfig, RateLimiter, RateType
from src.core.state_manager import StateManager
from src.insight.capture import ContentCapture
from src.insight.distill import InsightDistiller
from src.insight.models import ContentPackage, InsightNote

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark
    from src.sources.x_api_auth import XApiAuth

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_STATE_FILE = Path("data/insight_state.json")
DEFAULT_OUTPUT_DIR = Path("/workspace/notes/twitter/")


class InsightState:
    """Thin wrapper around StateManager for insight-specific state.

    State schema per bookmark:
    {
        "capture": {"status": "done", "completed_at": "..."},
        "distill": {"status": "done", "completed_at": "...", "value_type": "technique"},
        "output_path": "...",
        "needs_review": false,
        "error": null
    }
    """

    def __init__(self, state_file: Path = DEFAULT_STATE_FILE):
        self._sm = StateManager(state_file)
        self._sm._ensure_loaded()

    def get(self, bookmark_id: str) -> dict | None:
        return self._sm._state["processed"].get(bookmark_id)

    def is_done(self, bookmark_id: str) -> bool:
        entry = self.get(bookmark_id)
        if not entry:
            return False
        return (
            entry.get("capture", {}).get("status") == "done"
            and entry.get("distill", {}).get("status") == "done"
            and not entry.get("needs_review", False)
        )

    def is_capture_done(self, bookmark_id: str) -> bool:
        entry = self.get(bookmark_id)
        return bool(entry and entry.get("capture", {}).get("status") == "done")

    def needs_review(self, bookmark_id: str) -> bool:
        entry = self.get(bookmark_id)
        return bool(entry and entry.get("needs_review", False))

    def mark_capture_done(self, bookmark_id: str) -> None:
        entry = self.get(bookmark_id) or {}
        entry["capture"] = {
            "status": "done",
            "completed_at": datetime.now().isoformat(),
        }
        self._sm._state["processed"][bookmark_id] = entry
        self._sm.save()

    def mark_distill_done(
        self, bookmark_id: str, value_type: str, output_path: str
    ) -> None:
        entry = self.get(bookmark_id) or {}
        entry["distill"] = {
            "status": "done",
            "completed_at": datetime.now().isoformat(),
            "value_type": value_type,
        }
        entry["output_path"] = output_path
        entry["needs_review"] = False
        entry["error"] = None
        self._sm._state["processed"][bookmark_id] = entry
        self._sm.save()

    def mark_error(self, bookmark_id: str, error: str, needs_review: bool = True) -> None:
        entry = self.get(bookmark_id) or {}
        entry["error"] = error
        entry["needs_review"] = needs_review
        self._sm._state["processed"][bookmark_id] = entry
        self._sm.save()

    def get_review_ids(self) -> list[str]:
        """Get all bookmark IDs flagged for review."""
        return [
            bid
            for bid, entry in self._sm._state["processed"].items()
            if entry.get("needs_review", False)
        ]

    def get_stats(self) -> dict[str, int]:
        total = 0
        done = 0
        review = 0
        error = 0
        for entry in self._sm._state["processed"].values():
            total += 1
            if entry.get("distill", {}).get("status") == "done" and not entry.get("needs_review"):
                done += 1
            elif entry.get("needs_review"):
                review += 1
            elif entry.get("error"):
                error += 1
        return {"total": total, "done": done, "review": review, "error": error}


class InsightPipeline:
    """Orchestrates the insight extraction pipeline.

    Usage:
        pipeline = InsightPipeline(output_dir=Path("notes/twitter/"))
        note = await pipeline.process_bookmark(bookmark)
    """

    def __init__(
        self,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        state_file: Path = DEFAULT_STATE_FILE,
        x_api_auth: Optional["XApiAuth"] = None,
        api_key: str | None = None,
    ):
        self._output_dir = output_dir
        self._state = InsightState(state_file)

        # API key
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

        # Vision provider (Haiku for image analysis)
        vision_provider = None
        if self._api_key:
            try:
                vision_provider = AnthropicProvider(
                    api_key=self._api_key,
                    model="claude-haiku-4-5-20251001",
                )
            except Exception as e:
                logger.warning("Vision provider not available: %s", e)

        # Stage 1: Content Capture
        self._capture = ContentCapture(
            content_fetcher=AsyncContentFetcher(),
            vision_provider=vision_provider,
            x_api_auth=x_api_auth,
        )

        # Stage 2: Insight Distillation
        self._distill = InsightDistiller(api_key=self._api_key)

        # Rate limiter for multi-model
        self._rate_limiter = RateLimiter({
            RateType.LINK: RateConfig(requests_per_second=5.0, max_concurrent=3),
            RateType.LLM: RateConfig(requests_per_second=2.0, max_concurrent=2),
            RateType.VIDEO: RateConfig(requests_per_second=5.0, max_concurrent=5),  # Haiku vision
        })

        # Output writer (lazy import to avoid circular)
        self._writer = None

    def _get_writer(self):
        if self._writer is None:
            from src.insight.writer import InsightWriter
            self._writer = InsightWriter(self._output_dir)
        return self._writer

    async def process_bookmark(self, bookmark: "Bookmark") -> InsightNote | None:
        """Process a single bookmark through the insight pipeline.

        Returns None if the bookmark was skipped (already processed).
        """
        bid = bookmark.id

        # Skip if already done
        if self._state.is_done(bid):
            logger.debug("Skipping %s — already processed", bid)
            return None

        try:
            # Stage 1: Capture (or load from disk if already captured)
            if self._state.is_capture_done(bid):
                package = ContentCapture.load_package(bid)
                if not package:
                    logger.warning("Capture marked done but package missing for %s, re-capturing", bid)
                    package = await self._capture.capture(bookmark)
                    self._state.mark_capture_done(bid)
            else:
                package = await self._capture.capture(bookmark)
                self._state.mark_capture_done(bid)

            # Stage 2: Distill
            note = await self._distill.distill(package)

            # Write to Obsidian
            writer = self._get_writer()
            output_path = writer.write(note, package)

            # Update state
            self._state.mark_distill_done(
                bid,
                value_type=note.value_type.value,
                output_path=str(output_path),
            )

            logger.info(
                "Processed %s -> %s [%s]",
                bid, output_path.name, note.value_type.value,
            )
            return note

        except Exception as e:
            logger.error("Pipeline failed for %s: %s", bid, e, exc_info=True)
            self._state.mark_error(bid, str(e))
            return None

    async def reprocess_stage2(self, bookmark_id: str) -> InsightNote | None:
        """Re-run distillation on an existing content package.

        Useful for iterating on prompts without refetching content.
        """
        package = ContentCapture.load_package(bookmark_id)
        if not package:
            logger.error("No content package found for %s", bookmark_id)
            return None

        try:
            note = await self._distill.distill(package)

            writer = self._get_writer()
            output_path = writer.write(note, package)

            self._state.mark_distill_done(
                bookmark_id,
                value_type=note.value_type.value,
                output_path=str(output_path),
            )

            return note
        except Exception as e:
            logger.error("Reprocess failed for %s: %s", bookmark_id, e)
            self._state.mark_error(bookmark_id, str(e))
            return None

    async def retry_reviews(self) -> dict[str, str]:
        """Re-process all bookmarks flagged for review.

        Returns dict of {bookmark_id: "success"|"error: msg"}.
        """
        review_ids = self._state.get_review_ids()
        if not review_ids:
            return {}

        results = {}
        for bid in review_ids:
            note = await self.reprocess_stage2(bid)
            if note:
                results[bid] = "success"
            else:
                results[bid] = f"error: check logs"

        return results

    @property
    def state(self) -> InsightState:
        return self._state
