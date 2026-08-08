"""Load versioned prompts and output schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import Job


class PromptCatalog:
    def __init__(
        self,
        root: Path | None = None,
        *,
        version: str = "v1",
        max_source_chars: int = 12_000,
    ) -> None:
        self.root = root or Path(__file__).parent
        self.version = version
        if max_source_chars <= 0:
            raise ValueError("max_source_chars must be positive")
        self.max_source_chars = max_source_chars

    def _bounded(self, value: Any) -> Any:
        if isinstance(value, str):
            return value[: self.max_source_chars]
        if isinstance(value, list):
            return [self._bounded(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._bounded(item) for key, item in value.items()}
        return value

    def build(self, job: Job, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        task_profile = {
            "quick": "quick",
            "deep": "deep",
            "backlog": "backlog",
            "aggregate": "aggregate",
        }.get(job.task_kind)
        if task_profile is None:
            raise ValueError(f"no inference prompt for task: {job.task_kind}")
        instructions = (
            self.root / "prompts" / self.version / f"{task_profile}.md"
        ).read_text(encoding="utf-8")
        schema = json.loads(
            (self.root / "schemas" / self.version / f"{task_profile}.json").read_text(
                encoding="utf-8"
            )
        )
        untrusted_input = json.dumps(
            self._bounded(payload), ensure_ascii=False, sort_keys=True
        )
        prompt = f"{instructions.rstrip()}\n\n<untrusted_bookmark_input>\n{untrusted_input}\n</untrusted_bookmark_input>"
        return prompt, schema
