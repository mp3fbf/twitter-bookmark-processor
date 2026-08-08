"""Observable ingestion contracts for bookmark automation."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore


def test_likes_are_ignored_without_persisting_any_state(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store)

    result = automation.ingest(
        {
            "kind": "likes",
            "id": "1900000000000000001",
            "text": "Interesting, but a like is not an intent to process.",
        }
    )

    assert result.accepted is False
    assert store.count("bookmarks") == 0
    assert store.count("jobs") == 0


def test_new_bookmark_enqueues_immediate_notification_and_independent_quick_job(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store)

    result = automation.ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000002",
            "text": "A useful article about durable agent memory.",
            "urls": [{"expanded_url": "https://example.test/agent-memory"}],
        }
    )

    assert result.accepted is True
    assert result.created is True
    assert store.count("bookmarks") == 1
    assert [job.task_kind for job in store.list_jobs()] == [
        "notify",
        "fetch_article",
        "quick",
        "recall_context",
        "deep",
    ]
    assert store.list_jobs()[0].priority > store.list_jobs()[1].priority


def test_reingesting_same_bookmark_is_idempotent(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store)
    event = {
        "kind": "bookmarks",
        "id": "1900000000000000003",
        "text": "The same bookmark may appear in incremental reconciliation.",
    }

    first = automation.ingest(event)
    duplicate = automation.ingest(event)

    assert first.created is True
    assert duplicate.created is False
    assert store.count("bookmarks") == 1
    assert store.count("jobs") == 3
    assert any(job.task_kind == "recall_context" for job in store.list_jobs())


def test_changed_bookmark_payload_reanalyzes_without_renotifying(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store)
    bookmark_id = "1900000000000000004"

    first = automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Short preview"}
    )
    revised = automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Expanded full content"}
    )

    assert first.created is True
    assert revised.created is True
    assert store.count("bookmarks") == 1
    assert len({job.input_revision for job in store.list_jobs()}) == 2
    assert store.count("jobs") == 5
    assert [job.task_kind for job in store.list_jobs()].count("notify") == 1


def test_late_video_metadata_delivers_once_without_renotifying(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store)
    bookmark_id = "1900000000000000006"

    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Media still resolving"}
    )
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Media resolved",
            "hasVideo": True,
        }
    )
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Media resolved with richer metadata",
            "hasVideo": True,
            "media": [{"type": "video", "videoUrl": "https://video.twimg.com/one.mp4"}],
        }
    )

    task_kinds = [job.task_kind for job in store.list_jobs()]
    assert task_kinds.count("notify") == 1
    assert task_kinds.count("deliver_video") == 1
    video_job = next(
        job for job in store.list_jobs() if job.task_kind == "deliver_video"
    )
    assert store.job_payload(video_job)["media"][0]["videoUrl"] == (
        "https://video.twimg.com/one.mp4"
    )


def test_bootstrapped_bookmark_never_gains_capture_effects_on_revision(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store)
    bookmark_id = "1900000000000000007"

    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Historical"},
        bootstrap=True,
    )
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Historical payload expanded",
            "hasVideo": True,
        }
    )

    task_kinds = [job.task_kind for job in store.list_jobs()]
    assert "notify" not in task_kinds
    assert "deliver_video" not in task_kinds
    assert {"quick", "recall_context"} <= set(task_kinds)


def test_richer_video_revision_revives_the_same_exhausted_delivery_job(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store, clock=lambda: now)
    bookmark_id = "1900000000000000008"
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Video URL still resolving",
            "hasVideo": True,
        }
    )
    original = next(
        job for job in store.list_jobs() if job.task_kind == "deliver_video"
    )
    claim = store.lease_next(
        worker_id="video-worker",
        now=now,
        lease_for=timedelta(minutes=1),
        profiles={"none"},
        task_kinds={"deliver_video"},
    )
    assert claim is not None and claim.lease_token is not None
    attempt_id = store.start_attempt(
        job_id=claim.id,
        worker_id="video-worker",
        lease_token=claim.lease_token,
        now=now,
    )
    store.mark_effect_committed(
        job_id=claim.id,
        attempt_id=attempt_id,
        worker_id="video-worker",
        lease_token=claim.lease_token,
        now=now,
    )
    assert (
        store.complete_failure(
            job_id=claim.id,
            attempt_id=attempt_id,
            worker_id="video-worker",
            lease_token=claim.lease_token,
            detail_json='{"code":"video_url_unavailable"}',
            available_at=now,
            now=now,
            max_attempts=1,
        )
        == "dead_letter"
    )

    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Video URL resolved",
            "hasVideo": True,
            "media": [
                {
                    "type": "video",
                    "videoUrl": "https://video.twimg.com/resolved.mp4",
                }
            ],
        }
    )

    refreshed = next(
        job for job in store.list_jobs() if job.task_kind == "deliver_video"
    )
    assert refreshed.id == original.id
    assert refreshed.state == "pending"
    assert store.job_payload(refreshed)["media"][0]["videoUrl"].endswith(
        "/resolved.mp4"
    )
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT status FROM attempts WHERE id = ?", (attempt_id,)
        ).fetchone()[0] == "superseded"


def test_volatile_engagement_metrics_do_not_create_a_new_input_revision(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store)
    base = {
        "kind": "bookmarks",
        "id": "1900000000000000005",
        "text": "Stable source content",
    }

    automation.ingest({**base, "metrics": {"likes": 10, "views": 100}})
    metrics_only_change = automation.ingest(
        {**base, "metrics": {"likes": 11, "views": 150}}
    )

    assert metrics_only_change.created is False
    assert store.count("jobs") == 3
