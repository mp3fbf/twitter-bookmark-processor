"""Contracts between inference receipts and core-owned materialization."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from bookmark_automation.materialization import build_aggregate_message, build_deep_message
from bookmark_automation.notes import NoteConflictError, write_source_note
from bookmark_automation.runner import RunnerReceipt
from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore
from bookmark_automation.worker import InferenceWorker


class DeepRunner:
    def run(self, **request: Any) -> RunnerReceipt:
        return RunnerReceipt(
            status="succeeded",
            profile=request["profile"],
            provider="subscription-provider",
            model="configured-model",
            attempts=({"status": "succeeded"},),
            output={
                "summary": "Useful summary",
                "durable_insight": "Concepts should consolidate while episodes archive.",
                "why_interesting": "It matches the current Second Brain design.",
                "second_brain_fit": ["Second Brain", "Agent memory"],
                "next_action": "Compare with the current consolidation flow.",
                "source_note": {
                    "title": "Durable memory",
                    "source_type": "article",
                    "source_url": "https://attacker.invalid/invented",
                    "author": "Invented author",
                    "published_at": None,
                    "key_claims": ["Concepts consolidate"],
                    "evidence": [
                        {"claim": "Concepts consolidate", "source_locator": "article body"}
                    ],
                    "provenance": {
                        "bookmark_id": "wrong-bookmark",
                        "input_revision": "wrong-revision",
                        "content_status": "missing",
                        "recall_status": "missing",
                    },
                },
                "promotion_candidates": [],
                "knowledge_disposition": "conceptual",
                "prompt_injection_detected": False,
                "prompt_injection_evidence": [],
            },
        )


class AggregateRunner:
    def run(self, **request: Any) -> RunnerReceipt:
        return RunnerReceipt(
            status="succeeded",
            profile=request["profile"],
            provider="subscription-provider",
            model="configured-model",
            attempts=({"status": "succeeded"},),
            output={
                "themes": ["Agent memory"],
                "changes": [],
                "open_questions": [],
                "follow_ups": ["Compare both sources"],
                "coverage": {
                    "input_count": 2,
                    "processed_bookmark_ids": ["aggregate-one", "aggregate-two"],
                    "omitted_bookmark_ids": [],
                },
                "ranked_bookmarks": [
                    {"bookmark_id": "aggregate-one", "relevance": 0.8, "reason": "A"},
                    {"bookmark_id": "aggregate-two", "relevance": 0.7, "reason": "B"},
                ],
                "promotion_candidates": [],
                "archive_candidates": [],
                "prompt_injection_detected": False,
                "prompt_injection_evidence": [],
            },
        )


class PromptInjectionRunner(DeepRunner):
    def run(self, **request: Any) -> RunnerReceipt:
        receipt = super().run(**request)
        assert receipt.output is not None
        output = dict(receipt.output)
        output.update(
            {
                "summary": "![remote](https://attacker.invalid/pixel) <script>x</script>",
                "promotion_candidates": [{"concept": "Injected promotion"}],
                "knowledge_disposition": "conceptual",
                "prompt_injection_detected": True,
                "prompt_injection_evidence": ["source asked the model to ignore rules"],
                "source_note": {
                    **dict(output["source_note"]),
                    "title": "Unsafe <img src=x>\n# injected heading",
                },
            }
        )
        return RunnerReceipt(
            status=receipt.status,
            profile=receipt.profile,
            provider=receipt.provider,
            model=receipt.model,
            attempts=receipt.attempts,
            output=output,
        )


class InvalidCoverageAggregateRunner(AggregateRunner):
    def run(self, **request: Any) -> RunnerReceipt:
        receipt = super().run(**request)
        assert receipt.output is not None
        output = dict(receipt.output)
        output["coverage"] = {
            "input_count": 2,
            "processed_bookmark_ids": ["aggregate-one", "aggregate-one"],
            "omitted_bookmark_ids": [],
        }
        return RunnerReceipt(
            status=receipt.status,
            profile=receipt.profile,
            provider=receipt.provider,
            model=receipt.model,
            attempts=receipt.attempts,
            output=output,
        )


class AggregateWithUnsafePromotionRunner(AggregateRunner):
    def run(self, **request: Any) -> RunnerReceipt:
        receipt = super().run(**request)
        assert receipt.output is not None
        output = dict(receipt.output)
        output["promotion_candidates"] = [{"concept": "Must not survive"}]
        return RunnerReceipt(
            status=receipt.status,
            profile=receipt.profile,
            provider=receipt.provider,
            model=receipt.model,
            attempts=receipt.attempts,
            output=output,
        )


def test_deep_success_corrects_provenance_and_atomically_enqueues_materialization(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 14, 0, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000200"
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Durable memory https://example.test/memory",
            "author": {"username": "real-author"},
            "urls": [{"expanded_url": "https://example.test/memory"}],
        }
    )
    jobs = {job.task_kind: job for job in store.list_jobs()}
    store.complete_effect(
        job_id=jobs["quick"].id,
        effect_kind="quick",
        payload={"status": "available", "output": {"summary": "Quick"}},
        now=now,
    )
    store.complete_effect(
        job_id=jobs["fetch_article"].id,
        effect_kind="fetch_article",
        payload={
            "status": "available",
            "final_url": "https://example.test/memory",
            "author": "Real Author",
            "published_at": "2026-08-01",
            "text": "Captured article",
            "truncated": False,
        },
        now=now,
    )
    store.complete_effect(
        job_id=jobs["recall_context"].id,
        effect_kind="recall_context",
        payload={"status": "available", "hits": [{"path": "Concepts/Memory.md"}]},
        now=now,
    )
    deep = jobs["deep"]
    worker = InferenceWorker(
        store=store,
        runner=DeepRunner(),
        worker_id="deep-test",
    )

    outcome = worker.run_once(now=now + timedelta(minutes=16))

    assert outcome is not None and outcome.status == "done"
    current_jobs = store.list_jobs()
    downstream = {job.task_kind: job for job in current_jobs}
    assert downstream["write_source_note"].state == "pending"
    assert downstream["send_deep"].state == "pending"
    assert downstream["write_source_note"].input_revision == deep.input_revision
    payload = store.job_payload(downstream["write_source_note"])
    source = payload["analysis"]["source_note"]
    assert source["source_url"] == "https://example.test/memory"
    assert source["author"] == "Real Author"
    assert source["published_at"] == "2026-08-01"
    assert source["provenance"] == {
        "bookmark_id": bookmark_id,
        "input_revision": deep.input_revision,
        "content_status": "available",
        "recall_status": "available",
        "prompt_injection_detected": False,
    }


def test_external_article_without_byline_does_not_inherit_tweet_metadata(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 14, 15, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000203"
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "External source",
            "author": {"username": "tweet_author", "name": "Tweet Author"},
            "createdAt": "2026-08-07T12:34:56.000Z",
            "urls": [{"expanded_url": "https://example.test/no-byline"}],
        }
    )
    jobs = {job.task_kind: job for job in store.list_jobs()}
    for task_kind, payload in (
        ("quick", {"status": "available"}),
        ("recall_context", {"status": "available", "hits": []}),
        (
            "fetch_article",
            {
                "status": "available",
                "final_url": "https://example.test/no-byline",
                "author": None,
                "published_at": None,
                "text": "Captured body",
                "truncated": False,
            },
        ),
    ):
        store.complete_effect(
            job_id=jobs[task_kind].id,
            effect_kind=task_kind,
            payload=payload,
            now=now,
        )

    outcome = InferenceWorker(
        store=store,
        runner=DeepRunner(),
        worker_id="metadata-test",
    ).run_once(now=now + timedelta(minutes=16))

    assert outcome is not None and outcome.status == "done"
    note_job = next(
        job for job in store.list_jobs() if job.task_kind == "write_source_note"
    )
    source = store.job_payload(note_job)["analysis"]["source_note"]
    assert source["author"] is None
    assert source["published_at"] is None


def test_deep_never_preserves_hallucinated_source_metadata_when_capture_is_missing(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 14, 30, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000202"
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "A tweet without an external article",
            "author": {"username": "real_handle"},
            "createdAt": "2026-08-07T12:34:56.000Z",
        }
    )
    for job in store.list_jobs():
        if job.task_kind in {"quick", "recall_context"}:
            store.complete_effect(
                job_id=job.id,
                effect_kind=job.task_kind,
                payload={"status": "available"},
                now=now,
            )
    automation.decide(
        bookmark_id=bookmark_id,
        action="act",
        decision_id="telegram:provenance-test",
    )
    worker = InferenceWorker(
        store=store,
        runner=DeepRunner(),
        worker_id="deep-test",
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "done"
    note_job = next(
        job for job in store.list_jobs() if job.task_kind == "write_source_note"
    )
    source = store.job_payload(note_job)["analysis"]["source_note"]
    assert source["source_url"] == f"https://x.com/real_handle/status/{bookmark_id}"
    assert source["author"] == "real_handle"
    assert source["published_at"] == "2026-08-07T12:34:56.000Z"


def test_source_note_write_is_atomic_stable_and_never_overwrites_conflicting_content(
    tmp_path: Path,
) -> None:
    payload = {
        "bookmark": {
            "id": "1900000000000000201",
            "text": "Original bookmark",
            "author": {"username": "example"},
        },
        "input_revision": "a" * 64,
        "analysis": {
            "summary": "A concise summary",
            "durable_insight": "A durable insight",
            "why_interesting": "It connects to active work",
            "second_brain_fit": ["Second Brain"],
            "next_action": "Test the idea",
            "promotion_candidates": [],
            "knowledge_disposition": "conceptual",
            "source_note": {
                "title": "Durable source",
                "source_type": "article",
                "source_url": "https://example.test/source",
                "author": "Example Author",
                "published_at": "2026-08-01",
                "key_claims": ["One claim"],
                "evidence": [{"claim": "One claim", "source_locator": "paragraph 2"}],
                "provenance": {
                    "bookmark_id": "1900000000000000201",
                    "input_revision": "a" * 64,
                    "content_status": "available",
                    "recall_status": "available",
                },
            },
        },
        "inference": {"provider": "codex", "model": "configured-model"},
    }

    first = write_source_note(payload, tmp_path / "notes")
    replay = write_source_note(payload, tmp_path / "notes")

    assert first.path == replay.path
    assert first.created is True
    assert replay.created is False
    assert first.path.name == "1900000000000000201--aaaaaaaaaaaa.md"
    rendered = first.path.read_text(encoding="utf-8")
    assert "bookmark_id: \"1900000000000000201\"" in rendered
    assert 'input_revision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"' in rendered
    assert "https://example.test/source" in rendered
    assert not list((tmp_path / "notes").glob("*.tmp"))

    conflicting = {
        **payload,
        "analysis": {**payload["analysis"], "summary": "Conflicting rewrite"},
    }
    with pytest.raises(NoteConflictError):
        write_source_note(conflicting, tmp_path / "notes")


def test_prompt_injection_is_quarantined_and_visible_in_note_and_telegram(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 14, 45, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000204"
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Untrusted source"}
    )
    for job in store.list_jobs():
        if job.task_kind in {"quick", "recall_context"}:
            store.complete_effect(
                job_id=job.id,
                effect_kind=job.task_kind,
                payload={"status": "available"},
                now=now,
            )
    automation.decide(
        bookmark_id=bookmark_id,
        action="act",
        decision_id="telegram:injection-test",
    )

    outcome = InferenceWorker(
        store=store,
        runner=PromptInjectionRunner(),
        worker_id="injection-test",
    ).run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "done"
    note_job = next(
        job for job in store.list_jobs() if job.task_kind == "write_source_note"
    )
    payload = store.job_payload(note_job)
    analysis = payload["analysis"]
    assert analysis["promotion_candidates"] == []
    assert analysis["knowledge_disposition"] == "quarantined"
    assert analysis["source_note"]["provenance"]["prompt_injection_detected"] is True

    receipt = write_source_note(payload, tmp_path / "notes")
    rendered = receipt.path.read_text(encoding="utf-8")
    assert "prompt_injection_detected: true" in rendered
    assert "SAÍDA EM QUARENTENA" in rendered
    assert "\\![remote]" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered

    telegram = build_deep_message(payload)
    assert telegram.text.startswith("⚠️ Possível prompt injection")


def test_prompt_injection_quarantine_is_sticky_from_quick_into_deep(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 14, 50, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    bookmark_id = "1900000000000000205"
    automation = BookmarkAutomation(store, clock=lambda: now)
    automation.ingest(
        {"kind": "bookmarks", "id": bookmark_id, "text": "Sticky quarantine"}
    )
    jobs = {job.task_kind: job for job in store.list_jobs()}
    store.complete_effect(
        job_id=jobs["quick"].id,
        effect_kind="quick",
        payload={
            "status": "available",
            "output": {
                "summary": "Flagged during triage",
                "prompt_injection_detected": True,
                "prompt_injection_evidence": ["ignore previous instructions"],
            },
        },
        now=now,
    )
    store.complete_effect(
        job_id=jobs["recall_context"].id,
        effect_kind="recall_context",
        payload={"status": "available", "hits": []},
        now=now,
    )
    automation.decide(
        bookmark_id=bookmark_id,
        action="act",
        decision_id="telegram:sticky-injection-test",
    )

    outcome = InferenceWorker(
        store=store,
        runner=DeepRunner(),  # deliberately reports prompt_injection_detected=false
        worker_id="sticky-injection-test",
    ).run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "done"
    note_job = next(
        job for job in store.list_jobs() if job.task_kind == "write_source_note"
    )
    analysis = store.job_payload(note_job)["analysis"]
    assert analysis["prompt_injection_detected"] is True
    assert "upstream_stage_flagged" in analysis["prompt_injection_evidence"]
    assert analysis["promotion_candidates"] == []
    assert analysis["knowledge_disposition"] == "quarantined"
    assert analysis["source_note"]["provenance"]["prompt_injection_detected"] is True


def test_aggregate_success_enqueues_one_telegram_delivery_with_frozen_coverage(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 15, 0, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store, clock=lambda: now)
    for bookmark_id in ("aggregate-one", "aggregate-two"):
        automation.ingest(
            {"kind": "bookmarks", "id": bookmark_id, "text": f"Source {bookmark_id}"}
        )
    for job in store.list_jobs():
        if job.task_kind == "quick":
            store.complete_effect(
                job_id=job.id,
                effect_kind="quick",
                payload={"status": "available", "output": {"summary": "Quick"}},
                now=now,
            )
    scheduled = automation.schedule_periodic(
        task_kind="aggregate",
        input_revision="2026-08-08",
        batch_size=10,
    )
    aggregate_id = scheduled.job_id
    worker = InferenceWorker(
        store=store,
        runner=AggregateRunner(),
        worker_id="aggregate-test",
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.job_id == aggregate_id
    assert outcome.status == "done"
    delivery = next(job for job in store.list_jobs() if job.task_kind == "send_aggregate")
    payload = store.job_payload(delivery)
    assert payload["analysis"]["coverage"]["processed_bookmark_ids"] == [
        "aggregate-one",
        "aggregate-two",
    ]
    assert payload["coverage"]["bookmark_ids"] == ["aggregate-one", "aggregate-two"]


def test_aggregate_inherits_quarantine_from_any_upstream_bookmark(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 15, 15, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store, clock=lambda: now)
    for bookmark_id in ("aggregate-one", "aggregate-two"):
        automation.ingest(
            {"kind": "bookmarks", "id": bookmark_id, "text": f"Source {bookmark_id}"}
        )
    for job in store.list_jobs():
        if job.task_kind == "quick":
            store.complete_effect(
                job_id=job.id,
                effect_kind="quick",
                payload={
                    "status": "available",
                    "output": {
                        "summary": "Quick",
                        "prompt_injection_detected": job.bookmark_id == "aggregate-one",
                    },
                },
                now=now,
            )
    automation.schedule_periodic(
        task_kind="aggregate",
        input_revision="2026-08-08-quarantine",
        batch_size=10,
    )

    outcome = InferenceWorker(
        store=store,
        runner=AggregateWithUnsafePromotionRunner(),
        worker_id="aggregate-quarantine-test",
    ).run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "done"
    delivery = next(job for job in store.list_jobs() if job.task_kind == "send_aggregate")
    payload = store.job_payload(delivery)
    assert payload["analysis"]["prompt_injection_detected"] is True
    assert payload["analysis"]["promotion_candidates"] == []
    assert build_aggregate_message(payload).text.startswith(
        "⚠️ Possível prompt injection"
    )


def test_aggregate_rejects_duplicate_or_missing_coverage_before_delivery(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 8, 15, 30, tzinfo=UTC)
    store = AutomationStore(tmp_path / "automation.sqlite3")
    automation = BookmarkAutomation(store, clock=lambda: now)
    for bookmark_id in ("aggregate-one", "aggregate-two"):
        automation.ingest(
            {"kind": "bookmarks", "id": bookmark_id, "text": f"Source {bookmark_id}"}
        )
    for job in store.list_jobs():
        if job.task_kind == "quick":
            store.complete_effect(
                job_id=job.id,
                effect_kind="quick",
                payload={"status": "available", "output": {"summary": "Quick"}},
                now=now,
            )
    scheduled = automation.schedule_periodic(
        task_kind="aggregate",
        input_revision="2026-08-08-invalid",
        batch_size=10,
    )
    worker = InferenceWorker(
        store=store,
        runner=InvalidCoverageAggregateRunner(),
        worker_id="aggregate-test",
        failure_backoff=timedelta(minutes=5),
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "pending"
    aggregate = next(job for job in store.list_jobs() if job.id == scheduled.job_id)
    assert aggregate.state == "pending"
    assert aggregate.lease_owner is None
    assert not any(job.task_kind == "send_aggregate" for job in store.list_jobs())
    assert store.receipt_payload(aggregate.id, "aggregate") is None
