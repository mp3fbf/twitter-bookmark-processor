"""Obsidian Writer for Twitter Bookmark Processor.

Generates markdown files with YAML frontmatter for Obsidian.
Each processed bookmark becomes a note in the output directory.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark
    from src.processors.base import ProcessResult


def sanitize_filename(text: str) -> str:
    """Convert text to a safe filename.

    Removes/replaces characters that are invalid in filenames.

    Args:
        text: Original text to convert

    Returns:
        Safe filename string
    """
    # Remove or replace invalid filename characters
    # Invalid: / \ : * ? " < > |
    invalid_chars = r'[/\\:*?"<>|]'
    safe = re.sub(invalid_chars, '', text)

    # Replace multiple spaces/underscores with single space
    safe = re.sub(r'[\s_]+', ' ', safe)

    # Strip leading/trailing whitespace
    safe = safe.strip()

    # Truncate to reasonable length (200 chars max)
    if len(safe) > 200:
        safe = safe[:200].rsplit(' ', 1)[0]  # Don't cut mid-word

    return safe if safe else "untitled"


def escape_yaml_string(value: str) -> str:
    """Escape a string for YAML frontmatter.

    Handles special characters that would break YAML parsing.

    Args:
        value: String to escape

    Returns:
        Escaped string safe for YAML
    """
    # If string contains special chars, wrap in quotes
    special_chars = [':', '#', '[', ']', '{', '}', ',', '&', '*', '!', '|', '>', "'", '"']
    needs_quotes = any(c in value for c in special_chars) or value.startswith('@')

    if needs_quotes:
        # Escape double quotes and wrap in double quotes
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'

    return value


class ObsidianWriter:
    """Writes processed bookmarks as Obsidian markdown notes.

    Creates markdown files with YAML frontmatter containing metadata,
    followed by the processed content body.
    """

    def __init__(self, output_dir: Path):
        """Initialize writer with output directory.

        Args:
            output_dir: Directory where notes will be written
        """
        self.output_dir = output_dir

    def write(
        self,
        bookmark: "Bookmark",
        result: "ProcessResult",
    ) -> Path:
        """Write a processed bookmark as an Obsidian note.

        Creates a markdown file with:
        - YAML frontmatter (metadata)
        - Content body from ProcessResult

        Args:
            bookmark: Original bookmark data
            result: Processing result with content and tags

        Returns:
            Path to the created file
        """
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Generate filename from title
        title = result.title or "Untitled"
        safe_title = sanitize_filename(title)
        filename = f"{safe_title}.md"
        output_path = self.output_dir / filename

        # Handle filename collisions by appending bookmark ID
        if output_path.exists():
            filename = f"{safe_title} - {bookmark.id}.md"
            output_path = self.output_dir / filename

        # Build frontmatter
        frontmatter = self._build_frontmatter(bookmark, result)

        # Build full content
        content = self._build_content(frontmatter, result.content or "")

        # Write file
        output_path.write_text(content, encoding="utf-8")

        return output_path

    def _build_frontmatter(
        self,
        bookmark: "Bookmark",
        result: "ProcessResult",
    ) -> str:
        """Build YAML frontmatter for the note.

        Args:
            bookmark: Original bookmark data
            result: Processing result with tags

        Returns:
            YAML frontmatter string including delimiters
        """
        lines = ["---"]

        # Title
        title = result.title or "Untitled"
        lines.append(f"title: {escape_yaml_string(title)}")

        # Author
        lines.append(f"author: {escape_yaml_string('@' + bookmark.author_username)}")

        # Source URL
        lines.append(f"source: {bookmark.url}")

        # Content type
        lines.append(f"type: {bookmark.content_type.value}")

        # Tags
        if result.tags:
            lines.append("tags:")
            for tag in result.tags:
                lines.append(f"  - {escape_yaml_string(tag)}")

        # Dates
        if bookmark.created_at:
            lines.append(f"tweet_date: {escape_yaml_string(bookmark.created_at)}")

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"processed_at: {now}")

        # Bookmark ID for reference
        lines.append(f"tweet_id: {bookmark.id}")

        lines.append("---")

        return "\n".join(lines)

    def _build_content(self, frontmatter: str, body: str) -> str:
        """Combine frontmatter and body into final content.

        Args:
            frontmatter: YAML frontmatter with delimiters
            body: Markdown content body

        Returns:
            Complete markdown file content
        """
        return f"{frontmatter}\n\n{body}\n"
