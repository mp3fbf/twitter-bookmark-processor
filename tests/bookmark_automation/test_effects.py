"""External-effect contracts for bookmark automation."""

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bookmark_automation.effects import (
    DownloadLimitExceeded,
    ExternalEffectError,
    TelegramClient,
    TelegramFileTooLarge,
    TelegramMessage,
    VideoUrlUnavailable,
    build_bookmark_notification,
    download_native_video,
    find_native_video_url,
    transcode_video_for_telegram,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeStreamResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def __enter__(self) -> "FakeStreamResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self) -> Any:
        yield from self.chunks


class FakeStreamingHttp:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def stream(self, method: str, url: str, **kwargs: Any) -> FakeStreamResponse:
        self.calls.append((method, url, kwargs))
        return FakeStreamResponse(self.chunks)


def test_notification_is_deterministic_and_offers_all_four_actions() -> None:
    payload = {
        "id": "1900000000000000001",
        "username": "karpathy",
        "text": "Software is changing again.",
    }

    message = build_bookmark_notification(payload)

    assert message.text == (
        "🔖 Novo bookmark\n\n"
        "@karpathy: Software is changing again.\n\n"
        "https://x.com/karpathy/status/1900000000000000001\n\n"
        "Quer atuar nele agora?"
    )
    assert [
        button["callback_data"]
        for row in message.reply_markup["inline_keyboard"]
        for button in row
    ] == [
        "tw:act:1900000000000000001",
        "tw:keep:1900000000000000001",
        "tw:defer:1900000000000000001",
        "tw:skip:1900000000000000001",
    ]


def test_notification_truncates_long_text_below_telegram_limit() -> None:
    message = build_bookmark_notification(
        {
            "id": "1900000000000000015",
            "username": "longform",
            "text": "x" * 5_000,
        }
    )

    assert len(message.text) <= 4_096
    assert "…\n\nhttps://x.com/longform/status/1900000000000000015" in message.text
    assert message.text.endswith("Quer atuar nele agora?")


def test_notification_without_username_uses_stable_x_status_url() -> None:
    message = build_bookmark_notification(
        {"id": "1900000000000000016", "text": "No username in incremental payload"}
    )

    assert "https://x.com/i/web/status/1900000000000000016" in message.text
    assert "/unknown/status/" not in message.text


def test_notification_reads_nested_bird_author_username() -> None:
    message = build_bookmark_notification(
        {
            "id": "1900000000000000017",
            "author": {"username": "karpathy", "name": "Andrej Karpathy"},
            "text": "Bird keeps the author nested.",
        }
    )

    assert "@karpathy: Bird keeps the author nested." in message.text
    assert "https://x.com/karpathy/status/1900000000000000017" in message.text


def test_telegram_message_uses_explicit_credentials_and_returns_receipt() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        calls.append((url, kwargs))
        return FakeResponse({"ok": True, "result": {"message_id": 42}})

    client = TelegramClient(token="test-token", chat_id="123", http_post=post)
    receipt = client.send_message(
        build_bookmark_notification(
            {"id": "1900000000000000002", "username": "alice", "text": "Read me"}
        )
    )

    assert calls[0][0] == "https://api.telegram.org/bottest-token/sendMessage"
    assert calls[0][1]["json"]["chat_id"] == "123"
    assert calls[0][1]["json"]["reply_markup"]["inline_keyboard"]
    assert receipt.method == "sendMessage"
    assert receipt.chat_id == "123"
    assert receipt.message_id == 42


def test_telegram_message_omits_reply_markup_when_there_are_no_buttons() -> None:
    calls: list[dict[str, Any]] = []

    def post(_url: str, **kwargs: Any) -> FakeResponse:
        calls.append(kwargs)
        return FakeResponse({"ok": True, "result": {"message_id": 43}})

    client = TelegramClient(token="test-token", chat_id="123", http_post=post)

    client.send_message(TelegramMessage(text="Analysis ready"))

    assert "reply_markup" not in calls[0]["json"]


def test_telegram_credentials_can_come_from_injected_environment() -> None:
    calls: list[str] = []

    def post(url: str, **_kwargs: Any) -> FakeResponse:
        calls.append(url)
        return FakeResponse({"ok": True, "result": {"message_id": 45}})

    client = TelegramClient(
        environ={"TELEGRAM_BOT_TOKEN": "env-token", "TELEGRAM_CHAT_ID": "456"},
        http_post=post,
    )
    receipt = client.send_message(
        build_bookmark_notification(
            {"id": "1900000000000000011", "username": "bob", "text": "Env"}
        )
    )

    assert calls == ["https://api.telegram.org/botenv-token/sendMessage"]
    assert receipt.chat_id == "456"


def test_telegram_transport_error_never_exposes_token_or_endpoint() -> None:
    token = "super-secret-bot-token"
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    def post(_url: str, **_kwargs: Any) -> FakeResponse:
        raise RuntimeError(f"connection failed for {endpoint}")

    client = TelegramClient(token=token, chat_id="123", http_post=post)

    with pytest.raises(ExternalEffectError) as captured:
        client.send_message(
            build_bookmark_notification(
                {"id": "1900000000000000013", "username": "alice", "text": "Secret"}
            )
        )

    rendered = str(captured.value)
    assert captured.value.code == "telegram_transport_failed"
    assert token not in rendered
    assert endpoint not in rendered


def test_telegram_http_status_error_never_exposes_token_or_endpoint() -> None:
    token = "another-secret-token"
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    class FailingStatusResponse(FakeResponse):
        def raise_for_status(self) -> None:
            raise RuntimeError(f"401 from {endpoint}")

    client = TelegramClient(
        token=token,
        chat_id="123",
        http_post=lambda *_args, **_kwargs: FailingStatusResponse({}),
    )

    with pytest.raises(ExternalEffectError) as captured:
        client.send_message(
            build_bookmark_notification(
                {"id": "1900000000000000014", "username": "alice", "text": "Secret"}
            )
        )

    rendered = str(captured.value)
    assert captured.value.code == "telegram_http_failed"
    assert token not in rendered
    assert endpoint not in rendered


def test_video_url_is_found_recursively_inside_quoted_tweet_media() -> None:
    payload = {
        "id": "1900000000000000003",
        "author": {"username": "bookmark_author", "name": "Bookmark Author"},
        "quotedTweet": {
            "id": "1900000000000000002",
            "text": "Bird quote payload",
            "author": {"username": "video_author", "name": "Video Author"},
            "media": [
                {
                    "type": "video",
                    "url": "https://pbs.twimg.com/media/thumb.jpg",
                    "previewUrl": "https://pbs.twimg.com/media/thumb.jpg:small",
                    "videoUrl": "https://video.twimg.com/ext_tw_video/native.mp4",
                    "durationMs": 12_345,
                }
            ]
        },
    }

    assert find_native_video_url(payload) == (
        "https://video.twimg.com/ext_tw_video/native.mp4"
    )


def test_video_without_download_url_is_a_typed_retryable_failure() -> None:
    with pytest.raises(VideoUrlUnavailable) as captured:
        find_native_video_url(
            {
                "id": "1900000000000000004",
                "hasVideo": True,
                "media": [{"thumbnailUrl": "https://pbs.twimg.com/thumb.jpg"}],
            }
        )

    assert captured.value.code == "video_url_unavailable"
    assert captured.value.retryable is True


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/private.mp4",
        "https://example.com/not-x-media.mp4",
        "https://user:password@video.twimg.com/native.mp4",
    ],
)
def test_video_url_rejects_non_x_cdn_targets(url: str) -> None:
    with pytest.raises(ExternalEffectError) as captured:
        find_native_video_url({"media": [{"videoUrl": url}]})

    assert captured.value.code == "video_url_rejected"
    assert captured.value.retryable is False


