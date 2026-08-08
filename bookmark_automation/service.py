"""Domain service for ingesting bookmark events."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping

from .content import embedded_x_article_raw_text
from .store import AutomationStore, EnqueueResult
from .store import DecisionResult as StoreDecisionResult


_VOLATILE_REVISION_FIELDS = {
    "metrics",
    "_raw",
    "likeCount",
    "favoriteCount",
    "retweetCount",
    "replyCount",
    "quoteCount",
    "viewCount",
    "bookmarkCount",
}


def _stable_input(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_input(item)
            for key, item in value.items()
            if key not in _VOLATILE_REVISION_FIELDS
        }
    if isinstance(value, list):
        return [_stable_input(item) for item in value]
    return value


def _revision_input(event: Mapping[str, Any]) -> dict[str, Any]:
    stable = _stable_input(event)
    if not isinstance(stable, dict):
        raise TypeError("bookmark revision input must be an object")
    raw_article_text = embedded_x_article_raw_text(event)
    if raw_article_text is not None:
        stable["_raw_article_body_sha256"] = hashlib.sha256(
            raw_article_text.encode("utf-8")
        ).hexdigest()
    return stable


@dataclass(frozen=True)
class IngestionResult:
    accepted: bool
    created: bool = False


@dataclass(frozen=True)
class DecisionResult:
    created: bool


@dataclass(frozen=True)
class PeriodicScheduleResult:
    results: tuple[EnqueueResult, ...]

    @property
    def created(self) -> bool:
        return any(result.created for result in self.results)

    @property
    def job_id(self) -> int:
        if not self.results:
            raise ValueError("periodic schedule did not produce a job")
        return self.results[0].job_id

    @property
    def job_ids(self) -> tuple[int, ...]:
        return tuple(result.job_id for result in self.results)


class BookmarkAutomation:
    def __init__(
        self,
        store: AutomationStore,
        *,
        clock: Callable[[], datetime] | None = None,
        deep_grace: timedelta = timedelta(minutes=15),
        defer_for: timedelta = timedelta(hours=24),
    ) -> None:
        self.store = store
        self.clock = clock or (lambda: datetime.now(UTC))
        self.deep_grace = deep_grace
        self.defer_for = defer_for

    def ingest(
        self,
        event: Mapping[str, Any],
        *,
        bootstrap: bool = False,
    ) -> IngestionResult:
        if event.get("kind") != "bookmarks":
            return IngestionResult(accepted=False)
        bookmark_id = str(event.get("id") or "").strip()
        if not bookmark_id:
            raise ValueError("bookmark event requires a non-empty id")
        payload_json = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        revision_json = json.dumps(
            _revision_input(event),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        input_revision = hashlib.sha256(revision_json.encode("utf-8")).hexdigest()
        current_time = self.clock()
        now = current_time.isoformat()
        jobs: list[dict[str, Any]] = []
        if not bootstrap:
            jobs.extend(
                [
                    {"task_kind": "notify", "profile": "none", "priority": 1_000},
                    {"task_kind": "quick", "profile": "quick", "priority": 800},
                    {
                        "task_kind": "recall_context",
                        "profile": "none",
                        "priority": 700,
                    },
                ]
            )
            if event.get("urls") or event.get("article"):
                jobs.append(
                    {"task_kind": "fetch_article", "profile": "none", "priority": 850}
                )
                jobs.append(
                    {
                        "task_kind": "deep",
                        "profile": "deep",
                        "priority": 500,
                        "available_at": (current_time + self.deep_grace).isoformat(),
                    }
                )
            if event.get("hasVideo") is True:
                jobs.append(
                    {"task_kind": "deliver_video", "profile": "none", "priority": 900}
                )
        created = self.store.add_bookmark_with_jobs(
            bookmark_id=bookmark_id,
            payload_json=payload_json,
            input_revision=input_revision,
            created_at=now,
            jobs=jobs,
            mark_aggregate_covered=bootstrap,
        )
        return IngestionResult(accepted=True, created=created)

    def decide(self, *, bookmark_id: str, action: str, decision_id: str) -> DecisionResult:
        if action not in {"act", "keep", "defer", "skip"}:
            raise ValueError(f"unsupported action: {action}")
        if not decision_id.strip():
            raise ValueError("decision_id must not be empty")
        priorities = {"act": 1_200, "keep": 700, "defer": 500}
        now = self.clock()
        deep_available_at = now
        if action == "keep":
            deep_available_at = now + self.deep_grace
        elif action == "defer":
            deep_available_at = now + self.defer_for
        stored: StoreDecisionResult = self.store.record_decision(
            bookmark_id=bookmark_id,
            action=action,
            decision_id=decision_id,
            now=now,
            deep_priority=priorities.get(action),
            deep_available_at=deep_available_at,
            defer_until=now + self.defer_for if action == "defer" else None,
            cancel_semantic=action == "skip",
        )
        return DecisionResult(created=stored.created)

    def schedule_periodic(
        self,
        *,
        task_kind: str,
        input_revision: str,
        batch_size: int = 25,
    ) -> PeriodicScheduleResult:
        if task_kind not in {"aggregate", "backlog"}:
            raise ValueError(f"unsupported periodic task: {task_kind}")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        items = self.store.periodic_items(task_kind=task_kind)
        now = self.clock()
        results: list[EnqueueResult] = []
        if task_kind == "backlog":
            for item in items[:batch_size]:
                bookmark = item["bookmark"]
                specifications = [
                    ("quick", "quick", 140),
                    ("recall_context", "none", 120),
                ]
                if bookmark.get("urls") or bookmark.get("article"):
                    specifications.append(("fetch_article", "none", 130))
                specifications.append(("deep", "deep", 100))
                for scheduled_kind, profile, priority in specifications:
                    queued = self.store.enqueue_job(
                        bookmark_id=str(bookmark["id"]),
                        task_kind=scheduled_kind,
                        input_revision=str(item["input_revision"]),
                        profile=profile,
                        priority=priority,
                        input_json=json.dumps(bookmark, ensure_ascii=False, sort_keys=True),
                        now=now,
                    )
                    if scheduled_kind == "deep":
                        results.append(queued)
            return PeriodicScheduleResult(tuple(results))

        batches = [
            items[index : index + batch_size]
            for index in range(0, len(items), batch_size)
        ]
        for batch in batches:
            member_keys = [
                (str(item["bookmark"]["id"]), str(item["input_revision"]))
                for item in batch
            ]
            fingerprint = hashlib.sha256(
                json.dumps(member_keys, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:16]
            snapshot = {
                "scope": f"@periodic:aggregate:{fingerprint}",
                "task_kind": "aggregate",
                "input_revision": input_revision,
                "coverage": {
                    "input_count": len(batch),
                    "bookmark_ids": [item["bookmark"]["id"] for item in batch],
                },
                "bookmarks": batch,
            }
            results.append(
                self.store.enqueue_aggregate_job(
                    bookmark_id=snapshot["scope"],
                    input_revision=f"{input_revision}:batch:{fingerprint}",
                    priority=300,
                    input_json=json.dumps(snapshot, ensure_ascii=False, sort_keys=True),
                    members=member_keys,
                    period_revision=input_revision,
                    now=now,
                )
            )
        return PeriodicScheduleResult(tuple(results))
