"""Insight Writer — renders InsightNote + ContentPackage into Obsidian markdown.

Uses the insight.md.j2 Jinja2 template. Reuses sanitize_filename and
yaml_escape from the existing ObsidianWriter module.

Graph enrichment (wikilinks, hierarchical tags, MOC) is applied via
graph_enricher.enrich() — same as the legacy ObsidianWriter.
"""

import logging
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.insight.models import ContentPackage, InsightNote
from src.output.graph_enricher import enrich
from src.output.obsidian_writer import TEMPLATES_DIR, sanitize_filename, _yaml_escape_filter

logger = logging.getLogger(__name__)


class InsightWriter:
    """Writes InsightNote as Obsidian markdown notes."""

    def __init__(self, output_dir: Path):
        self._output_dir = output_dir
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        self._env.filters["yaml_escape"] = _yaml_escape_filter

    def write(self, note: InsightNote, package: ContentPackage) -> Path:
        """Write an InsightNote as an Obsidian markdown file.

        Returns the path to the created file.
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Filename from title
        safe_title = sanitize_filename(note.title)
        filename = f"{safe_title}.md"
        output_path = self._output_dir / filename

        # Handle collision
        if output_path.exists():
            filename = f"{safe_title} - {package.bookmark_id}.md"
            output_path = self._output_dir / filename

        # Build full body from sections for graph analysis
        body = "\n\n".join(
            f"## {s.heading}\n\n{s.content}" for s in note.sections
        )

        # Enrich with graph metadata (wikilinks, hierarchical tags, MOC)
        graph = enrich(
            title=note.title,
            body=body,
            content_type="insight",
            author_username=package.author_username,
        )

        # Merge tags: enricher (structural) first, then Opus (specific), no dupes
        seen = set()
        merged_tags = []
        for tag in graph["tags"] + note.tags:
            if tag not in seen:
                seen.add(tag)
                merged_tags.append(tag)

        # Build template context
        source_links = []
        for link in package.resolved_links:
            if not link.fetch_error:
                source_links.append({
                    "url": link.resolved_url,
                    "title": link.title,
                })

        media_urls = [img.url for img in package.analyzed_images]

        context = {
            "title": note.title,
            "author": f"@{package.author_username}",
            "source": package.tweet_url,
            "value_type": note.value_type.value,
            "tags": merged_tags,
            "wikilinks": graph["wikilinks"],
            "moc": graph["moc"],
            "tweet_date": package.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "bookmark_id": package.bookmark_id,
            "sections": [
                {"heading": s.heading, "content": s.content}
                for s in note.sections
            ],
            "original_content": note.original_content,
            "media_urls": media_urls,
            "source_links": source_links,
        }

        template = self._env.get_template("insight.md.j2")
        content = template.render(**context)

        output_path.write_text(content, encoding="utf-8")
        logger.info("Wrote insight note: %s", output_path)

        return output_path