def test_video_download_streams_atomically_and_returns_integrity_receipt(tmp_path: Path) -> None:
    body = b"video-bytes-in-two-chunks"
    http = FakeStreamingHttp([body[:9], body[9:]])
    payload = {
        "id": "1900000000000000005",
        "media": [{"video_url": "https://video.twimg.com/native.mp4"}],
    }

    receipt = download_native_video(payload, tmp_path, http=http, max_bytes=1024)

    sha256 = hashlib.sha256(body).hexdigest()
    target = tmp_path / f"1900000000000000005--{sha256}.mp4"
    assert target.read_bytes() == body
    assert not list(tmp_path.glob(".1900000000000000005.*.mp4.part"))
    assert receipt.path == target
    assert receipt.size_bytes == len(body)
    assert receipt.sha256 == sha256
    assert http.calls[0][:2] == ("GET", "https://video.twimg.com/native.mp4")


def test_video_download_never_overwrites_a_prior_revision(tmp_path: Path) -> None:
    payload = {
        "id": "1900000000000000028",
        "media": [{"video_url": "https://video.twimg.com/native.mp4"}],
    }

    first = download_native_video(
        payload,
        tmp_path,
        http=FakeStreamingHttp([b"first-revision"]),
    )
    second = download_native_video(
        payload,
        tmp_path,
        http=FakeStreamingHttp([b"second-revision"]),
    )

    assert first.path != second.path
    assert first.path.read_bytes() == b"first-revision"
    assert second.path.read_bytes() == b"second-revision"
    assert sorted(tmp_path.glob("1900000000000000028--*.mp4")) == sorted(
        [first.path, second.path]
    )


