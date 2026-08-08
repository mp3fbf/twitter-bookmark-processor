"""Deterministic external effects for bookmark automation."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urljoin, urlparse


@dataclass(frozen=True)
class TelegramMessage:
    text: str
    reply_markup: dict[str, Any] | None = None


@dataclass(frozen=True)
class TelegramReceipt:
    method: str
    chat_id: str
    message_id: int
    delivered_path: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None


class ExternalEffectError(RuntimeError):
    """An expected external-effect failure with retry semantics."""

    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class VideoUrlUnavailable(ExternalEffectError):
    def __init__(self) -> None:
        super().__init__(
            "Bird payload reports video but does not contain media[].videoUrl yet",
            code="video_url_unavailable",
            retryable=True,
        )


class VideoURLRejected(ExternalEffectError):
    def __init__(self) -> None:
        super().__init__(
            "Bird video URL is not an approved X media CDN target",
            code="video_url_rejected",
            retryable=False,
        )


class DownloadLimitExceeded(ExternalEffectError):
    def __init__(self, *, limit: int) -> None:
        super().__init__(
            f"Native video exceeds configured download limit ({limit} bytes)",
            code="video_download_too_large",
            retryable=False,
        )


class TelegramFileTooLarge(ExternalEffectError):
    def __init__(self, *, size: int, limit: int) -> None:
        super().__init__(
            f"Telegram document is {size} bytes; Bot API limit is {limit} bytes",
            code="telegram_file_too_large",
            retryable=False,
        )
        self.size = size
        self.limit = limit


@dataclass(frozen=True)
class VideoReceipt:
    path: Path
    source_url: str
    size_bytes: int
    sha256: str


def _default_http_post(url: str, **kwargs: Any) -> Any:
    import httpx

    return httpx.post(url, **kwargs)


class TelegramClient:
    """Small Bot API client whose credentials come only from args or env."""

    def __init__(
        self,
        *,
        token: str | None = None,
        chat_id: str | None = None,
        environ: Mapping[str, str] | None = None,
        http_post: Callable[..., Any] | None = None,
        timeout: float = 20.0,
        upload_timeout: float = 180.0,
        max_document_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        source = os.environ if environ is None else environ
        self.token = token or source.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = str(chat_id or source.get("TELEGRAM_CHAT_ID", ""))
        if not self.token or not self.chat_id:
            raise ValueError("Telegram token and chat_id are required via arguments or environment")
        self.http_post = http_post or _default_http_post
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.max_document_bytes = max_document_bytes

    def _endpoint(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{method}"

    def _post(self, method: str, **kwargs: Any) -> Any:
        try:
            return self.http_post(self._endpoint(method), **kwargs)
        except Exception:
            raise ExternalEffectError(
                "Telegram transport request failed",
                code="telegram_transport_failed",
                retryable=True,
            ) from None

    @staticmethod
    def _result(response: Any) -> Mapping[str, Any]:
        try:
            response.raise_for_status()
        except Exception:
            raise ExternalEffectError(
                "Telegram HTTP request failed",
                code="telegram_http_failed",
                retryable=True,
            ) from None
        try:
            payload = response.json()
        except Exception:
            raise ExternalEffectError(
                "Telegram returned an invalid response",
                code="telegram_invalid_response",
                retryable=True,
            ) from None
        if payload.get("ok") is not True:
            raise ExternalEffectError(
                "Telegram Bot API rejected the request",
                code="telegram_rejected",
                retryable=True,
            )
        return payload.get("result") or {}

    def send_message(self, message: TelegramMessage) -> TelegramReceipt:
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": message.text,
            "disable_web_page_preview": True,
        }
        if message.reply_markup is not None:
            payload["reply_markup"] = message.reply_markup
        response = self._post(
            "sendMessage",
            json=payload,
            timeout=self.timeout,
        )
        result = self._result(response)
        return TelegramReceipt(
            method="sendMessage",
            chat_id=self.chat_id,
            message_id=int(result["message_id"]),
        )

    def send_document(
        self,
        path: str | Path,
        *,
        transcoder: Callable[[Path, Path, int], Path] | None = None,
    ) -> TelegramReceipt:
        source = Path(path)
        size = source.stat().st_size
        if size > self.max_document_bytes:
            if transcoder is None:
                raise TelegramFileTooLarge(size=size, limit=self.max_document_bytes)
            target = source.with_name(f"{source.stem}.telegram{source.suffix}")
            delivered = Path(transcoder(source, target, self.max_document_bytes))
            if delivered.resolve() == source.resolve():
                raise ValueError("transcoder must preserve the original and return a separate copy")
            source = delivered
            size = source.stat().st_size
            if size > self.max_document_bytes:
                raise TelegramFileTooLarge(size=size, limit=self.max_document_bytes)
        digest = hashlib.sha256()
        with source.open("rb") as source_for_hash:
            for chunk in iter(lambda: source_for_hash.read(1024 * 1024), b""):
                digest.update(chunk)
        with source.open("rb") as handle:
            response = self._post(
                "sendDocument",
                data={"chat_id": self.chat_id},
                files={"document": (source.name, handle, "video/mp4")},
                timeout=self.upload_timeout,
            )
        result = self._result(response)
        return TelegramReceipt(
            method="sendDocument",
            chat_id=self.chat_id,
            message_id=int(result["message_id"]),
            delivered_path=str(source),
            size_bytes=size,
            sha256=digest.hexdigest(),
        )


def find_native_video_url(payload: Mapping[str, Any]) -> str:
    """Return the first Bird video URL, including nested quoted tweets."""

    rejected = False

    def approved(candidate: str) -> bool:
        parsed = urlparse(candidate)
        hostname = (parsed.hostname or "").rstrip(".").lower()
        approved_host = hostname == "video.twimg.com" or hostname.endswith(
            ".video.twimg.com"
        )
        return (
            parsed.scheme == "https"
            and approved_host
            and parsed.username is None
            and parsed.password is None
        )

    def walk(value: Any) -> str | None:
        nonlocal rejected
        if isinstance(value, Mapping):
            for key in ("videoUrl", "video_url"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    if approved(candidate):
                        return candidate
                    rejected = True
            for nested in value.values():
                found = walk(nested)
                if found:
                    return found
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for nested in value:
                found = walk(nested)
                if found:
                    return found
        return None

    found = walk(payload)
    if found is None:
        if rejected:
            raise VideoURLRejected()
        raise VideoUrlUnavailable()
    return found


def _require_approved_video_url(url: str) -> None:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not (
        parsed.scheme == "https"
        and (hostname == "video.twimg.com" or hostname.endswith(".video.twimg.com"))
        and parsed.username is None
        and parsed.password is None
    ):
        raise VideoURLRejected()


def _sanitized_video_url(url: str) -> str:
    return urlparse(url)._replace(query="", fragment="").geturl()


def download_native_video(
    payload: Mapping[str, Any],
    destination_dir: str | Path,
    *,
    http: Any | None = None,
    max_bytes: int = 500 * 1024 * 1024,
    timeout: float = 120.0,
    max_redirects: int = 5,
) -> VideoReceipt:
    """Archive a native X video immutably under a content-addressed path."""
    tweet_id = str(payload.get("id") or "").strip()
    if not tweet_id or not tweet_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("bookmark payload requires a safe, non-empty id")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    source_url = find_native_video_url(payload)
    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    partial: Path | None = None
    target: Path | None = None
    digest = hashlib.sha256()
    total = 0
    owns_http = http is None
    if http is None:
        try:
            import httpx

            http = httpx.Client(follow_redirects=False, trust_env=False)
        except Exception:
            raise ExternalEffectError(
                "Native video download failed",
                code="video_download_failed",
                retryable=True,
            ) from None
    try:
        current_url = source_url
        redirect_codes = {301, 302, 303, 307, 308}
        for redirect_count in range(max_redirects + 1):
            _require_approved_video_url(current_url)
            with http.stream("GET", current_url, timeout=timeout) as response:
                effective_url = getattr(response, "url", None)
                if effective_url is not None:
                    _require_approved_video_url(str(effective_url))
                if getattr(response, "status_code", 200) in redirect_codes:
                    location = getattr(response, "headers", {}).get("location")
                    if not location:
                        raise ExternalEffectError(
                            "X media redirect omitted Location header",
                            code="video_invalid_redirect",
                            retryable=False,
                        )
                    if redirect_count == max_redirects:
                        raise ExternalEffectError(
                            "X media exceeded redirect limit",
                            code="video_too_many_redirects",
                            retryable=False,
                        )
                    current_url = urljoin(current_url, str(location))
                    continue
                response.raise_for_status()
                headers = getattr(response, "headers", {})
                content_type = str(headers.get("content-type") or "").split(";", 1)[0]
                content_type = content_type.strip().lower()
                if content_type and content_type not in {
                    "application/octet-stream",
                    "binary/octet-stream",
                    "video/mp4",
                }:
                    raise ExternalEffectError(
                        "X media returned an unsupported Content-Type",
                        code="video_content_type_rejected",
                        retryable=False,
                    )
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=destination,
                    prefix=f".{tweet_id}.",
                    suffix=".mp4.part",
                    delete=False,
                ) as output:
                    partial = Path(output.name)
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            raise DownloadLimitExceeded(limit=max_bytes)
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                break
        sha256 = digest.hexdigest()
        if partial is None:
            raise ExternalEffectError(
                "Native video download did not produce an artifact",
                code="video_download_failed",
                retryable=True,
            )
        target = destination / f"{tweet_id}--{sha256}.mp4"
        try:
            os.link(partial, target)
        except FileExistsError:
            existing_digest = hashlib.sha256()
            existing_size = 0
            with target.open("rb") as existing:
                for chunk in iter(lambda: existing.read(1024 * 1024), b""):
                    existing_size += len(chunk)
                    existing_digest.update(chunk)
            if existing_size != total or existing_digest.hexdigest() != sha256:
                raise ExternalEffectError(
                    "Native video archive contains a conflicting artifact",
                    code="video_archive_conflict",
                    retryable=False,
                )
        partial.unlink()
        partial = None
        directory_fd = os.open(
            destination,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except ExternalEffectError:
        if partial is not None:
            partial.unlink(missing_ok=True)
        raise
    except Exception:
        if partial is not None:
            partial.unlink(missing_ok=True)
        raise ExternalEffectError(
            "Native video download failed",
            code="video_download_failed",
            retryable=True,
        ) from None
    finally:
        if owns_http:
            try:
                http.close()
            except Exception:
                # Download durability must not be reversed by client cleanup, and
                # transport exceptions may contain the signed media URL.
                pass
    return VideoReceipt(
        # ``target`` is assigned only after a complete, fsynced download.
        path=target,
        source_url=_sanitized_video_url(source_url),
        size_bytes=total,
        sha256=sha256,
    )


def transcode_video_for_telegram(
    source: Path,
    target: Path,
    max_bytes: int,
    *,
    runner: Callable[..., Any] = subprocess.run,
    timeout: float = 600.0,
    ffmpeg_path: str | Path = "/usr/bin/ffmpeg",
) -> Path:
    """Create a Telegram-sized copy with ffmpeg; the source is never modified."""
    source = Path(source)
    target = Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("ffmpeg target must differ from source")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    ffmpeg_binary = Path(ffmpeg_path)
    if not ffmpeg_binary.is_absolute():
        raise ValueError("ffmpeg_path must be absolute")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.part")
    command = [
        str(ffmpeg_binary),
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-protocol_whitelist",
        "file,pipe",
        "-f",
        "mp4",
        "-i",
        str(source),
        "-map_metadata",
        "-1",
        "-vf",
        "scale=-2:720:force_original_aspect_ratio=decrease",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-movflags",
        "+faststart",
        "-fs",
        str(max_bytes),
        "-f",
        "mp4",
        str(partial),
    ]
    try:
        runner(
            command,
            check=True,
            timeout=timeout,
            capture_output=True,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
        )
        if not partial.is_file():
            raise ExternalEffectError(
                "ffmpeg completed without producing an output file",
                code="ffmpeg_output_missing",
                retryable=False,
            )
        os.replace(partial, target)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return target


def build_bookmark_notification(payload: Mapping[str, Any]) -> TelegramMessage:
    """Build the immediate prompt without invoking inference."""
    tweet_id = str(payload.get("id") or "").strip()
    if not tweet_id:
        raise ValueError("bookmark payload requires a non-empty id")
    author = payload.get("author")
    nested_username = author.get("username") if isinstance(author, Mapping) else None
    raw_username = str(payload.get("username") or nested_username or "").lstrip("@")[:64]
    tweet_text = " ".join(str(payload.get("text") or "(sem texto)").split())
    tweet_url = (
        f"https://x.com/{raw_username}/status/{tweet_id}"
        if raw_username
        else f"https://x.com/i/web/status/{tweet_id}"
    )
    attribution = f"@{raw_username}" if raw_username else "X"
    prefix = f"🔖 Novo bookmark\n\n{attribution}: "
    suffix = f"\n\n{tweet_url}\n\nQuer atuar nele agora?"
    available = 4_096 - len(prefix) - len(suffix)
    if len(tweet_text) > available:
        tweet_text = f"{tweet_text[: max(0, available - 1)].rstrip()}…"
    text = f"{prefix}{tweet_text}{suffix}"
    actions = (
        ("▶️ Atuar agora", "act"),
        ("📌 Guardar", "keep"),
        ("⏰ Adiar", "defer"),
        ("🗑️ Ignorar", "skip"),
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": label, "callback_data": f"tw:{action}:{tweet_id}"}
                for label, action in actions[index : index + 2]
            ]
            for index in range(0, len(actions), 2)
        ]
    }
    return TelegramMessage(text=text, reply_markup=keyboard)
