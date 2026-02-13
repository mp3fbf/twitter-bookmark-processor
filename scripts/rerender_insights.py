"""Re-render existing Insight Engine notes with graph enrichment.

Reads each content package + parses the existing note to reconstruct
InsightNote, then re-renders using the updated InsightWriter (which now
includes wikilinks, hierarchical tags, and MOC from graph_enricher).

Usage:
    python3 scripts/rerender_insights.py [--dry-run] [--limit N]
"""

import argparse
import json
import logging
import re
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.insight.models import ContentPackage, InsightNote, Section, ValueType
from src.insight.writer import InsightWriter

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NOTES_DIR = Path("notes/Sources/twitter")  # relative, or override via --output-dir
STATE_FILE = Path("data/insight_state.json")
PACKAGES_DIR = Path("data/content_packages")


def parse_existing_note(path: Path) -> dict | None:
    """Parse an existing insight note to extract InsightNote fields.

    Returns dict with value_type, title, sections, tags, original_content
    or None if parsing fails.
    """
    text = path.read_text(encoding="utf-8")

    # Split frontmatter from body
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter_raw = parts[1].strip()
    body = parts[2]

    # Parse frontmatter manually (avoid yaml dependency)
    fm = {}
    for line in frontmatter_raw.split("\n"):
        line = line.strip()
        if ":" in line and not line.startswith("-"):
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")

    # Extract tags from frontmatter
    tags = []
    in_tags = False
    for line in frontmatter_raw.split("\n"):
        stripped = line.strip()
        if stripped == "tags:":
            in_tags = True
            continue
        if in_tags:
            if stripped.startswith("- "):
                tags.append(stripped[2:].strip().strip('"').strip("'"))
            else:
                in_tags = False

    # Extract value_type
    vt_str = fm.get("value_type", "reference")
    try:
        value_type = ValueType(vt_str)
    except ValueError:
        value_type = ValueType.REFERENCE

    # Extract title
    title = fm.get("title", path.stem)

    # Extract sections from body (## headings before the --- separator)
    sections = []
    # Split body at first "---" (which separates content sections from Original)
    content_part = body.split("\n---\n", 1)[0] if "\n---\n" in body else body

    current_heading = None
    current_lines = []

    for line in content_part.split("\n"):
        if line.startswith("## ") and line.strip() != "## Original":
            if current_heading:
                sections.append(Section(
                    heading=current_heading,
                    content="\n".join(current_lines).strip(),
                ))
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading:
            current_lines.append(line)

    if current_heading:
        sections.append(Section(
            heading=current_heading,
            content="\n".join(current_lines).strip(),
        ))

    # Extract original content (after ## Original)
    original = ""
    if "## Original" in body:
        orig_part = body.split("## Original", 1)[1]
        # Get blockquote content
        orig_lines = []
        for line in orig_part.split("\n"):
            if line.startswith("> "):
                orig_lines.append(line[2:])
            elif line.strip() == ">" or line.strip() == "":
                if orig_lines:
                    orig_lines.append("")
            elif orig_lines and line.startswith("## "):
                break
        original = "\n".join(orig_lines).strip()

    # Filter out enricher tags that we'll regenerate (avoid double-adding)
    opus_tags = [
        t for t in tags
        if not t.startswith("source/")
        and not t.startswith("topic/")
        and not t.startswith("person/")
        and not t.startswith("twitter/")
    ]

    return {
        "value_type": value_type,
        "title": title,
        "sections": sections,
        "tags": opus_tags,
        "original_content": original or title,
    }


def main():
    parser = argparse.ArgumentParser(description="Re-render insight notes with graph enrichment")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--limit", type=int, default=0, help="Limit notes to process")
    parser.add_argument("--output-dir", type=Path, default=None, help="Output directory (default: from state)")
    args = parser.parse_args()

    # Load state
    if not STATE_FILE.exists():
        logger.error("State file not found: %s", STATE_FILE)
        return 1

    with open(STATE_FILE) as f:
        state = json.load(f)

    processed = state.get("processed", {})
    logger.info("Found %d processed bookmarks in state", len(processed))

    # Determine output dir
    output_dir = args.output_dir or NOTES_DIR
    writer = InsightWriter(output_dir=output_dir)

    rerendered = 0
    skipped = 0
    errors = 0

    for bid, entry in processed.items():
        if args.limit and rerendered >= args.limit:
            break

        if entry.get("error") or entry.get("distill", {}).get("status") != "done":
            skipped += 1
            continue

        # Find content package
        pkg_path = PACKAGES_DIR / f"{bid}.json"
        if not pkg_path.exists():
            logger.warning("Missing content package for %s", bid)
            skipped += 1
            continue

        # Find existing note
        output_path = entry.get("output_path")
        if not output_path:
            skipped += 1
            continue

        # Map old paths to current output dir
        note_name = Path(output_path).name
        existing = output_dir / note_name
        if not existing.exists():
            # Try alternate locations
            for candidate_dir in [Path("/workspace/notes/Sources/twitter"), Path("/workspace/notes/twitter")]:
                candidate = candidate_dir / note_name
                if candidate.exists():
                    existing = candidate
                    break

        if not existing.exists():
            logger.warning("Note file not found: %s", note_name[:60])
            skipped += 1
            continue

        # Parse existing note
        parsed = parse_existing_note(existing)
        if not parsed:
            logger.warning("Failed to parse: %s", note_name[:60])
            errors += 1
            continue

        # Load content package
        try:
            with open(pkg_path) as f:
                pkg_data = json.load(f)
            pkg = ContentPackage(**pkg_data)
        except Exception as e:
            logger.warning("Bad content package %s: %s", bid, e)
            errors += 1
            continue

        # Reconstruct InsightNote
        note = InsightNote(
            value_type=parsed["value_type"],
            title=parsed["title"],
            sections=parsed["sections"],
            tags=parsed["tags"],
            original_content=parsed["original_content"],
        )

        if args.dry_run:
            from src.output.graph_enricher import enrich
            body = "\n\n".join(f"## {s.heading}\n\n{s.content}" for s in note.sections)
            graph = enrich(note.title, body, "insight", pkg.author_username)
            wikilinks = ", ".join(f"[[{w}]]" for w in graph["wikilinks"])
            moc = graph["moc"] or "none"
            logger.info("  [DRY] %s → MOC: %s | Links: %s", note_name[:60], moc, wikilinks or "none")
            rerendered += 1
            continue

        # Delete old file, write new one
        existing.unlink(missing_ok=True)

        new_path = writer.write(note, pkg)
        rerendered += 1

        # Update state with new path
        entry["output_path"] = str(new_path)

    # Save updated state
    if not args.dry_run and rerendered > 0:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
        logger.info("Updated state file")

    logger.info("Done: %d rerendered, %d skipped, %d errors", rerendered, skipped, errors)
    return 0


if __name__ == "__main__":
    sys.exit(main())
