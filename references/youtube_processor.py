#!/home/claude/.claude/skills/youtube-video/venv/bin/python3
"""YouTube Video Processor using Gemini API

Process YouTube videos to extract transcriptions, summaries, and generate
Obsidian-formatted notes.

Usage:
    youtube_processor.py <url> [--note] [--clip START-END] [--api-key KEY]

Examples:
    youtube_processor.py "https://youtube.com/watch?v=xxx"
    youtube_processor.py "https://youtube.com/watch?v=xxx" --note
    youtube_processor.py "https://youtube.com/watch?v=xxx" --clip 10:00-15:00
    youtube_processor.py "https://youtube.com/watch?v=xxx" --api-key YOUR_KEY

Environment:
    GOOGLE_API_KEY - API key for Gemini (alternative to --api-key)
"""

import os
import re
import sys
import json
import argparse
from datetime import datetime
from typing import Optional, Dict, Any

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Error: google-genai package not installed.")
    print("Install with: pip install google-genai")
    sys.exit(1)


# Model configuration - uses latest alias for auto-updates
MODEL = "gemini-flash-latest"


def slugify(text: str, max_length: int = 60) -> str:
    """Convert text to a safe filename slug."""
    # Remove special characters, keep alphanumeric and spaces
    slug = re.sub(r'[^\w\s-]', '', text)
    # Replace spaces with hyphens
    slug = re.sub(r'[\s_]+', '-', slug)
    # Remove consecutive hyphens
    slug = re.sub(r'-+', '-', slug)
    # Trim and lowercase
    slug = slug.strip('-').lower()
    # Truncate to max length
    if len(slug) > max_length:
        slug = slug[:max_length].rsplit('-', 1)[0]
    return slug


