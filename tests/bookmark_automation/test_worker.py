"""Inference worker state transition contracts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bookmark_automation.runner import RunnerReceipt
from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore
from bookmark_automation.worker import InferenceWorker


class SuccessfulRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, **request: Any) -> RunnerReceipt:
        self.calls.append(request)
        return RunnerReceipt(
            status="succeeded",
            profile=request["profile"],
            provider="subscription-provider",
            model="router-selected-model",
            attempts=({"status": "succeeded"},),
            output={"summary": "A concise summary", "why_interesting": "Agent memory"},
        )


class WaitingRunner:
    def run(self, **request: Any) -> RunnerReceipt:
        return RunnerReceipt(
            status="waiting_provider",
            profile=request["profile"],
            provider=None,
            model=None,
            attempts=({"status": "unavailable"},),
            output=None,
        )


class FailingRunner:
    def run(self, **request: Any) -> RunnerReceipt:
        return RunnerReceipt(
            status="failed",
            profile=request["profile"],
            provider=None,
            model=None,
            attempts=({"status": "failed", "code": "invalid_output"},),
            output=None,
        )


class EmptySuccessRunner:
    def run(self, **request: Any) -> RunnerReceipt:
        return RunnerReceipt(
            status="succeeded",
            profile=request["profile"],
            provider="subscription-provider",
            model="router-selected-model",
            attempts=({"status": "succeeded"},),
            output=None,
        )


class RaisingRunner:
    def run(self, **request: Any) -> RunnerReceipt:
        raise RuntimeError("simulated subprocess failure")


class SequenceRunner:
    def __init__(self, statuses: list[str]) -> None:
        self.statuses = iter(statuses)

    def run(self, **request: Any) -> RunnerReceipt:
        status = next(self.statuses)
        return RunnerReceipt(
            status=status,
            profile=request["profile"],
            provider=None,
            model=None,
            attempts=({"status": status},),
            output=None,
        )


class ExplodingPrompts:
    def build(self, *_args: Any, **_kwargs: Any) -> tuple[str, dict[str, Any]]:
        raise ValueError("simulated prompt construction failure")


class SkipDuringDeepRunner(SuccessfulRunner):
    def __init__(self, automation: BookmarkAutomation, bookmark_id: str) -> None:
        super().__init__()
        self.automation = automation
        self.bookmark_id = bookmark_id

    def run(self, **request: Any) -> RunnerReceipt:
        self.automation.decide(
            bookmark_id=self.bookmark_id,
            action="skip",
            decision_id="telegram:skip-during-deep",
        )
        return super().run(**request)


def test_worker_processes_semantic_job_and_records_attempt_and_receipt(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000030",
            "text": "Durable memory for autonomous agents",
        }
    )
    runner = SuccessfulRunner()
    worker = InferenceWorker(store=store, runner=runner, worker_id="test-worker")

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None
    assert outcome.status == "done"
    assert runner.calls[0]["profile"] == "quick"
    assert "Durable memory" in runner.calls[0]["prompt"]
    assert runner.calls[0]["schema"]["type"] == "object"
    assert store.count("attempts") == 1
    assert store.count("receipts") == 1
    states = {job.task_kind: job.state for job in store.list_jobs()}
    assert states == {"notify": "pending", "quick": "done", "recall_context": "pending"}
    quick_job = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert store.receipt_payload(quick_job.id, "quick")["output"]["summary"] == (
        "A concise summary"
    )


def test_worker_backs_off_in_waiting_provider_without_api_fallback(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {"kind": "bookmarks", "id": "1900000000000000031", "text": "Wait safely"}
    )
    worker = InferenceWorker(
        store=store,
        runner=WaitingRunner(),
        worker_id="test-worker",
        provider_backoff=timedelta(minutes=30),
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None
    assert outcome.status == "waiting_provider"
    quick = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert quick.state == "waiting_provider"
    assert quick.available_at == (now + timedelta(minutes=30, seconds=1)).isoformat()
    assert quick.lease_owner is None
    assert store.count("attempts") == 1
    assert store.count("receipts") == 0


def test_worker_retries_failed_router_receipt_after_bounded_backoff(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {"kind": "bookmarks", "id": "1900000000000000032", "text": "Retry safely"}
    )
    worker = InferenceWorker(
        store=store,
        runner=FailingRunner(),
        worker_id="test-worker",
        failure_backoff=timedelta(minutes=10),
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None
    assert outcome.status == "pending"
    quick = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert quick.state == "pending"
    assert quick.available_at == (now + timedelta(minutes=10, seconds=1)).isoformat()
    assert quick.lease_owner is None


def test_worker_rejects_a_success_receipt_without_structured_output(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {"kind": "bookmarks", "id": "1900000000000000037", "text": "Empty success"}
    )
    worker = InferenceWorker(
        store=store,
        runner=EmptySuccessRunner(),
        worker_id="test-worker",
        failure_backoff=timedelta(minutes=10),
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "pending"
    quick = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert quick.state == "pending"
    assert quick.lease_owner is None
    assert store.receipt_payload(quick.id, "quick") is None


def test_worker_moves_repeated_failures_to_dead_letter(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {"kind": "bookmarks", "id": "1900000000000000033", "text": "Bound retries"}
    )
    worker = InferenceWorker(
        store=store,
        runner=FailingRunner(),
        worker_id="test-worker",
        failure_backoff=timedelta(0),
        max_attempts=2,
    )

    first = worker.run_once(now=now + timedelta(seconds=1))
    second = worker.run_once(now=now + timedelta(seconds=2))

    assert first is not None and first.status == "pending"
    assert second is not None and second.status == "dead_letter"
    quick = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert quick.state == "dead_letter"
    assert store.count("attempts") == 2


def test_worker_records_runner_exception_and_releases_lease_for_retry(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {"kind": "bookmarks", "id": "1900000000000000034", "text": "Subprocess crash"}
    )
    worker = InferenceWorker(
        store=store,
        runner=RaisingRunner(),
        worker_id="test-worker",
        failure_backoff=timedelta(minutes=5),
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "pending"
    quick = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert quick.state == "pending"
    assert quick.lease_owner is None
    assert store.count("attempts") == 1


def test_worker_records_prompt_exception_and_releases_lease_for_retry(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {"kind": "bookmarks", "id": "1900000000000000036", "text": "Prompt crash"}
    )
    worker = InferenceWorker(
        store=store,
        runner=SuccessfulRunner(),
        worker_id="test-worker",
        prompts=ExplodingPrompts(),  # type: ignore[arg-type]
        failure_backoff=timedelta(minutes=5),
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "pending"
    quick = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert quick.state == "pending"
    assert quick.lease_owner is None
    assert store.count("attempts") == 1


def test_waiting_provider_attempts_do_not_consume_failure_dlq_budget(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {"kind": "bookmarks", "id": "1900000000000000035", "text": "Quota then fail"}
    )
    worker = InferenceWorker(
        store=store,
        runner=SequenceRunner(["waiting_provider", "waiting_provider", "failed"]),
        worker_id="test-worker",
        provider_backoff=timedelta(0),
        failure_backoff=timedelta(0),
        max_attempts=2,
    )

    worker.run_once(now=now + timedelta(seconds=1))
    worker.run_once(now=now + timedelta(seconds=2))
    third = worker.run_once(now=now + timedelta(seconds=3))

    assert third is not None and third.status == "pending"
    quick = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert quick.state == "pending"


def test_skip_during_deep_fences_materialization_and_keeps_video_delivery(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    bookmark_id = "1900000000000000038"
    clock_now = [now]
    automation = BookmarkAutomation(store, clock=lambda: clock_now[0])
    automation.ingest(
        {
            "kind": "bookmarks",
            "id": bookmark_id,
            "text": "Skip this deep analysis while it is running",
            "hasVideo": True,
            "urls": [{"expanded_url": "https://example.test/article"}],
        }
    )
    jobs = {job.task_kind: job for job in store.list_jobs()}
    for task_kind in ("quick", "fetch_article", "recall_context"):
        store.complete_effect(
            job_id=jobs[task_kind].id,
            effect_kind=task_kind,
            payload={"status": "available"},
            now=now + timedelta(minutes=15),
        )
    clock_now[0] = now + timedelta(minutes=16)
    worker = InferenceWorker(
        store=store,
        runner=SkipDuringDeepRunner(automation, bookmark_id),
        worker_id="deep-worker",
    )

    outcome = worker.run_once(now=now + timedelta(minutes=16))

    assert outcome is not None and outcome.status == "lease_lost"
    current = {job.task_kind: job.state for job in store.list_jobs()}
    assert current["deep"] == "cancelled"
    assert current["deliver_video"] == "pending"
    assert "write_source_note" not in current
    assert "send_deep" not in current
    deep = next(job for job in store.list_jobs() if job.task_kind == "deep")
    assert store.receipt_payload(deep.id, "deep") is None


def test_worker_cannot_complete_after_its_lease_expires_during_inference(
    tmp_path: Path,
) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 18, 0, tzinfo=UTC)
    BookmarkAutomation(store, clock=lambda: now).ingest(
        {
            "kind": "bookmarks",
            "id": "1900000000000000039",
            "text": "Inference outlives its claim",
        }
    )
    ticks = iter((0.0, 301.0))
    worker = InferenceWorker(
        store=store,
        runner=SuccessfulRunner(),
        worker_id="slow-worker",
        lease_for=timedelta(minutes=5),
        monotonic=lambda: next(ticks),
    )

    outcome = worker.run_once(now=now + timedelta(seconds=1))

    assert outcome is not None and outcome.status == "lease_lost"
    quick = next(job for job in store.list_jobs() if job.task_kind == "quick")
    assert quick.state == "leased"
    assert store.receipt_payload(quick.id, "quick") is None
