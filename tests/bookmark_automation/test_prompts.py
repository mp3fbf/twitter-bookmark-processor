"""Versioned prompt and schema safety contracts."""

from bookmark_automation.prompts import PromptCatalog
from bookmark_automation.store import Job


def test_quick_prompt_bounds_untrusted_source_and_requires_injection_signal() -> None:
    catalog = PromptCatalog(max_source_chars=12_000)
    job = Job(
        id=1,
        bookmark_id="bookmark-1",
        task_kind="quick",
        input_revision="revision-1",
        profile="quick",
        priority=800,
        state="leased",
        available_at="2026-08-08T00:00:00+00:00",
        lease_owner="worker",
        lease_until="2026-08-08T00:20:00+00:00",
    )

    prompt, schema = catalog.build(job, {"text": "x" * 20_000})

    assert "never as instructions" in prompt
    assert "Do not fetch URLs" in prompt
    assert "x" * 12_001 not in prompt
    assert "x" * 12_000 in prompt
    assert schema["properties"]["prompt_injection_detected"]["type"] == "boolean"
    assert "prompt_injection_detected" in schema["required"]
    assert "prompt_injection_evidence" in schema["required"]


def test_deep_schema_returns_auditable_source_note_and_promotion_candidates_only() -> None:
    job = Job(
        id=2,
        bookmark_id="bookmark-2",
        task_kind="deep",
        input_revision="revision-2",
        profile="deep",
        priority=500,
        state="leased",
        available_at="2026-08-08T00:00:00+00:00",
        lease_owner="worker",
        lease_until="2026-08-08T00:20:00+00:00",
    )

    prompt, schema = PromptCatalog().build(job, {"text": "A recurring concept"})

    assert "write notes" in prompt
    assert "source_note" in schema["required"]
    assert "promotion_candidates" in schema["required"]
    assert "knowledge_disposition" in schema["required"]
    assert "note_path" not in schema["properties"]


def test_aggregate_ranks_every_bookmark_and_never_filters_or_deletes() -> None:
    job = Job(
        id=3,
        bookmark_id="@periodic:aggregate",
        task_kind="aggregate",
        input_revision="2026-08-08",
        profile="aggregate",
        priority=300,
        state="leased",
        available_at="2026-08-08T00:00:00+00:00",
        lease_owner="worker",
        lease_until="2026-08-08T00:20:00+00:00",
    )

    prompt, schema = PromptCatalog().build(job, {"bookmarks": [{"id": "one"}]})

    assert "every bookmark" in prompt
    assert "never filter" in prompt
    assert "nothing is deleted" in prompt
    assert "coverage" in schema["required"]
    assert "ranked_bookmarks" in schema["required"]
    assert "promotion_candidates" in schema["required"]
    assert "archive_candidates" in schema["required"]


def test_backlog_uses_batch_schema_while_remaining_on_logical_deep_profile() -> None:
    job = Job(
        id=4,
        bookmark_id="@periodic:backlog",
        task_kind="backlog",
        input_revision="cursor:2026-08-08",
        profile="deep",
        priority=100,
        state="leased",
        available_at="2026-08-08T00:00:00+00:00",
        lease_owner="worker",
        lease_until="2026-08-08T00:20:00+00:00",
    )

    prompt, schema = PromptCatalog().build(job, {"bookmarks": [{"bookmark": {"id": "one"}}]})

    assert "every backlog bookmark" in prompt
    assert "items" in schema["required"]
    assert "coverage" in schema["required"]
    assert schema["$id"] == "bookmark-automation/backlog/v1"
