"""Deterministic article and Second Brain context contracts."""

import json
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

import bookmark_automation.content as content_module
from bookmark_automation.content import (
    ArticleContentError,
    RecallError,
    URLSecurityError,
    fetch_article,
    recall_second_brain,
    select_external_url,
    validate_public_url,
)


class FakeArticleResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.content = body.encode("utf-8")
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_external_article_url_ignores_x_and_media_hosts() -> None:
    payload = {
        "id": "1900000000000000010",
        "urls": [
            {"expanded_url": "https://x.com/alice/status/1900000000000000010"},
            {"url": "https://pbs.twimg.com/media/image.jpg"},
            {"expandedUrl": "https://video.twimg.com/native.mp4"},
            {"unwound_url": "https://example.org/research/agent-memory"},
        ],
    }

    assert select_external_url(payload) == "https://example.org/research/agent-memory"


def test_bird_x_article_without_external_url_is_extracted_without_network() -> None:
    def unexpected_get(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("embedded X Article must not perform an HTTP request")

    article = fetch_article(
        {
            "id": "1900000000000000020",
            "text": "Agent Memory\n\nDurable context matters.",
            "author": {"username": "ada", "name": "Ada Example"},
            "createdAt": "2026-08-08T12:00:00.000Z",
            "article": {
                "title": "Agent Memory",
                "previewText": "Durable context matters.",
            },
        },
        http_get=unexpected_get,
        max_text_chars=50_000,
    )

    expected_url = "https://x.com/ada/status/1900000000000000020"
    assert article.original_url == expected_url
    assert article.final_url == expected_url
    assert article.title == "Agent Memory"
    assert article.author == "Ada Example"
    assert article.published_at == "2026-08-08T12:00:00.000Z"
    assert article.text == "Agent Memory\n\nDurable context matters."
    assert article.truncated is False


def test_bird_json_full_x_article_uses_raw_body_before_preview() -> None:
    article = fetch_article(
        {
            "id": "1900000000000000021",
            "author": {"username": "ada", "name": "Ada Example"},
            "article": {"title": "Agent Memory", "previewText": "Only a preview."},
            "_raw": {
                "article": {
                    "article_results": {
                        "result": {
                            "title": "Agent Memory",
                            "plain_text": "The full embedded article body.",
                        }
                    }
                }
            },
        },
        http_get=lambda *_args, **_kwargs: pytest.fail("network must not be used"),
    )

    assert article.title == "Agent Memory"
    assert article.text == "The full embedded article body."


def test_bird_json_full_x_article_accepts_nested_body_text_shape() -> None:
    article = fetch_article(
        {
            "id": "1900000000000000022",
            "article": {"title": "Nested article"},
            "_raw": {
                "article": {
                    "article_results": {
                        "result": {"body": {"text": "Nested full body."}}
                    }
                }
            },
        },
        http_get=lambda *_args, **_kwargs: pytest.fail("network must not be used"),
    )

    assert article.text == "Nested full body."


def test_bird_json_full_x_article_raw_body_wins_over_top_level_teaser() -> None:
    article = fetch_article(
        {
            "id": "1900000000000000023",
            "text": "A teaser that is not the article title.",
            "article": {
                "title": "Durable agent memory",
                "previewText": "A short preview.",
            },
            "_raw": {
                "article": {
                    "article_results": {
                        "result": {
                            "body": {
                                "text": "The complete article body arrived later."
                            }
                        }
                    }
                }
            },
        },
        http_get=lambda *_args, **_kwargs: pytest.fail("network must not be used"),
    )

    assert article.text == "The complete article body arrived later."
    assert article.truncated is False


def test_embedded_x_article_accepts_body_inside_article_metadata() -> None:
    article = fetch_article(
        {
            "id": "1900000000000000024",
            "article": {
                "title": "Embedded article",
                "body": {"text": "Body shipped with the bookmark payload."},
            },
        },
        http_get=lambda *_args, **_kwargs: pytest.fail("network must not be used"),
    )

    assert article.title == "Embedded article"
    assert article.text == "Body shipped with the bookmark payload."


def test_bird_x_article_title_only_text_falls_back_to_preview_body() -> None:
    article = fetch_article(
        {
            "id": "1900000000000000025",
            "text": "Title only",
            "article": {
                "title": "Title only",
                "previewText": "The available article preview body.",
            },
        },
        http_get=lambda *_args, **_kwargs: pytest.fail("network must not be used"),
    )

    assert article.title == "Title only"
    assert article.text == "The available article preview body."
    assert article.truncated is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/admin",
        "http://10.2.3.4/internal",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/admin",
    ],
)
def test_url_validation_rejects_non_http_and_non_public_targets(url: str) -> None:
    with pytest.raises(URLSecurityError):
        validate_public_url(url)


