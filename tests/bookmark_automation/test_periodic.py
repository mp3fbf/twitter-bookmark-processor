"""Provider-neutral periodic scheduling contracts."""

from datetime import UTC, datetime
from pathlib import Path

from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore


def test_periodic_aggregate_and_backlog_jobs_store_logical_profiles_only(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 17, 0, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest(
        {"kind": "bookmarks", "id": "backlog-item", "text": "Needs deep processing"}
    )

    automation.schedule_periodic(task_kind="aggregate", input_revision="2026-08-08")
    automation.schedule_periodic(task_kind="backlog", input_revision="cursor:2026-08-08")

    jobs = {job.task_kind: job for job in store.list_jobs()}
    assert jobs["aggregate"].profile == "aggregate"
    assert jobs["deep"].profile == "deep"
    assert jobs["aggregate"].priority > jobs["deep"].priority
    assert all(job.profile not in {"claude", "codex", "sonnet", "terra"} for job in jobs.values())


def test_aggregate_input_contains_every_captured_bookmark_not_a_top_n(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 17, 0, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest({"kind": "bookmarks", "id": "low-relevance", "text": "Small note"})
    automation.ingest({"kind": "bookmarks", "id": "high-relevance", "text": "Big idea"})
    scheduled = automation.schedule_periodic(
        task_kind="aggregate", input_revision="2026-08-08"
    )
    aggregate = next(job for job in store.list_jobs() if job.id == scheduled.job_id)

    aggregate_input = store.job_input(aggregate)

    assert aggregate_input["coverage"]["input_count"] == 2
    assert {item["bookmark"]["id"] for item in aggregate_input["bookmarks"]} == {
        "low-relevance",
        "high-relevance",
    }


def test_aggregate_schedule_freezes_bounded_batches_covering_every_id_once(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 17, 30, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    expected = {f"bookmark-{index}" for index in range(5)}
    for bookmark_id in sorted(expected):
        automation.ingest(
            {"kind": "bookmarks", "id": bookmark_id, "text": f"Text {bookmark_id}"}
        )

    scheduled = automation.schedule_periodic(
        task_kind="aggregate",
        input_revision="2026-08-08",
        batch_size=2,
    )
    aggregate_jobs = [
        job for job in store.list_jobs() if job.id in set(scheduled.job_ids)
    ]
    frozen_payloads = [store.job_payload(job) for job in aggregate_jobs]

    assert len(aggregate_jobs) == 3
    assert all(len(payload["bookmarks"]) <= 2 for payload in frozen_payloads)
    captured_ids = [
        item["bookmark"]["id"]
        for payload in frozen_payloads
        for item in payload["bookmarks"]
    ]
    assert set(captured_ids) == expected
    assert len(captured_ids) == len(set(captured_ids))

    repeated = automation.schedule_periodic(
        task_kind="aggregate",
        input_revision="2026-08-08",
        batch_size=2,
    )
    assert repeated.results == ()

    automation.ingest(
        {"kind": "bookmarks", "id": "arrived-later", "text": "Must wait for next digest"}
    )
    assert [store.job_payload(job) for job in aggregate_jobs] == frozen_payloads
    incremental = automation.schedule_periodic(
        task_kind="aggregate",
        input_revision="2026-08-08",
        batch_size=2,
    )
    assert len(incremental.job_ids) == 1
    incremental_job = next(
        job for job in store.list_jobs() if job.id == incremental.job_id
    )
    assert store.job_payload(incremental_job)["coverage"]["bookmark_ids"] == [
        "arrived-later"
    ]


def test_backlog_schedule_skips_current_revisions_with_a_deep_receipt(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": "already-distilled",
            "text": "Already processed https://example.test/source",
            "urls": [{"expanded_url": "https://example.test/source"}],
        }
    )
    deep = next(job for job in store.list_jobs() if job.task_kind == "deep")
    store.complete_effect(
        job_id=deep.id,
        effect_kind="deep",
        payload={"status": "available", "output": {"summary": "Done"}},
        now=now,
    )

    scheduled = automation.schedule_periodic(
        task_kind="backlog",
        input_revision="cursor:2026-08-08",
    )

    assert scheduled.results == ()
    deep_jobs = [job for job in store.list_jobs() if job.task_kind == "deep"]
    assert len(deep_jobs) == 1
    assert deep_jobs[0].state == "done"


def test_bootstrap_backlog_is_bounded_and_never_creates_historical_notifications(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 30, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    for index in range(5):
        automation.ingest(
            {
                "kind": "bookmarks",
                "id": f"historical-{index}",
                "text": f"Historical {index} https://example.test/{index}",
                "urls": [{"expanded_url": f"https://example.test/{index}"}],
                "hasVideo": True,
            },
            bootstrap=True,
        )

    assert store.list_jobs() == []
    baseline_digest = automation.schedule_periodic(
        task_kind="aggregate",
        input_revision="2026-08-08",
        batch_size=2,
    )
    assert baseline_digest.results == ()
    first = automation.schedule_periodic(
        task_kind="backlog",
        input_revision="cursor:first",
        batch_size=2,
    )
    first_deep_jobs = [job for job in store.list_jobs() if job.id in set(first.job_ids)]
    first_bookmarks = {job.bookmark_id for job in first_deep_jobs}
    first_jobs = [job for job in store.list_jobs() if job.bookmark_id in first_bookmarks]

    assert len(first_bookmarks) == 2
    assert {job.task_kind for job in first_jobs} == {
        "quick",
        "fetch_article",
        "recall_context",
        "deep",
    }
    assert not any(
        job.task_kind in {"notify", "deliver_video"} for job in store.list_jobs()
    )

    second = automation.schedule_periodic(
        task_kind="backlog",
        input_revision="cursor:second",
        batch_size=2,
    )
    second_deep_jobs = [job for job in store.list_jobs() if job.id in set(second.job_ids)]
    second_bookmarks = {job.bookmark_id for job in second_deep_jobs}
    assert len(second_bookmarks) == 2
    assert first_bookmarks.isdisjoint(second_bookmarks)
