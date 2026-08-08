"""End-to-end contracts for deterministic bookmark effects."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import bookmark_automation.effect_worker as effect_worker_module
from bookmark_automation.content import ArticleContent, RecallResult
from bookmark_automation.effect_worker import EffectWorker
from bookmark_automation.effects import ExternalEffectError, TelegramReceipt, VideoReceipt
from bookmark_automation.notes import write_source_note
from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore


class RecordingTelegram:
    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.documents: list[Path] = []

    def send_message(
        self,
        message: Any,
        *,
        before_send: Any | None = None,
    ) -> TelegramReceipt:
        if before_send is not None:
            before_send()
        self.messages.append(message)
        return TelegramReceipt(
            method="sendMessage",
            chat_id="test-chat",
            message_id=len(self.messages),
        )

    def send_document(
        self,
        path: str | Path,
        *,
        before_send: Any | None = None,
        **_kwargs: Any,
    ) -> TelegramReceipt:
        if before_send is not None:
            before_send()
        document = Path(path)
        self.documents.append(document)
        return TelegramReceipt(
            method="sendDocument",
            chat_id="test-chat",
            message_id=100 + len(self.documents),
            delivered_path=str(document),
            size_bytes=document.stat().st_size,
            sha256="test-sha",
        )


def test_effect_worker_delivers_notification_once_and_records_receipt(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 0, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000100",
            "text": "A bookmark that should produce one immediate question",
            "author": {"username": "example"},
        }
    )
    telegram = RecordingTelegram()
    worker = EffectWorker(
        store=store,
        telegram=telegram,
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
    )

    first = worker.run_once(now=now + timedelta(seconds=1))
    replay = worker.run_once(now=now + timedelta(seconds=2), task_kinds={"notify"})

    assert first is not None and first.status == "done"
    assert replay is None
    assert len(telegram.messages) == 1
    notify = next(job for job in store.list_jobs() if job.task_kind == "notify")
    assert notify.state == "done"
    assert store.receipt_payload(notify.id, "notify") == {
        "chat_id": "test-chat",
        "message_id": 1,
        "method": "sendMessage",
        "status": "delivered",
    }
    assert store.count("attempts") == 1


def test_ambiguous_telegram_failure_remains_committed_and_is_never_replayed(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 5, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000199",
            "text": "Telegram may accept this before the client times out",
        }
    )

    class AcceptThenTimeoutTelegram(RecordingTelegram):
        def send_message(
            self,
            message: Any,
            *,
            before_send: Any | None = None,
        ) -> TelegramReceipt:
            if before_send is not None:
                before_send()
            self.messages.append(message)
            raise ExternalEffectError(
                "simulated timeout after acceptance",
                code="telegram_transport_failed",
                retryable=True,
            )

    telegram = AcceptThenTimeoutTelegram()
    worker = EffectWorker(
        store=store,
        telegram=telegram,
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
        failure_backoff=timedelta(0),
    )

    outcome = worker.run_once(now=now, task_kinds={"notify"})
    replay = worker.run_once(
        now=now + timedelta(hours=1), task_kinds={"notify"}
    )

    assert outcome is not None and outcome.status == "effect_committed"
    assert replay is None
    assert len(telegram.messages) == 1
    notify = next(job for job in store.list_jobs() if job.task_kind == "notify")
    assert notify.state == "leased"
    assert store.receipt_payload(notify.id, "notify") is None
    assert store.status_snapshot()["committed_effects"] == 1
    with store._connect() as connection:
        attempt = connection.execute(
            "SELECT status, detail_json FROM attempts WHERE job_id = ?",
            (notify.id,),
        ).fetchone()
    assert attempt["status"] == "effect_committed"
    assert json.loads(attempt["detail_json"])["code"] == (
        "telegram_transport_failed"
    )


def test_effect_worker_downloads_and_sends_video_without_waiting_for_decision(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 10, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000101"
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Native video",
            "hasVideo": True,
            "media": [
                {
                    "type": "video",
                    "videoUrl": "https://video.twimg.com/example.mp4",
                }
            ],
        }
    )
    telegram = RecordingTelegram()
    downloads: list[dict[str, Any]] = []

    def download(payload: dict[str, Any], destination: Path) -> VideoReceipt:
        downloads.append(payload)
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / f"{bookmark_id}.mp4"
        path.write_bytes(b"video")
        return VideoReceipt(
            path=path,
            source_url="https://video.twimg.com/example.mp4",
            size_bytes=5,
            sha256="video-sha",
        )

    worker = EffectWorker(
        store=store,
        telegram=telegram,
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
        download_video=download,
    )

    outcome = worker.run_once(
        now=now + timedelta(seconds=1), task_kinds={"deliver_video"}
    )

    assert outcome is not None and outcome.status == "done"
    assert store.count("decisions") == 0
    assert downloads[0]["id"] == bookmark_id
    assert telegram.documents == [tmp_path / "videos" / f"{bookmark_id}.mp4"]
    video_job = next(job for job in store.list_jobs() if job.task_kind == "deliver_video")
    receipt = store.receipt_payload(video_job.id, "deliver_video")
    assert receipt is not None
    assert receipt["status"] == "delivered"
    assert receipt["source_sha256"] == "video-sha"
    assert receipt["telegram_message_id"] == 101


def test_video_download_failure_before_commit_remains_retryable(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 15, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000198",
            "text": "Video metadata is still resolving",
            "hasVideo": True,
        }
    )

    def unavailable_download(
        _payload: dict[str, Any],
        _destination: Path,
    ) -> VideoReceipt:
        raise ExternalEffectError(
            "video URL unavailable",
            code="video_url_unavailable",
            retryable=True,
        )

    worker = EffectWorker(
        store=store,
        telegram=RecordingTelegram(),
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
        download_video=unavailable_download,
        failure_backoff=timedelta(0),
    )

    outcome = worker.run_once(now=now, task_kinds={"deliver_video"})

    assert outcome is not None and outcome.status == "pending"
    video = next(
        job for job in store.list_jobs() if job.task_kind == "deliver_video"
    )
    assert video.state == "pending"
    assert store.status_snapshot()["committed_effects"] == 0


def test_effect_worker_captures_article_and_local_recall_as_separate_receipts(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 20, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000102"
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Durable agent memory https://example.test/memory",
            "urls": [{"expanded_url": "https://example.test/memory"}],
        }
    )
    fetched: list[str] = []
    recalled: list[str] = []

    def fetch(payload: dict[str, Any]) -> ArticleContent:
        fetched.append(payload["id"])
        return ArticleContent(
            original_url="https://example.test/memory",
            final_url="https://example.test/memory",
            title="Durable Memory",
            author="Example Author",
            published_at="2026-08-01",
            text="Full article text",
            truncated=False,
        )

    def recall(query: str) -> RecallResult:
        recalled.append(query)
        return RecallResult(
            query=query,
            hits=({"path": "Concepts/Memory.md", "snippet": "Consolidation"},),
        )

    worker = EffectWorker(
        store=store,
        telegram=RecordingTelegram(),
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
        fetch_article_content=fetch,
        recall_context=recall,
    )

    article_outcome = worker.run_once(
        now=now + timedelta(seconds=1), task_kinds={"fetch_article"}
    )
    recall_outcome = worker.run_once(
        now=now + timedelta(seconds=2), task_kinds={"recall_context"}
    )

    assert article_outcome is not None and article_outcome.status == "done"
    assert recall_outcome is not None and recall_outcome.status == "done"
    assert fetched == [bookmark_id]
    assert "Durable agent memory" in recalled[0]
    jobs = {job.task_kind: job for job in store.list_jobs()}
    article_receipt = store.receipt_payload(jobs["fetch_article"].id, "fetch_article")
    recall_receipt = store.receipt_payload(jobs["recall_context"].id, "recall_context")
    assert article_receipt is not None
    assert article_receipt["status"] == "available"
    assert article_receipt["text"] == "Full article text"
    assert recall_receipt is not None
    assert recall_receipt["status"] == "available"
    assert recall_receipt["hits"][0]["path"] == "Concepts/Memory.md"


def test_skip_during_reversible_article_fetch_cancels_without_a_receipt(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 25, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000107"
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Cancel this fetch https://example.test/cancel",
            "urls": [{"expanded_url": "https://example.test/cancel"}],
        }
    )

    def fetch_after_skip(_payload: dict[str, Any]) -> ArticleContent:
        automation.decide(
            bookmark_id=bookmark_id,
            action="skip",
            decision_id="telegram:skip-during-fetch",
        )
        return ArticleContent(
            original_url="https://example.test/cancel",
            final_url="https://example.test/cancel",
            title="Cancelled",
            author=None,
            published_at=None,
            text="This reversible result must not commit.",
            truncated=False,
        )

    worker = EffectWorker(
        store=store,
        telegram=RecordingTelegram(),
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
        fetch_article_content=fetch_after_skip,
    )

    outcome = worker.run_once(now=now, task_kinds={"fetch_article"})

    assert outcome is not None and outcome.status == "lease_lost"
    fetch_job = next(
        job for job in store.list_jobs() if job.task_kind == "fetch_article"
    )
    assert fetch_job.state == "cancelled"
    assert store.receipt_payload(fetch_job.id, "fetch_article") is None


def test_effect_worker_retries_typed_failures_then_moves_job_to_dead_letter(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 30, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000103",
            "text": "Unavailable article https://example.test/unavailable",
            "urls": [{"expanded_url": "https://example.test/unavailable"}],
        }
    )

    def unavailable(_payload: dict[str, Any]) -> ArticleContent:
        raise ExternalEffectError(
            "temporary article failure",
            code="article_temporarily_unavailable",
            retryable=True,
        )

    worker = EffectWorker(
        store=store,
        telegram=RecordingTelegram(),
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
        fetch_article_content=unavailable,
        failure_backoff=timedelta(0),
        max_attempts=2,
    )

    first = worker.run_once(now=now, task_kinds={"fetch_article"})
    second = worker.run_once(
        now=now + timedelta(seconds=1), task_kinds={"fetch_article"}
    )

    assert first is not None and first.status == "pending"
    assert second is not None and second.status == "dead_letter"
    fetch_job = next(job for job in store.list_jobs() if job.task_kind == "fetch_article")
    assert fetch_job.state == "dead_letter"
    assert store.count("attempts") == 2
    assert store.receipt_payload(fetch_job.id, "fetch_article") is None


def test_effect_worker_materializes_source_note_and_sends_deep_summary(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 40, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    revision = "b" * 64
    payload = {
        "bookmark": {"id": "1900000000000000104", "text": "Source bookmark"},
        "input_revision": revision,
        "analysis": {
            "summary": "Summary sent to Telegram",
            "durable_insight": "Durable insight",
            "why_interesting": "Why it matters",
            "second_brain_fit": ["Second Brain"],
            "next_action": "Try it",
            "promotion_candidates": [],
            "knowledge_disposition": "conceptual",
            "source_note": {
                "title": "Source title",
                "source_type": "article",
                "source_url": "https://example.test/source",
                "author": "Author",
                "published_at": None,
                "key_claims": ["Claim"],
                "evidence": [],
                "provenance": {
                    "bookmark_id": "1900000000000000104",
                    "input_revision": revision,
                    "content_status": "available",
                    "recall_status": "available",
                },
            },
        },
        "inference": {"provider": "codex", "model": "configured-model"},
    }
    encoded = json.dumps(payload)
    note_job = store.enqueue_job(
        bookmark_id="1900000000000000104",
        task_kind="write_source_note",
        input_revision=revision,
        profile="none",
        priority=650,
        input_json=encoded,
        now=now,
    )
    message_job = store.enqueue_job(
        bookmark_id="1900000000000000104",
        task_kind="send_deep",
        input_revision=revision,
        profile="none",
        priority=640,
        input_json=encoded,
        now=now,
    )
    telegram = RecordingTelegram()
    worker = EffectWorker(
        store=store,
        telegram=telegram,
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
    )

    note_outcome = worker.run_once(now=now, task_kinds={"write_source_note"})
    message_outcome = worker.run_once(
        now=now + timedelta(seconds=1), task_kinds={"send_deep"}
    )

    assert note_outcome is not None and note_outcome.status == "done"
    assert message_outcome is not None and message_outcome.status == "done"
    note_receipt = store.receipt_payload(note_job.job_id, "write_source_note")
    assert note_receipt is not None
    note_path = Path(note_receipt["path"])
    assert note_path.is_file()
    assert note_path.parent == tmp_path / "notes"
    assert len(telegram.messages) == 1
    assert "Summary sent to Telegram" in telegram.messages[0].text
    assert store.receipt_payload(message_job.job_id, "send_deep")["status"] == "delivered"


def test_skip_after_source_note_effect_commit_preserves_receipt(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    now = datetime(2026, 8, 8, 13, 45, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000105"
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Committed source"}
    )
    revision = next(
        job.input_revision for job in store.list_jobs() if job.task_kind == "quick"
    )
    payload = {
        "bookmark": {"id": bookmark_id, "text": "Committed source"},
        "input_revision": revision,
        "analysis": {
            "summary": "Durable summary",
            "durable_insight": "Committed effects must remain auditable.",
            "why_interesting": "It closes the skip race.",
            "second_brain_fit": ["Second Brain"],
            "next_action": None,
            "promotion_candidates": [],
            "knowledge_disposition": "conceptual",
            "source_note": {
                "title": "Committed effect",
                "source_type": "tweet",
                "source_url": f"https://x.com/i/web/status/{bookmark_id}",
                "author": None,
                "published_at": None,
                "key_claims": [],
                "evidence": [],
                "provenance": {
                    "bookmark_id": bookmark_id,
                    "input_revision": revision,
                    "content_status": "not_applicable",
                    "recall_status": "missing",
                },
            },
        },
        "inference": {"provider": "codex", "model": "configured-model"},
    }
    queued = store.enqueue_job(
        bookmark_id=bookmark_id,
        task_kind="write_source_note",
        input_revision=revision,
        profile="none",
        priority=650,
        input_json=json.dumps(payload),
        now=now,
    )

    def write_after_skip(materialization: dict[str, Any], output_dir: Path) -> Any:
        automation.decide(
            bookmark_id=bookmark_id,
            action="skip",
            decision_id="telegram:skip-after-effect-commit",
        )
        return write_source_note(materialization, output_dir)

    monkeypatch.setattr(effect_worker_module, "write_source_note", write_after_skip)
    worker = EffectWorker(
        store=store,
        telegram=RecordingTelegram(),
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
    )

    outcome = worker.run_once(now=now, task_kinds={"write_source_note"})

    assert outcome is not None and outcome.status == "done"
    source_job = next(job for job in store.list_jobs() if job.id == queued.job_id)
    assert source_job.state == "done"
    receipt = store.receipt_payload(queued.job_id, "write_source_note")
    assert receipt is not None
    assert receipt["status"] == "written"
    assert Path(receipt["path"]).is_file()


def test_skip_after_effect_commit_preserves_ambiguous_failure_for_audit(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 46, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000106"
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Do not retry after skip"}
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
        input_json=json.dumps(
            {
                "bookmark": {"id": bookmark_id},
                "input_revision": revision,
                "analysis": {"summary": "Do not deliver twice", "source_note": {}},
            }
        ),
        now=now,
    )

    class SkipThenFailTelegram(RecordingTelegram):
        def send_message(
            self,
            message: Any,
            *,
            before_send: Any | None = None,
        ) -> TelegramReceipt:
            if before_send is not None:
                before_send()
            automation.decide(
                bookmark_id=bookmark_id,
                action="skip",
                decision_id="telegram:skip-before-effect-failure",
            )
            raise ExternalEffectError(
                "simulated delivery failure",
                code="simulated_failure",
                retryable=True,
            )

    worker = EffectWorker(
        store=store,
        telegram=SkipThenFailTelegram(),
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
        failure_backoff=timedelta(0),
    )

    outcome = worker.run_once(now=now, task_kinds={"send_deep"})
    replay = worker.run_once(
        now=now + timedelta(seconds=1), task_kinds={"send_deep"}
    )

    assert outcome is not None and outcome.status == "effect_committed"
    assert replay is None
    source_job = next(job for job in store.list_jobs() if job.id == queued.job_id)
    assert source_job.state == "leased"
    assert store.receipt_payload(queued.job_id, "send_deep") is None
    assert store.status_snapshot()["committed_effects"] == 1


def test_effect_worker_sends_the_aggregate_digest_from_a_frozen_payload(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 13, 50, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    payload = {
        "input_revision": "2026-08-08:batch:0000",
        "coverage": {
            "input_count": 2,
            "bookmark_ids": ["digest-one", "digest-two"],
        },
        "analysis": {
            "themes": ["Agent memory", "Second Brain"],
            "follow_ups": ["Compare the consolidation strategies"],
            "coverage": {
                "input_count": 2,
                "processed_bookmark_ids": ["digest-one", "digest-two"],
                "omitted_bookmark_ids": [],
            },
        },
    }
    queued = store.enqueue_job(
        bookmark_id="@periodic:aggregate:0000",
        task_kind="send_aggregate",
        input_revision=payload["input_revision"],
        profile="none",
        priority=250,
        input_json=json.dumps(payload),
        now=now,
    )
    telegram = RecordingTelegram()
    worker = EffectWorker(
        store=store,
        telegram=telegram,
        worker_id="effect-test",
        video_dir=tmp_path / "videos",
        note_dir=tmp_path / "notes",
    )

    outcome = worker.run_once(now=now, task_kinds={"send_aggregate"})

    assert outcome is not None and outcome.status == "done"
    assert len(telegram.messages) == 1
    assert "2 processados" in telegram.messages[0].text
    assert "Agent memory" in telegram.messages[0].text
    assert store.receipt_payload(queued.job_id, "send_aggregate")["status"] == "delivered"
