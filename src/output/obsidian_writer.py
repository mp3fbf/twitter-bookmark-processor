"""Obsidian Writer for Twitter Bookmark Processor.

Generates markdown files with YAML frontmatter for Obsidian.
Each processed bookmark becomes a note in the output directory.
Uses Jinja2 templates for flexible content generation.
"""

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark
    from src.processors.base import ProcessResult

# Processor version for footer
PROCESSOR_VERSION = "0.1.0"

# Path to templates directory
TEMPLATES_DIR = Path(__file__).parent / "templates"


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


def _yaml_escape_filter(value: str) -> str:
    """Jinja2 filter for YAML escaping."""
    return escape_yaml_string(value)


def _create_jinja_env() -> Environment:
    """Create and configure Jinja2 environment.

    Returns:
        Configured Jinja2 Environment
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.filters['yaml_escape'] = _yaml_escape_filter
    return env


class ObsidianWriter:
    """Writes processed bookmarks as Obsidian markdown notes.

    Creates markdown files with YAML frontmatter containing metadata,
    followed by the processed content body. Uses Jinja2 templates
    for flexible formatting per content type.
    """

    def __init__(self, output_dir: Path):
        """Initialize writer with output directory.

        Args:
            output_dir: Directory where notes will be written
        """
        self.output_dir = output_dir
        self._env = _create_jinja_env()

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

        # Render content using template
        content = self._render_template(bookmark, result)

        # Write file
        output_path.write_text(content, encoding="utf-8")

        return output_path

    def _get_template_name(self, bookmark: "Bookmark") -> str:
        """Get the template name for the bookmark's content type.

        Args:
            bookmark: Bookmark to get template for

        Returns:
            Template filename
        """
        # Map content type to template
        # For now, all types use tweet.md.j2
        # Future: thread.md.j2, video.md.j2, link.md.j2
        return "tweet.md.j2"

    def _render_template(
        self,
        bookmark: "Bookmark",
        result: "ProcessResult",
    ) -> str:
        """Render a template with bookmark and result data.

        Args:
            bookmark: Original bookmark data
            result: Processing result with content and tags

        Returns:
            Rendered markdown content
        """
        template_name = self._get_template_name(bookmark)
        template = self._env.get_template(template_name)

        # Prepare context for template
        title = result.title or "Untitled"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Extract TL;DR from content (first line or title)
        body = result.content or ""
        tldr = self._extract_tldr(body, title)

        context = {
            "title": title,
            "author": "@" + bookmark.author_username,
            "source": bookmark.url,
            "content_type": bookmark.content_type.value,
            "tags": result.tags or [],
            "tweet_date": bookmark.created_at,
            "processed_at": now,
            "tweet_id": bookmark.id,
            "processor_version": PROCESSOR_VERSION,
            "tldr": tldr,
            "body": body,
        }

        return template.render(**context)

    def _extract_tldr(self, content: str, title: str) -> str:
        """Extract a TL;DR summary from content.

        Args:
            content: Full content body
            title: Title as fallback

        Returns:
            Short summary string
        """
        if not content:
            return title

        # Use first non-empty line as TL;DR if it's short enough
        lines = content.strip().split('\n')
        for line in lines:
            line = line.strip()
            # Skip markdown formatting lines
            if line and not line.startswith('#') and not line.startswith('**'):
                if len(line) <= 280:  # Tweet-length limit
                    return line
                # Truncate long lines
                return line[:277] + "..."

        return title