def test_url_validation_rejects_hostname_that_resolves_to_private_ip() -> None:
    with pytest.raises(URLSecurityError):
        validate_public_url(
            "https://apparently-public.example/article",
            resolver=lambda _host: ["192.168.1.10"],
        )


def test_article_fetch_extracts_deterministic_metadata_and_readable_text() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    html = """
    <html><head>
      <title> Agent Memory — Example </title>
      <meta name="author" content="Ada Example">
      <meta property="article:published_time" content="2026-08-07">
    </head><body>
      <nav>Navigation noise</nav>
      <main><h1>Agent Memory</h1><p>Durable context matters.</p>
      <p>Retrieval should stay deterministic before inference.</p></main>
      <script>steal()</script>
    </body></html>
    """

    def get(url: str, **kwargs: Any) -> FakeArticleResponse:
        calls.append((url, kwargs))
        return FakeArticleResponse(body=html)

    article = fetch_article(
        {"urls": [{"expanded_url": "https://example.org/agent-memory"}]},
        http_get=get,
        resolver=lambda _host: ["93.184.216.34"],
    )

    assert article.original_url == "https://example.org/agent-memory"
    assert article.final_url == article.original_url
    assert article.title == "Agent Memory — Example"
    assert article.author == "Ada Example"
    assert article.published_at == "2026-08-07"
    assert article.text == (
        "Agent Memory\nDurable context matters.\n"
        "Retrieval should stay deterministic before inference."
    )
    assert article.truncated is False
    assert calls == [
        (
            "https://example.org/agent-memory",
            {"follow_redirects": False, "timeout": 20.0},
        )
    ]


def test_article_redirect_to_private_address_is_rejected_before_second_request() -> None:
    calls: list[str] = []

    def get(url: str, **_kwargs: Any) -> FakeArticleResponse:
        calls.append(url)
        return FakeArticleResponse(
            status_code=302,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
        )

    with pytest.raises(URLSecurityError):
        fetch_article(
            {"urls": [{"url": "https://t.co/short"}]},
            http_get=get,
            resolver=lambda _host: ["93.184.216.34"],
        )

    assert calls == ["https://t.co/short"]


def test_short_url_redirect_back_to_x_is_not_treated_as_an_article() -> None:
    calls: list[str] = []

    def get(url: str, **_kwargs: Any) -> FakeArticleResponse:
        calls.append(url)
        return FakeArticleResponse(
            status_code=302,
            headers={"location": "https://x.com/alice/status/1900000000000000012"},
        )

    with pytest.raises(ArticleContentError) as captured:
        fetch_article(
            {"urls": [{"url": "https://t.co/short"}]},
            http_get=get,
            resolver=lambda _host: ["93.184.216.34"],
        )

    assert getattr(captured.value, "code", None) == "article_target_excluded"
    assert calls == ["https://t.co/short"]


def test_article_text_is_bounded_for_inference_input() -> None:
    def get(_url: str, **_kwargs: Any) -> FakeArticleResponse:
        return FakeArticleResponse(body="<main><p>abcdefghijklmnopqrstuvwxyz</p></main>")

    article = fetch_article(
        {"urls": [{"url": "https://example.org/long"}]},
        http_get=get,
        resolver=lambda _host: ["93.184.216.34"],
        max_text_chars=10,
    )

    assert article.text == "abcdefghij"
    assert article.truncated is True


