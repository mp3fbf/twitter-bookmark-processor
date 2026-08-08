"""Queue ordering and lease recovery contracts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore, LeaseLostError


def test_lease_returns_highest_priority_job_and_hides_it_from_other_workers(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    BookmarkAutomation(store).ingest(
        {"kind": "bookmarks", "id": "1900000000000000010", "text": "Queue me"}
    )
    now = datetime.now(UTC) + timedelta(seconds=1)

    first = store.lease_next(worker_id="worker-a", now=now, lease_for=timedelta(minutes=5))
    second = store.lease_next(worker_id="worker-b", now=now, lease_for=timedelta(minutes=5))

    assert first is not None
    assert first.task_kind == "notify"
    assert first.state == "leased"
    assert second is not None
    assert second.task_kind == "quick"


def test_enqueue_is_idempotent_for_bookmark_task_and_input_revision(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime.now(UTC)

    first = store.enqueue_job(
        bookmark_id="1900000000000000011",
        task_kind="deep",
        input_revision="sha256:one",
        profile="deep",
        priority=900,
        now=now,
    )
    duplicate = store.enqueue_job(
        bookmark_id="1900000000000000011",
        task_kind="deep",
        input_revision="sha256:one",
        profile="deep",
        priority=900,
        now=now,
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.job_id == first.job_id
    assert store.count("jobs") == 1


def test_deep_job_is_not_leased_until_all_semantic_prerequisites_are_terminal(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    created = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: created).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000012",
            "text": "Article requiring complete evidence",
            "urls": [{"expanded_url": "https://example.test/article"}],
        }
    )
    jobs = {job.task_kind: job for job in store.list_jobs()}
    after_grace = created + timedelta(hours=1)

    blocked = store.lease_next(
        worker_id="deep-worker",
        now=after_grace,
        lease_for=timedelta(minutes=5),
        profiles={"deep"},
    )

    assert blocked is None
    for task_kind in ("quick", "fetch_article", "recall_context"):
        store.complete_effect(
            job_id=jobs[task_kind].id,
            effect_kind=task_kind,
            payload={"status": "available"},
            now=after_grace,
        )

    ready = store.lease_next(
        worker_id="deep-worker",
        now=after_grace,
        lease_for=timedelta(minutes=5),
        profiles={"deep"},
    )

    assert ready is not None and ready.task_kind == "deep"


def test_reclaimed_lease_fences_the_previous_worker_completion(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
    created = store.enqueue_job(
        bookmark_id="1900000000000000013",
        task_kind="quick",
        input_revision="sha256:fenced",
        profile="quick",
        priority=800,
        now=now,
    )
    first = store.lease_next(
        worker_id="worker-a", now=now, lease_for=timedelta(minutes=5)
    )
    assert first is not None and first.lease_token is not None
    first_attempt = store.start_attempt(
        job_id=first.id,
        worker_id="worker-a",
        lease_token=first.lease_token,
        now=now,
    )

    reclaimed_at = now + timedelta(minutes=6)
    second = store.lease_next(
        worker_id="worker-b", now=reclaimed_at, lease_for=timedelta(minutes=5)
    )
    assert second is not None
    assert second.id == created.job_id
    assert second.lease_token not in {None, first.lease_token}

    with pytest.raises(LeaseLostError):
        store.complete_success(
            job_id=first.id,
            attempt_id=first_attempt,
            worker_id="worker-a",
            lease_token=first.lease_token,
            provider="subscription-provider",
            model="model",
            effect_kind="quick",
            receipt_json='{"status":"succeeded"}',
            now=reclaimed_at,
            downstream_jobs=(
                {
                    "bookmark_id": "1900000000000000013",
                    "task_kind": "write_source_note",
                    "input_revision": "sha256:fenced",
                    "priority": 650,
                },
            ),
        )

    current = next(job for job in store.list_jobs() if job.id == created.job_id)
    assert current.state == "leased"
    assert current.lease_owner == "worker-b"
    assert current.lease_token == second.lease_token
    assert store.count("receipts") == 0
    assert {job.task_kind for job in store.list_jobs()} == {"quick"}


def test_expired_unreclaimed_lease_cannot_record_a_failure(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
    store.enqueue_job(
        bookmark_id="1900000000000000014",
        task_kind="quick",
        input_revision="sha256:expired",
        profile="quick",
        priority=800,
        now=now,
    )
    claim = store.lease_next(
        worker_id="worker-a", now=now, lease_for=timedelta(minutes=5)
    )
    assert claim is not None and claim.lease_token is not None
    attempt_id = store.start_attempt(
        job_id=claim.id,
        worker_id="worker-a",
        lease_token=claim.lease_token,
        now=now,
    )

    with pytest.raises(LeaseLostError):
        store.complete_failure(
            job_id=claim.id,
            attempt_id=attempt_id,
            worker_id="worker-a",
            lease_token=claim.lease_token,
            detail_json='{"status":"failed"}',
            available_at=now + timedelta(minutes=20),
            now=now + timedelta(minutes=6),
            max_attempts=3,
        )

    current = next(job for job in store.list_jobs() if job.id == claim.id)
    assert current.state == "leased"
    assert store.count("receipts") == 0
