"""Legacy Source-note coverage scanner contracts."""

from pathlib import Path

from bookmark_automation.note_coverage import scan_note_coverage


def test_short_knowledge_with_unfollowed_tco_uses_the_legacy_thin_heuristic(
    tmp_path: Path,
) -> None:
    (tmp_path / "thin.md").write_text(
        "---\nbookmark_id: 123456\n---\n\n"
        "## The Knowledge\n\nPointer https://t.co/unfollowed\n\n"
        "## Original\n\nOriginal tweet",
        encoding="utf-8",
    )
    (tmp_path / "good.md").write_text(
        "---\nbookmark_id: 789012\n---\n\n" + ("Substantive knowledge. " * 50),
        encoding="utf-8",
    )

    scan = scan_note_coverage(tmp_path, redo_thin=True)

    assert [entry.bookmark_id for entry in scan.entries] == ["789012"]
    assert scan.thin_requeued == 1
    assert scan.malformed == 0
