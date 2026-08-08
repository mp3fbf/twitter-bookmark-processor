"""Provider-neutral subprocess client for subscription inference."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass(frozen=True)
class RunnerReceipt:
    status: str
    profile: str
    provider: str | None
    model: str | None
    attempts: tuple[Mapping[str, Any], ...]
    output: Mapping[str, Any] | None


class RunnerInvocationError(RuntimeError):
    """The shared runner did not honor its process or JSON contract."""


Executor = Callable[..., subprocess.CompletedProcess[str]]


class SubprocessSubscriptionRunner:
    """Invoke the shared router without knowing providers or model names."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        executor: Executor = subprocess.run,
        timeout_seconds: float = 960,
        env: Mapping[str, str] | None = None,
    ) -> None:
        if not command:
            raise ValueError("runner command must not be empty")
        self.command = tuple(command)
        self.executor = executor
        self.timeout_seconds = timeout_seconds
        self.env = dict(env) if env is not None else None

    def run(
        self,
        *,
        profile: str,
        prompt: str,
        schema: Mapping[str, Any],
        job_id: str,
    ) -> RunnerReceipt:
        argv = [*self.command, "--profile", profile]
        request = json.dumps(
            {"prompt": prompt, "schema": schema, "job_id": job_id},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        completed = self.executor(
            argv,
            input=request,
            text=True,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds,
            env=self.env,
        )
        try:
            payload = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise RunnerInvocationError(
                f"subscription runner returned invalid JSON (exit {completed.returncode})"
            ) from exc
        if not isinstance(payload, dict):
            raise RunnerInvocationError("subscription runner receipt must be an object")
        status = payload.get("status")
        if status not in {"succeeded", "waiting_provider", "failed"}:
            raise RunnerInvocationError(f"unsupported runner status: {status!r}")
        expected_exit_code = {
            "succeeded": 0,
            "waiting_provider": 3,
            "failed": 4,
        }[status]
        if completed.returncode != expected_exit_code:
            raise RunnerInvocationError("subscription runner exit/status contract mismatch")
        if payload.get("profile") != profile:
            raise RunnerInvocationError("subscription runner profile mismatch")
        if payload.get("job_id") != job_id:
            raise RunnerInvocationError("subscription runner job_id mismatch")
        output = payload.get("output")
        if output is not None and not isinstance(output, dict):
            raise RunnerInvocationError("subscription runner output must be an object or null")
        attempts = payload.get("attempts") or []
        if not isinstance(attempts, list) or not all(
            isinstance(item, dict) for item in attempts
        ):
            raise RunnerInvocationError("subscription runner attempts must be an array of objects")
        provider = payload.get("provider")
        model = payload.get("model")
        if status == "succeeded" and (
            not isinstance(provider, str)
            or not provider
            or not isinstance(model, str)
            or not model
            or output is None
        ):
            raise RunnerInvocationError("successful runner receipt is incomplete")
        return RunnerReceipt(
            status=status,
            profile=profile,
            provider=str(provider) if provider is not None else None,
            model=str(model) if model is not None else None,
            attempts=tuple(attempts),
            output=output,
        )
