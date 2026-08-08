"""Production maintenance, alerting, and retention for bookmark automation."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from .effects import TelegramMessage
from .store import AutomationStore


class TelegramSender(Protocol):
    def send_message(self, message: TelegramMessage, **kwargs: Any) -> Any: ...


class MaintenanceWorker:
    """Run bounded operational checks independently from content workers."""

    def __init__(
        self,
        *,
        store: AutomationStore,
        telegram: TelegramSender,
        video_dir: str | Path,
        retention_days: int = 30,
        min_free_bytes: int = 1024 * 1024 * 1024,
        disk_usage: Callable[[Path], Any] = shutil.disk_usage,
    ) -> None:
        if retention_days < 0:
            raise ValueError("retention_days must be non-negative")
        if min_free_bytes <= 0:
            raise ValueError("min_free_bytes must be positive")
        self.store = store
        self.telegram = telegram
        self.video_dir = Path(video_dir)
        self.retention_days = retention_days
        self.min_free_bytes = min_free_bytes
        self.disk_usage = disk_usage

    def _prune_videos(self, *, now: datetime) -> tuple[int, int]:
        completed = self.store.completed_video_bookmark_ids()
        cutoff = now - timedelta(days=self.retention_days)
        removed = 0
        bytes_removed = 0
        if not self.video_dir.is_dir():
            return removed, bytes_removed
        for path in sorted(self.video_dir.glob("*.mp4")):
            if path.is_symlink() or not path.is_file():
                continue
            bookmark_id = path.stem.removesuffix(".telegram")
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime, tz=now.tzinfo)
            if bookmark_id not in completed or modified >= cutoff:
                continue
            path.unlink()
            removed += 1
            bytes_removed += stat.st_size
        return removed, bytes_removed

    def run(self, *, now: datetime) -> dict[str, int | bool]:
        checkpoint = self.store.checkpoint_wal()
        videos_removed, bytes_removed = self._prune_videos(now=now)
        wal_path = Path(f"{self.store.path}-wal")
        database_bytes = self.store.path.stat().st_size
        wal_bytes = wal_path.stat().st_size if wal_path.is_file() else 0
        usage = self.disk_usage(self.store.path.parent)
        free_bytes = int(usage.free)
        healthy = free_bytes >= self.min_free_bytes
        disk_alerts = 0
        prior_disk_state = self.store.health_state("disk_low")
        if not healthy and prior_disk_state != "active":
            self.telegram.send_message(
                TelegramMessage(
                    text=(
                        "🚨 Bookmark automation: disco baixo\n"
                        f"Livres: {free_bytes} bytes; mínimo operacional: "
                        f"{self.min_free_bytes} bytes. Workers permanecerão bloqueados."
                    )
                )
            )
            self.store.set_health_state("disk_low", "active")
            disk_alerts = 1
        elif healthy and prior_disk_state == "active":
            self.store.set_health_state("disk_low", "inactive")
        dead_letter_alerts = 0
        for pending in self.store.pending_dead_letter_alerts():
            self.telegram.send_message(
                TelegramMessage(
                    text=(
                        "🚨 Bookmark automation: dead letter\n"
                        f"job {pending['job_id']} · {pending['task_kind']} · "
                        f"{pending['attempt_count']} tentativa(s)\n"
                        "Inspecione com dead-letter-list e reencaminhe somente após corrigir a causa."
                    )
                )
            )
            if self.store.mark_dead_letter_alerted(
                job_id=int(pending["job_id"]),
                attempt_count=int(pending["attempt_count"]),
                now=now,
            ):
                dead_letter_alerts += 1
        committed_effect_alerts = 0
        for pending in self.store.pending_committed_effect_alerts():
            self.telegram.send_message(
                TelegramMessage(
                    text=(
                        "⚠️ Bookmark automation: efeito externo ambíguo\n"
                        f"job {pending['job_id']} · {pending['task_kind']}\n"
                        "Ele não será repetido automaticamente. Confirme o resultado com "
                        "committed-effect-resolve."
                    )
                )
            )
            if self.store.mark_committed_effect_alerted(
                job_id=int(pending["job_id"]),
                attempt_id=int(pending["attempt_id"]),
                now=now,
            ):
                committed_effect_alerts += 1
        return {
            "bytes_removed": bytes_removed,
            "committed_effect_alerts": committed_effect_alerts,
            "database_bytes": database_bytes,
            "dead_letter_alerts": dead_letter_alerts,
            "disk_alerts": disk_alerts,
            "free_bytes": free_bytes,
            "healthy": healthy,
            "videos_removed": videos_removed,
            "wal_bytes": wal_bytes,
            "wal_checkpoint_busy": checkpoint["busy"],
            "wal_checkpointed_frames": checkpoint["checkpointed_frames"],
            "wal_log_frames": checkpoint["log_frames"],
        }