def test_video_download_reuses_identical_content_without_replacing_it(
    tmp_path: Path,
) -> None:
    body = b"same-content"
    payload = {
        "id": "1900000000000000029",
        "media": [{"video_url": "https://video.twimg.com/native.mp4"}],
    }

    first = download_native_video(
        payload,
        tmp_path,
        http=FakeStreamingHttp([body]),
    )
    second = download_native_video(
        payload,
        tmp_path,
        http=FakeStreamingHttp([body]),
    )

    assert first == second
    assert first.path.read_bytes() == body
    assert list(tmp_path.glob("1900000000000000029--*.mp4")) == [first.path]


def test_video_download_limit_removes_partial_file_and_reports_nonretryable(
    tmp_path: Path,
) -> None:
    http = FakeStreamingHttp([b"12345", b"67890"])
    payload = {
        "id": "1900000000000000008",
        "media": [{"videoUrl": "https://video.twimg.com/native.mp4"}],
    }

    with pytest.raises(DownloadLimitExceeded) as captured:
        download_native_video(payload, tmp_path, http=http, max_bytes=8)

    assert captured.value.code == "video_download_too_large"
    assert captured.value.retryable is False
    assert not list(tmp_path.glob("1900000000000000008--*.mp4"))
    assert not list(tmp_path.glob(".1900000000000000008.*.mp4.part"))


def test_video_download_rejects_html_error_body_before_archiving(tmp_path: Path) -> None:
    class HTMLResponse(FakeStreamResponse):
        headers = {"content-type": "text/html; charset=utf-8"}

    class HTMLHttp:
        def stream(self, *_args: Any, **_kwargs: Any) -> HTMLResponse:
            return HTMLResponse([b"<html>not a video</html>"])

    with pytest.raises(ExternalEffectError) as captured:
        download_native_video(
            {
                "id": "1900000000000000030",
                "media": [{"videoUrl": "https://video.twimg.com/native.mp4"}],
            },
            tmp_path,
            http=HTMLHttp(),
        )

    assert captured.value.code == "video_content_type_rejected"
    assert captured.value.retryable is False
    assert not list(tmp_path.iterdir())


