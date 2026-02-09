"""Link processor for URL content extraction.

Processes bookmarks classified as LINK content type.
Fetches URLs and extracts clean text content, then uses LLM
to extract structured information (title, TL;DR, key points, tags).
"""

import re
import time
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlparse

import httpx

from src.core.exceptions import ExtractionError
from src.core.http_client import create_client
from src.core.link_cache import LinkCache
from src.core.llm_client import LLMClient, get_llm_client
from src.processors.base import BaseProcessor, ProcessResult

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark
    from src.core.content_fetcher import AsyncContentFetcher
    from src.core.smart_prompts import SmartPromptSelector


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

    Fetches URLs and extracts clean text content, then uses LLM
    to extract structured information (title, TL;DR, key points, tags).
    """

    # Default timeout for fetching URLs
    DEFAULT_TIMEOUT = 30

    # System prompt for LLM extraction
    EXTRACTION_PROMPT = """You are analyzing web page content. Extract the following information:

1. title: A clear, descriptive title for the article (max 10 words)
2. tldr: A 2-3 sentence summary of the main points
3. key_points: 3-5 bullet points capturing the key insights
4. tags: 3-5 relevant topic tags (lowercase, no # symbol)

Return your response as a JSON object with these exact keys.

Example response:
{
  "title": "Understanding Python Async Programming",
  "tldr": "This article explains async/await in Python. It covers the event loop, coroutines, and practical patterns for concurrent code.",
  "key_points": [
    "Async functions use await to pause execution",
    "The event loop manages concurrent tasks",
    "asyncio.gather runs multiple coroutines in parallel"
  ],
  "tags": ["python", "async", "concurrency", "programming"]
}"""

    def __init__(
        self,
        timeout: Optional[int] = None,
        llm_client: Optional[LLMClient] = None,
        cache: Optional[LinkCache] = None,
        content_fetcher: Optional["AsyncContentFetcher"] = None,
        smart_prompts: Optional["SmartPromptSelector"] = None,
    ):
        """Initialize link processor.

        Args:
            timeout: Fetch timeout in seconds (default: 30)
            llm_client: Optional LLMClient for content extraction. If not provided,
                       will try to use global singleton (fails gracefully if unavailable).
            cache: Optional LinkCache for caching LLM extraction results.
                   If provided, will check cache before calling LLM.
            content_fetcher: Optional AsyncContentFetcher for enhanced URL extraction
                            (paywall bypass, GitHub/YouTube handlers). Falls back to
                            basic httpx fetch when not provided.
            smart_prompts: Optional SmartPromptSelector class for content-type-aware
                          prompt generation. Falls back to generic EXTRACTION_PROMPT
                          when not provided.
        """
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self._llm_client = llm_client
        self._cache = cache
        self._content_fetcher = content_fetcher
        self._smart_prompts = smart_prompts

    async def process(self, bookmark: "Bookmark") -> ProcessResult:
        """Process a link bookmark by fetching and extracting content.

        Uses AsyncContentFetcher for enhanced extraction when available,
        otherwise falls back to basic httpx fetch.

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
            # Enhanced path: use AsyncContentFetcher for richer extraction
            fetched_content = None
            if self._content_fetcher is not None:
                fetched_content = await self._content_fetcher.fetch_content(link_url)

            if fetched_content and fetched_content.main_content and not fetched_content.fetch_error:
                text = fetched_content.main_content
                html_title = fetched_content.title or self._generate_title(text)
            else:
                # Fallback: basic httpx fetch
                html = await self._fetch_url(link_url)
                text = self._extract_text(html)
                html_title = self._extract_title(html) or self._generate_title(text)

            # Use LLM to extract structured content (checks cache first)
            # When smart_prompts is available, use content-type-aware prompt
            llm_data = self._extract_with_llm(
                text,
                url=link_url,
                bookmark=bookmark,
                fetched_content=fetched_content,
            )

            # Use LLM title if available, otherwise fallback to HTML title
            title = llm_data.get("title") or html_title

            # Get LLM-extracted fields with fallbacks
            tldr = llm_data.get("tldr", "")
            key_points = llm_data.get("key_points", [])
            tags = llm_data.get("tags", [])

            # Build metadata
            metadata = {
                "source_url": link_url,
                "raw_text": text,
                "tldr": tldr,
                "key_points": key_points,
            }

            # Add enriched metadata from content fetcher
            if fetched_content:
                if fetched_content.content_type != "unknown":
                    metadata["fetched_content_type"] = fetched_content.content_type
                if fetched_content.code_blocks:
                    metadata["code_blocks"] = fetched_content.code_blocks
                if fetched_content.lists_extracted:
                    metadata["lists_extracted"] = fetched_content.lists_extracted
                if fetched_content.paywall_detected:
                    metadata["paywall_detected"] = True

            # Format content with LLM-enhanced data
            content = self._format_content(bookmark, link_url, text, tldr, key_points)

            duration_ms = int((time.perf_counter() - start_time) * 1000)

            return ProcessResult(
                success=True,
                content=content,
                title=title,
                tags=tags,
                duration_ms=duration_ms,
                metadata=metadata,
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

    def _extract_with_llm(
        self,
        text: str,
        url: Optional[str] = None,
        bookmark: Optional["Bookmark"] = None,
        fetched_content: Any = None,
    ) -> dict[str, Any]:
        """Extract structured content using LLM.

        Checks cache first if available. Caches successful extractions.
        When smart_prompts is available, uses content-type-aware prompts
        for better extraction quality.

        Args:
            text: Raw text content from web page
            url: URL being processed (used as cache key)
            bookmark: Optional bookmark for smart prompt context
            fetched_content: Optional FetchedContent for enriched extraction

        Returns:
            Dict with title, tldr, key_points, tags (empty values if LLM unavailable)
        """
        if not text or len(text.strip()) < 50:
            # Not enough content to analyze
            return {}

        # Check cache first if available and URL is provided
        if self._cache is not None and url:
            cached_data = self._cache.get(url)
            if cached_data is not None:
                return cached_data

        # Get LLM client (use injected or global singleton)
        llm_client = self._llm_client
        if llm_client is None:
            try:
                llm_client = get_llm_client()
            except Exception:
                # LLM not available (no API key, etc.) - return empty
                return {}

        # Truncate text to avoid token limits (first 4000 chars)
        truncated_text = text[:4000]
        if len(text) > 4000:
            truncated_text += "\n\n[Content truncated...]"

        # Choose prompt: smart prompts (content-type-aware) or generic
        prompt = self.EXTRACTION_PROMPT
        if self._smart_prompts is not None and bookmark is not None:
            smart_prompt, _ = self._smart_prompts.build_prompt(
                bookmark.text,
                author=bookmark.author_username,
                has_link=True,
                link_content=truncated_text,
            )
            prompt = smart_prompt

        try:
            result = llm_client.extract_structured(truncated_text, prompt)
            validated = self._validate_llm_response(result)

            # Cache the validated result if cache is available and URL is provided
            if self._cache is not None and url and validated:
                self._cache.set(url, validated)

            return validated
        except ExtractionError:
            # LLM extraction failed - return empty (graceful degradation)
            return {}

    def _validate_llm_response(self, result: dict[str, Any]) -> dict[str, Any]:
        """Validate and sanitize LLM response.

        Args:
            result: Raw dict from LLM

        Returns:
            Sanitized dict with validated fields
        """
        validated: dict[str, Any] = {}

        # Validate title (string, max 100 chars)
        title = result.get("title")
        if isinstance(title, str) and title.strip():
            validated["title"] = title.strip()[:100]

        # Validate tldr (string, max 500 chars)
        tldr = result.get("tldr")
        if isinstance(tldr, str) and tldr.strip():
            validated["tldr"] = tldr.strip()[:500]

        # Validate key_points (list of strings, max 5)
        key_points = result.get("key_points")
        if isinstance(key_points, list):
            valid_points = [
                str(p).strip()[:200]
                for p in key_points
                if isinstance(p, str) and p.strip()
            ]
            validated["key_points"] = valid_points[:5]

        # Validate tags (list of strings, max 5, lowercase)
        tags = result.get("tags")
        if isinstance(tags, list):
            valid_tags = [
                str(t).strip().lower().lstrip("#")[:30]
                for t in tags
                if isinstance(t, str) and t.strip()
            ]
            validated["tags"] = valid_tags[:5]

        return validated

    def _format_content(
        self,
        bookmark: "Bookmark",
        url: str,
        text: str,
        tldr: str = "",
        key_points: list[str] | None = None,
    ) -> str:
        """Format link content as markdown.

        Args:
            bookmark: Original bookmark
            url: Fetched URL
            text: Extracted text content
            tldr: LLM-generated TL;DR summary
            key_points: LLM-generated key points

        Returns:
            Formatted markdown content
        """
        lines = []

        # Source info
        lines.append(f"**Source**: [{url}]({url})")
        lines.append("")

        # TL;DR if available
        if tldr:
            lines.append("### TL;DR")
            lines.append(tldr)
            lines.append("")

        # Key points if available
        if key_points:
            lines.append("### Key Points")
            for point in key_points:
                lines.append(f"- {point}")
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