class YouTubeProcessor:
    """Process YouTube videos using Gemini API."""

    # 1Password secret reference
    OP_SECRET_REF = "op://Dev/Claude Code Video Extractor/GOOGLE_API_KEY"

    def __init__(self, api_key: Optional[str] = None):
        """Initialize processor with API key."""
        self.api_key = api_key or os.getenv("GOOGLE_API_KEY") or self._get_api_key_from_1password()
        if not self.api_key:
            raise ValueError(
                "GOOGLE_API_KEY not found. Set via:\n"
                "  1. --api-key argument\n"
                "  2. GOOGLE_API_KEY environment variable\n"
                "  3. 1Password (requires OP_SERVICE_ACCOUNT_TOKEN)"
            )
        self.client = genai.Client(api_key=self.api_key)

    def _get_api_key_from_1password(self) -> Optional[str]:
        """Try to fetch API key from 1Password using op CLI."""
        if not os.getenv("OP_SERVICE_ACCOUNT_TOKEN"):
            return None
        try:
            import subprocess
            result = subprocess.run(
                ["op", "read", self.OP_SECRET_REF],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def validate_youtube_url(self, url: str) -> bool:
        """Validate if URL is a valid YouTube URL."""
        patterns = [
            r'(https?://)?(www\.)?youtube\.com/watch\?v=[\w-]+',
            r'(https?://)?(www\.)?youtu\.be/[\w-]+',
            r'(https?://)?(www\.)?youtube\.com/embed/[\w-]+',
            r'(https?://)?(www\.)?youtube\.com/v/[\w-]+',
        ]
        return any(re.match(p, url) for p in patterns)

    def parse_time_to_seconds(self, time_str: str) -> int:
        """Convert MM:SS or HH:MM:SS to seconds."""
        parts = time_str.split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return 0

    def process_video(
        self,
        url: str,
        mode: str = "transcript",
        clip_start: Optional[str] = None,
        clip_end: Optional[str] = None
    ) -> Dict[str, Any]:
        """Process a YouTube video.

        Args:
            url: YouTube video URL
            mode: "transcript" for basic, "note" for Obsidian format
            clip_start: Start time for clipping (MM:SS or HH:MM:SS)
            clip_end: End time for clipping (MM:SS or HH:MM:SS)

        Returns:
            Dictionary with processed content
        """
        if not self.validate_youtube_url(url):
            raise ValueError(f"Invalid YouTube URL: {url}")

        # Build the prompt based on mode
        if mode == "note":
            prompt = self._get_obsidian_prompt()
        else:
            prompt = self._get_transcript_prompt()

        # Build video part with optional clipping
        video_part = types.Part(
            file_data=types.FileData(file_uri=url)
        )

        # Add video metadata for clipping if specified
        if clip_start and clip_end:
            start_seconds = self.parse_time_to_seconds(clip_start)
            end_seconds = self.parse_time_to_seconds(clip_end)
            video_part = types.Part(
                file_data=types.FileData(file_uri=url),
                video_metadata=types.VideoMetadata(
                    start_offset=f"{start_seconds}s",
                    end_offset=f"{end_seconds}s"
                )
            )

        # Make API request
        response = self.client.models.generate_content(
            model=f"models/{MODEL}",
            contents=types.Content(
                parts=[
                    video_part,
                    types.Part(text=prompt)
                ]
            ),
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=self._get_schema(mode)
            )
        )

        # Parse response
        try:
            result = json.loads(response.text)
            result['source_url'] = url
            result['processed_at'] = datetime.now().isoformat()
            result['mode'] = mode
            return result
        except json.JSONDecodeError:
            return {
                'raw_response': response.text,
                'source_url': url,
                'processed_at': datetime.now().isoformat(),
                'mode': mode
            }

    def _get_transcript_prompt(self) -> str:
        """Get prompt for transcription mode."""
        return """Analyze this YouTube video and provide a detailed transcription.

Requirements:
1. Extract the video title and channel name
2. Estimate the video duration
3. Provide a summary in 3-5 sentences
4. List 5-10 key points with timestamps (format: MM:SS)
5. Provide full transcription with timestamps and speaker identification when possible
6. Detect the primary language

Be accurate with timestamps and speaker changes."""

    def _get_obsidian_prompt(self) -> str:
        """Get prompt for Obsidian note mode."""
        return """Analyze this YouTube video and extract content for an Obsidian note.

Requirements:
1. Extract video title and channel name
2. Create a TL;DR summary (2-3 sentences)
3. List 5-10 key points/takeaways
4. Generate 3-7 relevant tags for the content
5. Identify main topics discussed
6. Note any important quotes or statements
7. Suggest 2-3 related topics that could link to other notes
8. Provide detailed notes organized by topic/section

Format for knowledge management and future reference."""

    def _get_schema(self, mode: str) -> types.Schema:
        """Get JSON schema for structured output."""
        if mode == "note":
            return types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "title": types.Schema(type=types.Type.STRING),
                    "channel": types.Schema(type=types.Type.STRING),
                    "duration": types.Schema(type=types.Type.STRING),
                    "language": types.Schema(type=types.Type.STRING),
                    "tldr": types.Schema(type=types.Type.STRING),
                    "key_points": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING)
                    ),
                    "tags": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING)
                    ),
                    "topics": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING)
                    ),
                    "quotes": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING)
                    ),
                    "related_topics": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING)
                    ),
                    "detailed_notes": types.Schema(type=types.Type.STRING),
                },
                required=["title", "channel", "tldr", "key_points", "tags", "detailed_notes"]
            )
        else:
            return types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "title": types.Schema(type=types.Type.STRING),
                    "channel": types.Schema(type=types.Type.STRING),
                    "duration": types.Schema(type=types.Type.STRING),
                    "language": types.Schema(type=types.Type.STRING),
                    "summary": types.Schema(type=types.Type.STRING),
                    "key_points": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "timestamp": types.Schema(type=types.Type.STRING),
                                "content": types.Schema(type=types.Type.STRING)
                            }
                        )
                    ),
                    "transcript": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(
                            type=types.Type.OBJECT,
                            properties={
                                "timestamp": types.Schema(type=types.Type.STRING),
                                "speaker": types.Schema(type=types.Type.STRING),
                                "content": types.Schema(type=types.Type.STRING)
                            }
                        )
                    ),
                },
                required=["title", "summary", "key_points"]
            )

    def format_transcript_output(self, data: Dict[str, Any]) -> str:
        """Format transcript data as markdown."""
        output = []

        title = data.get('title', 'Unknown Video')
        output.append(f"## Transcription: {title}\n")

        if data.get('channel'):
            output.append(f"**Channel**: {data['channel']}")
        if data.get('duration'):
            output.append(f"**Duration**: {data['duration']}")
        if data.get('language'):
            output.append(f"**Language**: {data['language']}")
        output.append(f"**Source**: {data.get('source_url', 'N/A')}\n")

        if data.get('summary'):
            output.append("### Summary")
            output.append(f"{data['summary']}\n")

        if data.get('key_points'):
            output.append("### Key Points")
            for point in data['key_points']:
                if isinstance(point, dict):
                    ts = point.get('timestamp', '')
                    content = point.get('content', '')
                    output.append(f"- [{ts}] {content}")
                else:
                    output.append(f"- {point}")
            output.append("")

        if data.get('transcript'):
            output.append("### Transcript")
            for entry in data['transcript']:
                ts = entry.get('timestamp', '')
                speaker = entry.get('speaker', 'Speaker')
                content = entry.get('content', '')
                output.append(f"[{ts}] **{speaker}**: {content}")

        return '\n'.join(output)

    def format_obsidian_note(self, data: Dict[str, Any]) -> str:
        """Format data as Obsidian note with frontmatter."""
        output = []

        # YAML Frontmatter
        output.append("---")
        output.append(f'title: "{data.get("title", "YouTube Video")}"')
        output.append(f"source: {data.get('source_url', '')}")
        output.append(f"channel: \"{data.get('channel', '')}\"")
        output.append(f"date: {datetime.now().strftime('%Y-%m-%d')}")

        tags = data.get('tags', [])
        if tags:
            tags_str = ', '.join(tags[:7])  # Limit to 7 tags
            output.append(f"tags: [{tags_str}]")

        output.append("type: video")
        output.append("status: processed")
        output.append("---\n")

        # Title
        output.append(f"# {data.get('title', 'YouTube Video')}\n")

        # TL;DR
        if data.get('tldr'):
            output.append("## TL;DR")
            output.append(f"{data['tldr']}\n")

        # Key Points
        if data.get('key_points'):
            output.append("## Key Points")
            for i, point in enumerate(data['key_points'], 1):
                output.append(f"{i}. {point}")
            output.append("")

        # Quotes
        if data.get('quotes'):
            output.append("## Notable Quotes")
            for quote in data['quotes']:
                output.append(f"> {quote}")
            output.append("")

        # Detailed Notes
        if data.get('detailed_notes'):
            output.append("## Notes")
            output.append(data['detailed_notes'])
            output.append("")

        # Related Topics
        if data.get('related_topics'):
            output.append("## Related")
            for topic in data['related_topics']:
                # Format as Obsidian wiki-links
                output.append(f"- [[{topic}]]")
            output.append("")

        # Metadata footer
        output.append("---")
        output.append(f"*Processed: {data.get('processed_at', datetime.now().isoformat())}*")

        return '\n'.join(output)


