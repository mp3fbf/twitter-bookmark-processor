"""SQLite sidecar for bookmark automation state."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


_SEMANTIC_TASK_KINDS = (
    "quick",
    "fetch_article",
    "recall_context",
    "deep",
    "write_source_note",
    "send_deep",
)


@dataclass(frozen=True)
class Job:
    id: int
    bookmark_id: str | None
    task_kind: str
    input_revision: str
    profile: str
    priority: int
    state: str
    available_at: str
    lease_owner: str | None
    lease_until: str | None
    lease_token: str | None = None


@dataclass(frozen=True)
class EnqueueResult:
    job_id: int
    created: bool


@dataclass(frozen=True)
class DecisionResult:
    created: bool


class LeaseLostError(RuntimeError):
    """Raised when a worker tries to mutate a job through a stale claim."""


class _ClosingConnection(sqlite3.Connection):
    """Make the existing `with _connect()` contract close handles deterministically."""

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def _write_marker(path: Path, value: dict[str, str | int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            json.dump(value, temporary, ensure_ascii=False, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


class AutomationStore:
    """Own the operational sidecar database, never the source content."""

    def __init__(self, path: str | Path, *, initialize: bool = True) -> None:
        self.path = Path(path)
        self.read_only = not initialize
        self._read_only_temporary: tempfile.TemporaryDirectory[str] | None = None
        self._read_only_database_path: Path | None = None
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(descriptor)
            os.chmod(self.path, 0o600)
            self._initialize()
        elif not self.path.is_file():
            raise FileNotFoundError(self.path)
        else:
            self._read_only_database_path = self._snapshot_database()

    def _snapshot_database(self) -> Path:
        """Copy a stable DB+WAL pair so observations cannot recover the live DB."""
        self._read_only_temporary = tempfile.TemporaryDirectory(
            prefix="bookmark-automation-readonly-"
        )
        snapshot = Path(self._read_only_temporary.name) / self.path.name
        source_wal = Path(f"{self.path}-wal")
        snapshot_wal = Path(f"{snapshot}-wal")

        def wal_identity() -> tuple[int, int, int] | None:
            try:
                stat = source_wal.stat()
            except FileNotFoundError:
                return None
            return stat.st_ino, stat.st_size, stat.st_mtime_ns

        for _ in range(5):
            before_database = self.path.stat()
            before_wal = wal_identity()
            shutil.copyfile(self.path, snapshot)
            os.chmod(snapshot, 0o600)
            if before_wal is None:
                snapshot_wal.unlink(missing_ok=True)
            else:
                try:
                    shutil.copyfile(source_wal, snapshot_wal)
                    os.chmod(snapshot_wal, 0o600)
                except FileNotFoundError:
                    continue
            after_wal = wal_identity()
            after_database = self.path.stat()
            database_stable = (
                before_database.st_ino,
                before_database.st_size,
                before_database.st_mtime_ns,
            ) == (
                after_database.st_ino,
                after_database.st_size,
                after_database.st_mtime_ns,
            )
            if database_stable and before_wal == after_wal:
                return snapshot
        raise RuntimeError("could not obtain a stable read-only database snapshot")

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            assert self._read_only_database_path is not None
            connection = sqlite3.connect(
                f"{self._read_only_database_path.resolve().as_uri()}?mode=ro",
                uri=True,
                factory=_ClosingConnection,
            )
            connection.execute("PRAGMA query_only=ON")
        else:
            connection = sqlite3.connect(self.path, factory=_ClosingConnection)
            os.chmod(self.path, 0o600)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _latest_decision_action(
        connection: sqlite3.Connection,
        bookmark_id: str,
    ) -> str | None:
        row = connection.execute(
            """
            SELECT action
            FROM decisions
            WHERE bookmark_id = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (bookmark_id,),
        ).fetchone()
        return None if row is None else str(row["action"])

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS bookmarks (
                    bookmark_id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    input_revision TEXT NOT NULL,
                    capture_effects_allowed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bookmark_id TEXT,
                    task_kind TEXT NOT NULL,
                    input_revision TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    input_json TEXT,
                    priority INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_until TEXT,
                    lease_token TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(bookmark_id, task_kind, input_revision)
                );

                CREATE TABLE IF NOT EXISTS decisions (
                    decision_id TEXT PRIMARY KEY,
                    bookmark_id TEXT NOT NULL,
                    action TEXT NOT NULL CHECK (action IN ('act', 'keep', 'defer', 'skip')),
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (bookmark_id) REFERENCES bookmarks(bookmark_id)
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    provider TEXT,
                    model TEXT,
                    detail_json TEXT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    UNIQUE(job_id, attempt_no),
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id INTEGER NOT NULL,
                    effect_kind TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, effect_kind),
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS aggregate_coverage (
                    bookmark_id TEXT NOT NULL,
                    input_revision TEXT NOT NULL,
                    period_revision TEXT NOT NULL,
                    job_id INTEGER,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (bookmark_id, input_revision),
                    FOREIGN KEY (job_id) REFERENCES jobs(id)
                );

                CREATE TABLE IF NOT EXISTS backlog_coverage (
                    bookmark_id TEXT NOT NULL,
                    input_revision TEXT NOT NULL,
                    note_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (bookmark_id, input_revision)
                );

                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO metadata (key, value)
                VALUES ('instance_uuid', ?)
                """,
                (str(uuid.uuid4()),),
            )
            job_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "lease_token" not in job_columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN lease_token TEXT")
            if "cancel_requested" not in job_columns:
                connection.execute(
                    "ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
                )
            bookmark_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(bookmarks)").fetchall()
            }
            if "capture_effects_allowed" not in bookmark_columns:
                connection.execute(
                    "ALTER TABLE bookmarks ADD COLUMN "
                    "capture_effects_allowed INTEGER NOT NULL DEFAULT 0"
                )
        os.chmod(self.path, 0o600)

    def count(self, table: str) -> int:
        if table not in {"bookmarks", "jobs", "decisions", "attempts", "receipts"}:
            raise ValueError(f"unsupported table: {table}")
        with self._connect() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()
        return int(row["total"])

    def status_snapshot(self) -> dict[str, Any]:
        """Return aggregate operational counts without exposing stored content."""
        with self._connect() as connection:
            state_rows = connection.execute(
                "SELECT state, COUNT(*) AS total FROM jobs GROUP BY state ORDER BY state"
            ).fetchall()
            task_rows = connection.execute(
                "SELECT task_kind, COUNT(*) AS total FROM jobs GROUP BY task_kind ORDER BY task_kind"
            ).fetchall()
            totals = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) AS total FROM {table}").fetchone()[
                        "total"
                    ]
                )
                for table in ("bookmarks", "jobs", "attempts", "receipts", "decisions")
            }
            aggregate_covered = int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM aggregate_coverage"
                ).fetchone()["total"]
            )
            backlog_covered = int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM backlog_coverage"
                ).fetchone()["total"]
            )
            committed_effects = int(
                connection.execute(
                    """
                    SELECT COUNT(DISTINCT jobs.id) AS total
                    FROM jobs
                    JOIN attempts ON attempts.job_id = jobs.id
                    WHERE jobs.state = 'leased'
                      AND attempts.status = 'effect_committed'
                    """
                ).fetchone()["total"]
            )
        states = {str(row["state"]): int(row["total"]) for row in state_rows}
        return {
            **totals,
            "jobs_by_state": states,
            "jobs_by_task": {
                str(row["task_kind"]): int(row["total"]) for row in task_rows
            },
            "dead_letter": states.get("dead_letter", 0),
            "waiting_provider": states.get("waiting_provider", 0),
            "aggregate_covered": aggregate_covered,
            "bootstrap_completed": self.bootstrap_completed(),
            "backlog_covered": backlog_covered,
            "committed_effects": committed_effects,
            "note_coverage_completed": self.note_coverage_completed(),
        }

    def add_bookmark_with_jobs(
        self,
        *,
        bookmark_id: str,
        payload_json: str,
        input_revision: str,
        created_at: str,
        jobs: Iterable[dict[str, Any]],
        mark_aggregate_covered: bool = False,
    ) -> bool:
        """Persist a bookmark and its initial work in one transaction."""
        candidate_jobs = tuple(jobs)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT input_revision, capture_effects_allowed
                FROM bookmarks
                WHERE bookmark_id = ?
                """,
                (bookmark_id,),
            ).fetchone()
            if existing is not None and existing["input_revision"] == input_revision:
                connection.execute(
                    "UPDATE bookmarks SET payload_json = ? WHERE bookmark_id = ?",
                    (payload_json, bookmark_id),
                )
                if mark_aggregate_covered:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO aggregate_coverage (
                            bookmark_id, input_revision, period_revision,
                            job_id, created_at
                        ) VALUES (?, ?, '@bootstrap', NULL, ?)
                        """,
                        (bookmark_id, input_revision, created_at),
                    )
                return False
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO bookmarks
                        (bookmark_id, payload_json, input_revision,
                         capture_effects_allowed, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        bookmark_id,
                        payload_json,
                        input_revision,
                        int(not mark_aggregate_covered),
                        created_at,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE bookmarks
                    SET payload_json = ?, input_revision = ?
                    WHERE bookmark_id = ?
                    """,
                    (payload_json, input_revision, bookmark_id),
                )
            capture_effects_allowed = (
                not mark_aggregate_covered
                if existing is None
                else bool(existing["capture_effects_allowed"])
            )
            semantic_paused = (
                self._latest_decision_action(connection, bookmark_id) == "skip"
            )
            video_job = connection.execute(
                """
                SELECT id, state FROM jobs
                WHERE bookmark_id = ? AND task_kind = 'deliver_video'
                ORDER BY id ASC
                LIMIT 1
                """,
                (bookmark_id,),
            ).fetchone()
            for job in candidate_jobs:
                task_kind = str(job["task_kind"])
                if task_kind == "notify" and existing is not None:
                    continue
                if task_kind == "deliver_video":
                    if not capture_effects_allowed:
                        continue
                    if video_job is not None:
                        if video_job["state"] != "done":
                            connection.execute(
                                """
                                UPDATE attempts
                                SET status = 'superseded'
                                WHERE job_id = ? AND status = 'failed'
                                """,
                                (video_job["id"],),
                            )
                            connection.execute(
                                """
                                UPDATE jobs
                                SET input_json = ?,
                                    priority = MAX(priority, ?),
                                    state = CASE
                                        WHEN state IN (
                                            'dead_letter', 'cancelled',
                                            'waiting_provider'
                                        ) THEN 'pending'
                                        ELSE state
                                    END,
                                    available_at = CASE
                                        WHEN state IN (
                                            'dead_letter', 'cancelled',
                                            'waiting_provider'
                                        ) THEN ?
                                        ELSE available_at
                                    END,
                                    lease_owner = CASE
                                        WHEN state IN (
                                            'dead_letter', 'cancelled',
                                            'waiting_provider'
                                        ) THEN NULL
                                        ELSE lease_owner
                                    END,
                                    lease_until = CASE
                                        WHEN state IN (
                                            'dead_letter', 'cancelled',
                                            'waiting_provider'
                                        ) THEN NULL
                                        ELSE lease_until
                                    END,
                                    lease_token = CASE
                                        WHEN state IN (
                                            'dead_letter', 'cancelled',
                                            'waiting_provider'
                                        ) THEN NULL
                                        ELSE lease_token
                                    END,
                                    cancel_requested = 0
                                WHERE id = ?
                                """,
                                (
                                    payload_json,
                                    job["priority"],
                                    created_at,
                                    video_job["id"],
                                ),
                            )
                        continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO jobs (
                        bookmark_id, task_kind, input_revision, profile, input_json,
                        priority, state, available_at, cancel_requested, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bookmark_id,
                        task_kind,
                        input_revision,
                        job["profile"],
                        payload_json,
                        job["priority"],
                        (
                            "cancelled"
                            if semantic_paused and task_kind in _SEMANTIC_TASK_KINDS
                            else "pending"
                        ),
                        job.get("available_at", created_at),
                        int(
                            semantic_paused and task_kind in _SEMANTIC_TASK_KINDS
                        ),
                        created_at,
                    ),
                )
                if task_kind == "deliver_video":
                    video_job = connection.execute(
                        """
                        SELECT id, state FROM jobs
                        WHERE bookmark_id = ? AND task_kind = 'deliver_video'
                        ORDER BY id ASC
                        LIMIT 1
                        """,
                        (bookmark_id,),
                    ).fetchone()
            if mark_aggregate_covered:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO aggregate_coverage (
                        bookmark_id, input_revision, period_revision,
                        job_id, created_at
                    ) VALUES (?, ?, '@bootstrap', NULL, ?)
                    """,
                    (bookmark_id, input_revision, created_at),
                )
        return True

    @property
    def bootstrap_marker_path(self) -> Path:
        return Path(f"{self.path}.bootstrap-complete")

    @property
    def note_coverage_marker_path(self) -> Path:
        return Path(f"{self.path}.note-coverage-complete")

    def _metadata(self, *keys: str) -> dict[str, str]:
        placeholders = ",".join("?" for _ in keys)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT key, value FROM metadata WHERE key IN ({placeholders})",
                keys,
            ).fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    def instance_uuid(self) -> str:
        value = self._metadata("instance_uuid").get("instance_uuid")
        if not value:
            raise ValueError("database has no instance UUID")
        return value

    def _marker_valid(
        self,
        *,
        marker_path: Path,
        marker_kind: str,
        completed_key: str,
    ) -> bool:
        metadata = self._metadata("instance_uuid", completed_key)
        if set(metadata) != {"instance_uuid", completed_key}:
            return False
        marker = self._load_marker(marker_path)
        if marker is None:
            return False
        return bool(
            marker.get("schema_version") == 1
            and marker.get("marker_kind") == marker_kind
            and marker.get("instance_uuid") == metadata["instance_uuid"]
            and marker.get("completed_at") == metadata[completed_key]
            and marker.get("database_path") == str(self.path.resolve())
        )

    @staticmethod
    def _load_marker(marker_path: Path) -> dict[str, Any] | None:
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return marker if isinstance(marker, dict) else None

    def _integrity_valid(self) -> bool:
        with self._connect() as connection:
            rows = connection.execute("PRAGMA quick_check").fetchall()
        return len(rows) == 1 and str(rows[0][0]).lower() == "ok"

    def _bootstrap_count_valid(self) -> bool:
        marker = self._load_marker(self.bootstrap_marker_path)
        if marker is None:
            return False
        accepted = marker.get("accepted")
        expected_minimum = marker.get("expected_minimum")
        if (
            type(accepted) is not int
            or type(expected_minimum) is not int
            or expected_minimum <= 0
            or accepted < expected_minimum
        ):
            return False
        with self._connect() as connection:
            current_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS total FROM bookmarks"
                ).fetchone()["total"]
            )
        return current_count >= accepted

    def gate_status(self, *, require_note_coverage: bool = False) -> dict[str, Any]:
        try:
            instance_uuid = self.instance_uuid()
            integrity_valid = self._integrity_valid()
            bootstrap_count_valid = self._bootstrap_count_valid()
            bootstrap_marker_valid = self._marker_valid(
                marker_path=self.bootstrap_marker_path,
                marker_kind="bootstrap",
                completed_key="bootstrap_completed_at",
            )
            note_coverage_marker_valid = self._marker_valid(
                marker_path=self.note_coverage_marker_path,
                marker_kind="note_coverage",
                completed_key="note_coverage_completed_at",
            )
        except (sqlite3.DatabaseError, ValueError):
            instance_uuid = None
            integrity_valid = False
            bootstrap_count_valid = False
            bootstrap_marker_valid = False
            note_coverage_marker_valid = False
        bootstrap_valid = (
            bootstrap_marker_valid and bootstrap_count_valid and integrity_valid
        )
        note_coverage_valid = note_coverage_marker_valid and integrity_valid
        valid = bootstrap_valid and (note_coverage_valid or not require_note_coverage)
        return {
            "bootstrap_count_valid": bootstrap_count_valid,
            "bootstrap_valid": bootstrap_valid,
            "database_exists": True,
            "integrity_valid": integrity_valid,
            "instance_uuid": instance_uuid,
            "note_coverage_required": require_note_coverage,
            "note_coverage_valid": note_coverage_valid,
            "valid": valid,
        }

    def bootstrap_completed(self) -> bool:
        try:
            return bool(
                self._marker_valid(
                    marker_path=self.bootstrap_marker_path,
                    marker_kind="bootstrap",
                    completed_key="bootstrap_completed_at",
                )
                and self._bootstrap_count_valid()
                and self._integrity_valid()
            )
        except sqlite3.DatabaseError:
            return False

    def mark_bootstrap_completed(
        self,
        *,
        now: datetime,
        accepted: int,
        expected_minimum: int,
    ) -> None:
        """Commit the DB gate, then atomically publish the systemd marker."""
        if expected_minimum <= 0 or accepted < expected_minimum:
            raise ValueError("bootstrap cardinality is not valid")
        if self.count("bookmarks") < accepted:
            raise ValueError("bootstrap accepted count exceeds stored bookmarks")
        completed_at = now.isoformat()
        instance_uuid = self.instance_uuid()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO metadata (key, value)
                VALUES ('bootstrap_completed_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (completed_at,),
            )
        marker: dict[str, str | int] = {
            "completed_at": completed_at,
            "database_path": str(self.path.resolve()),
            "instance_uuid": instance_uuid,
            "marker_kind": "bootstrap",
            "schema_version": 1,
        }
        marker["accepted"] = accepted
        marker["expected_minimum"] = expected_minimum
        _write_marker(self.bootstrap_marker_path, marker)

    def note_coverage_completed(self) -> bool:
        try:
            return bool(
                self._marker_valid(
                    marker_path=self.note_coverage_marker_path,
                    marker_kind="note_coverage",
                    completed_key="note_coverage_completed_at",
                )
                and self._integrity_valid()
            )
        except sqlite3.DatabaseError:
            return False

    def import_note_coverage(
        self,
        *,
        entries: Iterable[tuple[str, str]],
        now: datetime,
    ) -> tuple[int, int]:
        """Mark current bookmark revisions represented by legacy Source notes."""
        imported = 0
        unmatched = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for bookmark_id, note_path in entries:
                bookmark = connection.execute(
                    "SELECT input_revision FROM bookmarks WHERE bookmark_id = ?",
                    (bookmark_id,),
                ).fetchone()
                if bookmark is None:
                    unmatched += 1
                    continue
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO backlog_coverage (
                        bookmark_id, input_revision, note_path, created_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        bookmark_id,
                        str(bookmark["input_revision"]),
                        note_path,
                        now.isoformat(),
                    ),
                )
                imported += int(cursor.rowcount == 1)
        return imported, unmatched

    def mark_note_coverage_completed(self, *, now: datetime) -> None:
        completed_at = now.isoformat()
        instance_uuid = self.instance_uuid()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO metadata (key, value)
                VALUES ('note_coverage_completed_at', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (completed_at,),
            )
        _write_marker(
            self.note_coverage_marker_path,
            {
                "completed_at": completed_at,
                "database_path": str(self.path.resolve()),
                "instance_uuid": instance_uuid,
                "marker_kind": "note_coverage",
                "schema_version": 1,
            },
        )

    def dead_letter_snapshot(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Inspect terminal jobs without changing attempts or queue state."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT jobs.id, jobs.bookmark_id, jobs.task_kind,
                       jobs.input_revision, jobs.available_at,
                       COUNT(attempts.id) AS attempt_count,
                       MAX(attempts.finished_at) AS last_attempt_at
                FROM jobs
                LEFT JOIN attempts ON attempts.job_id = jobs.id
                WHERE jobs.state = 'dead_letter'
                GROUP BY jobs.id
                ORDER BY jobs.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_jobs(self) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, bookmark_id, task_kind, input_revision, profile,
                       priority, state, available_at, lease_owner, lease_until,
                       lease_token
                FROM jobs
                ORDER BY priority DESC, id ASC
                """
            ).fetchall()
        return [Job(**dict(row)) for row in rows]

    def enqueue_job(
        self,
        *,
        bookmark_id: str,
        task_kind: str,
        input_revision: str,
        profile: str,
        priority: int,
        now: datetime,
        input_json: str | None = None,
    ) -> EnqueueResult:
        now_text = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            semantic_paused = (
                self._latest_decision_action(connection, bookmark_id) == "skip"
                and task_kind in _SEMANTIC_TASK_KINDS
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    bookmark_id, task_kind, input_revision, profile, input_json,
                    priority, state, available_at, cancel_requested, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bookmark_id,
                    task_kind,
                    input_revision,
                    profile,
                    input_json,
                    priority,
                    "cancelled" if semantic_paused else "pending",
                    now_text,
                    int(semantic_paused),
                    now_text,
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                """
                SELECT id FROM jobs
                WHERE bookmark_id = ? AND task_kind = ? AND input_revision = ?
                """,
                (bookmark_id, task_kind, input_revision),
            ).fetchone()
        return EnqueueResult(job_id=int(row["id"]), created=created)

    def enqueue_aggregate_job(
        self,
        *,
        bookmark_id: str,
        input_revision: str,
        priority: int,
        input_json: str,
        members: Iterable[tuple[str, str]],
        period_revision: str,
        now: datetime,
    ) -> EnqueueResult:
        """Enqueue one frozen aggregate batch and watermark every member atomically."""
        now_text = now.isoformat()
        frozen_members = tuple(members)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO jobs (
                    bookmark_id, task_kind, input_revision, profile, input_json,
                    priority, state, available_at, created_at
                ) VALUES (?, 'aggregate', ?, 'aggregate', ?, ?, 'pending', ?, ?)
                """,
                (
                    bookmark_id,
                    input_revision,
                    input_json,
                    priority,
                    now_text,
                    now_text,
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                """
                SELECT id FROM jobs
                WHERE bookmark_id = ?
                  AND task_kind = 'aggregate'
                  AND input_revision = ?
                """,
                (bookmark_id, input_revision),
            ).fetchone()
            job_id = int(row["id"])
            for member_bookmark_id, member_revision in frozen_members:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO aggregate_coverage (
                        bookmark_id, input_revision, period_revision,
                        job_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        member_bookmark_id,
                        member_revision,
                        period_revision,
                        job_id,
                        now_text,
                    ),
                )
        return EnqueueResult(job_id=job_id, created=created)

    def record_decision(
        self,
        *,
        bookmark_id: str,
        action: str,
        decision_id: str,
        now: datetime,
        deep_priority: int | None,
        deep_available_at: datetime | None = None,
        defer_until: datetime | None = None,
        cancel_semantic: bool = False,
    ) -> DecisionResult:
        """Record a callback and enqueue its semantic work atomically."""
        now_text = now.isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            bookmark = connection.execute(
                "SELECT input_revision, payload_json FROM bookmarks WHERE bookmark_id = ?",
                (bookmark_id,),
            ).fetchone()
            if bookmark is None:
                raise KeyError(f"unknown bookmark: {bookmark_id}")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO decisions
                    (decision_id, bookmark_id, action, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (decision_id, bookmark_id, action, now_text),
            )
            created = cursor.rowcount == 1
            if created and deep_priority is not None:
                semantic_available_at = deep_available_at or now
                connection.execute(
                    """
                    INSERT INTO jobs (
                        bookmark_id, task_kind, input_revision, profile, input_json,
                        priority, state, available_at, created_at
                    ) VALUES (?, 'deep', ?, 'deep', ?, ?, 'pending', ?, ?)
                    ON CONFLICT(bookmark_id, task_kind, input_revision)
                    DO UPDATE SET
                        priority = MAX(priority, excluded.priority),
                        available_at = MIN(available_at, excluded.available_at),
                        state = CASE
                            WHEN state = 'cancelled' THEN 'pending'
                            ELSE state
                        END,
                        cancel_requested = 0
                    """,
                    (
                        bookmark_id,
                        bookmark["input_revision"],
                        bookmark["payload_json"],
                        deep_priority,
                        semantic_available_at.isoformat(),
                        now_text,
                    ),
                )
                connection.execute(
                    """
                    UPDATE jobs
                    SET state = CASE
                            WHEN state = 'cancelled' THEN 'pending'
                            ELSE state
                        END,
                        available_at = CASE
                            WHEN state = 'cancelled' THEN ?
                            ELSE available_at
                        END,
                        lease_owner = CASE
                            WHEN state = 'cancelled' THEN NULL
                            ELSE lease_owner
                        END,
                        lease_until = CASE
                            WHEN state = 'cancelled' THEN NULL
                            ELSE lease_until
                        END,
                        lease_token = CASE
                            WHEN state = 'cancelled' THEN NULL
                            ELSE lease_token
                        END,
                        cancel_requested = 0
                    WHERE bookmark_id = ?
                      AND input_revision = ?
                      AND task_kind IN ('quick', 'fetch_article', 'recall_context')
                      AND state IN ('cancelled', 'leased')
                    """,
                    (now_text, bookmark_id, bookmark["input_revision"]),
                )
                connection.execute(
                    """
                    UPDATE jobs AS downstream
                    SET state = CASE
                            WHEN state = 'cancelled' THEN 'pending'
                            ELSE state
                        END,
                        available_at = CASE
                            WHEN state = 'cancelled' THEN ?
                            ELSE available_at
                        END,
                        lease_owner = CASE
                            WHEN state = 'cancelled' THEN NULL
                            ELSE lease_owner
                        END,
                        lease_until = CASE
                            WHEN state = 'cancelled' THEN NULL
                            ELSE lease_until
                        END,
                        lease_token = CASE
                            WHEN state = 'cancelled' THEN NULL
                            ELSE lease_token
                        END,
                        cancel_requested = 0
                    WHERE bookmark_id = ?
                      AND input_revision = ?
                      AND task_kind IN ('write_source_note', 'send_deep')
                      AND state IN ('cancelled', 'leased')
                      AND EXISTS (
                          SELECT 1
                          FROM jobs AS completed_deep
                          JOIN receipts
                            ON receipts.job_id = completed_deep.id
                           AND receipts.effect_kind = 'deep'
                          WHERE completed_deep.bookmark_id = downstream.bookmark_id
                            AND completed_deep.input_revision = downstream.input_revision
                            AND completed_deep.task_kind = 'deep'
                            AND completed_deep.state = 'done'
                      )
                    """,
                    (
                        semantic_available_at.isoformat(),
                        bookmark_id,
                        bookmark["input_revision"],
                    ),
                )
            if created and defer_until is not None:
                connection.execute(
                    """
                    UPDATE jobs
                    SET available_at = MAX(available_at, ?)
                    WHERE bookmark_id = ?
                      AND task_kind = 'deep'
                      AND state IN ('pending', 'waiting_provider')
                    """,
                    (defer_until.isoformat(), bookmark_id),
                )
            if created and cancel_semantic:
                cancellable_kinds = (
                    "quick",
                    "fetch_article",
                    "recall_context",
                    "deep",
                    "write_source_note",
                    "send_deep",
                )
                placeholders = ",".join("?" for _ in cancellable_kinds)
                connection.execute(
                    f"""
                    UPDATE jobs
                    SET cancel_requested = 1
                    WHERE bookmark_id = ?
                      AND task_kind IN ({placeholders})
                      AND state IN ('pending', 'waiting_provider', 'leased')
                    """,
                    (bookmark_id, *cancellable_kinds),
                )
                connection.execute(
                    f"""
                    UPDATE attempts
                    SET status = 'cancelled',
                        detail_json = '{{"reason":"bookmark_skip"}}',
                        finished_at = ?
                    WHERE status = 'running'
                      AND job_id IN (
                          SELECT id FROM jobs
                          WHERE bookmark_id = ?
                            AND task_kind IN ({placeholders})
                      )
                    """,
                    (now_text, bookmark_id, *cancellable_kinds),
                )
                connection.execute(
                    f"""
                    UPDATE jobs
                    SET state = 'cancelled', lease_owner = NULL,
                        lease_until = NULL, lease_token = NULL
                    WHERE bookmark_id = ?
                      AND task_kind IN ({placeholders})
                      AND (
                          state IN ('pending', 'waiting_provider')
                          OR (
                              state = 'leased'
                              AND NOT EXISTS (
                                  SELECT 1 FROM attempts
                                  WHERE attempts.job_id = jobs.id
                                    AND attempts.status = 'effect_committed'
                              )
                          )
                      )
                    """,
                    (bookmark_id, *cancellable_kinds),
                )
        return DecisionResult(created=created)

    def lease_next(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_for: timedelta,
        profiles: set[str] | None = None,
        task_kinds: set[str] | None = None,
    ) -> Job | None:
        """Atomically lease the highest-priority available job."""
        now_text = now.isoformat()
        lease_until = (now + lease_for).isoformat()
        lease_token = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE attempts
                SET status = 'lease_expired', finished_at = ?
                WHERE status = 'running'
                  AND job_id IN (
                      SELECT id FROM jobs
                      WHERE state = 'leased'
                        AND cancel_requested = 1
                        AND lease_until <= ?
                  )
                """,
                (now_text, now_text),
            )
            connection.execute(
                """
                UPDATE jobs
                SET state = 'cancelled', lease_owner = NULL,
                    lease_until = NULL, lease_token = NULL
                WHERE state = 'leased'
                  AND cancel_requested = 1
                  AND lease_until <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM attempts
                      WHERE attempts.job_id = jobs.id
                        AND attempts.status = 'effect_committed'
                  )
                """,
                (now_text,),
            )
            profile_filter = ""
            parameters: list[Any] = [now_text, now_text]
            if profiles is not None:
                if not profiles:
                    return None
                placeholders = ",".join("?" for _ in profiles)
                profile_filter = f" AND profile IN ({placeholders})"
                parameters.extend(sorted(profiles))
            task_filter = ""
            if task_kinds is not None:
                if not task_kinds:
                    return None
                placeholders = ",".join("?" for _ in task_kinds)
                task_filter = f" AND task_kind IN ({placeholders})"
                parameters.extend(sorted(task_kinds))
            row = connection.execute(
                f"""
                SELECT id
                FROM jobs
                WHERE available_at <= ?
                  AND cancel_requested = 0
                  AND (
                    state = 'pending'
                    OR (
                        state = 'leased'
                        AND lease_until <= ?
                        AND NOT EXISTS (
                            SELECT 1 FROM attempts
                            WHERE attempts.job_id = jobs.id
                              AND attempts.status = 'effect_committed'
                        )
                    )
                    OR state = 'waiting_provider'
                  )
                  {profile_filter}
                  {task_filter}
                  AND (
                    jobs.task_kind != 'deep'
                    OR NOT EXISTS (
                        SELECT 1
                        FROM jobs AS prerequisite
                        WHERE prerequisite.bookmark_id = jobs.bookmark_id
                          AND prerequisite.input_revision = jobs.input_revision
                          AND prerequisite.task_kind IN (
                              'quick', 'fetch_article', 'recall_context'
                          )
                          AND prerequisite.state NOT IN (
                              'done', 'dead_letter', 'cancelled'
                          )
                    )
                  )
                ORDER BY priority DESC, id ASC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE attempts
                SET status = 'lease_expired', finished_at = ?
                WHERE job_id = ? AND status = 'running'
                """,
                (now_text, row["id"]),
            )
            connection.execute(
                """
                UPDATE jobs
                SET state = 'leased', lease_owner = ?, lease_until = ?,
                    lease_token = ?
                WHERE id = ?
                """,
                (worker_id, lease_until, lease_token, row["id"]),
            )
            leased = connection.execute(
                """
                SELECT id, bookmark_id, task_kind, input_revision, profile,
                       priority, state, available_at, lease_owner, lease_until,
                       lease_token
                FROM jobs WHERE id = ?
                """,
                (row["id"],),
            ).fetchone()
        return Job(**dict(leased))

    def job_input(self, job: Job) -> dict[str, Any]:
        if job.bookmark_id and not job.bookmark_id.startswith("@periodic:"):
            with self._connect() as connection:
                bookmark_row = connection.execute(
                    "SELECT payload_json FROM bookmarks WHERE bookmark_id = ?",
                    (job.bookmark_id,),
                ).fetchone()
                job_row = connection.execute(
                    "SELECT input_json FROM jobs WHERE id = ?",
                    (job.id,),
                ).fetchone()
                upstream_rows = connection.execute(
                    """
                    SELECT jobs.task_kind, jobs.state, receipts.payload_json
                    FROM jobs
                    LEFT JOIN receipts
                      ON receipts.job_id = jobs.id
                     AND receipts.effect_kind = jobs.task_kind
                    WHERE jobs.bookmark_id = ?
                      AND jobs.id != ?
                      AND jobs.input_revision = ?
                      AND jobs.task_kind IN ('quick', 'fetch_article', 'recall_context')
                    ORDER BY jobs.id ASC
                    """,
                    (job.bookmark_id, job.id, job.input_revision),
                ).fetchall()
            if bookmark_row is None:
                raise KeyError(f"missing bookmark payload for job {job.id}")
            payload_json = (
                job_row["input_json"]
                if job_row is not None and job_row["input_json"] is not None
                else bookmark_row["payload_json"]
            )
            upstream: dict[str, Any] = {}
            evidence_status: dict[str, str] = {}
            for upstream_row in upstream_rows:
                task_kind = upstream_row["task_kind"]
                if upstream_row["payload_json"] is not None:
                    payload = json.loads(upstream_row["payload_json"])
                    upstream[task_kind] = payload
                    evidence_status[task_kind] = str(payload.get("status") or "available")
                else:
                    evidence_status[task_kind] = str(upstream_row["state"])
            return {
                "bookmark": json.loads(payload_json),
                "upstream": upstream,
                "evidence_status": evidence_status,
            }
        if job.bookmark_id and job.bookmark_id.startswith("@periodic:"):
            with self._connect() as connection:
                frozen = connection.execute(
                    "SELECT input_json FROM jobs WHERE id = ?",
                    (job.id,),
                ).fetchone()
            if frozen is not None and frozen["input_json"] is not None:
                payload = json.loads(frozen["input_json"])
                if not isinstance(payload, dict):
                    raise ValueError(f"periodic job {job.id} input must be an object")
                return payload
            with self._connect() as connection:
                bookmark_rows = connection.execute(
                    """
                    SELECT bookmark_id, input_revision, payload_json
                    FROM bookmarks
                    ORDER BY created_at ASC, bookmark_id ASC
                    """
                ).fetchall()
                receipt_rows = connection.execute(
                    """
                    SELECT jobs.bookmark_id, jobs.input_revision, jobs.task_kind,
                           receipts.payload_json
                    FROM jobs
                    JOIN bookmarks
                      ON bookmarks.bookmark_id = jobs.bookmark_id
                     AND bookmarks.input_revision = jobs.input_revision
                    JOIN receipts ON receipts.job_id = jobs.id
                    WHERE jobs.task_kind IN (
                        'quick', 'fetch_article', 'recall_context', 'deep'
                    )
                    ORDER BY receipts.id ASC
                    """
                ).fetchall()
            upstream_by_bookmark: dict[tuple[str, str], dict[str, Any]] = {}
            for receipt_row in receipt_rows:
                key = (receipt_row["bookmark_id"], receipt_row["input_revision"])
                upstream_by_bookmark.setdefault(key, {})[receipt_row["task_kind"]] = (
                    json.loads(receipt_row["payload_json"])
                )
            items: list[dict[str, Any]] = []
            for bookmark_row in bookmark_rows:
                key = (bookmark_row["bookmark_id"], bookmark_row["input_revision"])
                upstream = upstream_by_bookmark.get(key, {})
                if job.task_kind == "backlog" and "deep" in upstream:
                    continue
                items.append(
                    {
                        "bookmark": json.loads(bookmark_row["payload_json"]),
                        "input_revision": bookmark_row["input_revision"],
                        "upstream": upstream,
                    }
                )
            return {
                "scope": job.bookmark_id,
                "task_kind": job.task_kind,
                "input_revision": job.input_revision,
                "coverage": {
                    "input_count": len(items),
                    "bookmark_ids": [item["bookmark"]["id"] for item in items],
                },
                "bookmarks": items,
            }
        return {
            "scope": job.bookmark_id,
            "task_kind": job.task_kind,
            "input_revision": job.input_revision,
        }

    def periodic_items(self, *, task_kind: str) -> list[dict[str, Any]]:
        """Snapshot current bookmark inputs for aggregate or backlog scheduling."""
        if task_kind not in {"aggregate", "backlog"}:
            raise ValueError(f"unsupported periodic task: {task_kind}")
        with self._connect() as connection:
            bookmark_rows = connection.execute(
                """
                SELECT bookmarks.bookmark_id, bookmarks.input_revision,
                       bookmarks.payload_json,
                       (
                           SELECT decisions.action
                           FROM decisions
                           WHERE decisions.bookmark_id = bookmarks.bookmark_id
                           ORDER BY decisions.rowid DESC
                           LIMIT 1
                       ) AS latest_action
                FROM bookmarks
                ORDER BY created_at ASC, bookmark_id ASC
                """
            ).fetchall()
            receipt_rows = connection.execute(
                """
                SELECT jobs.bookmark_id, jobs.input_revision, jobs.task_kind,
                       receipts.payload_json
                FROM jobs
                JOIN bookmarks
                  ON bookmarks.bookmark_id = jobs.bookmark_id
                 AND bookmarks.input_revision = jobs.input_revision
                JOIN receipts ON receipts.job_id = jobs.id
                WHERE jobs.task_kind IN (
                    'quick', 'fetch_article', 'recall_context', 'deep'
                )
                ORDER BY receipts.id ASC
                """
            ).fetchall()
            covered_rows = connection.execute(
                "SELECT bookmark_id, input_revision FROM aggregate_coverage"
            ).fetchall()
            deep_job_rows = connection.execute(
                """
                SELECT jobs.bookmark_id, jobs.input_revision
                FROM jobs
                JOIN bookmarks
                  ON bookmarks.bookmark_id = jobs.bookmark_id
                 AND bookmarks.input_revision = jobs.input_revision
                WHERE jobs.task_kind = 'deep'
                """
            ).fetchall()
            backlog_covered_rows = connection.execute(
                "SELECT bookmark_id, input_revision FROM backlog_coverage"
            ).fetchall()
        aggregate_covered = {
            (str(row["bookmark_id"]), str(row["input_revision"]))
            for row in covered_rows
        }
        deep_scheduled = {
            (str(row["bookmark_id"]), str(row["input_revision"]))
            for row in deep_job_rows
        }
        backlog_covered = {
            (str(row["bookmark_id"]), str(row["input_revision"]))
            for row in backlog_covered_rows
        }
        upstream_by_bookmark: dict[tuple[str, str], dict[str, Any]] = {}
        for receipt_row in receipt_rows:
            key = (str(receipt_row["bookmark_id"]), str(receipt_row["input_revision"]))
            upstream_by_bookmark.setdefault(key, {})[str(receipt_row["task_kind"])] = (
                json.loads(receipt_row["payload_json"])
            )
        items: list[dict[str, Any]] = []
        for row in bookmark_rows:
            if task_kind == "backlog" and row["latest_action"] == "skip":
                continue
            key = (str(row["bookmark_id"]), str(row["input_revision"]))
            upstream = upstream_by_bookmark.get(key, {})
            if task_kind == "aggregate" and key in aggregate_covered:
                continue
            if task_kind == "backlog" and (
                "deep" in upstream
                or key in deep_scheduled
                or key in backlog_covered
            ):
                continue
            items.append(
                {
                    "bookmark": json.loads(row["payload_json"]),
                    "input_revision": str(row["input_revision"]),
                    "upstream": upstream,
                }
            )
        return items

    @staticmethod
    def _assert_active_lease(
        connection: sqlite3.Connection,
        *,
        job_id: int,
        worker_id: str,
        lease_token: str,
        now: datetime,
        attempt_id: int | None = None,
    ) -> None:
        if attempt_id is None:
            row = connection.execute(
                """
                SELECT 1
                FROM jobs
                WHERE id = ?
                  AND state = 'leased'
                  AND lease_owner = ?
                  AND lease_token = ?
                  AND lease_until > ?
                """,
                (job_id, worker_id, lease_token, now.isoformat()),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT 1
                FROM jobs
                JOIN attempts ON attempts.job_id = jobs.id
                WHERE jobs.id = ?
                  AND jobs.state = 'leased'
                  AND jobs.lease_owner = ?
                  AND jobs.lease_token = ?
                  AND attempts.id = ?
                  AND attempts.status IN ('running', 'effect_committed')
                  AND (
                      jobs.lease_until > ?
                      OR attempts.status = 'effect_committed'
                  )
                """,
                (
                    job_id,
                    worker_id,
                    lease_token,
                    attempt_id,
                    now.isoformat(),
                ),
            ).fetchone()
        if row is None:
            raise LeaseLostError(f"lease lost for job {job_id}")

    def start_attempt(
        self,
        *,
        job_id: int,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_active_lease(
                connection,
                job_id=job_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            row = connection.execute(
                "SELECT COALESCE(MAX(attempt_no), 0) + 1 AS next_no FROM attempts WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            cursor = connection.execute(
                """
                INSERT INTO attempts (job_id, attempt_no, status, started_at)
                VALUES (?, ?, 'running', ?)
                """,
                (job_id, row["next_no"], now.isoformat()),
            )
        return int(cursor.lastrowid)

    def mark_effect_committed(
        self,
        *,
        job_id: int,
        attempt_id: int,
        worker_id: str,
        lease_token: str,
        now: datetime,
    ) -> None:
        """Cross the durable boundary immediately before an irreversible effect."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_active_lease(
                connection,
                job_id=job_id,
                attempt_id=attempt_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            connection.execute(
                """
                UPDATE attempts
                SET status = 'effect_committed'
                WHERE id = ? AND job_id = ? AND status = 'running'
                """,
                (attempt_id, job_id),
            )

    def complete_success(
        self,
        *,
        job_id: int,
        attempt_id: int,
        worker_id: str,
        lease_token: str,
        provider: str | None,
        model: str | None,
        effect_kind: str,
        receipt_json: str,
        now: datetime,
        downstream_jobs: Iterable[dict[str, Any]] = (),
    ) -> None:
        """Atomically finish work, persist its receipt, and enqueue dependents."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_active_lease(
                connection,
                job_id=job_id,
                attempt_id=attempt_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            connection.execute(
                """
                UPDATE attempts
                SET status = 'succeeded', provider = ?, model = ?,
                    detail_json = ?, finished_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (provider, model, receipt_json, now.isoformat(), attempt_id, job_id),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO receipts (
                    job_id, effect_kind, idempotency_key, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    effect_kind,
                    f"job:{job_id}:{effect_kind}",
                    receipt_json,
                    now.isoformat(),
                ),
            )
            connection.execute(
                """
                UPDATE jobs
                SET state = 'done', lease_owner = NULL, lease_until = NULL,
                    lease_token = NULL
                WHERE id = ?
                """,
                (job_id,),
            )
            for downstream in downstream_jobs:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO jobs (
                        bookmark_id, task_kind, input_revision, profile, input_json,
                        priority, state, available_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        downstream["bookmark_id"],
                        downstream["task_kind"],
                        downstream["input_revision"],
                        downstream.get("profile", "none"),
                        downstream.get("input_json"),
                        downstream["priority"],
                        downstream.get("available_at", now.isoformat()),
                        now.isoformat(),
                    ),
                )

    def complete_effect(
        self,
        *,
        job_id: int,
        effect_kind: str,
        payload: dict[str, Any],
        now: datetime,
    ) -> None:
        """Persist one deterministic effect receipt and finish its job idempotently."""
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO receipts (
                    job_id, effect_kind, idempotency_key, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    effect_kind,
                    f"job:{job_id}:{effect_kind}",
                    payload_json,
                    now.isoformat(),
                ),
            )
            connection.execute(
                """
                UPDATE jobs
                SET state = 'done', lease_owner = NULL, lease_until = NULL,
                    lease_token = NULL
                WHERE id = ?
                """,
                (job_id,),
            )

    def receipt_payload(self, job_id: int, effect_kind: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM receipts
                WHERE job_id = ? AND effect_kind = ?
                """,
                (job_id, effect_kind),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if not isinstance(payload, dict):
            raise ValueError(f"receipt for job {job_id} is not a JSON object")
        return payload

    def job_payload(self, job: Job) -> dict[str, Any]:
        """Return the immutable JSON payload captured for a leased job."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT input_json FROM jobs WHERE id = ?",
                (job.id,),
            ).fetchone()
        if row is None or row["input_json"] is None:
            raise KeyError(f"job {job.id} does not have an input snapshot")
        payload = json.loads(row["input_json"])
        if not isinstance(payload, dict):
            raise ValueError(f"job {job.id} input must be a JSON object")
        return payload

    def recall_query(self, job: Job, *, max_chars: int = 1_000) -> str:
        """Build a bounded local-retrieval query; quick output is optional."""
        if job.task_kind != "recall_context" or not job.bookmark_id:
            raise ValueError("recall_query requires a bookmark recall_context job")
        with self._connect() as connection:
            bookmark_row = connection.execute(
                """
                SELECT COALESCE(jobs.input_json, bookmarks.payload_json) AS payload_json
                FROM jobs
                JOIN bookmarks ON bookmarks.bookmark_id = jobs.bookmark_id
                WHERE jobs.id = ?
                """,
                (job.id,),
            ).fetchone()
            quick_row = connection.execute(
                """
                SELECT receipts.payload_json
                FROM jobs
                JOIN receipts ON receipts.job_id = jobs.id
                WHERE jobs.bookmark_id = ?
                  AND jobs.task_kind = 'quick'
                  AND jobs.input_revision = ?
                  AND receipts.effect_kind = 'quick'
                ORDER BY receipts.id DESC
                LIMIT 1
                """,
                (job.bookmark_id, job.input_revision),
            ).fetchone()
        if bookmark_row is None:
            raise KeyError(f"missing bookmark payload for job {job.id}")
        terms: list[str] = []
        if quick_row is not None:
            receipt = json.loads(quick_row["payload_json"])
            quick_output = receipt.get("output") if isinstance(receipt, dict) else None
            if isinstance(quick_output, dict):
                for key in ("topic", "summary", "why_interesting"):
                    value = quick_output.get(key)
                    if isinstance(value, str) and value.strip():
                        terms.append(value.strip())
        if not terms:
            bookmark = json.loads(bookmark_row["payload_json"])
            article = bookmark.get("article")
            if isinstance(article, dict) and isinstance(article.get("title"), str):
                terms.append(article["title"].strip())
            text = bookmark.get("text")
            if isinstance(text, str) and text.strip():
                terms.append(text.strip())
        return " ".join(terms)[:max_chars]

    def complete_waiting_provider(
        self,
        *,
        job_id: int,
        attempt_id: int,
        worker_id: str,
        lease_token: str,
        detail_json: str,
        available_at: datetime,
        now: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_active_lease(
                connection,
                job_id=job_id,
                attempt_id=attempt_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            connection.execute(
                """
                UPDATE attempts
                SET status = 'waiting_provider', detail_json = ?, finished_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (detail_json, now.isoformat(), attempt_id, job_id),
            )
            connection.execute(
                """
                UPDATE jobs
                SET state = 'waiting_provider', available_at = ?,
                    lease_owner = NULL, lease_until = NULL, lease_token = NULL
                WHERE id = ?
                """,
                (available_at.isoformat(), job_id),
            )

    def complete_failure(
        self,
        *,
        job_id: int,
        attempt_id: int,
        worker_id: str,
        lease_token: str,
        detail_json: str,
        available_at: datetime,
        now: datetime,
        max_attempts: int,
    ) -> str:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_active_lease(
                connection,
                job_id=job_id,
                attempt_id=attempt_id,
                worker_id=worker_id,
                lease_token=lease_token,
                now=now,
            )
            attempt = connection.execute(
                "SELECT status FROM attempts WHERE id = ? AND job_id = ?",
                (attempt_id, job_id),
            ).fetchone()
            if attempt["status"] == "effect_committed":
                connection.execute(
                    """
                    UPDATE attempts
                    SET detail_json = ?, finished_at = ?
                    WHERE id = ? AND job_id = ?
                      AND status = 'effect_committed'
                    """,
                    (detail_json, now.isoformat(), attempt_id, job_id),
                )
                return "effect_committed"
            connection.execute(
                """
                UPDATE attempts
                SET status = 'failed', detail_json = ?, finished_at = ?
                WHERE id = ? AND job_id = ?
                """,
                (detail_json, now.isoformat(), attempt_id, job_id),
            )
            attempts = connection.execute(
                """
                SELECT COUNT(*) AS total FROM attempts
                WHERE job_id = ? AND status = 'failed'
                """,
                (job_id,),
            ).fetchone()
            job = connection.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if bool(job["cancel_requested"]):
                target_state = "cancelled"
            else:
                target_state = (
                    "dead_letter"
                    if int(attempts["total"]) >= max_attempts
                    else "pending"
                )
            connection.execute(
                """
                UPDATE jobs
                SET state = ?, available_at = ?,
                    lease_owner = NULL, lease_until = NULL, lease_token = NULL
                WHERE id = ?
                """,
                (target_state, available_at.isoformat(), job_id),
            )
        return target_state
