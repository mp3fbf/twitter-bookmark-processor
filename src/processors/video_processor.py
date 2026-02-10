"""Video processor for YouTube and Twitter native video content.

Processes bookmarks classified as VIDEO content type.
- YouTube videos: Calls /youtube-video skill (Gemini direct URL mode)
- Twitter native videos: Downloads with yt-dlp, uploads to Gemini File API
"""

import asyncio
import json
import logging
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.core.exceptions import SkillError
from src.processors.base import BaseProcessor, ProcessResult

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark

logger = logging.getLogger(__name__)


class VideoProcessor(BaseProcessor):
    """Processor for video content (YouTube and Twitter native).

    YouTube: Uses the /youtube-video skill via subprocess (Gemini direct URL).
    Twitter: Downloads MP4 with yt-dlp → uploads to Gemini File API → processes.
    """

    SKILL_SCRIPT = Path.home() / ".claude/skills/youtube-video/scripts/youtube_processor.py"
    DEFAULT_TIMEOUT = 300

    def __init__(self, timeout: Optional[int] = None, output_dir: Optional[Path] = None):
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.output_dir = output_dir

    async def process(self, bookmark: "Bookmark") -> ProcessResult:
        start_time = time.perf_counter()

        youtube_url = self._get_youtube_url(bookmark)
        if youtube_url:
            return await self._process_youtube(youtube_url, start_time)

        # Twitter native video — download + Gemini
        return await self._process_twitter_video(bookmark, start_time)

    # ── YouTube (existing flow) ──────────────────────────────────────

    def _get_youtube_url(self, bookmark: "Bookmark") -> Optional[str]:
        for url in bookmark.video_urls:
            if "youtube.com" in url or "youtu.be" in url:
                return url
        for url in bookmark.links:
            if "youtube.com" in url or "youtu.be" in url:
                return url
        return None

    async def _process_youtube(self, youtube_url: str, start_time: float) -> ProcessResult:
        try:
            data, output_file = await self._call_skill(youtube_url)
            result = self._parse_skill_output(data)
            result.duration_ms = int((time.perf_counter() - start_time) * 1000)
            result.output_file = output_file
            return result
        except asyncio.TimeoutError:
            return ProcessResult(
                success=False,
                error=f"Skill timeout after {self.timeout}s",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )
        except SkillError as e:
            return ProcessResult(
                success=False,
                error=str(e),
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )
        except Exception as e:
            return ProcessResult(
                success=False,
                error=f"Unexpected error: {e}",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

    async def _call_skill(self, url: str) -> tuple[dict, Optional[Path]]:
        cmd = [sys.executable, str(self.SKILL_SCRIPT), url, "--json"]
        if self.output_dir:
            cmd.extend(["-o", str(self.output_dir)])

        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout),
            ),
            timeout=self.timeout + 5,
        )

        if result.returncode != 0:
            error_msg = (
                result.stderr.strip()
                or result.stdout.strip()
                or "Unknown skill error (no output)"
            )
            raise SkillError(f"youtube-video skill failed: {error_msg}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise SkillError(f"Failed to parse skill output: {e}")

        output_file = self._extract_output_file(result.stderr)
        return data, output_file

    # ── Twitter native video ─────────────────────────────────────────

    async def _process_twitter_video(
        self, bookmark: "Bookmark", start_time: float
    ) -> ProcessResult:
        """Download Twitter video with yt-dlp, upload to Gemini, process."""
        try:
            # Download video
            video_path = await self._download_video(bookmark)
            if not video_path:
                return ProcessResult(
                    success=False,
                    error="Failed to download video from tweet",
                    duration_ms=int((time.perf_counter() - start_time) * 1000),
                )

            try:
                # Process with Gemini
                data = await self._process_with_gemini(video_path, bookmark)
                data["source_url"] = bookmark.url
                result = self._parse_skill_output(data)
                result.duration_ms = int((time.perf_counter() - start_time) * 1000)
                return result
            finally:
                # Cleanup temp file
                if video_path.exists():
                    video_path.unlink()

        except asyncio.TimeoutError:
            return ProcessResult(
                success=False,
                error=f"Twitter video processing timeout after {self.timeout}s",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )
        except Exception as e:
            return ProcessResult(
                success=False,
                error=f"Twitter video error: {e}",
                duration_ms=int((time.perf_counter() - start_time) * 1000),
            )

    async def _download_video(self, bookmark: "Bookmark") -> Optional[Path]:
        """Download video using yt-dlp from tweet URL."""
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        cmd = [
            "yt-dlp",
            "--no-warnings",
            "-f", "best[height<=480][ext=mp4]/best[height<=480]/best[ext=mp4]/best",
            "-S", "res:480",
            "-o", str(tmp_path),
            "--no-playlist",
            "--no-part",
            bookmark.url,
        ]

        loop = asyncio.get_event_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout),
                ),
                timeout=self.timeout + 5,
            )
            if result.returncode != 0:
                logger.warning("yt-dlp failed for %s: %s", bookmark.url, result.stderr[:200])
                tmp_path.unlink(missing_ok=True)
                return None

            if tmp_path.exists() and tmp_path.stat().st_size > 0:
                logger.info("Downloaded video: %s (%d bytes)", tmp_path.name, tmp_path.stat().st_size)
                return tmp_path

        except (asyncio.TimeoutError, subprocess.TimeoutExpired):
            logger.warning("yt-dlp timeout for %s", bookmark.url)
            tmp_path.unlink(missing_ok=True)

        return None

    async def _process_with_gemini(
        self, video_path: Path, bookmark: "Bookmark"
    ) -> dict:
        """Upload video to Gemini File API and process."""
        loop = asyncio.get_event_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, self._gemini_sync, video_path, bookmark),
            timeout=self.timeout,
        )

    def _gemini_sync(self, video_path: Path, bookmark: "Bookmark") -> dict:
        """Synchronous Gemini processing (runs in executor)."""
        import os

        # Get API key same way as youtube skill
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key and os.environ.get("OP_SERVICE_ACCOUNT_TOKEN"):
            try:
                r = subprocess.run(
                    ["op", "read", "op://Dev/Claude Code Video Extractor/GOOGLE_API_KEY"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    api_key = r.stdout.strip()
            except Exception:
                pass

        if not api_key:
            raise SkillError("GOOGLE_API_KEY not available for Twitter video processing")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        # Upload file
        logger.info("Uploading video to Gemini File API...")
        uploaded = client.files.upload(file=video_path, config={"mime_type": "video/mp4"})
        logger.info("Upload complete: %s", uploaded.name)

        # Wait for processing
        import time as _time

        for _ in range(60):  # max 5 min wait
            uploaded = client.files.get(name=uploaded.name)
            if uploaded.state.name == "ACTIVE":
                break
            _time.sleep(5)
        else:
            raise SkillError("Gemini file processing timed out")

        # Build prompt with tweet context
        tweet_text = bookmark.text[:500] if bookmark.text else ""
        author = bookmark.author_name or bookmark.author_handle or "unknown"

        prompt = f"""Analyze this Twitter/X video posted by @{author}.

Tweet context: "{tweet_text}"

Requirements:
1. Provide the video title (infer from content/tweet)
2. Identify the speaker/creator
3. Create a TL;DR summary (2-3 sentences)
4. List 5-10 key points/takeaways
5. Generate tags following hierarchy: topic/X, person/X
6. Extract any important quotes
7. Provide detailed notes

This is a native Twitter video, not a YouTube video."""

        schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "title": types.Schema(type=types.Type.STRING),
                "channel": types.Schema(type=types.Type.STRING, description="Author/creator"),
                "duration": types.Schema(type=types.Type.STRING),
                "language": types.Schema(type=types.Type.STRING),
                "tldr": types.Schema(type=types.Type.STRING),
                "key_points": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                ),
                "tags": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                ),
                "quotes": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                ),
                "detailed_notes": types.Schema(type=types.Type.STRING),
            },
            required=["title", "tldr", "key_points", "tags"],
        )

        response = client.models.generate_content(
            model="models/gemini-3-pro-preview",
            contents=types.Content(
                parts=[
                    types.Part(file_data=types.FileData(file_uri=uploaded.uri)),
                    types.Part(text=prompt),
                ]
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )

        # Cleanup uploaded file
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

        result = json.loads(response.text)
        result["processed_at"] = __import__("datetime").datetime.now().isoformat()
        result["mode"] = "note"
        return result

    # ── Shared helpers ───────────────────────────────────────────────

    def _extract_output_file(self, stderr: str) -> Optional[Path]:
        if not stderr:
            return None
        match = re.search(r"Saved:\s*(.+\.(?:md|json))", stderr)
        if match:
            path = Path(match.group(1).strip())
            if path.exists():
                return path
        return None

    def _parse_skill_output(self, data: dict) -> ProcessResult:
        title = data.get("title", "Untitled Video")
        tags = []
        for tag in data.get("tags", []):
            if "/" in tag:
                tags.append(tag.split("/")[-1])
            else:
                tags.append(tag)

        content = self._format_content(data)
        return ProcessResult(success=True, content=content, title=title, tags=tags)

    def _format_content(self, data: dict) -> str:
        lines = []

        if data.get("channel"):
            lines.append(f"**Channel**: {data['channel']}")
        if data.get("duration"):
            lines.append(f"**Duration**: {data['duration']}")
        lines.append("")

        tldr = data.get("tldr") or data.get("summary")
        if tldr:
            lines.append("## TL;DR")
            lines.append(tldr)
            lines.append("")

        if data.get("key_points"):
            lines.append("## Key Points")
            for point in data["key_points"]:
                if isinstance(point, dict):
                    ts = point.get("timestamp", "")
                    content = point.get("content", "")
                    lines.append(f"- [{ts}] {content}")
                else:
                    lines.append(f"- {point}")
            lines.append("")

        if data.get("quotes"):
            lines.append("## Notable Quotes")
            for quote in data["quotes"]:
                lines.append(f"> {quote}")
            lines.append("")

        if data.get("detailed_notes"):
            lines.append("## Notes")
            lines.append(data["detailed_notes"])
            lines.append("")

        if data.get("source_url"):
            lines.append(f"[Source]({data['source_url']})")

        return "\n".join(lines)
