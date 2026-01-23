"""Link processor for URL content extraction.

Processes bookmarks classified as LINK content type.
Fetches URLs and extracts clean text content.
"""

import re
import time
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

import httpx

from src.core.http_client import create_client
from src.processors.base import BaseProcessor, ProcessResult

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark


class HTMLTextExtractor(HTMLParser):
    """Extract text content from HTML, ignoring scripts and styles."""

    # Tags whose content should be skipped (not void elements)
    _SKIP_TAGS = {"script", "style", "noscript", "head"}

    # Void elements (self-closing, no end tag) - don't add to stack
    _VOID_ELEMENTS = {"meta", "link", "br", "hr", "img", "input", "area", "base", "col", "embed", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.text_parts: list[str] = []
        self._skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag_lower = tag.lower()
        # Only add non-void elements to skip stack
        if tag_lower in self._SKIP_TAGS:
            self._skip_stack.append(tag_lower)

    def handle_endtag(self, tag: str) -> None:
        tag_lower = tag.lower()
        # Only pop if we have a matching tag in the stack
        if tag_lower in self._SKIP_TAGS and self._skip_stack and self._skip_stack[-1] == tag_lower:
            self._skip_stack.pop()

    def handle_data(self, data: str) -> None:
        if not self._skip_stack:
            text = data.strip()
            if text:
                self.text_parts.append(text)

    def get_text(self) -> str:
        """Get extracted text joined with spaces."""
        return " ".join(self.text_parts)


class LinkProcessor(BaseProcessor):
    """Processor for link/article content.

    Fetches URLs and extracts clean text content for further processing.
    Does NOT use LLM - that's handled by issue #29.
    """

    # Default timeout for fetching URLs
    DEFAULT_TIMEOUT = 30

    def __init__(self, timeout: Optional[int] = None):
        """Initialize link processor.

        Args:
            timeout: Fetch timeout in seconds (default: 30)
        """
        self.timeout = timeout or self.DEFAULT_TIMEOUT

    async def process(self, bookmark: "Bookmark") -> ProcessResult:
        """Process a link bookmark by fetching and extracting content.

        Args:
            bookmark: The link bookmark to process

        Returns:
            ProcessResult with extracted content
        """
        start_time = time.perf_counter()

        # Get link URL from bookmark
        link_url = self._get_link_url(bookmark)
        if not link_url:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error="No external URL found in bookmark",
                duration_ms=duration_ms,
            )

        try:
            # Fetch HTML content
            html = await self._fetch_url(link_url)

            # Extract text from HTML
            text = self._extract_text(html)

            # Extract title
            title = self._extract_title(html) or self._generate_title(text)

            # Format content
            content = self._format_content(bookmark, link_url, text)

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            return ProcessResult(
                success=True,
                content=content,
                title=title,
                tags=[],  # Tags will be extracted by LLM in issue #29
                duration_ms=duration_ms,
                metadata={
                    "source_url": link_url,
                    "raw_text": text,
                },
            )

        except httpx.TimeoutException:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=f"Timeout fetching URL after {self.timeout}s",
                duration_ms=duration_ms,
            )
        except httpx.HTTPStatusError as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=f"HTTP error {e.response.status_code}: {e.response.reason_phrase}",
                duration_ms=duration_ms,
            )
        except httpx.RequestError as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=f"Request error: {e}",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=f"Unexpected error: {e}",
                duration_ms=duration_ms,
            )

    def _get_link_url(self, bookmark: "Bookmark") -> Optional[str]:
        """Extract external URL from bookmark.

        Skips Twitter/X URLs as those are handled by other processors.

        Args:
            bookmark: The bookmark to extract URL from

        Returns:
            External URL if found, None otherwise
        """
        # Check bookmark links first (more likely to be external)
        for url in bookmark.links:
            if not self._is_twitter_url(url):
                return url

        # Fall back to main URL if not Twitter
        if bookmark.url and not self._is_twitter_url(bookmark.url):
            return bookmark.url

        return None

    def _is_twitter_url(self, url: str) -> bool:
        """Check if URL is a Twitter/X URL.

        Args:
            url: URL to check

        Returns:
            True if Twitter/X URL, False otherwise
        """
        parsed = urlparse(url)
        return parsed.netloc in ("twitter.com", "x.com", "www.twitter.com", "www.x.com")

    async def _fetch_url(self, url: str) -> str:
        """Fetch HTML content from URL.

        Args:
            url: URL to fetch

        Returns:
            HTML content as string

        Raises:
            httpx.TimeoutException: On timeout
            httpx.HTTPStatusError: On 4xx/5xx response
            FetchError: On other fetch errors
        """
        timeout = httpx.Timeout(
            connect=10.0,
            read=float(self.timeout),
            write=10.0,
            pool=10.0,
        )

        async with create_client(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text

    def _extract_text(self, html: str) -> str:
        """Extract clean text from HTML.

        Args:
            html: HTML content

        Returns:
            Extracted text content
        """
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()

        # Clean up excessive whitespace
        text = re.sub(r"\s+", " ", text).strip()

        return text

    def _extract_title(self, html: str) -> Optional[str]:
        """Extract title from HTML.

        Args:
            html: HTML content

        Returns:
            Page title if found, None otherwise
        """
        # Try <title> tag
        match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if match:
            title = match.group(1).strip()
            # Clean up common patterns
            title = re.sub(r"\s*[|\-–—]\s*.*$", "", title)  # Remove site name after separator
            return title.strip() if title.strip() else None

        # Try og:title
        match = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()

        return None

    def _generate_title(self, text: str) -> str:
        """Generate title from text content.

        Args:
            text: Extracted text content

        Returns:
            Generated title (first 8 words)
        """
        words = text.split()[:8]
        if words:
            title = " ".join(words)
            if len(text.split()) > 8:
                title += "..."
            return title
        return "Untitled Link"

    def _format_content(self, bookmark: "Bookmark", url: str, text: str) -> str:
        """Format link content as markdown.

        Args:
            bookmark: Original bookmark
            url: Fetched URL
            text: Extracted text content

        Returns:
            Formatted markdown content
        """
        lines = []

        # Source info
        lines.append(f"**Source**: [{url}]({url})")
        lines.append("")

        # Tweet context if available
        if bookmark.text:
            lines.append("### Tweet Context")
            lines.append(f"> {bookmark.text}")
            lines.append("")

        # Extracted content (truncated preview)
        lines.append("### Content Preview")
        preview = text[:1000] + "..." if len(text) > 1000 else text
        lines.append(preview)
        lines.append("")

        return "\n".join(lines)