def test_article_rejects_non_text_content_type_before_reading_body() -> None:
    class BinaryResponse:
        status_code = 200
        headers = {"content-type": "application/pdf", "content-length": "8"}

        def raise_for_status(self) -> None:
            return None

        @property
        def content(self) -> bytes:
            pytest.fail("binary body must not be materialized")

    with pytest.raises(ArticleContentError) as captured:
        fetch_article(
            {"urls": [{"url": "https://example.org/report.pdf"}]},
            http_get=lambda *_args, **_kwargs: BinaryResponse(),
            resolver=lambda _host: ["93.184.216.34"],
        )

    assert captured.value.code == "unsupported_content_type"


def test_article_rejects_oversized_content_length_before_reading_body() -> None:
    class OversizedResponse:
        status_code = 200
        headers = {"content-type": "text/html", "content-length": "1001"}

        def raise_for_status(self) -> None:
            return None

        @property
        def content(self) -> bytes:
            pytest.fail("oversized body must not be materialized")

    with pytest.raises(ArticleContentError) as captured:
        fetch_article(
            {"urls": [{"url": "https://example.org/large"}]},
            http_get=lambda *_args, **_kwargs: OversizedResponse(),
            resolver=lambda _host: ["93.184.216.34"],
            max_response_bytes=1_000,
        )

    assert captured.value.code == "article_too_large"


def test_article_checks_actual_body_size_when_content_length_is_missing() -> None:
    response = FakeArticleResponse(
        body="<main>body larger than limit</main>",
        headers={"content-type": "text/html"},
    )

    with pytest.raises(ArticleContentError) as captured:
        fetch_article(
            {"urls": [{"url": "https://example.org/chunked"}]},
            http_get=lambda *_args, **_kwargs: response,
            resolver=lambda _host: ["93.184.216.34"],
            max_response_bytes=10,
        )

    assert captured.value.code == "article_too_large"


def test_default_article_fetch_streams_and_stops_at_body_limit() -> None:
    yielded: list[bytes] = []

    class StreamingResponse:
        status_code = 200
        headers = {"content-type": "text/html"}

        def __enter__(self) -> "StreamingResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_bytes(self) -> Any:
            for chunk in (b"12345", b"67890", b"must-not-be-read"):
                yielded.append(chunk)
                yield chunk

        @property
        def content(self) -> bytes:
            pytest.fail("default fetch must not materialize response.content")

    calls: list[tuple[str, tuple[str, ...], float]] = []

    def pinned_get(
        url: str,
        *,
        addresses: tuple[str, ...],
        timeout: float,
    ) -> StreamingResponse:
        calls.append((url, addresses, timeout))
        return StreamingResponse()

    with pytest.raises(ArticleContentError) as captured:
        fetch_article(
            {"urls": [{"url": "https://example.org/chunked"}]},
            resolver=lambda _host: ["93.184.216.34"],
            pinned_get=pinned_get,
            max_response_bytes=8,
        )

    assert captured.value.code == "article_too_large"
    assert yielded == [b"12345", b"67890"]
    assert calls == [
        ("https://example.org/chunked", ("93.184.216.34",), 20.0)
    ]


def test_default_article_fetch_validates_redirect_before_second_request() -> None:
    class RedirectResponse:
        status_code = 302
        headers = {"location": "http://169.254.169.254/latest/meta-data"}

        def __enter__(self) -> "RedirectResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    calls: list[tuple[str, tuple[str, ...], float]] = []

    def pinned_get(
        url: str,
        *,
        addresses: tuple[str, ...],
        timeout: float,
    ) -> RedirectResponse:
        calls.append((url, addresses, timeout))
        return RedirectResponse()

    with pytest.raises(URLSecurityError):
        fetch_article(
            {"urls": [{"url": "https://t.co/short"}]},
            resolver=lambda _host: ["93.184.216.34"],
            pinned_get=pinned_get,
        )

    assert calls == [
        ("https://t.co/short", ("93.184.216.34",), 20.0)
    ]