def test_default_video_download_rejects_redirect_off_x_media_cdn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_url = "https://video.twimg.com/ext_tw_video/native.mp4?token=signed"

    class RedirectResponse:
        status_code = 302
        headers = {"location": "https://attacker.example/stolen.mp4"}

        def __enter__(self) -> "RedirectResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> Any:
            pytest.fail("redirect body must not be downloaded")

    class NoRedirectClient:
        instances: list["NoRedirectClient"] = []

        def __init__(self, **kwargs: Any) -> None:
            self.options = kwargs
            self.calls: list[tuple[str, str, dict[str, Any]]] = []
            self.closed = False
            self.__class__.instances.append(self)

        def stream(self, method: str, url: str, **kwargs: Any) -> RedirectResponse:
            self.calls.append((method, url, kwargs))
            return RedirectResponse()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=NoRedirectClient))

    with pytest.raises(ExternalEffectError) as captured:
        download_native_video(
            {
                "id": "1900000000000000018",
                "media": [{"type": "video", "videoUrl": source_url}],
            },
            tmp_path,
        )

    client = NoRedirectClient.instances[0]
    assert captured.value.code == "video_url_rejected"
    assert client.options == {"follow_redirects": False, "trust_env": False}
    assert client.calls == [("GET", source_url, {"timeout": 120.0})]
    assert client.closed is True
    assert not list(tmp_path.glob(".1900000000000000018.*.mp4.part"))
    assert not list(tmp_path.glob("1900000000000000018--*.mp4"))


def test_video_transport_error_sanitizes_signed_url_and_cleans_partial(
    tmp_path: Path,
) -> None:
    signed_url = "https://video.twimg.com/native.mp4?token=secret-signature"

    class FailingStream:
        def __enter__(self) -> Any:
            raise RuntimeError(f"connection reset while requesting {signed_url}")

        def __exit__(self, *_args: Any) -> None:
            return None

    class FailingHttp:
        def stream(self, _method: str, _url: str, **_kwargs: Any) -> FailingStream:
            return FailingStream()

    with pytest.raises(ExternalEffectError) as captured:
        download_native_video(
            {
                "id": "1900000000000000019",
                "media": [{"type": "video", "videoUrl": signed_url}],
            },
            tmp_path,
            http=FailingHttp(),
        )

    rendered = str(captured.value)
    assert captured.value.code == "video_download_failed"
    assert captured.value.retryable is True
    assert signed_url not in rendered
    assert "secret-signature" not in rendered
    assert not list(tmp_path.glob(".1900000000000000019.*.mp4.part"))
    assert not list(tmp_path.glob("1900000000000000019--*.mp4"))


def test_video_receipt_does_not_persist_signed_query_parameters(tmp_path: Path) -> None:
    signed_url = (
        "https://video.twimg.com/ext_tw_video/native.mp4"
        "?tag=12&token=secret-signature"
    )

    receipt = download_native_video(
        {
            "id": "1900000000000000023",
            "media": [{"type": "video", "videoUrl": signed_url}],
        },
        tmp_path,
        http=FakeStreamingHttp([b"video"]),
    )

    assert receipt.source_url == "https://video.twimg.com/ext_tw_video/native.mp4"
    assert "secret-signature" not in repr(receipt)


def test_default_video_client_close_failure_cannot_leak_signed_url_or_lose_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_url = "https://video.twimg.com/native.mp4?token=secret-signature"

    class ClosingClient:
        def __init__(self, **_kwargs: Any) -> None:
            return None

        def stream(self, _method: str, _url: str, **_kwargs: Any) -> FakeStreamResponse:
            return FakeStreamResponse([b"video"])

        def close(self) -> None:
            raise RuntimeError(f"failed to close request for {signed_url}")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=ClosingClient))

    receipt = download_native_video(
        {
            "id": "1900000000000000026",
            "media": [{"type": "video", "videoUrl": signed_url}],
        },
        tmp_path,
    )

    assert receipt.path.read_bytes() == b"video"
    assert "secret-signature" not in repr(receipt)


def test_default_video_client_creation_failure_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_url = "https://video.twimg.com/native.mp4?token=secret-signature"

    class FailingClient:
        def __init__(self, **_kwargs: Any) -> None:
            raise RuntimeError(f"proxy refused {signed_url}")

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(Client=FailingClient))

    with pytest.raises(ExternalEffectError) as captured:
        download_native_video(
            {
                "id": "1900000000000000027",
                "media": [{"type": "video", "videoUrl": signed_url}],
            },
            tmp_path,
        )

    assert captured.value.code == "video_download_failed"
    assert signed_url not in str(captured.value)
    assert "secret-signature" not in str(captured.value)


