"""Staged systemd safety and go-live gate contracts."""

from pathlib import Path

SYSTEMD = Path(__file__).parents[2] / "bookmark_automation" / "systemd"
BOOTSTRAP_MARKER = (
    "ConditionPathExists=/workspace/twitter-bookmark-processor/data/"
    "bookmark-automation.sqlite3.bootstrap-complete"
)
NOTE_COVERAGE_MARKER = (
    "ConditionPathExists=/workspace/twitter-bookmark-processor/data/"
    "bookmark-automation.sqlite3.note-coverage-complete"
)


def _unit(name: str) -> str:
    return (SYSTEMD / name).read_text(encoding="utf-8")


def test_every_runtime_service_is_gated_by_the_completed_bootstrap_marker() -> None:
    services = sorted(SYSTEMD.glob("*.service"))

    assert services
    assert all(BOOTSTRAP_MARKER in path.read_text(encoding="utf-8") for path in services)
    assert all(
        "ExecCondition=/usr/bin/python3 -m bookmark_automation --db "
        "/workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3 gate"
        in path.read_text(encoding="utf-8")
        for path in services
    )


def test_periodic_service_also_requires_legacy_note_coverage_import() -> None:
    periodic = _unit("bookmark-automation-periodic.service")

    assert NOTE_COVERAGE_MARKER in periodic
    assert "gate --require-note-coverage" in periodic
    assert "schedule --task-kind aggregate --batch-size 25" in periodic
    assert "schedule --task-kind backlog --batch-size 5" in periodic


def test_inference_and_effect_units_fit_one_job_inside_lease_and_process_timeouts() -> None:
    inference = _unit("bookmark-automation-inference.service")
    effects = _unit("bookmark-automation-effects.service")

    assert "Environment=CODEX_HOME=/workspace/.mcp-tools/codex" in inference
    assert "BOOKMARK_AUTOMATION_RUNNER_COMMAND_JSON" in inference
    assert "worker --max-jobs 1" in inference
    assert "TimeoutStartSec=19min" in inference
    assert "effects --max-jobs 1" in effects
    assert "TimeoutStartSec=15min" in effects
    assert "EnvironmentFile=/etc/bookmark-automation/telegram.env" in effects


def test_reconciliation_is_full_but_live_and_decisions_never_poll_telegram() -> None:
    reconciliation = _unit("bookmark-automation-reconcile.service")
    poll = _unit("bookmark-automation-poll.service")
    decisions = _unit("bookmark-automation-decisions.service")

    assert "bird bookmarks --all --json" in reconciliation
    assert "bookmarks --all -n" not in reconciliation
    assert "--bootstrap" not in reconciliation
    assert "TimeoutStartSec=30min" in reconciliation
    assert "TimeoutStartSec=2min" in poll
    assert "twitter_bookmark_actions.jsonl" in decisions
    assert "getUpdates" not in decisions


def test_runtime_workers_fail_closed_below_the_storage_floor() -> None:
    services = sorted(SYSTEMD.glob("*.service"))
    workers = [path for path in services if path.name != "bookmark-automation-maintenance.service"]
    operational_gate = (
        "ExecCondition=/usr/bin/python3 -m bookmark_automation --db "
        "/workspace/twitter-bookmark-processor/data/bookmark-automation.sqlite3 "
        "operational-gate --path /workspace/twitter-bookmark-processor/data "
        "--min-free-bytes 1073741824"
    )

    assert workers
    assert all(operational_gate in path.read_text(encoding="utf-8") for path in workers)


def test_maintenance_timer_alerts_and_prunes_without_being_blocked_by_low_disk() -> None:
    service = _unit("bookmark-automation-maintenance.service")
    timer = _unit("bookmark-automation-maintenance.timer")

    assert "EnvironmentFile=/etc/bookmark-automation/telegram.env" in service
    assert "maintenance --video-dir /workspace/twitter-bookmark-processor/data/videos" in service
    assert "--retention-days 30 --min-free-bytes 1073741824" in service
    assert "operational-gate" not in service
    assert "ReadWritePaths=/workspace/twitter-bookmark-processor/data" in service
    assert "OnUnitActiveSec=5min" in timer
