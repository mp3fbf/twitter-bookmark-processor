"""Offline JSON/fixture command-line contracts."""

import io
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from bookmark_automation.cli import main
from bookmark_automation.store import AutomationStore


def _bootstrap(database: Path, *bookmarks: dict[str, object]) -> None:
    payload = list(bookmarks) or [{"id": "bootstrap-seed", "text": "Seed"}]
    main(
        [
            "--db",
            str(database),
            "ingest",
            "--input",
            "-",
            "--kind",
            "bookmarks",
            "--bootstrap",
            "--expected-minimum",
            str(len(payload)),
        ],
        stdin=io.StringIO(json.dumps(payload)),
        stdout=io.StringIO(),
    )


def _complete_gates(database: Path, *, note_coverage: bool = False) -> None:
    store = AutomationStore(database)
    added_seed = False
    if store.count("bookmarks") == 0:
        from bookmark_automation.service import BookmarkAutomation

        BookmarkAutomation(store).ingest(
            {"kind": "bookmarks", "id": "bootstrap-seed", "text": "Seed"},
            bootstrap=True,
        )
        added_seed = True
    now = datetime.now(UTC)
    accepted = store.count("bookmarks")
    store.mark_bootstrap_completed(
        now=now,
        accepted=accepted,
        expected_minimum=accepted,
    )
    if note_coverage:
        if added_seed:
            store.import_note_coverage(
                entries=(("bootstrap-seed", "test://bootstrap-seed"),),
                now=now,
            )
        store.mark_note_coverage_completed(now=now)


def test_status_on_absent_database_is_read_only(tmp_path: Path) -> None:
    database = tmp_path / "absent" / "automation.sqlite3"
    stdout = io.StringIO()

    assert main(["--db", str(database), "status"], stdout=stdout) == 0

    assert json.loads(stdout.getvalue()) == {"database_exists": False}
    assert not database.exists()
    assert not database.parent.exists()


