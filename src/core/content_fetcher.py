"""Async Content Fetcher — extracts full content from URLs in bookmarks.

Handles URL expansion, full page content extraction, paywall bypass attempts,
special handling for GitHub and YouTube, and integrates with LinkCache.

Ported from twitter-bookmarks-app/content_fetcher.py, rewritten with httpx (async).
"""

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from src.core.link_cache import LinkCache

logger = logging.getLogger(__name__)


@dataclass
class FetchedContent:
    """Content fetched and extracted from a URL."""

    url: str
    expanded_url: str
    title: Optional[str] = None
    description: Optional[str] = None
    main_content: Optional[str] = None
    content_type: str = "unknown"
    site_name: Optional[str] = None
    author: Optional[str] = None
    published_date: Optional[str] = None
    lists_extracted: list[str] = field(default_factory=list)
    code_blocks: list[str] = field(default_factory=list)
    extra_data: dict = field(default_factory=dict)
    fetch_error: Optional[str] = None
    fetched_at: str = field(default_factory=lambda: datetime.now().isoformat())
    paywall_detected: bool = False
    cached: bool = False


# Known paywall sites
PAYWALL_SITES = frozenset(
    {
        "nytimes.com",
        "wsj.com",
        "washingtonpost.com",
        "ft.com",
        "economist.com",
        "bloomberg.com",
        "oglobo.globo.com",
        "globo.com",
        "folha.uol.com.br",
        "estadao.com.br",
        "medium.com",
    }
)

# User agents to rotate
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# CSS selectors for finding article content, in priority order
CONTENT_SELECTORS = [
    "article",
    '[role="main"]',
    ".post-content",
    ".article-content",
    ".entry-content",
    ".content",
    "main",
    "#content",
    ".markdown-body",
    ".post",
]


