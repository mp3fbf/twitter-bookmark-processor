"""Core-owned, immutable Source note materialization."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


class NoteConflictError(RuntimeError):
    """A stable Source-note identity already contains different bytes."""


@dataclass(frozen=True)
class NoteReceipt:
    path: Path
    created: bool
    sha256: str
    size_bytes: int


def _yaml(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _lines(values: Any, *, empty: str = "_Nenhum._") -> str:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        return empty
    return "\n".join(f"- {_safe_markdown(value, inline=True)}" for value in values)


def _safe_markdown(value: Any, *, inline: bool = False) -> str:
    """Render model text as inert Markdown, never as HTML or a remote embed."""
    text = str(value or "").strip()
    if inline:
        text = " ".join(text.split())
    text = html.escape(text, quote=False)
    text = re.sub(r"!(?=\[)", r"\\!", text)
    if not inline:
        text = re.sub(r"(?m)^([ \t]*)(?=(?:#{1,6}\s|```|~~~|---\s*$))", r"\1\\", text)
    return text


def _render(payload: Mapping[str, Any]) -> bytes:
    bookmark = payload.get("bookmark") or {}
    analysis = payload.get("analysis") or {}
    source = analysis.get("source_note") or {}
    provenance = source.get("provenance") or {}
    inference = payload.get("inference") or {}
    bookmark_id = str(bookmark.get("id") or provenance.get("bookmark_id") or "")
    revision = str(payload.get("input_revision") or provenance.get("input_revision") or "")
    title = _safe_markdown(
        source.get("title") or f"Twitter bookmark {bookmark_id}",
        inline=True,
    )
    prompt_injection_detected = bool(
        analysis.get("prompt_injection_detected")
        or provenance.get("prompt_injection_detected")
    )
    evidence = source.get("evidence") or []
    evidence_lines = []
    for item in evidence:
        if isinstance(item, Mapping):
            evidence_lines.append(
                f"- {_safe_markdown(item.get('claim'), inline=True)} — "
                f"{_safe_markdown(item.get('source_locator'), inline=True)}"
            )
    quarantine = (
        "\n> [!warning] SAÍDA EM QUARENTENA\n"
        "> Foi detectada possível prompt injection na fonte. "
        "Revise antes de promover qualquer conceito.\n"
        if prompt_injection_detected
        else ""
    )
    content = f"""---
type: source
source_type: {_yaml(source.get("source_type") or "other")}
bookmark_id: {_yaml(bookmark_id)}
input_revision: {_yaml(revision)}
source_url: {_yaml(source.get("source_url"))}
author: {_yaml(source.get("author"))}
published_at: {_yaml(source.get("published_at"))}
content_status: {_yaml(provenance.get("content_status"))}
recall_status: {_yaml(provenance.get("recall_status"))}
prompt_injection_detected: {_yaml(prompt_injection_detected)}
inference_provider: {_yaml(inference.get("provider"))}
inference_model: {_yaml(inference.get("model"))}
---

# {title}
{quarantine}

## Resumo

{_safe_markdown(analysis.get("summary"))}

## Insight durável

{_safe_markdown(analysis.get("durable_insight"))}

## Por que me interessou

{_safe_markdown(analysis.get("why_interesting"))}

## Encaixe no Second Brain

{_lines(analysis.get("second_brain_fit"))}

## Claims

{_lines(source.get("key_claims"))}

## Evidências

{chr(10).join(evidence_lines) if evidence_lines else "_Nenhuma._"}

## Próxima ação

{_safe_markdown(analysis.get("next_action") or "_Nenhuma._")}

## Fonte original

- Bookmark: https://x.com/i/web/status/{bookmark_id}
- URL: {_safe_markdown(source.get("source_url") or "não disponível", inline=True)}
"""
    return content.encode("utf-8")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_source_note(payload: Mapping[str, Any], output_dir: str | Path) -> NoteReceipt:
    """Publish immutable bytes under a stable bookmark+revision identity."""
    bookmark = payload.get("bookmark") or {}
    bookmark_id = str(bookmark.get("id") or "").strip()
    revision = str(payload.get("input_revision") or "").strip().lower()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", bookmark_id):
        raise ValueError("Source note requires a safe bookmark id")
    if not re.fullmatch(r"[a-f0-9]{12,128}", revision):
        raise ValueError("Source note requires a hexadecimal input revision")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f"{bookmark_id}--{revision[:12]}.md"
    desired = _render(payload)
    digest = hashlib.sha256(desired).hexdigest()
    if target.exists():
        if target.read_bytes() != desired:
            raise NoteConflictError(f"Source note identity already exists: {target.name}")
        return NoteReceipt(target, False, digest, len(desired))

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=destination,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(desired)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.link(temporary_path, target)
            created = True
        except FileExistsError:
            if target.read_bytes() != desired:
                raise NoteConflictError(f"Source note identity already exists: {target.name}")
            created = False
        _fsync_directory(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return NoteReceipt(target, created, digest, len(desired))