def test_telegram_sends_small_video_as_document_with_structured_receipt(
    tmp_path: Path,
) -> None:
    source = tmp_path / "1900000000000000006.mp4"
    source.write_bytes(b"small-video")
    calls: list[dict[str, Any]] = []

    def post(_url: str, **kwargs: Any) -> FakeResponse:
        filename, handle, media_type = kwargs["files"]["document"]
        calls.append(
            {
                "filename": filename,
                "body": handle.read(),
                "media_type": media_type,
                "data": kwargs["data"],
            }
        )
        return FakeResponse({"ok": True, "result": {"message_id": 43}})

    client = TelegramClient(token="test-token", chat_id="123", http_post=post)
    receipt = client.send_document(source)

    assert calls == [
        {
            "filename": source.name,
            "body": b"small-video",
            "media_type": "video/mp4",
            "data": {"chat_id": "123"},
        }
    ]
    assert receipt.method == "sendDocument"
    assert receipt.delivered_path == str(source)
    assert receipt.size_bytes == len(b"small-video")
    assert receipt.sha256 == hashlib.sha256(b"small-video").hexdigest()


def test_oversized_video_is_transcoded_to_separate_copy_before_telegram(
    tmp_path: Path,
) -> None:
    original = tmp_path / "1900000000000000007.mp4"
    original_bytes = b"original-is-too-large"
    original.write_bytes(original_bytes)
    transcoder_calls: list[tuple[Path, Path, int]] = []

    def transcoder(source: Path, target: Path, limit: int) -> Path:
        transcoder_calls.append((source, target, limit))
        target.write_bytes(b"small")
        return target

    def post(_url: str, **_kwargs: Any) -> FakeResponse:
        return FakeResponse({"ok": True, "result": {"message_id": 44}})

    client = TelegramClient(
        token="test-token",
        chat_id="123",
        http_post=post,
        max_document_bytes=10,
    )
    receipt = client.send_document(original, transcoder=transcoder)

    assert original.read_bytes() == original_bytes
    assert transcoder_calls == [(original, tmp_path / "1900000000000000007.telegram.mp4", 10)]
    assert receipt.delivered_path == str(tmp_path / "1900000000000000007.telegram.mp4")
    assert receipt.size_bytes == 5


def test_ffmpeg_transcoder_is_injectable_and_writes_target_atomically(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    target = tmp_path / "source.telegram.mp4"
    source.write_bytes(b"original")
    commands: list[tuple[list[str], dict[str, Any]]] = []

    def runner(command: list[str], **kwargs: Any) -> Any:
        commands.append((command, kwargs))
        Path(command[-1]).write_bytes(b"compressed")
        return object()

    result = transcode_video_for_telegram(
        source,
        target,
        50_000_000,
        runner=runner,
        timeout=30.0,
    )

    assert result == target
    assert target.read_bytes() == b"compressed"
    assert not (tmp_path / "source.telegram.mp4.part").exists()
    command, kwargs = commands[0]
    assert command[0] == "/usr/bin/ffmpeg"
    assert command[command.index("-protocol_whitelist") + 1] == "file,pipe"
    input_index = command.index("-i")
    assert command[input_index - 2 : input_index] == ["-f", "mp4"]
    assert command[command.index("-fs") + 1] == "50000000"
    assert command[-1] == str(tmp_path / "source.telegram.mp4.part")
    assert kwargs == {
        "check": True,
        "timeout": 30.0,
        "capture_output": True,
        "env": {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    }


def test_transcoded_copy_still_over_limit_fails_clearly_and_keeps_original(
    tmp_path: Path,
) -> None:
    original = tmp_path / "1900000000000000009.mp4"
    original.write_bytes(b"original-too-large")

    def transcoder(_source: Path, target: Path, _limit: int) -> Path:
        target.write_bytes(b"still-too-large")
        return target

    client = TelegramClient(
        token="test-token",
        chat_id="123",
        http_post=lambda *_args, **_kwargs: pytest.fail("network must not be called"),
        max_document_bytes=5,
    )

    with pytest.raises(TelegramFileTooLarge) as captured:
        client.send_document(original, transcoder=transcoder)

    assert captured.value.code == "telegram_file_too_large"
    assert original.read_bytes() == b"original-too-large"