class AsyncContentFetcher:
    """Async fetcher that extracts full content from URLs.

    Integrates with the existing LinkCache for caching extracted data.
    Uses httpx for async HTTP requests.
    """

    def __init__(
        self,
        timeout: int = 15,
        cache: Optional[LinkCache] = None,
    ):
        self.timeout = timeout
        self._cache = cache
        self._ua_index = 0

    def _get_user_agent(self) -> str:
        ua = USER_AGENTS[self._ua_index % len(USER_AGENTS)]
        self._ua_index += 1
        return ua

    async def fetch_content(self, url: str) -> FetchedContent:
        """Fetch and extract content from a URL.

        Args:
            url: URL to fetch

        Returns:
            FetchedContent with extracted data
        """
        # Expand shortened URLs
        expanded_url = url
        if any(s in url for s in ("t.co", "bit.ly", "tinyurl", "goo.gl")):
            expanded_url = await self._expand_url(url)

        content = FetchedContent(url=url, expanded_url=expanded_url)
        domain = urlparse(expanded_url).netloc.lower()

        # Route to special handlers
        if "github.com" in domain:
            return await self._extract_github_content(expanded_url)

        if "youtube.com" in domain or "youtu.be" in domain:
            return await self._extract_youtube_content(expanded_url)

        if "twitter.com" in domain or "x.com" in domain:
            content.content_type = "twitter"
            content.main_content = "Twitter/X link - content already available in tweet"
            return content

        # Generic fetch
        try:
            timeout = httpx.Timeout(
                connect=10.0,
                read=float(self.timeout),
                write=10.0,
                pool=10.0,
            )
            headers = {"User-Agent": self._get_user_agent()}

            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=True,
            ) as client:
                response = await client.get(expanded_url, headers=headers)
                response.raise_for_status()
                html = response.text

            soup = BeautifulSoup(html, "html.parser")

            # Extract metadata
            self._extract_metadata(soup, content)

            # Check for paywall
            if self._is_paywall_site(expanded_url):
                page_text = soup.get_text().lower()
                paywall_indicators = [
                    "paywall",
                    "subscribe",
                    "subscription required",
                    "premium content",
                ]
                if any(ind in page_text for ind in paywall_indicators):
                    content.paywall_detected = True
                    bypass_html = await self._try_archive_bypass(expanded_url)
                    if bypass_html:
                        soup = BeautifulSoup(bypass_html, "html.parser")
                        content.extra_data["bypass_used"] = True

            # Extract article content
            main_text, lists, code_blocks = self._extract_article_content(soup)
            content.main_content = main_text
            content.lists_extracted = lists
            content.code_blocks = code_blocks

            # Determine content type
            if lists:
                content.content_type = "list/guide"
            elif code_blocks:
                content.content_type = "code/tutorial"
            else:
                content.content_type = "article"

        except httpx.TimeoutException:
            content.fetch_error = "Timeout fetching URL"
        except httpx.HTTPStatusError as e:
            content.fetch_error = f"HTTP error: {e.response.status_code}"
        except Exception as e:
            content.fetch_error = str(e)

        return content

    async def _expand_url(self, short_url: str) -> str:
        """Expand a shortened URL by following redirects."""
        try:
            if "twitter.com" in short_url or "x.com" in short_url:
                return short_url

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                follow_redirects=True,
            ) as client:
                response = await client.head(
                    short_url,
                    headers={"User-Agent": self._get_user_agent()},
                )
                return str(response.url)
        except Exception as e:
            logger.warning("URL expansion failed for %s: %s", short_url, e)
            return short_url

    def _is_paywall_site(self, url: str) -> bool:
        domain = urlparse(url).netloc.lower()
        return any(pw in domain for pw in PAYWALL_SITES)

    async def _try_archive_bypass(self, url: str) -> Optional[str]:
        """Try to get content via archive.org or 12ft.io."""
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0),
            follow_redirects=True,
        ) as client:
            # Try archive.org
            try:
                archive_url = f"https://web.archive.org/web/2/{url}"
                response = await client.get(
                    archive_url,
                    headers={"User-Agent": self._get_user_agent()},
                )
                if response.status_code == 200:
                    return response.text
            except Exception:
                pass

            # Try 12ft.io
            try:
                bypass_url = f"https://12ft.io/{url}"
                response = await client.get(
                    bypass_url,
                    headers={"User-Agent": self._get_user_agent()},
                )
                if response.status_code == 200:
                    return response.text
            except Exception:
                pass

        return None

    def _extract_metadata(self, soup: BeautifulSoup, content: FetchedContent) -> None:
        """Extract OG metadata from parsed HTML."""
        og_title = soup.find("meta", property="og:title")
        if og_title:
            content.title = og_title.get("content", "")

        og_desc = soup.find("meta", property="og:description")
        if og_desc:
            content.description = og_desc.get("content", "")

        og_site = soup.find("meta", property="og:site_name")
        if og_site:
            content.site_name = og_site.get("content", "")

        if not content.title:
            title_tag = soup.find("title")
            if title_tag:
                content.title = title_tag.text.strip()

    def _extract_article_content(
        self, soup: BeautifulSoup
    ) -> tuple[str, list[str], list[str]]:
        """Extract main article content, lists, and code blocks.

        Returns:
            Tuple of (main_content, lists_extracted, code_blocks)
        """
        lists_extracted: list[str] = []
        code_blocks: list[str] = []

        # Find content element
        content_element = None
        for selector in CONTENT_SELECTORS:
            content_element = soup.select_one(selector)
            if content_element:
                break

        if not content_element:
            content_element = soup.body

        if not content_element:
            return "", [], []

        # Remove noise
        for unwanted in content_element.select(
            "nav, footer, aside, .sidebar, .comments, .advertisement, script, style"
        ):
            unwanted.decompose()

        # Extract text
        main_content = content_element.get_text(separator="\n", strip=True)

        # Extract lists
        for list_elem in content_element.select("ol, ul"):
            items = [li.get_text(strip=True) for li in list_elem.select("li")]
            if items and len(items) > 2:
                lists_extracted.append("\n".join(f"- {item}" for item in items))

        # Extract code blocks
        for code in content_element.select("pre code, pre, code"):
            code_text = code.get_text(strip=True)
            if code_text and len(code_text) > 20:
                code_blocks.append(code_text)

        # Truncate
        if len(main_content) > 15000:
            main_content = main_content[:15000] + "...[truncated]"

        return main_content, lists_extracted, code_blocks

    async def _extract_github_content(self, url: str) -> FetchedContent:
        """Special handler for GitHub repository URLs."""
        content = FetchedContent(url=url, expanded_url=url, content_type="github")

        parsed = urlparse(url)
        parts = parsed.path.strip("/").split("/")

        if len(parts) < 2:
            return content

        owner, repo = parts[0], parts[1]
        content.extra_data["owner"] = owner
        content.extra_data["repo"] = repo

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
            ) as client:
                # Get repo info
                api_url = f"https://api.github.com/repos/{owner}/{repo}"
                response = await client.get(api_url)
                if response.status_code == 200:
                    data = response.json()
                    content.title = f"{owner}/{repo}"
                    content.description = data.get("description", "")
                    content.extra_data.update(
                        {
                            "stars": data.get("stargazers_count", 0),
                            "forks": data.get("forks_count", 0),
                            "language": data.get("language", ""),
                            "topics": data.get("topics", []),
                            "homepage": data.get("homepage", ""),
                        }
                    )

                # Get README
                import base64

                readme_url = (
                    f"https://api.github.com/repos/{owner}/{repo}/readme"
                )
                response = await client.get(readme_url)
                if response.status_code == 200:
                    readme_data = response.json()
                    readme_content = base64.b64decode(
                        readme_data.get("content", "")
                    ).decode("utf-8")
                    content.main_content = readme_content[:10000]

        except Exception as e:
            content.fetch_error = str(e)

        return content

    async def _extract_youtube_content(self, url: str) -> FetchedContent:
        """Special handler for YouTube URLs."""
        content = FetchedContent(url=url, expanded_url=url, content_type="youtube")

        # Extract video ID
        video_id = None
        if "youtube.com/watch" in url:
            match = re.search(r"v=([a-zA-Z0-9_-]+)", url)
            if match:
                video_id = match.group(1)
        elif "youtu.be/" in url:
            match = re.search(r"youtu\.be/([a-zA-Z0-9_-]+)", url)
            if match:
                video_id = match.group(1)

        if video_id:
            content.extra_data["video_id"] = video_id

            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0),
                ) as client:
                    oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
                    response = await client.get(oembed_url)
                    if response.status_code == 200:
                        data = response.json()
                        content.title = data.get("title", "")
                        content.author = data.get("author_name", "")
            except Exception as e:
                content.fetch_error = str(e)

        return content

    @staticmethod
    def extract_urls(text: str) -> list[str]:
        """Extract external URLs from text, filtering Twitter media URLs."""
        url_pattern = r"https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+(?:[/](?:[-\w._~!$&'()*+,;=:@]|%[\da-fA-F]{2})*)*(?:\?(?:[-\w._~!$&'()*+,;=:@/?]|%[\da-fA-F]{2})*)?(?:#(?:[-\w._~!$&'()*+,;=:@/?]|%[\da-fA-F]{2})*)?"
        urls = re.findall(url_pattern, text)

        filtered = []
        for url in urls:
            if "twitter.com" in url and ("/photo/" in url or "/video/" in url):
                continue
            if "pbs.twimg.com" in url or "video.twimg.com" in url:
                continue
            filtered.append(url)

        return filtered
