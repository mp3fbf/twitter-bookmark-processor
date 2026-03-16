#!/usr/bin/env python3
"""Render an InsightNote JSON into an Obsidian markdown note.

Takes bookmark_id and InsightNote JSON, uses InsightWriter to render.
Deletes old note if it exists in insight_state.

Usage:
    echo '{"value_type":"tip","title":"...","sections":[...],"tags":[...],"original_content":"..."}' | \
        python3 scripts/render_note.py <bookmark_id>
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.insight.capture import ContentCapture
from src.insight.models import InsightNote
from src.insight.pipeline import InsightState
from src.insight.writer import InsightWriter

OUTPUT_DIR = Path("/workspace/notes/Sources/twitter")
STATE_FILE = Path("data/insight_state.json")


def main():
    if len(sys.argv) < 2:
        print("Usage: echo JSON | python3 scripts/render_note.py <bookmark_id>", file=sys.stderr)
        sys.exit(1)

    bookmark_id = sys.argv[1]

    # Read InsightNote JSON from stdin
    note_json = sys.stdin.read().strip()
    note = InsightNote.model_validate_json(note_json)

    # Load content package
    package = ContentCapture.load_package(bookmark_id)
    if not package:
        print(f"ERROR: No content package for {bookmark_id}", file=sys.stderr)
        sys.exit(1)

    # Delete old note if exists
    state = InsightState(STATE_FILE)
    old_entry = state.get(bookmark_id)
    if old_entry and old_entry.get("output_path"):
        old_path = Path(old_entry["output_path"])
        # Also check container-mapped path
        if not old_path.exists() and "/Users/robertoscunha/projects/" in str(old_path):
            old_path = Path(str(old_path).replace("/Users/robertoscunha/projects/", "/workspace/"))
        if old_path.exists():
            old_path.unlink()

    # Write new note
    writer = InsightWriter(OUTPUT_DIR)
    output_path = writer.write(note, package)

    # Update state
    state.mark_distill_done(
        bookmark_id,
        value_type=note.value_type.value,
        output_path=str(output_path),
    )

    print(f"OK {bookmark_id} -> {output_path.name}")


if __name__ == "__main__":
    main()