def test_normal_mutation_fails_closed_until_exact_bootstrap_gate_is_valid(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    AutomationStore(database)

    with pytest.raises(ValueError, match="bootstrap gate"):
        main(
            ["--db", str(database), "ingest", "--input", "-", "--kind", "bookmarks"],
            stdin=io.StringIO(json.dumps([{"id": "blocked"}])),
            stdout=io.StringIO(),
        )

    _bootstrap(database)
    marker = json.loads(Path(f"{database}.bootstrap-complete").read_text())
    marker["instance_uuid"] = "00000000-0000-0000-0000-000000000000"
    Path(f"{database}.bootstrap-complete").write_text(json.dumps(marker), encoding="utf-8")

    assert main(["--db", str(database), "gate"], stdout=io.StringIO()) == 1
    with pytest.raises(ValueError, match="bootstrap gate"):
        main(
            ["--db", str(database), "ingest", "--input", "-", "--kind", "bookmarks"],
            stdin=io.StringIO(json.dumps([{"id": "still-blocked"}])),
            stdout=io.StringIO(),
        )


def test_normal_mutation_does_not_create_an_absent_database(tmp_path: Path) -> None:
    database = tmp_path / "absent" / "automation.sqlite3"

    with pytest.raises(ValueError, match="bootstrap gate"):
        main(
            ["--db", str(database), "worker", "--max-jobs", "0"],
            stdout=io.StringIO(),
        )

    assert not database.exists()
    assert not database.parent.exists()


def test_bootstrap_requires_explicit_minimum_and_terminal_cursor(tmp_path: Path) -> None:
    missing_minimum = tmp_path / "missing.sqlite3"
    with pytest.raises(ValueError, match="expected-minimum"):
        main(
            [
                "--db",
                str(missing_minimum),
                "ingest",
                "--input",
                "-",
                "--kind",
                "bookmarks",
                "--bootstrap",
            ],
            stdin=io.StringIO(json.dumps([{"id": "one"}])),
            stdout=io.StringIO(),
        )
    assert not Path(f"{missing_minimum}.bootstrap-complete").exists()

    partial = tmp_path / "partial.sqlite3"
    with pytest.raises(ValueError, match="accepted 1.*expected minimum 2"):
        main(
            [
                "--db",
                str(partial),
                "ingest",
                "--input",
                "-",
                "--kind",
                "bookmarks",
                "--bootstrap",
                "--expected-minimum",
                "2",
            ],
            stdin=io.StringIO(json.dumps([{"id": "one"}])),
            stdout=io.StringIO(),
        )
    assert AutomationStore(partial).count("bookmarks") == 0
    assert not Path(f"{partial}.bootstrap-complete").exists()

    cursor = tmp_path / "cursor.sqlite3"
    with pytest.raises(ValueError, match="nextCursor.*not terminal"):
        main(
            [
                "--db",
                str(cursor),
                "ingest",
                "--input",
                "-",
                "--kind",
                "bookmarks",
                "--bootstrap",
                "--expected-minimum",
                "1",
            ],
            stdin=io.StringIO(
                json.dumps({"tweets": [{"id": "one"}], "nextCursor": "more"})
            ),
            stdout=io.StringIO(),
        )
    assert AutomationStore(cursor).count("bookmarks") == 0

    duplicate = tmp_path / "duplicate.sqlite3"
    with pytest.raises(ValueError, match="duplicate bookmark id"):
        main(
            [
                "--db",
                str(duplicate),
                "ingest",
                "--input",
                "-",
                "--kind",
                "bookmarks",
                "--bootstrap",
                "--expected-minimum",
                "2",
            ],
            stdin=io.StringIO(json.dumps([{"id": "same"}, {"id": "same"}])),
            stdout=io.StringIO(),
        )
    assert not duplicate.exists()
    assert not Path(f"{duplicate}.bootstrap-complete").exists()


def test_database_identity_markers_permissions_and_foreign_keys(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database)
    store = AutomationStore(database)

    marker = json.loads(store.bootstrap_marker_path.read_text(encoding="utf-8"))
    with sqlite3.connect(database) as connection:
        instance_uuid = connection.execute(
            "SELECT value FROM metadata WHERE key = 'instance_uuid'"
        ).fetchone()[0]
    with store._connect() as connection:
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]

    assert marker["instance_uuid"] == instance_uuid
    assert marker["database_path"] == str(database.resolve())
    assert marker["accepted"] == 1
    assert marker["expected_minimum"] == 1
    assert os.stat(database).st_mode & 0o777 == 0o600
    assert foreign_keys == 1
    assert main(["--db", str(database), "gate"], stdout=io.StringIO()) == 0


