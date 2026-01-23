"""Base processor interface for Twitter Bookmark Processor.

This module defines the abstract base class for all content processors
and the ProcessResult dataclass for standardized processing output.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark


@dataclass
class ProcessResult:
    """Result of processing a bookmark.

    Contains the extracted content and metadata from a processor.
    All processors return this standardized format.

    Attributes:
        success: Whether processing completed without errors
        content: Extracted/formatted content (markdown)
        title: Extracted title for the note
        tags: List of extracted tags
        error: Error message if success is False
        duration_ms: Processing time in milliseconds
    """

    success: bool
    content: Optional[str] = None
    title: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_ms: int = 0


class BaseProcessor(ABC):
    """Abstract base class for content processors.

    All processors (TweetProcessor, ThreadProcessor, VideoProcessor,
    LinkProcessor) must inherit from this class and implement the
    process method.

    Example:
        class TweetProcessor(BaseProcessor):
            async def process(self, bookmark: Bookmark) -> ProcessResult:
                # Extract content from simple tweet
                return ProcessResult(success=True, content="...", title="...")
    """

    @abstractmethod
    async def process(self, bookmark: "Bookmark") -> ProcessResult:
        """Process a bookmark and extract content.

        Args:
            bookmark: The bookmark to process

        Returns:
            ProcessResult containing extracted content and metadata
        """
        pass
