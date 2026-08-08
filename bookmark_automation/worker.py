"""Lease and execute provider-neutral semantic jobs."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from .prompts import PromptCatalog
from .runner import RunnerReceipt
from .store import AutomationStore, Job, LeaseLostError


class SubscriptionRunner(Protocol):
    def run(
        self,
        *,
        profile: str,
        prompt: str,
        schema: dict,
        job_id: str,
    ) -> RunnerReceipt: ...


@dataclass(frozen=True)
class WorkerOutcome:
    job_id: int
    status: str


class InferenceWorker:
    PROFILES = {"quick", "deep", "vision", "aggregate"}

    def __init__(
        self,
        *,
        store: AutomationStore,
        runner: SubscriptionRunner,
        worker_id: str,
        prompts: PromptCatalog | None = None,
        lease_for: timedelta = timedelta(minutes=20),
        provider_backoff: timedelta = timedelta(minutes=30),
        failure_backoff: timedelta = timedelta(minutes=10),
        max_attempts: int = 3,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.runner = runner
        self.worker_id = worker_id
        self.prompts = prompts or PromptCatalog()
        self.lease_for = lease_for
        self.provider_backoff = provider_backoff
        self.failure_backoff = failure_backoff
        self.monotonic = monotonic
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.max_attempts = max_attempts

    def run_once(self, *, now: datetime) -> WorkerOutcome | None:
        started_monotonic = self.monotonic()
        job = self.store.lease_next(
            worker_id=self.worker_id,
            now=now,
            lease_for=self.lease_for,
            profiles=self.PROFILES,
        )
        if job is None:
            return None
        if job.lease_token is None:
            return WorkerOutcome(job_id=job.id, status="lease_lost")
        try:
            attempt_id = self.store.start_attempt(
                job_id=job.id,
                worker_id=self.worker_id,
                lease_token=job.lease_token,
                now=now,
            )
        except LeaseLostError:
            return WorkerOutcome(job_id=job.id, status="lease_lost")
        try:
            job_input = self.store.job_input(job)
            prompt, schema = self.prompts.build(job, job_input)
            receipt = self.runner.run(
                profile=job.profile,
                prompt=prompt,
                schema=schema,
                job_id=str(job.id),
            )
        except Exception as exc:
            completion_now = self._completion_now(now, started_monotonic)
            detail_json = json.dumps(
                {"status": "failed", "error_type": type(exc).__name__},
                sort_keys=True,
            )
            return self._complete_failure(
                job=job,
                attempt_id=attempt_id,
                detail_json=detail_json,
                available_at=completion_now + self.failure_backoff,
                now=completion_now,
            )
        receipt_json = json.dumps(asdict(receipt), ensure_ascii=False, sort_keys=True)
        if receipt.status == "waiting_provider":
            completion_now = self._completion_now(now, started_monotonic)
            try:
                self.store.complete_waiting_provider(
                    job_id=job.id,
                    attempt_id=attempt_id,
                    worker_id=self.worker_id,
                    lease_token=job.lease_token,
                    detail_json=receipt_json,
                    available_at=completion_now + self.provider_backoff,
                    now=completion_now,
                )
            except LeaseLostError:
                return WorkerOutcome(job_id=job.id, status="lease_lost")
            return WorkerOutcome(job_id=job.id, status="waiting_provider")
        if receipt.status == "failed":
            completion_now = self._completion_now(now, started_monotonic)
            return self._complete_failure(
                job=job,
                attempt_id=attempt_id,
                detail_json=receipt_json,
                available_at=completion_now + self.failure_backoff,
                now=completion_now,
            )
        if receipt.output is None:
            completion_now = self._completion_now(now, started_monotonic)
            return self._complete_failure(
                job=job,
                attempt_id=attempt_id,
                detail_json=json.dumps(
                    {"status": "failed", "error_type": "MissingStructuredOutput"},
                    sort_keys=True,
                ),
                available_at=completion_now + self.failure_backoff,
                now=completion_now,
            )
        try:
            normalized_output = dict(receipt.output)
            upstream_injection = self._upstream_prompt_injection(job_input)
            if upstream_injection:
                normalized_output["prompt_injection_detected"] = True
                evidence = list(normalized_output.get("prompt_injection_evidence") or [])
                if "upstream_stage_flagged" not in evidence:
                    evidence.append("upstream_stage_flagged")
                normalized_output["prompt_injection_evidence"] = evidence
            if bool(normalized_output.get("prompt_injection_detected")):
                normalized_output["promotion_candidates"] = []
            if job.task_kind == "deep":
                normalized_output = self._normalize_deep_output(
                    job=job,
                    job_input=job_input,
                    output=normalized_output,
                )
            if job.task_kind == "aggregate":
                self._validate_aggregate_output(job_input, normalized_output)
            receipt = replace(receipt, output=normalized_output)
        except Exception as exc:  # noqa: BLE001 - invalid semantic output is retryable work
            completion_now = self._completion_now(now, started_monotonic)
            return self._complete_failure(
                job=job,
                attempt_id=attempt_id,
                detail_json=json.dumps(
                    {"status": "failed", "error_type": type(exc).__name__},
                    sort_keys=True,
                ),
                available_at=completion_now + self.failure_backoff,
                now=completion_now,
            )
        receipt_json = json.dumps(asdict(receipt), ensure_ascii=False, sort_keys=True)
        downstream_jobs: list[dict[str, Any]] = []
        if job.task_kind == "deep":
            materialization = {
                "bookmark": job_input["bookmark"],
                "analysis": dict(receipt.output),
                "evidence_status": job_input.get("evidence_status", {}),
                "input_revision": job.input_revision,
                "inference": {
                    "provider": receipt.provider,
                    "model": receipt.model,
                },
            }
            input_json = json.dumps(materialization, ensure_ascii=False, sort_keys=True)
            for task_kind, priority in (
                ("write_source_note", 650),
                ("send_deep", 640),
            ):
                downstream_jobs.append(
                    {
                        "bookmark_id": job.bookmark_id,
                        "task_kind": task_kind,
                        "input_revision": job.input_revision,
                        "profile": "none",
                        "input_json": input_json,
                        "priority": priority,
                    }
                )
        if job.task_kind == "aggregate":
            materialization = {
                "analysis": dict(receipt.output),
                "coverage": job_input.get("coverage", {}),
                "input_revision": job.input_revision,
                "inference": {
                    "provider": receipt.provider,
                    "model": receipt.model,
                },
            }
            downstream_jobs.append(
                {
                    "bookmark_id": job.bookmark_id,
                    "task_kind": "send_aggregate",
                    "input_revision": job.input_revision,
                    "profile": "none",
                    "input_json": json.dumps(
                        materialization, ensure_ascii=False, sort_keys=True
                    ),
                    "priority": 250,
                }
            )
        completion_now = self._completion_now(now, started_monotonic)
        try:
            self.store.complete_success(
                job_id=job.id,
                attempt_id=attempt_id,
                worker_id=self.worker_id,
                lease_token=job.lease_token,
                provider=receipt.provider,
                model=receipt.model,
                effect_kind=job.task_kind,
                receipt_json=receipt_json,
                now=completion_now,
                downstream_jobs=downstream_jobs,
            )
        except LeaseLostError:
            return WorkerOutcome(job_id=job.id, status="lease_lost")
        return WorkerOutcome(job_id=job.id, status="done")

    def _completion_now(self, started_at: datetime, started_monotonic: float) -> datetime:
        elapsed_seconds = max(0, int(self.monotonic() - started_monotonic))
        return started_at + timedelta(seconds=elapsed_seconds)

    @staticmethod
    def _upstream_prompt_injection(job_input: dict[str, Any]) -> bool:
        """Keep a prompt-injection signal sticky across semantic stages."""

        def flagged(value: Any) -> bool:
            if isinstance(value, dict):
                if value.get("prompt_injection_detected") is True:
                    return True
                return any(flagged(item) for item in value.values())
            if isinstance(value, list):
                return any(flagged(item) for item in value)
            return False

        if flagged(job_input.get("upstream") or {}):
            return True
        bookmarks = job_input.get("bookmarks") or []
        if not isinstance(bookmarks, list):
            return False
        return any(
            flagged(item.get("upstream") or {})
            for item in bookmarks
            if isinstance(item, dict)
        )

    def _complete_failure(
        self,
        *,
        job: Job,
        attempt_id: int,
        detail_json: str,
        available_at: datetime,
        now: datetime,
    ) -> WorkerOutcome:
        if job.lease_token is None:
            return WorkerOutcome(job_id=job.id, status="lease_lost")
        try:
            target_state = self.store.complete_failure(
                job_id=job.id,
                attempt_id=attempt_id,
                worker_id=self.worker_id,
                lease_token=job.lease_token,
                detail_json=detail_json,
                available_at=available_at,
                now=now,
                max_attempts=self.max_attempts,
            )
        except LeaseLostError:
            return WorkerOutcome(job_id=job.id, status="lease_lost")
        return WorkerOutcome(job_id=job.id, status=target_state)

    @staticmethod
    def _normalize_deep_output(
        *,
        job: Any,
        job_input: dict[str, Any],
        output: dict[str, Any],
    ) -> dict[str, Any]:
        source = dict(output.get("source_note") or {})
        upstream = job_input.get("upstream") or {}
        evidence_status = job_input.get("evidence_status") or {}
        article = upstream.get("fetch_article") or {}
        recall = upstream.get("recall_context") or {}
        bookmark = job_input.get("bookmark") or {}

        article_status = str(article.get("status") or evidence_status.get("fetch_article") or "")
        if not article_status:
            content_status = "not_applicable"
        elif article_status == "available":
            content_status = "partial" if article.get("truncated") else "available"
        elif article_status == "partial":
            content_status = "partial"
        else:
            content_status = "missing"
        recall_status_raw = str(
            recall.get("status") or evidence_status.get("recall_context") or "missing"
        )
        recall_status = (
            recall_status_raw if recall_status_raw in {"available", "partial"} else "missing"
        )

        nested_author = bookmark.get("author")
        if not isinstance(nested_author, dict):
            nested_author = {}
        raw_username = str(
            bookmark.get("username") or nested_author.get("username") or ""
        ).lstrip("@")
        bookmark_id = str(bookmark.get("id") or job.bookmark_id or "")
        tweet_url = (
            f"https://x.com/{raw_username}/status/{bookmark_id}"
            if re.fullmatch(r"[A-Za-z0-9_]{1,15}", raw_username)
            else f"https://x.com/i/web/status/{bookmark_id}"
        )
        source["source_url"] = (
            article.get("final_url") or article.get("original_url") or tweet_url
        )
        if article_status:
            # External article metadata must not silently inherit the tweet's
            # author/date. Embedded X Articles already carry those values in
            # their deterministic fetch receipt.
            source["author"] = article.get("author") or None
            source["published_at"] = article.get("published_at") or None
        else:
            source["author"] = (
                nested_author.get("name")
                or nested_author.get("username")
                or bookmark.get("username")
                or None
            )
            source["published_at"] = (
                bookmark.get("createdAt") or bookmark.get("created_at") or None
            )
        prompt_injection_detected = bool(output.get("prompt_injection_detected"))
        source["provenance"] = {
            "bookmark_id": str(job.bookmark_id),
            "input_revision": job.input_revision,
            "content_status": content_status,
            "recall_status": recall_status,
            "prompt_injection_detected": prompt_injection_detected,
        }
        if prompt_injection_detected:
            # No flagged model suggestion may become a promotion candidate.
            # The immutable Source remains useful evidence, but is explicit
            # quarantine pending human review.
            output["promotion_candidates"] = []
            output["knowledge_disposition"] = "quarantined"
        output["source_note"] = source
        return output

    @staticmethod
    def _validate_aggregate_output(
        job_input: dict[str, Any], output: dict[str, Any]
    ) -> None:
        expected = [str(value) for value in (job_input.get("coverage") or {}).get("bookmark_ids", [])]
        coverage = output.get("coverage") or {}
        processed = [str(value) for value in coverage.get("processed_bookmark_ids", [])]
        omitted = list(coverage.get("omitted_bookmark_ids", []))
        ranked = [
            str(item.get("bookmark_id"))
            for item in output.get("ranked_bookmarks", [])
            if isinstance(item, dict)
        ]
        if int(coverage.get("input_count", -1)) != len(expected):
            raise ValueError("aggregate coverage input_count mismatch")
        if omitted:
            raise ValueError("aggregate omitted bookmarks")
        if len(processed) != len(set(processed)) or set(processed) != set(expected):
            raise ValueError("aggregate processed bookmark coverage mismatch")
        if len(ranked) != len(set(ranked)) or set(ranked) != set(expected):
            raise ValueError("aggregate ranking coverage mismatch")