def test_gate_fails_when_bookmark_cardinality_falls_below_bootstrap_marker(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(
        database,
        {"id": "bootstrap-one", "text": "One"},
        {"id": "bootstrap-two", "text": "Two"},
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DELETE FROM aggregate_coverage WHERE bookmark_id = ?",
            ("bootstrap-two",),
        )
        connection.execute(
            "DELETE FROM bookmarks WHERE bookmark_id = ?",
            ("bootstrap-two",),
        )
    stdout = io.StringIO()

    assert main(["--db", str(database), "gate"], stdout=stdout) == 1

    payload = json.loads(stdout.getvalue())
    assert payload["integrity_valid"] is True
    assert payload["bootstrap_count_valid"] is False
    assert payload["valid"] is False


def test_gate_rejects_incoherent_bootstrap_cardinality_metadata(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database)
    marker_path = Path(f"{database}.bootstrap-complete")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["expected_minimum"] = marker["accepted"] + 1
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    assert main(["--db", str(database), "gate"], stdout=io.StringIO()) == 1


def test_gate_fails_closed_for_an_uninitialized_database_file(tmp_path: Path) -> None:
    database = tmp_path / "empty.sqlite3"
    database.touch()
    original_stat = database.stat()
    stdout = io.StringIO()

    assert main(["--db", str(database), "gate"], stdout=stdout) == 1

    assert json.loads(stdout.getvalue())["valid"] is False
    assert database.stat().st_size == original_stat.st_size
    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    assert tables == []


def test_periodic_schedule_requires_the_exact_note_coverage_gate(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database)

    assert (
        main(
            ["--db", str(database), "gate", "--require-note-coverage"],
            stdout=io.StringIO(),
        )
        == 1
    )
    with pytest.raises(ValueError, match="note coverage gates"):
        main(
            ["--db", str(database), "schedule", "--task-kind", "aggregate"],
            stdout=io.StringIO(),
        )

    AutomationStore(database).mark_note_coverage_completed(now=datetime.now(UTC))
    note_marker = json.loads(
        Path(f"{database}.note-coverage-complete").read_text(encoding="utf-8")
    )
    bootstrap_marker = json.loads(
        Path(f"{database}.bootstrap-complete").read_text(encoding="utf-8")
    )
    assert note_marker["instance_uuid"] == bootstrap_marker["instance_uuid"]
    assert (
        main(
            ["--db", str(database), "gate", "--require-note-coverage"],
            stdout=io.StringIO(),
        )
        == 0
    )


def test_dead_letter_listing_is_read_only_and_requeue_is_explicitly_unsupported(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    AutomationStore(database)
    before = database.stat().st_size
    stdout = io.StringIO()

    assert main(["--db", str(database), "dead-letter-list"], stdout=stdout) == 0

    assert json.loads(stdout.getvalue()) == {
        "database_exists": True,
        "jobs": [],
        "requeue_supported": False,
    }
    assert database.stat().st_size == before


def test_ingest_cli_accepts_json_batch_from_stdin_and_ignores_likes(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database, {"id": "bookmark-kept", "text": "Historical"})
    stdin = io.StringIO(
        json.dumps(
            [
                {"kind": "likes", "id": "like-ignored"},
                {"kind": "bookmarks", "id": "bookmark-kept", "text": "Keep me"},
            ]
        )
    )
    stdout = io.StringIO()

    exit_code = main(
        ["--db", str(database), "ingest", "--input", "-"],
        stdin=stdin,
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"accepted": 1, "created": 1, "ignored": 1}
    store = AutomationStore(database)
    assert store.count("bookmarks") == 1


def test_ingest_cli_can_label_raw_bird_bookmark_output_and_detect_video(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database, {"id": "historical", "text": "Historical"})
    stdin = io.StringIO(
        json.dumps(
            [
                {
                    "id": "bird-bookmark-video",
                    "text": "Native video",
                    "media": [
                        {
                            "type": "video",
                            "videoUrl": "https://video.twimg.com/preview.mp4",
                        }
                    ],
                }
            ]
        )
    )
    stdout = io.StringIO()

    exit_code = main(
        ["--db", str(database), "ingest", "--input", "-", "--kind", "bookmarks"],
        stdin=stdin,
        stdout=stdout,
    )

    assert exit_code == 0
    jobs = AutomationStore(database).list_jobs()
    assert any(job.task_kind == "deliver_video" for job in jobs)


def test_ingest_cli_accepts_paginated_bird_tweets_envelope(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database, {"id": "from-reconciliation", "text": "Historical"})
    stdin = io.StringIO(
        json.dumps(
            {
                "tweets": [{"id": "from-reconciliation", "text": "Recovered by full scan"}],
                "nextCursor": None,
            }
        )
    )
    stdout = io.StringIO()

    exit_code = main(
        ["--db", str(database), "ingest", "--input", "-", "--kind", "bookmarks"],
        stdin=stdin,
        stdout=stdout,
    )

    assert exit_code == 0
    assert AutomationStore(database).count("bookmarks") == 1


def test_bootstrap_cli_preseeds_history_without_telegram_or_video_jobs(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    historical = {
        "id": "historical-video",
        "text": "Old video https://example.test/article",
        "hasVideo": True,
        "media": [
            {
                "type": "video",
                "videoUrl": "https://video.twimg.com/historical.mp4",
            }
        ],
        "urls": [{"expanded_url": "https://example.test/article"}],
    }

    exit_code = main(
        [
            "--db",
            str(database),
            "ingest",
            "--input",
            "-",
            "--kind",
            "bookmarks",
            "--bootstrap",
            "--expected-minimum",
            "1",
        ],
        stdin=io.StringIO(json.dumps([historical])),
        stdout=io.StringIO(),
    )

    store = AutomationStore(database)
    assert exit_code == 0
    assert store.count("bookmarks") == 1
    assert store.list_jobs() == []
    assert store.status_snapshot()["bootstrap_completed"] is True
    assert store.bootstrap_marker_path.is_file()

    main(
        ["--db", str(database), "ingest", "--input", "-", "--kind", "bookmarks"],
        stdin=io.StringIO(
            json.dumps(
                [
                    historical,
                    {"id": "missed-new-bookmark", "text": "Found by reconciliation"},
                ]
            )
        ),
        stdout=io.StringIO(),
    )
    jobs = AutomationStore(database).list_jobs()
    assert {job.bookmark_id for job in jobs} == {"missed-new-bookmark"}
    assert any(job.task_kind == "notify" for job in jobs)
    assert not any(job.bookmark_id == "historical-video" for job in jobs)


def test_note_coverage_import_skips_existing_sources_and_can_requeue_thin_notes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    notes = tmp_path / "notes"
    notes.mkdir()
    bookmarks = [
        {"id": "existing-good", "text": "Already distilled"},
        {"id": "existing-thin", "text": "Thin capture"},
        {"id": "missing-note", "text": "Needs backlog"},
    ]
    main(
        [
            "--db",
            str(database),
            "ingest",
            "--input",
            "-",
            "--kind",
            "bookmarks",
            "--bootstrap",
            "--expected-minimum",
            str(len(bookmarks)),
        ],
        stdin=io.StringIO(json.dumps(bookmarks)),
        stdout=io.StringIO(),
    )
    (notes / "good.md").write_text(
        "---\nbookmark_id: existing-good\n---\n\n" + ("Durable content " * 30),
        encoding="utf-8",
    )
    (notes / "thin.md").write_text(
        "---\nbookmark_id: \"existing-thin\"\ntags: [thin-content]\n---\n\nUnavailable.",
        encoding="utf-8",
    )
    stdout = io.StringIO()

    exit_code = main(
        [
            "--db",
            str(database),
            "import-note-coverage",
            "--notes-dir",
            str(notes),
            "--redo-thin",
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {
        "duplicates": 0,
        "imported": 1,
        "malformed": 0,
        "scanned": 2,
        "thin_requeued": 1,
        "unmatched": 0,
    }
    store = AutomationStore(database)
    assert store.note_coverage_marker_path.is_file()
    scheduled = __import__(
        "bookmark_automation.service", fromlist=["BookmarkAutomation"]
    ).BookmarkAutomation(store).schedule_periodic(
        task_kind="backlog",
        input_revision="coverage-test",
        batch_size=10,
    )
    deep_jobs = [job for job in store.list_jobs() if job.id in set(scheduled.job_ids)]
    assert {job.bookmark_id for job in deep_jobs} == {"existing-thin", "missing-note"}


def test_note_coverage_import_fails_closed_without_publishing_its_marker(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    notes = tmp_path / "notes"
    notes.mkdir()
    main(
        [
            "--db",
            str(database),
            "ingest",
            "--input",
            "-",
            "--kind",
            "bookmarks",
            "--bootstrap",
            "--expected-minimum",
            "1",
        ],
        stdin=io.StringIO(json.dumps([{"id": "one", "text": "One"}])),
        stdout=io.StringIO(),
    )
    (notes / "malformed.md").write_text("No frontmatter here", encoding="utf-8")

    with pytest.raises(ValueError, match="malformed"):
        main(
            [
                "--db",
                str(database),
                "import-note-coverage",
                "--notes-dir",
                str(notes),
            ],
            stdout=io.StringIO(),
        )

    store = AutomationStore(database)
    assert not store.note_coverage_marker_path.exists()
    assert store.status_snapshot()["note_coverage_completed"] is False
    assert store.status_snapshot()["backlog_covered"] == 0


def test_decision_cli_accepts_callback_json_from_stdin(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    store = AutomationStore(database)
    from bookmark_automation.service import BookmarkAutomation

    BookmarkAutomation(store).ingest(
        {"kind": "bookmarks", "id": "callback-bookmark", "text": "Act now"}
    )
    store.mark_bootstrap_completed(
        now=datetime.now(UTC), accepted=1, expected_minimum=1
    )
    stdout = io.StringIO()

    exit_code = main(
        ["--db", str(database), "decision", "--input", "-"],
        stdin=io.StringIO(
            json.dumps(
                {
                    "bookmark_id": "callback-bookmark",
                    "action": "act",
                    "decision_id": "telegram-update-5001",
                }
            )
        ),
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"created": True}
    deep = next(job for job in AutomationStore(database).list_jobs() if job.task_kind == "deep")
    assert deep.priority > 1_000


def test_schedule_cli_enqueues_periodic_job_by_logical_profile(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    from bookmark_automation.service import BookmarkAutomation

    store = AutomationStore(database)
    BookmarkAutomation(store).ingest(
        {"kind": "bookmarks", "id": "daily-item", "text": "Daily delta"}
    )
    now = datetime.now(UTC)
    store.mark_bootstrap_completed(now=now, accepted=1, expected_minimum=1)
    store.mark_note_coverage_completed(now=now)
    stdout = io.StringIO()

    exit_code = main(
        [
            "--db",
            str(database),
            "schedule",
            "--task-kind",
            "aggregate",
            "--input-revision",
            "2026-08-08",
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["created"] is True
    job = next(
        job
        for job in AutomationStore(database).list_jobs()
        if job.task_kind == "aggregate"
    )
    assert job.profile == "aggregate"


def test_worker_cli_can_run_an_offline_zero_job_drain(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database)
    stdout = io.StringIO()

    exit_code = main(
        ["--db", str(database), "worker", "--max-jobs", "0"],
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"processed": 0, "states": {}}


def test_effects_cli_can_run_a_zero_job_drain_without_network(
    tmp_path: Path, monkeypatch,
) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database)
    stdout = io.StringIO()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "offline-test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "offline-test-chat")

    exit_code = main(
        [
            "--db",
            str(database),
            "effects",
            "--max-jobs",
            "0",
            "--video-dir",
            str(tmp_path / "videos"),
            "--note-dir",
            str(tmp_path / "notes"),
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"processed": 0, "states": {}}


def test_schedule_cli_defaults_to_brasilia_date_and_reports_all_batch_ids(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    automation = __import__(
        "bookmark_automation.service", fromlist=["BookmarkAutomation"]
    ).BookmarkAutomation(AutomationStore(database))
    for bookmark_id in ("batch-one", "batch-two", "batch-three"):
        automation.ingest(
            {"kind": "bookmarks", "id": bookmark_id, "text": bookmark_id}
        )
    _complete_gates(database, note_coverage=True)
    stdout = io.StringIO()
    expected_date = datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()

    exit_code = main(
        [
            "--db",
            str(database),
            "schedule",
            "--task-kind",
            "aggregate",
            "--batch-size",
            "2",
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["created"] is True
    assert payload["job_id"] == payload["job_ids"][0]
    assert len(payload["job_ids"]) == 2
    aggregate_jobs = [
        job for job in AutomationStore(database).list_jobs() if job.task_kind == "aggregate"
    ]
    assert {job.input_revision.split(":batch:")[0] for job in aggregate_jobs} == {
        expected_date
    }


def test_schedule_cli_handles_an_empty_backlog_without_inventing_a_job_id(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    _complete_gates(database, note_coverage=True)
    stdout = io.StringIO()

    exit_code = main(
        ["--db", str(database), "schedule", "--task-kind", "backlog"],
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {
        "created": False,
        "job_id": None,
        "job_ids": [],
    }


def test_import_decisions_cli_deduplicates_bridge_jsonl_by_event_id(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    from bookmark_automation.service import BookmarkAutomation

    store = AutomationStore(database)
    BookmarkAutomation(store).ingest(
        {"kind": "bookmarks", "id": "bridge-tweet", "text": "Bridge callback"}
    )
    store.mark_bootstrap_completed(
        now=datetime.now(UTC), accepted=1, expected_minimum=1
    )
    event = {"event_id": "telegram:6001", "tweet_id": "bridge-tweet", "action": "keep"}
    stdin = io.StringIO(f"{json.dumps(event)}\n{json.dumps(event)}\n")
    stdout = io.StringIO()

    exit_code = main(
        ["--db", str(database), "import-decisions", "--input", "-"],
        stdin=stdin,
        stdout=stdout,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue()) == {"created": 1, "duplicates": 1, "errors": 0}
    assert AutomationStore(database).count("decisions") == 1


def test_raw_bird_tco_link_schedules_article_capture_and_deep(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database, {"id": "bird-tco", "text": "Historical"})
    stdout = io.StringIO()

    exit_code = main(
        ["--db", str(database), "ingest", "--input", "-", "--kind", "bookmarks"],
        stdin=io.StringIO(
            json.dumps(
                [{"id": "bird-tco", "text": "Worth reading https://t.co/AbCdEf1234"}]
            )
        ),
        stdout=stdout,
    )

    assert exit_code == 0
    task_kinds = {job.task_kind for job in AutomationStore(database).list_jobs()}
    assert {"fetch_article", "deep"} <= task_kinds


def test_raw_bird_nested_animated_video_schedules_delivery(tmp_path: Path) -> None:
    database = tmp_path / "automation.sqlite3"
    _bootstrap(database, {"id": "historical", "text": "Historical"})

    main(
        ["--db", str(database), "ingest", "--input", "-", "--kind", "bookmarks"],
        stdin=io.StringIO(
            json.dumps(
                [
                    {
                        "id": "quoted-video",
                        "text": "Quoted animation",
                        "quotedTweet": {
                            "media": [
                                {
                                    "type": "animated_gif",
                                    "videoUrl": "https://video.twimg.com/animation.mp4",
                                }
                            ]
                        },
                    }
                ]
            )
        ),
        stdout=io.StringIO(),
    )

    assert any(
        job.task_kind == "deliver_video" for job in AutomationStore(database).list_jobs()
    )


def test_status_cli_reports_queue_and_receipt_counts_without_external_work(
    tmp_path: Path,
) -> None:
    database = tmp_path / "automation.sqlite3"
    store = AutomationStore(database)
    from bookmark_automation.service import BookmarkAutomation

    BookmarkAutomation(store).ingest(
        {"kind": "bookmarks", "id": "status-bookmark", "text": "Status me"}
    )
    stdout = io.StringIO()

    exit_code = main(["--db", str(database), "status"], stdout=stdout)

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert payload["bookmarks"] == 1
    assert payload["jobs_by_state"] == {"pending": 3}
    assert payload["jobs_by_task"] == {"notify": 1, "quick": 1, "recall_context": 1}
    assert payload["receipts"] == 0
    assert payload["dead_letter"] == 0
