"""Operational maintenance contracts for the production bookmark loop."""

import io
import json
import os
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bookmark_automation.cli import main
from bookmark_automation.maintenance import MaintenanceWorker
from bookmark_automation.service import BookmarkAutomation
from bookmark_automation.store import AutomationStore


class RecordingTelegram:
    def __init__(self) -> None:
        self.messages: list[Any] = []

    def send_message(self, message: Any, **_: Any) -> Any:
        self.messages.append(message)
        return SimpleNamespace(message_id=len(self.messages))


def test_maintenance_alerts_each_dead_letter_once(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 19, 0, tzinfo=UTC)
    queued = store.enqueue_job(
        bookmark_id="failed-bookmark",
        task_kind="quick",
        input_revision="revision-1",
        profile="quick",
        priority=100,
        now=now,
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'dead_letter' WHERE id = ?",
            (queued.job_id,),
        )
        connection.execute(
            """
            INSERT INTO attempts (
                job_id, attempt_no, status, detail_json, started_at, finished_at
            ) VALUES (?, 1, 'failed', '{}', ?, ?)
            """,
            (queued.job_id, now.isoformat(), now.isoformat()),
        )
    telegram = RecordingTelegram()
    worker = MaintenanceWorker(
        store=store,
        telegram=telegram,
        video_dir=tmp_path / "videos",
    )

    first = worker.run(now=now)
    second = worker.run(now=now)

    assert first["dead_letter_alerts"] == 1
    assert second["dead_letter_alerts"] == 0
    assert len(telegram.messages) == 1
    assert f"job {queued.job_id}" in telegram.messages[0].text
    assert "quick" in telegram.messages[0].text


def test_failed_dead_letter_alert_is_retried_on_the_next_cycle(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 19, 2, tzinfo=UTC)
    queued = store.enqueue_job(
        bookmark_id="retry-alert",
        task_kind="quick",
        input_revision="revision-alert",
        profile="quick",
        priority=100,
        now=now,
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'dead_letter' WHERE id = ?",
            (queued.job_id,),
        )

    class FailingTelegram:
        def send_message(self, _message: Any, **_: Any) -> Any:
            raise RuntimeError("telegram unavailable")

    failing = MaintenanceWorker(
        store=store,
        telegram=FailingTelegram(),
        video_dir=tmp_path / "videos",
    )
    with pytest.raises(RuntimeError, match="telegram unavailable"):
        failing.run(now=now)

    telegram = RecordingTelegram()
    recovered = MaintenanceWorker(
        store=store,
        telegram=telegram,
        video_dir=tmp_path / "videos",
    ).run(now=now)

    assert recovered["dead_letter_alerts"] == 1
    assert len(telegram.messages) == 1


def test_maintenance_alerts_unresolved_committed_effect_once(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 19, 5, tzinfo=UTC)
    queued = store.enqueue_job(
        bookmark_id="ambiguous-bookmark",
        task_kind="notify",
        input_revision="revision-2",
        profile="none",
        priority=100,
        now=now,
    )
    with store._connect() as connection:
        connection.execute(
            """
            UPDATE jobs SET state = 'leased', lease_owner = 'crashed-worker',
                lease_until = ?, lease_token = 'expired-token' WHERE id = ?
            """,
            (now.isoformat(), queued.job_id),
        )
        connection.execute(
            """
            INSERT INTO attempts (job_id, attempt_no, status, started_at)
            VALUES (?, 1, 'effect_committed', ?)
            """,
            (queued.job_id, now.isoformat()),
        )
    telegram = RecordingTelegram()
    worker = MaintenanceWorker(
        store=store,
        telegram=telegram,
        video_dir=tmp_path / "videos",
    )

    first = worker.run(now=now)
    second = worker.run(now=now)

    assert first["committed_effect_alerts"] == 1
    assert second["committed_effect_alerts"] == 0
    assert len(telegram.messages) == 1
    assert f"job {queued.job_id}" in telegram.messages[0].text
    assert "não será repetido automaticamente" in telegram.messages[0].text


