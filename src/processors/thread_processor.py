"""Thread processor for Twitter threads.

Processes bookmarks classified as THREAD content type.
Calls the /twitter skill via subprocess to extract thread content.
"""

import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.core.exceptions import ExtractionError, SkillError
from src.core.llm_client import LLMClient, get_llm_client
from src.processors.base import BaseProcessor, ProcessResult

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark


class ThreadProcessor(BaseProcessor):
    """Processor for thread content (Twitter threads).

    Uses the /twitter skill to read threads via bird CLI or fallbacks.
    The skill extracts all tweets in the thread with metadata.
    """

    # Path to the twitter skill script
    SKILL_SCRIPT = Path.home() / ".claude/skills/twitter/scripts/twitter_reader.py"

    # Default timeout for skill execution (30 seconds for threads)
    DEFAULT_TIMEOUT = 30

    def __init__(
        self,
        timeout: Optional[int] = None,
        output_dir: Optional[Path] = None,
        llm_client: Optional[LLMClient] = None,
    ):
        """Initialize thread processor.

        Args:
            timeout: Skill execution timeout in seconds (default: 30)
            output_dir: Directory where output should be saved (not used by twitter skill)
            llm_client: Optional LLMClient for key points extraction. If not provided,
                       will try to use global singleton (fails gracefully if unavailable).
        """
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.output_dir = output_dir
        self._llm_client = llm_client

    async def process(self, bookmark: "Bookmark") -> ProcessResult:
        """Process a thread bookmark by calling the twitter skill.

        Args:
            bookmark: The thread bookmark to process

        Returns:
            ProcessResult with extracted thread content and metadata
        """
        start_time = time.perf_counter()

        # Get thread URL from bookmark
        thread_url = self._get_thread_url(bookmark)
        if not thread_url:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error="No Twitter/X URL found in bookmark",
                duration_ms=duration_ms,
            )

        try:
            # Call skill and get JSON output
            data = await self._call_skill(thread_url)

            # Parse output into ProcessResult
            process_result = self._parse_skill_output(data)
            process_result.duration_ms = int((time.perf_counter() - start_time) * 1000)

            return process_result

        except (asyncio.TimeoutError, subprocess.TimeoutExpired):
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=f"Skill timeout after {self.timeout}s",
                duration_ms=duration_ms,
            )
        except SkillError as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=f"Unexpected error: {e}",
                duration_ms=duration_ms,
            )

    def _get_thread_url(self, bookmark: "Bookmark") -> Optional[str]:
        """Extract Twitter/X URL from bookmark.

        Args:
            bookmark: The bookmark to extract URL from

        Returns:
            Twitter/X URL if found, None otherwise
        """
        # Use the bookmark's main URL (which should be the tweet URL)
        if bookmark.url:
            if "twitter.com" in bookmark.url or "x.com" in bookmark.url:
                return bookmark.url

        # Fall back to links
        for url in bookmark.links:
            if "twitter.com" in url or "x.com" in url:
                return url

        return None

    async def _call_skill(self, url: str) -> dict:
        """Call the twitter skill via subprocess.

        Args:
            url: Twitter/X URL to process as thread

        Returns:
            Parsed JSON output from skill

        Raises:
            SkillError: If skill execution fails or thread is deleted
            asyncio.TimeoutError: If skill times out
        """
        cmd = [
            "python3",
            str(self.SKILL_SCRIPT),
            url,
            "--thread",
            "--json",
        ]

        # Run subprocess in thread pool to not block event loop
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            ),
            timeout=self.timeout + 5,  # Extra buffer for executor overhead
        )

        # Parse JSON output regardless of return code (may have error details)
        try:
            data = json.loads(result.stdout) if result.stdout else {}
        except json.JSONDecodeError:
            data = {}

        # Check for skill failure (non-zero exit or success=False in output)
        if result.returncode != 0:
            error_msg = data.get("error") or result.stderr.strip() or "Unknown skill error"
            raise SkillError(f"twitter skill failed: {error_msg}")

        if not data.get("success", True):
            error_msg = data.get("error", "Thread not found or deleted")
            raise SkillError(f"Thread error: {error_msg}")

        return data

    def _parse_skill_output(self, data: dict) -> ProcessResult:
        """Parse skill JSON output into ProcessResult.

        Args:
            data: JSON data from skill

        Returns:
            ProcessResult with extracted content
        """
        tweets = data.get("tweets", [])
        author = data.get("author", "unknown")

        # Generate title from first tweet (truncated)
        title = self._generate_title(tweets, author)

        # Extract tags from thread content
        tags = self._extract_tags(tweets)

        # Build content from all tweets
        content = self._format_content(data, tweets)

        # Extract key points using LLM (if available)
        key_points = self._extract_key_points(tweets)

        # Build metadata for template rendering
        metadata = {
            "tweets": tweets,
            "tweet_count": len(tweets),
            "author": author,
            "source": data.get("source", ""),
            "key_points": key_points,
        }

        return ProcessResult(
            success=True,
            content=content,
            title=title,
            tags=tags,
            metadata=metadata,
        )

    def _generate_title(self, tweets: list, author: str) -> str:
        """Generate a title from the first tweet.

        Args:
            tweets: List of tweet dicts
            author: Thread author username

        Returns:
            Generated title (max 8 words from first tweet)
        """
        if not tweets:
            return f"Thread by @{author}"

        first_tweet = tweets[0]
        text = first_tweet.get("text", "")

        # Clean text (remove URLs, mentions at start)
        clean_text = re.sub(r"https?://\S+", "", text)
        clean_text = re.sub(r"^(@\w+\s*)+", "", clean_text).strip()

        # Get first 8 words
        words = clean_text.split()[:8]
        if words:
            title = " ".join(words)
            if len(clean_text.split()) > 8:
                title += "..."
            return title

        return f"Thread by @{author}"

    def _extract_tags(self, tweets: list) -> list[str]:
        """Extract hashtags from all tweets as tags.

        Args:
            tweets: List of tweet dicts

        Returns:
            List of unique tags (without #)
        """
        tags = set()
        for tweet in tweets:
            text = tweet.get("text", "")
            hashtags = re.findall(r"#(\w+)", text)
            tags.update(tag.lower() for tag in hashtags)

        return list(tags)

    def _format_content(self, data: dict, tweets: list) -> str:
        """Format thread as markdown content.

        Args:
            data: Full JSON data from skill
            tweets: List of tweet dicts

        Returns:
            Formatted markdown content
        """
        lines = []
        author = data.get("author", "unknown")
        source = data.get("source", "")

        # Thread info
        lines.append(f"**Author**: @{author}")
        lines.append(f"**Tweets**: {len(tweets)}")
        if source:
            lines.append(f"**Source**: {source}")
        lines.append("")

        # Each tweet numbered
        for i, tweet in enumerate(tweets, 1):
            lines.append(f"### Tweet {i}")
            lines.append("")

            # Tweet text as blockquote
            text = tweet.get("text", "")
            if text:
                lines.append("> " + text.replace("\n", "\n> "))
                lines.append("")

            # Media
            media_urls = tweet.get("media_urls", [])
            if media_urls:
                for url in media_urls:
                    lines.append(f"![image]({url})")
                lines.append("")

            # Links (excluding twitter.com)
            links = tweet.get("links", [])
            external_links = [
                link for link in links
                if "twitter.com" not in link and "x.com" not in link
            ]
            if external_links:
                lines.append("**Links:**")
                for link in external_links:
                    lines.append(f"- {link}")
                lines.append("")

        # Original thread URL
        if tweets and tweets[0].get("url"):
            lines.append("---")
            lines.append(f"[View original thread]({tweets[0]['url']})")

        return "\n".join(lines)

    def _extract_key_points(self, tweets: list) -> list[str]:
        """Extract key points from thread content using LLM.

        Args:
            tweets: List of tweet dicts

        Returns:
            List of key points (3-5 bullet points), empty if LLM unavailable or fails
        """
        if not tweets:
            return []

        # Get LLM client (use injected or global singleton)
        llm_client = self._llm_client
        if llm_client is None:
            try:
                llm_client = get_llm_client()
            except Exception:
                # LLM not available (no API key, etc.) - return empty
                return []

        # Build thread text for analysis
        thread_text = "\n\n".join(
            f"Tweet {i}: {tweet.get('text', '')}"
            for i, tweet in enumerate(tweets, 1)
        )

        system_prompt = """You are analyzing a Twitter thread. Extract 3-5 key points that summarize the main ideas.

Return your response as a JSON object with a single key "key_points" containing an array of strings.
Each key point should be a concise sentence (under 100 characters).

Example response:
{
  "key_points": [
    "First key insight from the thread",
    "Second important point",
    "Third takeaway"
  ]
}"""

        try:
            result = llm_client.extract_structured(thread_text, system_prompt)
            key_points = result.get("key_points", [])
            # Validate: must be list of strings
            if isinstance(key_points, list) and all(isinstance(p, str) for p in key_points):
                return key_points[:5]  # Max 5 points
            return []
        except ExtractionError:
            # LLM extraction failed - return empty (graceful degradation)
            return []