def main():
    parser = argparse.ArgumentParser(
        description="Process YouTube videos using Gemini API"
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "--note",
        action="store_true",
        help="Generate Obsidian note format"
    )
    parser.add_argument(
        "--clip",
        help="Clip video (format: START-END, e.g., 10:00-15:00)"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of formatted markdown"
    )
    parser.add_argument(
        "--api-key",
        help="Google API key (alternative to GOOGLE_API_KEY env var)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output directory or file path. If directory, filename is auto-generated from video title."
    )

    args = parser.parse_args()

    # Parse clip times if provided
    clip_start = None
    clip_end = None
    if args.clip:
        try:
            clip_start, clip_end = args.clip.split('-')
        except ValueError:
            print("Error: --clip format should be START-END (e.g., 10:00-15:00)")
            sys.exit(1)

    try:
        processor = YouTubeProcessor(api_key=args.api_key)

        mode = "note" if args.note else "transcript"
        print(f"Processing video in '{mode}' mode...", file=sys.stderr)

        result = processor.process_video(
            url=args.url,
            mode=mode,
            clip_start=clip_start,
            clip_end=clip_end
        )

        # Format output
        if args.json:
            output_content = json.dumps(result, indent=2, ensure_ascii=False)
            extension = ".json"
        elif args.note:
            output_content = processor.format_obsidian_note(result)
            extension = ".md"
        else:
            output_content = processor.format_transcript_output(result)
            extension = ".md"

        # Save to file or print to stdout
        if args.output:
            from pathlib import Path
            output_path = Path(args.output)

            # If it's a directory, generate filename from title
            if output_path.is_dir() or args.output.endswith('/'):
                output_path.mkdir(parents=True, exist_ok=True)
                title = result.get('title', 'youtube-video')
                filename = slugify(title) + extension
                output_path = output_path / filename

            # Write file
            output_path.write_text(output_content, encoding='utf-8')
            print(f"Saved: {output_path}", file=sys.stderr)
        else:
            print(output_content)

    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error processing video: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
