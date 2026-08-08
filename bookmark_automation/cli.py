"""Offline command line interface for the bookmark automation sidecar."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence, TextIO
from zoneinfo import ZoneInfo

from .effect_worker import EffectWorker
from .effects import TelegramClient
from .note_coverage import scan_note_coverage
from .runner import SubprocessSubscriptionRunner
from .service import BookmarkAutomation
from .store import AutomationStore
from .worker import InferenceWorker

BRASILIA = ZoneInfo("America/Sao_Paulo")
DEFAULT_VIDEO_DIR = Path("/workspace/twitter-bookmark-processor/data/videos")
DEFAULT_NOTE_DIR = Path("/workspace/notes/Sources/twitter")


def _has_video(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("type") in {"video", "animated_gif"}:
            return True
        if isinstance(value.get("videoUrl") or value.get("video_url"), str):
            return True
        return any(_has_video(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_video(item) for item in value)
    return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bookmark-automation")
    parser.add_argument("--db", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="ingest bookmark JSON without network access")
    ingest.add_argument("--input", default="-", help="JSON fixture path or - for stdin")
    ingest.add_argument("--kind", choices=("bookmarks", "likes"))
    ingest.add_argument(
        "--bootstrap",
        action="store_true",
        help="one-time historical preseed without notification or video jobs",
    )
    ingest.add_argument(
        "--expected-minimum",
        type=int,
        help="required positive lower bound for a complete bootstrap export",
    )
    decision = commands.add_parser("decision", help="persist an idempotent Telegram callback")
    decision.add_argument("--input", default="-", help="callback JSON path or - for stdin")
    schedule = commands.add_parser("schedule", help="enqueue periodic semantic work")
    schedule.add_argument("--task-kind", required=True, choices=("aggregate", "backlog"))
    schedule.add_argument(
        "--input-revision",
        default=datetime.now(BRASILIA).date().isoformat(),
        help="immutable period identifier (defaults to today's date in Brasilia)",
    )
    schedule.add_argument("--batch-size", type=int, default=25)
    worker = commands.add_parser("worker", help="drain provider-neutral inference jobs")
    worker.add_argument("--max-jobs", type=int, default=20)
    worker.add_argument("--worker-id", default=f"{socket.gethostname()}:{os.getpid()}")
    worker.add_argument(
        "--runner-command-json",
        default=os.environ.get("BOOKMARK_AUTOMATION_RUNNER_COMMAND_JSON"),
    )
    effects = commands.add_parser("effects", help="drain deterministic content and Telegram jobs")
    effects.add_argument("--max-jobs", type=int, default=50)
    effects.add_argument("--worker-id", default=f"{socket.gethostname()}:{os.getpid()}:effects")
    effects.add_argument("--video-dir", type=Path, default=DEFAULT_VIDEO_DIR)
    effects.add_argument("--note-dir", type=Path, default=DEFAULT_NOTE_DIR)
    import_decisions = commands.add_parser(
        "import-decisions", help="idempotently import Telegram bridge JSONL"
    )
    import_decisions.add_argument("--input", default="-", help="JSONL path or - for stdin")
    note_coverage = commands.add_parser(
        "import-note-coverage",
        help="preseed backlog coverage from existing Twitter Source notes",
    )
    note_coverage.add_argument("--notes-dir", type=Path, default=DEFAULT_NOTE_DIR)
    note_coverage.add_argument(
        "--redo-thin",
        action="store_true",
        help="leave legacy thin-content notes eligible for backlog processing",
    )
    gate = commands.add_parser("gate", help="read-only activation gate validation")
    gate.add_argument("--require-note-coverage", action="store_true")
    commands.add_parser("status", help="report sidecar queue counts without stored content")
    dead_letter = commands.add_parser(
        "dead-letter-list",
        help="read-only DLQ inspection; requeue is intentionally unsupported",
    )
    dead_letter.add_argument("--limit", type=int, default=100)
    return parser


def _events(payload: Any, *, forced_kind: str | None = None) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        events = payload
        default_kind = forced_kind
    elif isinstance(payload, dict):
        default_kind = payload.get("kind") or forced_kind
        candidate = payload.get("items")
        if candidate is None:
            candidate = payload.get("bookmarks")
        if candidate is None:
            candidate = payload.get("tweets")
        if candidate is None and isinstance(payload.get("payload"), dict):
            candidate = payload["payload"].get("data")
        events = candidate if isinstance(candidate, list) else [payload]
    else:
        raise ValueError("input must be a JSON object or array")
    normalized: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("each event must be a JSON object")
        item = dict(event)
        if default_kind is not None:
            item.setdefault("kind", default_kind)
        if item.get("kind") == "bookmarks" and "hasVideo" not in item:
            item["hasVideo"] = _has_video(item)
        if item.get("kind") == "bookmarks" and not item.get("urls"):
            text = item.get("text")
            if isinstance(text, str):
                extracted = [
                    match.rstrip(".,;:!?)]}")
                    for match in re.findall(r"https?://[^\s<>\"']+", text)
                ]
                if extracted:
                    item["urls"] = [{"url": url} for url in extracted]
        normalized.append(item)
    return normalized


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> int:
    args = _parser().parse_args(argv)

    if args.command == "status":
        if not args.db.is_file():
            json.dump({"database_exists": False}, stdout, sort_keys=True)
            stdout.write("\n")
            return 0
        store = AutomationStore(args.db, initialize=False)
        snapshot = {"database_exists": True, **store.status_snapshot()}
        json.dump(snapshot, stdout, ensure_ascii=False, sort_keys=True)
        stdout.write("\n")
        return 0
    if args.command == "gate":
        if not args.db.is_file():
            result = {
                "bootstrap_count_valid": False,
                "bootstrap_valid": False,
                "database_exists": False,
                "integrity_valid": False,
                "note_coverage_required": args.require_note_coverage,
                "note_coverage_valid": False,
                "valid": False,
            }
        else:
            result = AutomationStore(args.db, initialize=False).gate_status(
                require_note_coverage=args.require_note_coverage
            )
        json.dump(result, stdout, sort_keys=True)
        stdout.write("\n")
        return 0 if result["valid"] else 1
    if args.command == "dead-letter-list":
        if not args.db.is_file():
            json.dump({"database_exists": False, "jobs": []}, stdout, sort_keys=True)
            stdout.write("\n")
            return 0
        store = AutomationStore(args.db, initialize=False)
        jobs = store.dead_letter_snapshot(limit=args.limit)
        json.dump(
            {"database_exists": True, "jobs": jobs, "requeue_supported": False},
            stdout,
            sort_keys=True,
        )
        stdout.write("\n")
        return 0

    def load_input(path: str) -> Any:
        if path == "-":
            return json.load(stdin)
        with Path(path).open(encoding="utf-8") as fixture:
            return json.load(fixture)

    bootstrap_requested = args.command == "ingest" and args.bootstrap
    prepared_payload: Any | None = None
    prepared_events: list[dict[str, Any]] | None = None
    if args.command == "ingest":
        if args.bootstrap and args.kind != "bookmarks":
            raise ValueError("bootstrap requires --kind bookmarks")
        if args.expected_minimum is not None and not args.bootstrap:
            raise ValueError("--expected-minimum is only valid with --bootstrap")
        if args.bootstrap and (args.expected_minimum is None or args.expected_minimum <= 0):
            raise ValueError("bootstrap requires a positive --expected-minimum")
        if args.bootstrap:
            prepared_payload = load_input(args.input)
            prepared_events = _events(prepared_payload, forced_kind=args.kind)
            if (
                isinstance(prepared_payload, dict)
                and "nextCursor" in prepared_payload
                and prepared_payload["nextCursor"] not in (None, "")
            ):
                raise ValueError("bootstrap envelope nextCursor is not terminal")
            accepted_events = [
                event for event in prepared_events if event.get("kind") == "bookmarks"
            ]
            bookmark_ids: list[str] = []
            for event in accepted_events:
                bookmark_id = str(event.get("id") or "").strip()
                if not bookmark_id:
                    raise ValueError("bookmark event requires a non-empty id")
                bookmark_ids.append(bookmark_id)
            duplicate_ids = sorted(
                bookmark_id
                for bookmark_id, count in Counter(bookmark_ids).items()
                if count > 1
            )
            if duplicate_ids:
                raise ValueError(
                    f"bootstrap contains duplicate bookmark id: {duplicate_ids[0]}"
                )
            if len(accepted_events) < args.expected_minimum:
                raise ValueError(
                    f"bootstrap accepted {len(accepted_events)} bookmark(s); "
                    f"expected minimum {args.expected_minimum}"
                )

    if bootstrap_requested:
        if args.db.is_file() and AutomationStore(
            args.db, initialize=False
        ).gate_status()["valid"]:
            raise ValueError("bookmark automation bootstrap is already complete")
    else:
        if not args.db.is_file():
            raise ValueError("bookmark bootstrap gate is not valid for this database")
        read_only_store = AutomationStore(args.db, initialize=False)
        gate = read_only_store.gate_status(
            require_note_coverage=args.command == "schedule"
        )
        if not gate["valid"]:
            if args.command == "schedule":
                raise ValueError(
                    "bootstrap and note coverage gates are not valid for this database"
                )
            raise ValueError("bookmark bootstrap gate is not valid for this database")

    store = AutomationStore(args.db)
    automation = BookmarkAutomation(store)

    if args.command == "ingest":
        payload = prepared_payload if args.bootstrap else load_input(args.input)
        events = prepared_events if args.bootstrap else _events(payload, forced_kind=args.kind)
        assert events is not None
        summary = {"accepted": 0, "created": 0, "ignored": 0}
        for event in events:
            result = automation.ingest(event, bootstrap=args.bootstrap)
            if result.accepted:
                summary["accepted"] += 1
                summary["created"] += int(result.created)
            else:
                summary["ignored"] += 1
        if args.bootstrap:
            store.mark_bootstrap_completed(
                now=datetime.now(UTC),
                accepted=summary["accepted"],
                expected_minimum=args.expected_minimum,
            )
        json.dump(summary, stdout, ensure_ascii=False, sort_keys=True)
        stdout.write("\n")
        return 0
    if args.command == "decision":
        payload = load_input(args.input)
        if not isinstance(payload, dict):
            raise ValueError("decision input must be a JSON object")
        result = automation.decide(
            bookmark_id=str(payload.get("bookmark_id") or ""),
            action=str(payload.get("action") or ""),
            decision_id=str(payload.get("decision_id") or ""),
        )
        json.dump({"created": result.created}, stdout, sort_keys=True)
        stdout.write("\n")
        return 0
    if args.command == "schedule":
        result = automation.schedule_periodic(
            task_kind=args.task_kind,
            input_revision=args.input_revision,
            batch_size=args.batch_size,
        )
        job_ids = list(result.job_ids)
        json.dump(
            {
                "created": result.created,
                "job_id": job_ids[0] if job_ids else None,
                "job_ids": job_ids,
            },
            stdout,
            sort_keys=True,
        )
        stdout.write("\n")
        return 0
    if args.command == "worker":
        if args.max_jobs < 0:
            raise ValueError("max-jobs must be non-negative")
        if args.runner_command_json:
            command = json.loads(args.runner_command_json)
            if not isinstance(command, list) or not all(
                isinstance(part, str) and part for part in command
            ):
                raise ValueError("runner command must be a JSON array of non-empty strings")
        else:
            command = [sys.executable, "-m", "subscription_inference", "run"]
        worker = InferenceWorker(
            store=store,
            runner=SubprocessSubscriptionRunner(command=command),
            worker_id=args.worker_id,
        )
        states: Counter[str] = Counter()
        processed = 0
        for _ in range(args.max_jobs):
            outcome = worker.run_once(now=datetime.now(UTC))
            if outcome is None:
                break
            processed += 1
            states[outcome.status] += 1
        json.dump({"processed": processed, "states": dict(states)}, stdout, sort_keys=True)
        stdout.write("\n")
        return 0
    if args.command == "effects":
        if args.max_jobs < 0:
            raise ValueError("max-jobs must be non-negative")
        effect_worker = EffectWorker(
            store=store,
            telegram=TelegramClient(environ=os.environ),
            worker_id=args.worker_id,
            video_dir=args.video_dir,
            note_dir=args.note_dir,
        )
        states: Counter[str] = Counter()
        processed = 0
        for _ in range(args.max_jobs):
            outcome = effect_worker.run_once(now=datetime.now(UTC))
            if outcome is None:
                break
            processed += 1
            states[outcome.status] += 1
        json.dump({"processed": processed, "states": dict(states)}, stdout, sort_keys=True)
        stdout.write("\n")
        return 0
    if args.command == "import-decisions":
        if args.input == "-":
            decision_stream = stdin
            should_close = False
        else:
            decision_stream = Path(args.input).open(encoding="utf-8")
            should_close = True
        summary = {"created": 0, "duplicates": 0, "errors": 0}
        try:
            for line in decision_stream:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                    if not isinstance(payload, dict):
                        raise ValueError("bridge event must be an object")
                    result = automation.decide(
                        bookmark_id=str(
                            payload.get("tweet_id") or payload.get("bookmark_id") or ""
                        ),
                        action=str(payload.get("action") or ""),
                        decision_id=str(payload.get("event_id") or ""),
                    )
                except (json.JSONDecodeError, KeyError, ValueError):
                    summary["errors"] += 1
                    continue
                summary["created" if result.created else "duplicates"] += 1
        finally:
            if should_close:
                decision_stream.close()
        json.dump(summary, stdout, sort_keys=True)
        stdout.write("\n")
        return 0
    if args.command == "import-note-coverage":
        if store.note_coverage_completed():
            raise ValueError("Twitter note coverage import is already complete")
        scan = scan_note_coverage(args.notes_dir, redo_thin=args.redo_thin)
        if scan.malformed:
            raise ValueError(
                f"note coverage scan found {scan.malformed} malformed Markdown file(s)"
            )
        imported, unmatched = store.import_note_coverage(
            entries=((entry.bookmark_id, str(entry.path)) for entry in scan.entries),
            now=datetime.now(UTC),
        )
        store.mark_note_coverage_completed(now=datetime.now(UTC))
        json.dump(
            {
                "duplicates": scan.duplicates,
                "imported": imported,
                "malformed": scan.malformed,
                "scanned": scan.scanned,
                "thin_requeued": scan.thin_requeued,
                "unmatched": unmatched,
            },
            stdout,
            sort_keys=True,
        )
        stdout.write("\n")
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
