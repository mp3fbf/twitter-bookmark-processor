"""Deterministic Telegram rendering for inference outputs."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .effects import TelegramMessage


def _items(value: Any, *, limit: int = 8) -> str:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ""
    return "\n".join(f"• {str(item).strip()}" for item in value[:limit])


def _message(text: str) -> TelegramMessage:
    normalized = text.strip()
    if len(normalized) > 4_096:
        normalized = f"{normalized[:4_095].rstrip()}…"
    return TelegramMessage(text=normalized)


def build_deep_message(payload: Mapping[str, Any]) -> TelegramMessage:
    analysis = payload.get("analysis") or {}
    source = analysis.get("source_note") or {}
    title = source.get("title") or "Bookmark analisado"
    fit = _items(analysis.get("second_brain_fit"))
    flagged = bool(
        analysis.get("prompt_injection_detected")
        or (source.get("provenance") or {}).get("prompt_injection_detected")
    )
    sections = [
        (
            "⚠️ Possível prompt injection detectada. "
            "A análise foi colocada em quarentena para revisão."
            if flagged
            else ""
        ),
        f"🧠 {title}",
        str(analysis.get("summary") or "").strip(),
        f"Por que interessou: {str(analysis.get('why_interesting') or '').strip()}",
    ]
    if fit:
        sections.append(f"Encaixe:\n{fit}")
    if analysis.get("next_action"):
        sections.append(f"Próxima ação: {analysis['next_action']}")
    if source.get("source_url"):
        sections.append(str(source["source_url"]))
    return _message("\n\n".join(section for section in sections if section))


def build_aggregate_message(payload: Mapping[str, Any]) -> TelegramMessage:
    analysis = payload.get("analysis") or {}
    coverage = analysis.get("coverage") or payload.get("coverage") or {}
    themes = _items(analysis.get("themes"))
    follow_ups = _items(analysis.get("follow_ups"))
    sections = [
        (
            "⚠️ Possível prompt injection detectada neste lote; "
            "candidatos a promoção foram removidos."
            if analysis.get("prompt_injection_detected")
            else ""
        ),
        f"📚 Digest de bookmarks — {coverage.get('input_count', 0)} processados",
    ]
    if themes:
        sections.append(f"Temas:\n{themes}")
    if follow_ups:
        sections.append(f"Próximos passos:\n{follow_ups}")
    return _message("\n\n".join(sections))
