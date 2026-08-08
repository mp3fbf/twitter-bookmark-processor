"""Telegram callback decision contracts."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore


def test_act_decision_is_idempotent_and_enqueues_one_high_priority_deep_job(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000020"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Act on this",
            "urls": [{"expanded_url": "https://example.test/read"}],
        }
    )

    first = automation.decide(
        bookmark_id=bookmark_id,
        action="act",
        decision_id="telegram-update-4001",
    )
    duplicate = automation.decide(
        bookmark_id=bookmark_id,
        action="act",
        decision_id="telegram-update-4001",
    )

    assert first.created is True
    assert duplicate.created is False
    assert store.count("decisions") == 1
    deep_jobs = [job for job in store.list_jobs() if job.task_kind == "deep"]
    assert len(deep_jobs) == 1
    assert deep_jobs[0].profile == "deep"
    assert deep_jobs[0].priority > 1_000
    assert deep_jobs[0].available_at == now.isoformat()


def test_defer_moves_scheduled_deep_work_to_the_defer_window(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    automation = BookmarkAutomation(
        store,
        clock=lambda: now,
        defer_for=timedelta(hours=24),
    )
    bookmark_id = "1900000000000000021"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Read this later",
            "urls": [{"expanded_url": "https://example.test/later"}],
        }
    )

    automation.decide(
        bookmark_id=bookmark_id,
        action="defer",
        decision_id="telegram-update-4002",
    )

    deep = next(job for job in store.list_jobs() if job.task_kind == "deep")
    assert deep.available_at == (now + timedelta(hours=24)).isoformat()


def test_defer_creates_deep_work_for_a_bookmark_without_a_url(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 5, tzinfo=UTC)
    automation = BookmarkAutomation(
        store,
        clock=lambda: now,
        defer_for=timedelta(hours=24),
    )
    bookmark_id = "1900000000000000028"
    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "A useful plain tweet"}
    )

    automation.decide(
        bookmark_id=bookmark_id,
        action="defer",
        decision_id="telegram-update-4010",
    )

    deep = next(job for job in store.list_jobs() if job.task_kind == "deep")
    assert deep.state == "pending"
    assert deep.available_at == (now + timedelta(hours=24)).isoformat()


def test_defer_after_skip_resumes_evidence_but_keeps_deep_deferred(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 10, tzinfo=UTC)
    automation = BookmarkAutomation(
        store,
        clock=lambda: now,
        defer_for=timedelta(hours=24),
    )
    bookmark_id = "1900000000000000029"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Reconsider this tomorrow",
            "urls": [{"expanded_url": "https://example.test/tomorrow"}],
        }
    )
    automation.decide(
        bookmark_id=bookmark_id,
        action="skip",
        decision_id="telegram-update-4011",
    )

    automation.decide(
        bookmark_id=bookmark_id,
        action="defer",
        decision_id="telegram-update-4012",
    )

    jobs = {job.task_kind: job for job in store.list_jobs()}
    assert jobs["quick"].state == "pending"
    assert jobs["fetch_article"].state == "pending"
    assert jobs["recall_context"].state == "pending"
    assert jobs["deep"].state == "pending"
    assert jobs["deep"].available_at == (now + timedelta(hours=24)).isoformat()


def test_skip_cancels_future_semantic_work_but_not_delivery_jobs(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000022"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Video with an article link",
            "hasVideo": True,
            "urls": [{"expanded_url": "https://example.test/article"}],
        }
    )

    automation.decide(
        bookmark_id=bookmark_id,
        action="skip",
        decision_id="telegram-update-4003",
    )

    states = {job.task_kind: job.state for job in store.list_jobs()}
    assert states["quick"] == "cancelled"
    assert states["recall_context"] == "cancelled"
    assert states["fetch_article"] == "cancelled"
    assert states["deep"] == "cancelled"
    assert states["notify"] == "pending"
    assert states["deliver_video"] == "pending"


def test_skip_remains_sticky_across_semantic_revisions_until_user_reactivates(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 2, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000030"
    base = {
        "kind": "bookmarks",
        "id": bookmark_id,
        "text": "A teaser that is not the article title.",
        "hasVideo": True,
        "article": {
            "title": "Durable agent memory",
            "previewText": "A short preview.",
        },
    }
    automation.ingest(base)
    automation.decide(
        bookmark_id=bookmark_id,
        action="skip",
        decision_id="telegram-update-4013",
    )

    automation.ingest(
        {
            **base,
            "_raw": {
                "article": {
                    "article_results": {
                        "result": {"body": {"text": "The complete article body."}}
                    }
                }
            },
        }
    )

    jobs = store.list_jobs()
    revisions = sorted({job.input_revision for job in jobs})
    assert len(revisions) == 2
    current_revision = max(
        (job for job in jobs if job.task_kind == "fetch_article"),
        key=lambda job: job.id,
    ).input_revision
    current_semantic = {
        job.task_kind: job
        for job in jobs
        if job.input_revision == current_revision
        and job.task_kind in {"quick", "fetch_article", "recall_context", "deep"}
    }
    assert set(current_semantic) == {
        "quick",
        "fetch_article",
        "recall_context",
        "deep",
    }
    assert {job.state for job in current_semantic.values()} == {"cancelled"}
    assert [job.task_kind for job in jobs].count("notify") == 1
    assert [job.task_kind for job in jobs].count("deliver_video") == 1
    aggregate_schedule = automation.schedule_periodic(
        task_kind="aggregate",
        input_revision="period:2026-08-08",
    )
    assert len(aggregate_schedule.results) == 1
    aggregate_job = next(
        job
        for job in store.list_jobs()
        if job.id == aggregate_schedule.results[0].job_id
    )
    assert store.job_payload(aggregate_job)["coverage"] == {
        "input_count": 1,
        "bookmark_ids": [bookmark_id],
    }
    assert automation.schedule_periodic(
        task_kind="backlog",
        input_revision="backlog:2026-08-08",
    ).results == ()
    assert (
        store.lease_next(
            worker_id="must-stay-ignored",
            now=now + timedelta(hours=1),
            lease_for=timedelta(minutes=1),
            task_kinds={"quick", "fetch_article", "recall_context", "deep"},
        )
        is None
    )

    automation.decide(
        bookmark_id=bookmark_id,
        action="act",
        decision_id="telegram-update-4014",
    )

    reactivated = {
        job.task_kind: job
        for job in store.list_jobs()
        if job.input_revision == current_revision
        and job.task_kind in {"quick", "fetch_article", "recall_context", "deep"}
    }
    assert {job.state for job in reactivated.values()} == {"pending"}
    assert reactivated["deep"].available_at == now.isoformat()


def test_skip_fences_direct_semantic_enqueue_for_a_later_simple_tweet_revision(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 3, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000031"
    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Initial thought"}
    )
    automation.decide(
        bookmark_id=bookmark_id,
        action="skip",
        decision_id="telegram-update-4015",
    )
    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Richer thought"}
    )
    current_revision = max(
        (job for job in store.list_jobs() if job.task_kind == "quick"),
        key=lambda job: job.id,
    ).input_revision

    queued = store.enqueue_job(
        bookmark_id=bookmark_id,
        task_kind="deep",
        input_revision=current_revision,
        profile="deep",
        priority=100,
        now=now,
        input_json='{"bookmark":{"id":"1900000000000000031"}}',
    )

    deep = next(job for job in store.list_jobs() if job.id == queued.job_id)
    assert deep.state == "cancelled"
    with sqlite3.connect(store.path) as connection:
        cancel_requested = connection.execute(
            "SELECT cancel_requested FROM jobs WHERE id = ?", (deep.id,)
        ).fetchone()[0]
    assert cancel_requested == 1
    assert automation.schedule_periodic(
        task_kind="backlog",
        input_revision="backlog:2026-08-08",
    ).results == ()


def test_keep_confirms_normal_deep_schedule_without_accelerating_it(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000023"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Keep on normal schedule",
            "urls": [{"expanded_url": "https://example.test/normal"}],
        }
    )

    automation.decide(
        bookmark_id=bookmark_id,
        action="keep",
        decision_id="telegram-update-4004",
    )

    deep = next(job for job in store.list_jobs() if job.task_kind == "deep")
    assert deep.available_at == (now + timedelta(minutes=15)).isoformat()
    assert deep.priority < 1_000


def test_skip_cancels_pending_deep_materialization_but_never_video(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000024"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Already analyzed, not yet materialized",
            "hasVideo": True,
        }
    )
    revision = next(
        job.input_revision for job in store.list_jobs() if job.task_kind == "quick"
    )
    for task_kind, priority in (("write_source_note", 650), ("send_deep", 640)):
        store.enqueue_job(
            bookmark_id=bookmark_id,
            task_kind=task_kind,
            input_revision=revision,
            profile="none",
            priority=priority,
            now=now,
            input_json="{}",
        )

    automation.decide(
        bookmark_id=bookmark_id,
        action="skip",
        decision_id="telegram-update-4005",
    )

    states = {job.task_kind: job.state for job in store.list_jobs()}
    assert states["write_source_note"] == "cancelled"
    assert states["send_deep"] == "cancelled"
    assert states["deliver_video"] == "pending"


def test_act_after_skip_resumes_materialization_from_completed_deep_result(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 15, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000026"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Analyzed before the user changed their mind",
            "urls": [{"expanded_url": "https://example.test/resume"}],
        }
    )
    revision = next(
        job.input_revision for job in store.list_jobs() if job.task_kind == "deep"
    )
    deep = next(job for job in store.list_jobs() if job.task_kind == "deep")
    store.complete_effect(
        job_id=deep.id,
        effect_kind="deep",
        payload={"status": "available"},
        now=now,
    )
    materialization_payload = '{"analysis":{"summary":"already analyzed"}}'
    for task_kind, priority in (("write_source_note", 650), ("send_deep", 640)):
        store.enqueue_job(
            bookmark_id=bookmark_id,
            task_kind=task_kind,
            input_revision=revision,
            profile="none",
            priority=priority,
            now=now,
            input_json=materialization_payload,
        )

    automation.decide(
        bookmark_id=bookmark_id,
        action="skip",
        decision_id="telegram-update-4006",
    )
    automation.decide(
        bookmark_id=bookmark_id,
        action="act",
        decision_id="telegram-update-4007",
    )

    jobs = {job.task_kind: job for job in store.list_jobs()}
    assert jobs["deep"].state == "done"
    assert jobs["write_source_note"].state == "pending"
    assert jobs["send_deep"].state == "pending"
    assert store.job_payload(jobs["write_source_note"]) == {
        "analysis": {"summary": "already analyzed"}
    }


def test_act_after_skip_resumes_upstream_evidence_before_deep(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 20, tzinfo=UTC)
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000027"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Resume the full evidence chain",
            "urls": [{"expanded_url": "https://example.test/full-chain"}],
        }
    )

    automation.decide(
        bookmark_id=bookmark_id,
        action="skip",
        decision_id="telegram-update-4008",
    )
    automation.decide(
        bookmark_id=bookmark_id,
        action="act",
        decision_id="telegram-update-4009",
    )

    jobs = {job.task_kind: job for job in store.list_jobs()}
    assert jobs["quick"].state == "pending"
    assert jobs["fetch_article"].state == "pending"
    assert jobs["recall_context"].state == "pending"
    assert jobs["deep"].state == "pending"
    assert jobs["deep"].available_at == now.isoformat()
    assert (
        store.lease_next(
            worker_id="deep-must-wait",
            now=now,
            lease_for=timedelta(minutes=1),
            profiles={"deep"},
        )
        is None
    )


def test_skip_preserves_expired_committed_effect_for_its_owner_receipt(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 15, 30, tzinfo=UTC)
    clock_now = [now]
    automation = BookmarkAutomation(store, clock=lambda: clock_now[0])
    bookmark_id = "1900000000000000025"
    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Expired effect"}
    )
    revision = next(
        job.input_revision for job in store.list_jobs() if job.task_kind == "quick"
    )
    queued = store.enqueue_job(
        bookmark_id=bookmark_id,
        task_kind="send_deep",
        input_revision=revision,
        profile="none",
        priority=640,
        input_json="{}",
        now=now,
    )
    claim = store.lease_next(
        worker_id="expired-worker",
        now=now,
        lease_for=timedelta(minutes=1),
        profiles={"none"},
        task_kinds={"send_deep"},
    )
    assert claim is not None and claim.lease_token is not None
    attempt_id = store.start_attempt(
        job_id=claim.id,
        worker_id="expired-worker",
        lease_token=claim.lease_token,
        now=now,
    )
    store.mark_effect_committed(
        job_id=claim.id,
        attempt_id=attempt_id,
        worker_id="expired-worker",
        lease_token=claim.lease_token,
        now=now,
    )

    clock_now[0] = now + timedelta(minutes=2)
    automation.decide(
        bookmark_id=bookmark_id,
        action="skip",
        decision_id="telegram:skip-after-expired-effect",
    )

    current = next(job for job in store.list_jobs() if job.id == queued.job_id)
    assert current.state == "leased"
    with sqlite3.connect(store.path) as connection:
        attempt_status = connection.execute(
            "SELECT status FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0]
        cancel_requested = connection.execute(
            "SELECT cancel_requested FROM jobs WHERE id = ?", (queued.job_id,)
        ).fetchone()[0]
    assert attempt_status == "effect_committed"
    assert cancel_requested == 1
    assert store.status_snapshot()["committed_effects"] == 1
    assert (
        store.lease_next(
            worker_id="replacement-worker",
            now=clock_now[0],
            lease_for=timedelta(minutes=1),
            profiles={"none"},
            task_kinds={"send_deep"},
        )
        is None
    )
    store.complete_success(
        job_id=claim.id,
        attempt_id=attempt_id,
        worker_id="expired-worker",
        lease_token=claim.lease_token,
        provider=None,
        model=None,
        effect_kind="send_deep",
        receipt_json='{"status":"delivered"}',
        now=clock_now[0],
    )
    completed = next(job for job in store.list_jobs() if job.id == queued.job_id)
    assert completed.state == "done"
    assert store.receipt_payload(queued.job_id, "send_deep") == {
        "status": "delivered"
    }
    assert store.status_snapshot()["committed_effects"] == 0
