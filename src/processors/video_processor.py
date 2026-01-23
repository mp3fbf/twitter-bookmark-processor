"""Video processor for YouTube content.

Processes bookmarks classified as VIDEO content type.
Calls the /youtube-video skill via subprocess to extract content.
"""

import asyncio
import json
import re
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.core.exceptions import SkillError
from src.processors.base import BaseProcessor, ProcessResult

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark


class VideoProcessor(BaseProcessor):
    """Processor for video content (YouTube links).

    Uses the /youtube-video skill to process videos via Gemini API.
    The skill extracts transcription, summary, and generates Obsidian notes.
    """

    # Path to the youtube-video skill script
    SKILL_SCRIPT = Path.home() / ".claude/skills/youtube-video/scripts/youtube_processor.py"

    # Default timeout for skill execution (5 minutes for long videos)
    DEFAULT_TIMEOUT = 300

    def __init__(self, timeout: Optional[int] = None, output_dir: Optional[Path] = None):
        """Initialize video processor.

        Args:
            timeout: Skill execution timeout in seconds (default: 300)
            output_dir: Directory where skill should save generated .md files
        """
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.output_dir = output_dir

    async def process(self, bookmark: "Bookmark") -> ProcessResult:
        """Process a video bookmark by calling the youtube-video skill.

        Args:
            bookmark: The video bookmark to process

        Returns:
            ProcessResult with extracted content and metadata
        """
        start_time = time.perf_counter()

        # Get YouTube URL from bookmark
        youtube_url = self._get_youtube_url(bookmark)
        if not youtube_url:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error="No YouTube URL found in bookmark",
                duration_ms=duration_ms,
            )

        try:
            # Call skill and get JSON output
            data, output_file = await self._call_skill(youtube_url)

            # Parse output into ProcessResult
            process_result = self._parse_skill_output(data)
            process_result.duration_ms = int((time.perf_counter() - start_time) * 1000)
            process_result.output_file = output_file

            return process_result

        except asyncio.TimeoutError:
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

    def _get_youtube_url(self, bookmark: "Bookmark") -> Optional[str]:
        """Extract YouTube URL from bookmark.

        Checks video_urls first, then falls back to links.

        Args:
            bookmark: The bookmark to extract URL from

        Returns:
            YouTube URL if found, None otherwise
        """
        # Check video_urls first (from classifier)
        for url in bookmark.video_urls:
            if "youtube.com" in url or "youtu.be" in url:
                return url

        # Fall back to links
        for url in bookmark.links:
            if "youtube.com" in url or "youtu.be" in url:
                return url

        return None

    async def _call_skill(self, url: str) -> tuple[dict, Optional[Path]]:
        """Call the youtube-video skill via subprocess.

        Args:
            url: YouTube URL to process

        Returns:
            Tuple of (parsed JSON output, path to generated file if any)

        Raises:
            SkillError: If skill execution fails
            asyncio.TimeoutError: If skill times out
        """
        cmd = [
            "python3",
            str(self.SKILL_SCRIPT),
            url,
            "--json",
        ]

        # If output_dir specified, ask skill to save file there
        if self.output_dir:
            cmd.extend(["-o", str(self.output_dir)])

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

        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else "Unknown skill error"
            raise SkillError(f"youtube-video skill failed: {error_msg}")

        # Parse JSON output
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise SkillError(f"Failed to parse skill output: {e}")

        # Extract generated file path from stderr (format: "Saved: /path/to/file.md")
        output_file = self._extract_output_file(result.stderr)

        return data, output_file

    def _extract_output_file(self, stderr: str) -> Optional[Path]:
        """Extract generated file path from skill stderr.

        The skill outputs "Saved: /path/to/file.md" to stderr when saving.

        Args:
            stderr: Stderr output from skill

        Returns:
            Path to generated file if found, None otherwise
        """
        if not stderr:
            return None

        match = re.search(r"Saved:\s*(.+\.(?:md|json))", stderr)
        if match:
            path = Path(match.group(1).strip())
            if path.exists():
                return path
        return None

    def _parse_skill_output(self, data: dict) -> ProcessResult:
        """Parse skill JSON output into ProcessResult.

        Args:
            data: JSON data from skill

        Returns:
            ProcessResult with extracted content
        """
        # Extract title
        title = data.get("title", "Untitled Video")

        # Extract tags (hierarchical tags from skill)
        tags = []
        for tag in data.get("tags", []):
            # Convert hierarchical tags like "topic/ai" to just "ai"
            if "/" in tag:
                tags.append(tag.split("/")[-1])
            else:
                tags.append(tag)

        # Build content from available data
        content = self._format_content(data)

        return ProcessResult(
            success=True,
            content=content,
            title=title,
            tags=tags,
        )

    def _format_content(self, data: dict) -> str:
        """Format skill output as markdown content.

        Args:
            data: JSON data from skill

        Returns:
            Formatted markdown content
        """
        lines = []

        # Video info
        if data.get("channel"):
            lines.append(f"**Channel**: {data['channel']}")
        if data.get("duration"):
            lines.append(f"**Duration**: {data['duration']}")
        lines.append("")

        # TL;DR or Summary
        tldr = data.get("tldr") or data.get("summary")
        if tldr:
            lines.append("## TL;DR")
            lines.append(tldr)
            lines.append("")

        # Key points
        if data.get("key_points"):
            lines.append("## Key Points")
            for point in data["key_points"]:
                if isinstance(point, dict):
                    # Transcript mode with timestamps
                    ts = point.get("timestamp", "")
                    content = point.get("content", "")
                    lines.append(f"- [{ts}] {content}")
                else:
                    # Note mode
                    lines.append(f"- {point}")
            lines.append("")

        # Source link
        if data.get("source_url"):
            lines.append(f"[Watch on YouTube]({data['source_url']})")

        return "\n".join(lines)
