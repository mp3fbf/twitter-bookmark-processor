"""Lease and execute deterministic bookmark effects."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .content import (
    ArticleContent,
    RecallResult,
    fetch_article,
    recall_second_brain,
)
from .effects import (
    TelegramMessage,
    VideoReceipt,
    build_bookmark_notification,
    download_native_video,
    transcode_video_for_telegram,
)
from .materialization import build_aggregate_message, build_deep_message
from .notes import write_source_note
from .store import AutomationStore, Job, LeaseLostError


class TelegramSender(Protocol):
    def send_message(
        self,
        message: TelegramMessage,
        *,
        before_send: Callable[[], None] | None = None,
    ) -> Any: ...

    def send_document(
        self,
        path: str | Path,
        *,
        before_send: Callable[[], None] | None = None,
        **kwargs: Any,
    ) -> Any: ...


@dataclass(frozen=True)
class EffectOutcome:
    job_id: int
    status: str


class EffectWorker:
    """Execute core-owned effects without invoking an inference provider."""

    TASK_KINDS = {
        "notify",
        "deliver_video",
        "fetch_article",
        "recall_context",
        "write_source_note",
        "send_deep",
        "send_aggregate",
    }
    IRREVERSIBLE_TASK_KINDS = {
        "notify",
        "deliver_video",
        "write_source_note",
        "send_deep",
        "send_aggregate",
    }

    def __init__(
        self,
        *,
        store: AutomationStore,
        telegram: TelegramSender,
        worker_id: str,
        video_dir: str | Path,
        note_dir: str | Path,
        download_video: Callable[[Mapping[str, Any], Path], VideoReceipt] = download_native_video,
        transcode_video: Callable[[Path, Path, int], Path] = transcode_video_for_telegram,
        fetch_article_content: Callable[[Mapping[str, Any]], ArticleContent] = fetch_article,
        recall_context: Callable[[str], RecallResult] = recall_second_brain,
        lease_for: timedelta = timedelta(minutes=20),
        failure_backoff: timedelta = timedelta(minutes=10),
        max_attempts: int = 3,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.telegram = telegram
        self.worker_id = worker_id
        self.video_dir = Path(video_dir)
        self.note_dir = Path(note_dir)
        self.download_video = download_video
        self.transcode_video = transcode_video
        self.fetch_article_content = fetch_article_content
        self.recall_context = recall_context
        self.lease_for = lease_for
        self.failure_backoff = failure_backoff
        self.max_attempts = max_attempts
        self.monotonic = monotonic

    def run_once(
        self,
        *,
        now: datetime,
        task_kinds: set[str] | None = None,
    ) -> EffectOutcome | None:
        started_monotonic = self.monotonic()
        selected = self.TASK_KINDS if task_kinds is None else task_kinds & self.TASK_KINDS
        job = self.store.lease_next(
            worker_id=self.worker_id,
            now=now,
            lease_for=self.lease_for,
            profiles={"none"},
            task_kinds=selected,
        )
        if job is None:
            return None
        if job.lease_token is None:
            return EffectOutcome(job_id=job.id, status="lease_lost")
        try:
            attempt_id = self.store.start_attempt(
                job_id=job.id,
                worker_id=self.worker_id,
                lease_token=job.lease_token,
                now=now,
            )
        except LeaseLostError:
            return EffectOutcome(job_id=job.id, status="lease_lost")
        payload = self.store.job_payload(job)
        effect_committed = False

        def commit_effect() -> None:
            nonlocal effect_committed
            if effect_committed:
                return
            if job.task_kind not in self.IRREVERSIBLE_TASK_KINDS:
                raise RuntimeError(
                    f"task cannot cross an effect boundary: {job.task_kind}"
                )
            self.store.mark_effect_committed(
                job_id=job.id,
                attempt_id=attempt_id,
                worker_id=self.worker_id,
                lease_token=job.lease_token,
                now=self._completion_now(now, started_monotonic),
            )
            effect_committed = True

        try:
            result = self._execute(
                job.task_kind,
                payload,
                job,
                commit_effect=commit_effect,
            )
        except LeaseLostError:
            return EffectOutcome(job_id=job.id, status="lease_lost")
        except Exception as exc:  # noqa: BLE001 - converted into durable retry state
            completion_now = self._completion_now(now, started_monotonic)
            explicitly_retryable = getattr(exc, "retryable", None)
            classified_retryable = (
                bool(explicitly_retryable)
                if explicitly_retryable is not None
                else not isinstance(
                    exc,
                    (KeyError, NotImplementedError, TypeError, ValueError),
                )
            )
            retryable = classified_retryable and not effect_committed
            detail = json.dumps(
                {
                    "status": "effect_committed" if effect_committed else "failed",
                    "error_type": type(exc).__name__,
                    "code": getattr(exc, "code", "effect_failed"),
                    "retryable": retryable,
                    "operator_action_required": effect_committed,
                },
                sort_keys=True,
            )
            try:
                target_state = self.store.complete_failure(
                    job_id=job.id,
                    attempt_id=attempt_id,
                    worker_id=self.worker_id,
                    lease_token=job.lease_token,
                    detail_json=detail,
                    available_at=completion_now + self.failure_backoff,
                    now=completion_now,
                    max_attempts=self.max_attempts if retryable else 1,
                )
            except LeaseLostError:
                return EffectOutcome(job_id=job.id, status="lease_lost")
            return EffectOutcome(job_id=job.id, status=target_state)
        completion_now = self._completion_now(now, started_monotonic)
        try:
            self.store.complete_success(
                job_id=job.id,
                attempt_id=attempt_id,
                worker_id=self.worker_id,
                lease_token=job.lease_token,
                provider=None,
                model=None,
                effect_kind=job.task_kind,
                receipt_json=json.dumps(result, ensure_ascii=False, sort_keys=True),
                now=completion_now,
            )
        except LeaseLostError:
            return EffectOutcome(job_id=job.id, status="lease_lost")
        return EffectOutcome(job_id=job.id, status="done")

    def _completion_now(self, started_at: datetime, started_monotonic: float) -> datetime:
        elapsed_seconds = max(0, int(self.monotonic() - started_monotonic))
        return started_at + timedelta(seconds=elapsed_seconds)

    def _execute(
        self,
        task_kind: str,
        payload: dict[str, Any],
        job: Job,
        *,
        commit_effect: Callable[[], None],
    ) -> dict[str, Any]:
        if task_kind == "notify":
            message = build_bookmark_notification(payload)
            receipt = self.telegram.send_message(
                message,
                before_send=commit_effect,
            )
            return {
                "status": "delivered",
                **{key: value for key, value in asdict(receipt).items() if value is not None},
            }
        if task_kind == "deliver_video":
            video = self.download_video(payload, self.video_dir)
            delivered = self.telegram.send_document(
                video.path,
                transcoder=self.transcode_video,
                before_send=commit_effect,
            )
            return {
                "status": "delivered",
                "source_path": str(video.path),
                "source_url": video.source_url,
                "source_size_bytes": video.size_bytes,
                "source_sha256": video.sha256,
                "telegram_message_id": delivered.message_id,
                "telegram_path": delivered.delivered_path,
                "telegram_size_bytes": delivered.size_bytes,
                "telegram_sha256": delivered.sha256,
            }
        if task_kind == "fetch_article":
            article = self.fetch_article_content(payload)
            return {"status": "available", **asdict(article)}
        if task_kind == "recall_context":
            recalled = self.recall_context(self.store.recall_query(job))
            return {"status": "available", **asdict(recalled)}
        if task_kind == "write_source_note":
            commit_effect()
            note = write_source_note(payload, self.note_dir)
            return {
                "status": "written" if note.created else "exists",
                "path": str(note.path),
                "sha256": note.sha256,
                "size_bytes": note.size_bytes,
            }
        if task_kind in {"send_deep", "send_aggregate"}:
            message = (
                build_deep_message(payload)
                if task_kind == "send_deep"
                else build_aggregate_message(payload)
            )
            receipt = self.telegram.send_message(
                message,
                before_send=commit_effect,
            )
            return {
                "status": "delivered",
                **{key: value for key, value in asdict(receipt).items() if value is not None},
            }
        raise NotImplementedError(task_kind)
