"""Tweet processor for simple text/image tweets.

Processes bookmarks classified as TWEET content type.
Extracts title, content, and tags from the tweet text.
"""

import re
import time
from typing import TYPE_CHECKING

from src.processors.base import BaseProcessor, ProcessResult

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark


class TweetProcessor(BaseProcessor):
    """Processor for simple tweets (text and images).

    Handles tweets that are NOT threads, videos, or link-shares.
    Extracts:
    - Title from first N words of tweet
    - Hashtags as tags
    - Formats content with images if present
    """

    # Max words to use for title
    TITLE_MAX_WORDS = 8

    async def process(self, bookmark: "Bookmark") -> ProcessResult:
        """Process a simple tweet bookmark.

        Args:
            bookmark: The tweet bookmark to process

        Returns:
            ProcessResult with extracted content and metadata
        """
        start_time = time.perf_counter()

        try:
            # Extract title from first words
            title = self._extract_title(bookmark.text)

            # Extract hashtags as tags
            tags = self._extract_hashtags(bookmark.text)

            # Format content
            content = self._format_content(bookmark)

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            return ProcessResult(
                success=True,
                content=content,
                title=title,
                tags=tags,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )

    def _extract_title(self, text: str) -> str:
        """Extract title from first N words of tweet.

        Removes hashtags and URLs from consideration.
        Truncates to TITLE_MAX_WORDS.

        Args:
            text: Full tweet text

        Returns:
            Cleaned title string
        """
        # Remove URLs
        text_no_urls = re.sub(r'https?://\S+', '', text)

        # Remove hashtags for title extraction
        text_no_hashtags = re.sub(r'#\w+', '', text_no_urls)

        # Clean up whitespace
        cleaned = ' '.join(text_no_hashtags.split())

        # Get first N words
        words = cleaned.split()[:self.TITLE_MAX_WORDS]
        title = ' '.join(words)

        # Add ellipsis if truncated
        if len(cleaned.split()) > self.TITLE_MAX_WORDS:
            title += '...'

        return title.strip() if title.strip() else "Untitled Tweet"

    def _extract_hashtags(self, text: str) -> list[str]:
        """Extract hashtags from tweet text.

        Args:
            text: Full tweet text

        Returns:
            List of hashtag strings without # prefix
        """
        hashtags = re.findall(r'#(\w+)', text)
        # Return unique hashtags preserving order
        seen = set()
        result = []
        for tag in hashtags:
            lower_tag = tag.lower()
            if lower_tag not in seen:
                seen.add(lower_tag)
                result.append(tag)
        return result

    def _format_content(self, bookmark: "Bookmark") -> str:
        """Format tweet content as markdown.

        Args:
            bookmark: The bookmark containing tweet data

        Returns:
            Formatted markdown content
        """
        lines = []

        # Author info
        author_display = bookmark.author_name or bookmark.author_username
        lines.append(f"**{author_display}** (@{bookmark.author_username})")
        lines.append("")

        # Tweet text
        lines.append(bookmark.text)
        lines.append("")

        # Images if present
        if bookmark.media_urls:
            lines.append("### Images")
            for url in bookmark.media_urls:
                lines.append(f"![image]({url})")
            lines.append("")

        # Source link
        lines.append(f"[View original tweet]({bookmark.url})")

        return '\n'.join(lines)
