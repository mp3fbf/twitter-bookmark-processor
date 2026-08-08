"""Deterministic article extraction and local context retrieval."""

from __future__ import annotations

import ipaddress
import http.client
import json
import socket
import ssl
import subprocess
import sys
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urljoin, urlparse


_URL_KEYS = ("expanded_url", "expandedUrl", "unwound_url", "unwoundUrl", "url")
_X_HOSTS = {"x.com", "twitter.com", "pbs.twimg.com", "video.twimg.com"}


class URLSecurityError(ValueError):
    """A URL cannot safely be fetched by the article extractor."""


class ArticleContentError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class RecallError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class ArticleContent:
    original_url: str
    final_url: str
    title: str | None
    author: str | None
    published_at: str | None
    text: str
    truncated: bool


@dataclass(frozen=True)
class RecallResult:
    query: str
    hits: tuple[dict[str, Any], ...]


def _resolve_public_ips(hostname: str) -> Iterable[str]:
    return {
        result[4][0]
        for result in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    }


def _validated_public_addresses(
    url: str,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
) -> tuple[str, ...]:
    """Return the exact public addresses approved for one outbound request."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise URLSecurityError("article URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise URLSecurityError("article URL must not contain credentials")
    try:
        port = parsed.port
    except ValueError as exc:
        raise URLSecurityError("article URL contains an invalid port") from exc
    if port is not None and not 1 <= port <= 65_535:
        raise URLSecurityError("article URL contains an invalid port")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise URLSecurityError("localhost article targets are forbidden")
    try:
        literal = ipaddress.ip_address(hostname)
        raw_addresses = [str(literal)]
    except ValueError:
        raw_addresses = list((resolver or _resolve_public_ips)(hostname))
    if not raw_addresses:
        raise URLSecurityError("article hostname did not resolve")
    addresses: dict[tuple[int, int], str] = {}
    for address in raw_addresses:
        try:
            ip = ipaddress.ip_address(address)
        except ValueError as exc:
            raise URLSecurityError("resolver returned an invalid IP address") from exc
        if not ip.is_global:
            raise URLSecurityError(f"non-public article target is forbidden: {ip}")
        addresses[(ip.version, int(ip))] = str(ip)
    return tuple(addresses[key] for key in sorted(addresses))


def validate_public_url(
    url: str,
    *,
    resolver: Callable[[str], Iterable[str]] | None = None,
) -> str:
    """Validate scheme and every resolved address before an outbound fetch."""
    _validated_public_addresses(url, resolver=resolver)
    return url


class _PinnedHTTPResponse:
    """Small streaming response facade backed by one preconnected socket."""

    def __init__(self, connection: http.client.HTTPConnection, response: Any) -> None:
        self._connection = connection
        self._response = response
        self.status_code = int(response.status)
        self.headers = {
            str(key).lower(): str(value) for key, value in response.getheaders()
        }

    def __enter__(self) -> "_PinnedHTTPResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        self._connection.close()

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        retryable = self.status_code in {408, 425, 429} or self.status_code >= 500
        raise ArticleContentError(
            f"article server returned HTTP {self.status_code}",
            code="article_http_error",
            retryable=retryable,
        )

    def iter_bytes(self, chunk_size: int = 64 * 1024) -> Iterable[bytes]:
        while True:
            chunk = self._response.read(chunk_size)
            if not chunk:
                return
            yield chunk


def _numeric_socket(address: str, port: int, timeout: float) -> socket.socket:
    ip = ipaddress.ip_address(address)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        endpoint: Any = (address, port, 0, 0) if ip.version == 6 else (address, port)
        sock.connect(endpoint)
        return sock
    except BaseException:
        sock.close()
        raise


def _pinned_http_get(
    url: str,
    *,
    addresses: tuple[str, ...],
    timeout: float,
) -> _PinnedHTTPResponse:
    """Connect only to an already validated numeric IP, preserving Host and SNI."""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname is None:
        raise URLSecurityError("article URL must contain a hostname")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise URLSecurityError("article URL contains an invalid port") from exc
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    host_header = hostname
    try:
        if ipaddress.ip_address(hostname).version == 6:
            host_header = f"[{hostname}]"
    except ValueError:
        host_header = hostname.encode("idna").decode("ascii")
    default_port = 443 if parsed.scheme == "https" else 80
    if port != default_port:
        host_header = f"{host_header}:{port}"

    for address in addresses:
        raw_socket: socket.socket | None = None
        connection: http.client.HTTPConnection | None = None
        try:
            raw_socket = _numeric_socket(address, port, timeout)
            connected_socket: Any = raw_socket
            if parsed.scheme == "https":
                connected_socket = ssl.create_default_context().wrap_socket(
                    raw_socket,
                    server_hostname=hostname,
                )
                raw_socket = None
            connection = http.client.HTTPConnection(hostname, port, timeout=timeout)
            connection.sock = connected_socket
            connection.request(
                "GET",
                path,
                headers={
                    "Host": host_header,
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
                    "Accept-Encoding": "identity",
                    "User-Agent": "twitter-bookmark-automation/1",
                    "Connection": "close",
                },
            )
            return _PinnedHTTPResponse(connection, connection.getresponse())
        except (KeyboardInterrupt, SystemExit):
            if connection is not None:
                connection.close()
            elif raw_socket is not None:
                raw_socket.close()
            raise
        except Exception:
            if connection is not None:
                connection.close()
            elif raw_socket is not None:
                raw_socket.close()
    raise ArticleContentError(
        "article transport request failed",
        code="article_transport_failed",
        retryable=True,
    )


def _is_excluded_x_host(hostname: str) -> bool:
    host = hostname.rstrip(".").lower()
    return any(host == excluded or host.endswith(f".{excluded}") for excluded in _X_HOSTS)


def select_external_url(payload: Mapping[str, Any]) -> str | None:
    """Select the first HTTP(S) URL that is not an X/Twitter media URL."""

    def walk(value: Any) -> str | None:
        if isinstance(value, Mapping):
            for key in _URL_KEYS:
                candidate = value.get(key)
                if not isinstance(candidate, str):
                    continue
                parsed = urlparse(candidate)
                if (
                    parsed.scheme in {"http", "https"}
                    and parsed.hostname
                    and not _is_excluded_x_host(parsed.hostname)
                ):
                    return candidate
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

    return walk(payload)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def embedded_x_article_raw_text(payload: Mapping[str, Any]) -> str | None:
    """Return only the stable raw X Article body consumed by extraction."""
    if not isinstance(payload.get("article"), Mapping):
        return None
    raw = payload.get("_raw")
    raw_article = raw.get("article") if isinstance(raw, Mapping) else None
    raw_result = (
        raw_article.get("article_results", {}).get("result")
        if isinstance(raw_article, Mapping)
        and isinstance(raw_article.get("article_results"), Mapping)
        else None
    )
    if not isinstance(raw_result, Mapping):
        return None
    raw_body = raw_result.get("body")
    for candidate in (
        raw_result.get("plain_text"),
        raw_result.get("text"),
        raw_body.get("text") if isinstance(raw_body, Mapping) else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _meta_content(soup: Any, selectors: Sequence[dict[str, str]]) -> str | None:
    for selector in selectors:
        tag = soup.find("meta", attrs=selector)
        if tag and tag.get("content"):
            return _clean(str(tag["content"]))
    return None


def _header(headers: Mapping[str, Any], name: str) -> str | None:
    expected = name.lower()
    for key, value in headers.items():
        if str(key).lower() == expected:
            return str(value)
    return None


def _extract_html(
    body: bytes,
    *,
    max_text_chars: int,
) -> tuple[str | None, str | None, str | None, str, bool]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(body.decode("utf-8", errors="replace"), "html.parser")
    title = _meta_content(soup, ({"property": "og:title"}, {"name": "twitter:title"}))
    if title is None and soup.title:
        title = _clean(soup.title.get_text(" ", strip=True))
    author = _meta_content(
        soup,
        (
            {"name": "author"},
            {"property": "article:author"},
            {"name": "byl"},
        ),
    )
    published = _meta_content(
        soup,
        (
            {"property": "article:published_time"},
            {"name": "date"},
            {"name": "datePublished"},
        ),
    )
    readable = soup.find("main") or soup.find("article") or soup.body or soup
    for tag in readable.find_all(
        ["script", "style", "noscript", "nav", "header", "footer", "form", "svg"]
    ):
        tag.decompose()
    lines = [" ".join(line.split()) for line in readable.get_text("\n").splitlines()]
    text = "\n".join(line for line in lines if line)
    truncated = len(text) > max_text_chars
    if truncated:
        text = text[:max_text_chars].rstrip()
    return title, author, published, text, truncated


def _extract_embedded_x_article(
    payload: Mapping[str, Any],
    *,
    max_text_chars: int,
) -> ArticleContent | None:
    article = payload.get("article")
    if not isinstance(article, Mapping):
        return None
    title = _clean(article.get("title") if isinstance(article.get("title"), str) else None)
    body: Any = None
    article_body = article.get("body")
    for candidate in (
        article.get("plain_text"),
        article.get("text"),
        article_body.get("text") if isinstance(article_body, Mapping) else None,
    ):
        if isinstance(candidate, str) and candidate.strip():
            body = candidate
            break
    if not isinstance(body, str) or not body.strip():
        body = embedded_x_article_raw_text(payload)
    if not isinstance(body, str) or not body.strip():
        body = payload.get("text")
        if isinstance(body, str) and title is not None and _clean(body) == title:
            body = None
    preview_only = False
    if not isinstance(body, str) or not body.strip():
        body = article.get("previewText")
        preview_only = isinstance(body, str) and bool(body.strip())
    if not isinstance(body, str) or not body.strip():
        return None
    text = body.strip()
    exceeds_limit = len(text) > max_text_chars
    truncated = preview_only or exceeds_limit
    if exceeds_limit:
        text = text[:max_text_chars].rstrip()
    tweet_id = str(payload.get("id") or "").strip()
    author = payload.get("author")
    username = ""
    author_name = None
    if isinstance(author, Mapping):
        username = str(author.get("username") or "").lstrip("@").strip()
        raw_name = author.get("name")
        author_name = _clean(raw_name if isinstance(raw_name, str) else None)
    status_url = (
        f"https://x.com/{username}/status/{tweet_id}"
        if username and tweet_id
        else f"https://x.com/i/web/status/{tweet_id}"
    )
    created_at = payload.get("createdAt")
    return ArticleContent(
        original_url=status_url,
        final_url=status_url,
        title=title,
        author=author_name or (f"@{username}" if username else None),
        published_at=_clean(created_at if isinstance(created_at, str) else None),
        text=text,
        truncated=truncated,
    )


def _read_bounded_body(response: Any, *, max_response_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    iter_bytes = getattr(response, "iter_bytes", None)
    source = iter_bytes() if callable(iter_bytes) else (bytes(response.content),)
    for chunk in source:
        if not chunk:
            continue
        data = bytes(chunk)
        total += len(data)
        if total > max_response_bytes:
            raise ArticleContentError(
                f"article response exceeds {max_response_bytes} bytes",
                code="article_too_large",
                retryable=False,
            )
        chunks.append(data)
    return b"".join(chunks)


def fetch_article(
    payload: Mapping[str, Any],
    *,
    http_get: Callable[..., Any] | None = None,
    pinned_get: Callable[..., Any] | None = None,
    resolver: Callable[[str], Iterable[str]] | None = None,
    timeout: float = 20.0,
    max_redirects: int = 5,
    max_response_bytes: int = 5 * 1024 * 1024,
    max_text_chars: int = 50_000,
) -> ArticleContent:
    """Fetch one article directly, validating every redirect against SSRF."""
    embedded = _extract_embedded_x_article(payload, max_text_chars=max_text_chars)
    if embedded is not None:
        return embedded
    original_url = select_external_url(payload)
    if original_url is None:
        raise ArticleContentError(
            "bookmark does not contain an external article URL",
            code="article_url_unavailable",
            retryable=True,
        )
    current_url = original_url
    redirect_codes = {301, 302, 303, 307, 308}
    for redirect_count in range(max_redirects + 1):
        current_host = urlparse(current_url).hostname
        if current_host and _is_excluded_x_host(current_host):
            raise ArticleContentError(
                "resolved URL points back to X/Twitter rather than an external article",
                code="article_target_excluded",
                retryable=False,
            )
        addresses = _validated_public_addresses(current_url, resolver=resolver)
        response_context = (
            nullcontext(http_get(current_url, follow_redirects=False, timeout=timeout))
            if http_get is not None
            else (pinned_get or _pinned_http_get)(
                current_url,
                addresses=addresses,
                timeout=timeout,
            )
        )
        with response_context as response:
            if response.status_code in redirect_codes:
                location = response.headers.get("location")
                if not location:
                    raise ArticleContentError(
                        "article redirect omitted Location header",
                        code="invalid_redirect",
                        retryable=False,
                    )
                if redirect_count == max_redirects:
                    raise ArticleContentError(
                        "article exceeded redirect limit",
                        code="too_many_redirects",
                        retryable=False,
                    )
                current_url = urljoin(current_url, location)
                continue
            response.raise_for_status()
            content_type = (_header(response.headers, "content-type") or "").split(
                ";", 1
            )[0]
            content_type = content_type.strip().lower()
            if not (
                content_type.startswith("text/")
                or content_type == "application/xhtml+xml"
            ):
                raise ArticleContentError(
                    f"unsupported article Content-Type: {content_type or 'missing'}",
                    code="unsupported_content_type",
                    retryable=False,
                )
            content_length = _header(response.headers, "content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise ArticleContentError(
                        "article Content-Length is invalid",
                        code="invalid_content_length",
                        retryable=False,
                    ) from exc
                if declared_size < 0:
                    raise ArticleContentError(
                        "article Content-Length must not be negative",
                        code="invalid_content_length",
                        retryable=False,
                    )
                if declared_size > max_response_bytes:
                    raise ArticleContentError(
                        f"article response exceeds {max_response_bytes} bytes",
                        code="article_too_large",
                        retryable=False,
                    )
            body = _read_bounded_body(
                response,
                max_response_bytes=max_response_bytes,
            )
            title, author, published, text, truncated = _extract_html(
                body,
                max_text_chars=max_text_chars,
            )
            return ArticleContent(
                original_url=original_url,
                final_url=current_url,
                title=title,
                author=author,
                published_at=published,
                text=text,
                truncated=truncated,
            )
    raise AssertionError("article redirect loop terminated without a response")


def recall_second_brain(
    query: str,
    *,
    limit: int = 12,
    timeout: float = 15.0,
    runner: Callable[..., Any] = subprocess.run,
    script_path: str | Path = "/workspace/_scripts/memory/recall.py",
) -> RecallResult:
    """Retrieve bounded local snippets before constructing an inference request."""
    normalized_query = " ".join(query.split())
    if not normalized_query:
        raise ValueError("recall query must not be empty")
    if not 1 <= limit <= 100:
        raise ValueError("recall limit must be between 1 and 100")
    command = [
        sys.executable,
        str(Path(script_path)),
        "--json",
        "--limit",
        str(limit),
        normalized_query,
    ]
    try:
        completed = runner(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecallError(
            "Second Brain recall timed out",
            code="recall_timeout",
            retryable=True,
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RecallError(
            "Second Brain recall command failed",
            code="recall_command_failed",
            retryable=True,
        ) from exc
    try:
        parsed = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RecallError(
            "Second Brain recall returned invalid JSON",
            code="recall_invalid_json",
            retryable=False,
        ) from exc
    if not isinstance(parsed, list) or not all(isinstance(hit, dict) for hit in parsed):
        raise RecallError(
            "Second Brain recall JSON must be a list of objects",
            code="recall_invalid_schema",
            retryable=False,
        )
    return RecallResult(query=normalized_query, hits=tuple(parsed))
