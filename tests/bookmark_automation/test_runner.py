"""Shared subscription runner subprocess contract."""

import json
import subprocess
from typing import Any

import pytest

from bookmark_automation.runner import RunnerInvocationError, SubprocessSubscriptionRunner


def test_subscription_runner_sends_one_json_request_over_stdin_and_parses_receipt() -> None:
    observed: dict[str, Any] = {}

    def fake_executor(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=json.dumps(
                {
                    "status": "succeeded",
                    "job_id": "job-42",
                    "profile": "quick",
                    "provider": "codex",
                    "model": "model-selected-by-router",
                    "attempts": [{"status": "succeeded"}],
                    "output": {"summary": "Useful memory pattern"},
                }
            ),
            stderr="",
        )

    runner = SubprocessSubscriptionRunner(
        command=("python3", "-m", "subscription_inference", "run"),
        executor=fake_executor,
    )

    receipt = runner.run(
        profile="quick",
        prompt="Classify this bookmark",
        schema={"type": "object"},
        job_id="job-42",
    )

    assert observed["argv"][-2:] == ["--profile", "quick"]
    request = json.loads(observed["kwargs"]["input"])
    assert request == {
        "prompt": "Classify this bookmark",
        "schema": {"type": "object"},
        "job_id": "job-42",
    }
    assert observed["kwargs"]["text"] is True
    assert observed["kwargs"]["check"] is False
    assert observed["kwargs"]["timeout"] == 960
    assert receipt.status == "succeeded"
    assert receipt.output == {"summary": "Useful memory pattern"}


@pytest.mark.parametrize(
    ("returncode", "status"),
    [(0, "waiting_provider"), (3, "succeeded"), (4, "waiting_provider")],
)
def test_subscription_runner_rejects_spoofed_exit_status_pairs(
    returncode: int,
    status: str,
) -> None:
    payload = {
        "status": status,
        "job_id": "job-42",
        "profile": "quick",
        "provider": "codex" if status == "succeeded" else None,
        "model": "configured-model" if status == "succeeded" else None,
        "attempts": [],
        "output": {"summary": "value"} if status == "succeeded" else None,
    }

    def fake_executor(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            argv,
            returncode,
            stdout=json.dumps(payload),
            stderr="secret diagnostic that must never be surfaced",
        )

    runner = SubprocessSubscriptionRunner(command=("runner",), executor=fake_executor)

    with pytest.raises(RunnerInvocationError) as captured:
        runner.run(
            profile="quick",
            prompt="prompt",
            schema={"type": "object"},
            job_id="job-42",
        )

    assert "secret diagnostic" not in str(captured.value)


@pytest.mark.parametrize(
    "override",
    [
        {"job_id": "another-job"},
        {"profile": "deep"},
        {"provider": None},
        {"model": None},
        {"output": None},
    ],
)
def test_subscription_runner_rejects_mismatched_or_incomplete_success_receipts(
    override: dict[str, Any],
) -> None:
    payload = {
        "status": "succeeded",
        "job_id": "job-42",
        "profile": "quick",
        "provider": "codex",
        "model": "configured-model",
        "attempts": [],
        "output": {"summary": "value"},
        **override,
    }

    def fake_executor(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    runner = SubprocessSubscriptionRunner(command=("runner",), executor=fake_executor)

    with pytest.raises(RunnerInvocationError):
        runner.run(
            profile="quick",
            prompt="prompt",
            schema={"type": "object"},
            job_id="job-42",
        )