def test_maintenance_prunes_only_expired_completed_videos(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    now = datetime(2026, 8, 8, 19, 10, tzinfo=UTC)
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    old = video_dir / "old-video.mp4"
    recent = video_dir / "recent-video.mp4"
    unresolved = video_dir / "unresolved-video.mp4"
    old.write_bytes(b"old")
    recent.write_bytes(b"recent")
    unresolved.write_bytes(b"pending")
    old_timestamp = (now - timedelta(days=31)).timestamp()
    recent_timestamp = (now - timedelta(days=2)).timestamp()
    os.utime(old, (old_timestamp, old_timestamp))
    os.utime(recent, (recent_timestamp, recent_timestamp))
    os.utime(unresolved, (old_timestamp, old_timestamp))
    queued: dict[str, int] = {}
    for bookmark_id in ("old-video", "recent-video", "unresolved-video"):
        queued[bookmark_id] = store.enqueue_job(
            bookmark_id=bookmark_id,
            task_kind="deliver_video",
            input_revision="revision-video",
            profile="none",
            priority=100,
            now=now,
        ).job_id
    with store._connect() as connection:
        connection.execute(
            "UPDATE jobs SET state = 'done' WHERE id IN (?, ?)",
            (queued["old-video"], queued["recent-video"]),
        )
    worker = MaintenanceWorker(
        store=store,
        telegram=RecordingTelegram(),
        video_dir=video_dir,
        retention_days=30,
    )

    result = worker.run(now=now)

    assert result["videos_removed"] == 1
    assert result["bytes_removed"] == 3
    assert not old.exists()
    assert recent.exists()
    assert unresolved.exists()


def test_maintenance_alerts_once_per_low_disk_incident(tmp_path: Path) -> None:
    store = AutomationStore(tmp_path / "automation.sqlite3")
    telegram = RecordingTelegram()
    DiskUsage = namedtuple("DiskUsage", "total used free")
    free_bytes = [100]

    def disk_usage(_path: Path) -> Any:
        return DiskUsage(total=10_000, used=10_000 - free_bytes[0], free=free_bytes[0])

    worker = MaintenanceWorker(
        store=store,
        telegram=telegram,
        video_dir=tmp_path / "videos",
        min_free_bytes=1_000,
        disk_usage=disk_usage,
    )
    now = datetime(2026, 8, 8, 19, 20, tzinfo=UTC)

    first = worker.run(now=now)
    repeated = worker.run(now=now)
    free_bytes[0] = 2_000
    recovered = worker.run(now=now)
    free_bytes[0] = 100
    relapsed = worker.run(now=now)

    assert first["healthy"] is False
    assert first["disk_alerts"] == 1
    assert repeated["disk_alerts"] == 0
    assert recovered["healthy"] is True
    assert relapsed["disk_alerts"] == 1
    assert len(telegram.messages) == 2
    assert all("disco baixo" in message.text for message in telegram.messages)


def test_maintenance_cli_runs_the_bounded_operational_cycle(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    database = tmp_path / "automation.sqlite3"
    store = AutomationStore(database)
    BookmarkAutomation(store).ingest(
        {"kind": "bookmarks", "id": "bootstrap-seed", "text": "Seed"},
        bootstrap=True,
    )
    store.mark_bootstrap_completed(
        now=datetime.now(UTC),
        accepted=1,
        expected_minimum=1,
    )
    telegram = RecordingTelegram()
    monkeypatch.setattr(
        "bookmark_automation.cli.TelegramClient",
        lambda **_: telegram,
    )
    stdout = io.StringIO()

    assert (
        main(
            [
                "--db",
                str(database),
                "maintenance",
                "--video-dir",
                str(tmp_path / "videos"),
                "--retention-days",
                "30",
                "--min-free-bytes",
                "1",
            ],
            stdout=stdout,
        )
        == 0
    )
    payload = json.loads(stdout.getvalue())
    assert payload["healthy"] is True
    assert payload["dead_letter_alerts"] == 0
    assert payload["committed_effect_alerts"] == 0
    assert payload["database_bytes"] > 0
    assert payload["wal_bytes"] >= 0
    assert payload["wal_checkpoint_busy"] in {0, 1}
    assert payload["wal_checkpointed_frames"] >= 0
