"""Capture, retrieval, and sensemaking boundary contracts."""

from datetime import UTC, datetime
from pathlib import Path

from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore


def test_deep_input_consumes_separate_content_and_recall_receipts(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000040",
            "text": "Article about durable knowledge",
            "urls": [{"expanded_url": "https://example.test/durable"}],
        }
    )
    jobs = {job.task_kind: job for job in store.list_jobs()}
    store.complete_effect(
        job_id=jobs["fetch_article"].id,
        effect_kind="fetch_article",
        payload={"status": "available", "title": "Durable Knowledge", "text": "Full text"},
        now=now,
    )
    store.complete_effect(
        job_id=jobs["recall_context"].id,
        effect_kind="recall_context",
        payload={"status": "available", "hits": [{"path": "Concepts/Memory.md"}]},
        now=now,
    )

    deep_input = store.job_input(jobs["deep"])

    assert deep_input["bookmark"]["id"] == "1900000000000000040"
    assert deep_input["upstream"]["fetch_article"]["text"] == "Full text"
    assert deep_input["upstream"]["recall_context"]["hits"][0]["path"] == "Concepts/Memory.md"
    assert deep_input["evidence_status"]["fetch_article"] == "available"
    assert deep_input["evidence_status"]["recall_context"] == "available"


def test_recall_query_falls_back_mechanically_to_bookmark_text(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000041",
            "text": "Durable agent memory with consolidation and archival",
        }
    )
    recall_job = next(job for job in store.list_jobs() if job.task_kind == "recall_context")

    query = store.recall_query(recall_job)

    assert query == "Durable agent memory with consolidation and archival"


def test_each_job_keeps_the_payload_snapshot_for_its_input_revision(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store)
    bookmark_id = "1900000000000000042"
    automation.ingest({"kind": "bookmarks", "id": bookmark_id, "text": "Revision one"})
    automation.ingest({"kind": "bookmarks", "id": bookmark_id, "text": "Revision two"})
    quick_jobs = sorted(
        (job for job in store.list_jobs() if job.task_kind == "quick"),
        key=lambda job: job.id,
    )

    first_input = store.job_input(quick_jobs[0])
    second_input = store.job_input(quick_jobs[1])

    assert first_input["bookmark"]["text"] == "Revision one"
    assert second_input["bookmark"]["text"] == "Revision two"


def test_deep_context_uses_upstream_receipts_from_the_same_revision_only(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000043"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Revision one",
            "urls": [{"expanded_url": "https://example.test/one"}],
        }
    )
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Revision two",
            "urls": [{"expanded_url": "https://example.test/two"}],
        }
    )
    jobs = store.list_jobs()
    fetch_jobs = sorted(
        (job for job in jobs if job.task_kind == "fetch_article"), key=lambda job: job.id
    )
    deep_jobs = sorted((job for job in jobs if job.task_kind == "deep"), key=lambda job: job.id)
    store.complete_effect(
        job_id=fetch_jobs[0].id,
        effect_kind="fetch_article",
        payload={"status": "available", "text": "Content one"},
        now=now,
    )
    store.complete_effect(
        job_id=fetch_jobs[1].id,
        effect_kind="fetch_article",
        payload={"status": "available", "text": "Content two"},
        now=now,
    )

    first_context = store.job_input(deep_jobs[0])

    assert first_context["upstream"]["fetch_article"]["text"] == "Content one"
