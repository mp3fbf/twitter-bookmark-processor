"""Deterministically scan legacy Twitter Source-note coverage."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_BOOKMARK_ID = re.compile(
    r"^bookmark_id:\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s#]+))\s*$",
    re.MULTILINE,
)
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_KNOWLEDGE_END = re.compile(r"^## (?:Original|Media|Sources)\b", re.MULTILINE)


@dataclass(frozen=True)
class NoteCoverage:
    bookmark_id: str
    path: Path
    thin: bool


@dataclass(frozen=True)
class NoteCoverageScan:
    entries: tuple[NoteCoverage, ...]
    scanned: int
    malformed: int
    duplicates: int
    thin_requeued: int


def _parse(path: Path) -> NoteCoverage | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    closing_index = next(
        (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        return None
    frontmatter = "".join(lines[1:closing_index])
    matches = list(_BOOKMARK_ID.finditer(frontmatter))
    if len(matches) != 1:
        return None
    match = matches[0]
    bookmark_id = next(value for value in match.groups() if value is not None).strip()
    if _SAFE_ID.fullmatch(bookmark_id) is None:
        return None
    body = "".join(lines[closing_index + 1 :])
    knowledge = _KNOWLEDGE_END.split(body, maxsplit=1)[0]
    thin = "thin-content" in frontmatter or (
        len(knowledge.strip()) < 600 and "t.co/" in knowledge
    )
    return NoteCoverage(bookmark_id=bookmark_id, path=path.resolve(), thin=thin)


def scan_note_coverage(
    notes_dir: str | Path,
    *,
    redo_thin: bool = False,
) -> NoteCoverageScan:
    """Read Markdown frontmatter only for identity; port the legacy thin heuristic."""
    root = Path(notes_dir)
    if not root.is_dir():
        raise ValueError(f"notes directory does not exist: {root}")
    candidates: dict[str, NoteCoverage] = {}
    malformed = 0
    duplicates = 0
    thin_requeued = 0
    paths = sorted(root.rglob("*.md"), key=lambda path: str(path))
    for path in paths:
        parsed = _parse(path)
        if parsed is None:
            malformed += 1
            continue
        existing = candidates.get(parsed.bookmark_id)
        if existing is not None:
            duplicates += 1
            # A non-thin duplicate is sufficient evidence that the bookmark is done.
            if existing.thin and not parsed.thin:
                candidates[parsed.bookmark_id] = parsed
            continue
        candidates[parsed.bookmark_id] = parsed
    entries: list[NoteCoverage] = []
    for bookmark_id in sorted(candidates):
        candidate = candidates[bookmark_id]
        if redo_thin and candidate.thin:
            thin_requeued += 1
            continue
        entries.append(candidate)
    return NoteCoverageScan(
        entries=tuple(entries),
        scanned=len(paths),
        malformed=malformed,
        duplicates=duplicates,
        thin_requeued=thin_requeued,
    )