def test_default_article_fetch_pins_the_validated_dns_answer() -> None:
    resolution_count = 0
    calls: list[tuple[str, tuple[str, ...], float]] = []

    def changing_resolver(_host: str) -> list[str]:
        nonlocal resolution_count
        resolution_count += 1
        return ["93.184.216.34"] if resolution_count == 1 else ["127.0.0.1"]

    class Response(FakeArticleResponse):
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

    def pinned_get(
        url: str,
        *,
        addresses: tuple[str, ...],
        timeout: float,
    ) -> Response:
        calls.append((url, addresses, timeout))
        return Response(body="<main>safe pinned body</main>")

    article = fetch_article(
        {"urls": [{"url": "https://changing.example/article"}]},
        resolver=changing_resolver,
        pinned_get=pinned_get,
    )

    assert article.text == "safe pinned body"
    assert resolution_count == 1
    assert calls == [
        ("https://changing.example/article", ("93.184.216.34",), 20.0)
    ]


def test_pinned_transport_connects_numeric_ip_but_preserves_host_and_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_socket = object()
    tls_socket = object()
    calls: dict[str, Any] = {}

    def numeric_socket(address: str, port: int, timeout: float) -> object:
        calls["connect"] = (address, port, timeout)
        return raw_socket

    class TLSContext:
        def wrap_socket(self, sock: object, *, server_hostname: str) -> object:
            calls["tls"] = (sock, server_hostname)
            return tls_socket

    class RawResponse:
        status = 200

        def getheaders(self) -> list[tuple[str, str]]:
            return [("content-type", "text/plain")]

        def read(self, _size: int) -> bytes:
            return b""

    class Connection:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            calls["connection"] = (host, port, timeout)
            self.sock: object | None = None

        def request(
            self,
            method: str,
            path: str,
            *,
            headers: dict[str, str],
        ) -> None:
            calls["request"] = (method, path, headers, self.sock)

        def getresponse(self) -> RawResponse:
            return RawResponse()

        def close(self) -> None:
            calls["closed"] = True

    monkeypatch.setattr(content_module, "_numeric_socket", numeric_socket)
    monkeypatch.setattr(
        content_module.ssl,
        "create_default_context",
        lambda: TLSContext(),
    )
    monkeypatch.setattr(content_module.http.client, "HTTPConnection", Connection)

    with content_module._pinned_http_get(
        "https://example.org/article?q=memory",
        addresses=("93.184.216.34",),
        timeout=7.0,
    ):
        pass

    assert calls["connect"] == ("93.184.216.34", 443, 7.0)
    assert calls["tls"] == (raw_socket, "example.org")
    assert calls["connection"] == ("example.org", 443, 7.0)
    method, path, headers, connected = calls["request"]
    assert (method, path, connected) == ("GET", "/article?q=memory", tls_socket)
    assert headers["Host"] == "example.org"
    assert headers["Accept-Encoding"] == "identity"
    assert calls["closed"] is True


def test_second_brain_recall_runs_local_script_with_timeout_and_parses_json() -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []
    hits = [
        {
            "source": "memory",
            "path": "/workspace/brain/note.md",
            "snippet": "A prior decision about agent memory.",
            "score": 7,
        }
    ]

    def runner(command: list[str], **kwargs: Any) -> Any:
        calls.append((command, kwargs))
        return SimpleNamespace(stdout=json.dumps(hits))

    result = recall_second_brain(
        "agent memory",
        limit=3,
        timeout=7.0,
        runner=runner,
    )

    assert calls == [
        (
            [
                sys.executable,
                "/workspace/_scripts/memory/recall.py",
                "--json",
                "--limit",
                "3",
                "agent memory",
            ],
            {
                "capture_output": True,
                "text": True,
                "timeout": 7.0,
                "check": True,
            },
        )
    ]
    assert result.query == "agent memory"
    assert result.hits == tuple(hits)


def test_second_brain_recall_timeout_is_typed_and_retryable() -> None:
    def runner(command: list[str], **_kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(command, timeout=1)

    with pytest.raises(RecallError) as captured:
        recall_second_brain("slow topic", runner=runner, timeout=1)

    assert captured.value.code == "recall_timeout"
    assert captured.value.retryable is True
