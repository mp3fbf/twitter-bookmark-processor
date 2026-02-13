"""Stage 1: Content Capture.

Gathers everything a bookmark points to — tweet text, threads, links,
images (via vision), quote-tweets, video transcripts — into a ContentPackage.

Reuses existing infrastructure: AsyncContentFetcher for links, AnthropicProvider
for vision, and the X API pattern from ThreadProcessor for thread expansion.

On partial failure (dead link, failed image), logs the error and continues
with available data. One failure never kills the whole bookmark.
"""

import asyncio
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

import httpx
import tiktoken

from src.core.content_fetcher import AsyncContentFetcher, FetchedContent
from src.core.http_client import create_client
from src.insight.models import (
    AnalyzedImage,
    ContentPackage,
    FetchedContentType,
    ResolvedLink,
    ThreadTweet,
)

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark
    from src.core.llm_factory import AnthropicProvider
    from src.sources.x_api_auth import XApiAuth

logger = logging.getLogger(__name__)

# Token budget for Stage 2 input
MAX_STAGE2_TOKENS = 150_000

# tiktoken encoding (close enough to Anthropic's tokenizer for budget checks)
_encoding: tiktoken.Encoding | None = None


def _get_encoding() -> tiktoken.Encoding:
    global _encoding
    if _encoding is None:
        _encoding = tiktoken.get_encoding("cl100k_base")
    return _encoding


def estimate_tokens(text: str) -> int:
    return len(_get_encoding().encode(text))


# X API v2 constants (same as ThreadProcessor)
X_API_BASE = "https://api.twitter.com/2"
TWEET_FIELDS = "id,text,created_at,conversation_id,entities,attachments,author_id,note_tweet"
EXPANSIONS = "attachments.media_keys,author_id"
MEDIA_FIELDS = "media_key,type,url,preview_image_url"
USER_FIELDS = "id,username,name"

# Vision prompt for image analysis
VISION_PROMPT = (
    "Describe what this image shows. If it contains text, transcribe it fully. "
    "If it shows a screenshot of a video, app, website, or tool, identify the "
    "source URL if visible. If it shows code, transcribe the code. "
    "Be factual and thorough."
)

# Where content packages are persisted
PACKAGES_DIR = Path("data/content_packages")


class ContentCapture:
    """Captures all content a bookmark points to.

    Produces a ContentPackage that Stage 2 (InsightDistiller) can reason over.
    """

    def __init__(
        self,
        content_fetcher: AsyncContentFetcher | None = None,
        vision_provider: Optional["AnthropicProvider"] = None,
        x_api_auth: Optional["XApiAuth"] = None,
        bearer_token: str | None = None,
    ):
        self._fetcher = content_fetcher or AsyncContentFetcher()
        self._vision = vision_provider
        self._x_api_auth = x_api_auth
        self._bearer_token = bearer_token

    async def capture(self, bookmark: "Bookmark") -> ContentPackage:
        """Capture all content for a bookmark into a ContentPackage.

        Steps:
        1. Extract tweet text and metadata
        2. Detect & expand thread (if X API available)
        3. Resolve all URLs and fetch linked content
        4. Analyze all images via vision model (parallel)
        5. Follow sources revealed by images
        6. Capture quote-tweet (1 level)
        7. Estimate tokens and truncate if needed
        8. Persist to disk
        """
        start = time.perf_counter()

        # Enrich thin bookmarks (backfill) via X API or legacy notes
        if not bookmark.text:
            if self._x_api_auth:
                await self._enrich_bookmark(bookmark)
            # Fallback: try to read from legacy output file
            if not bookmark.text:
                self._enrich_from_legacy(bookmark)

        # Base package from bookmark data
        package = ContentPackage(
            bookmark_id=bookmark.id,
            tweet_text=bookmark.text,
            author_name=bookmark.author_name or bookmark.author_username,
            author_username=bookmark.author_username,
            tweet_url=bookmark.url or f"https://x.com/{bookmark.author_username}/status/{bookmark.id}",
            created_at=self._parse_date(bookmark.created_at),
            captured_at=datetime.now(),
        )

        # Gather all URLs from tweet text
        all_urls = self._extract_safe_urls(bookmark.text)
        # Also include pre-parsed links from bookmark
        for link in bookmark.links:
            if link not in all_urls and self._is_safe_url(link):
                all_urls.append(link)

        # Run independent tasks in parallel
        tasks = {}

        # Thread expansion
        if self._x_api_auth and (bookmark.is_thread or bookmark.conversation_id):
            tasks["thread"] = self._expand_thread(bookmark)

        # Link resolution + content fetching
        if all_urls:
            tasks["links"] = self._resolve_links(all_urls)

        # Image analysis (vision)
        if self._vision and bookmark.media_urls:
            tasks["images"] = self._analyze_images(bookmark.media_urls)

        # Execute all in parallel
        if tasks:
            results = await asyncio.gather(
                *tasks.values(), return_exceptions=True
            )
            result_map = dict(zip(tasks.keys(), results))

            if "thread" in result_map and not isinstance(result_map["thread"], Exception):
                package.thread_tweets = result_map["thread"]
            elif "thread" in result_map and isinstance(result_map["thread"], Exception):
                logger.warning("Thread expansion failed: %s", result_map["thread"])

            if "links" in result_map and not isinstance(result_map["links"], Exception):
                package.resolved_links = result_map["links"]
            elif "links" in result_map and isinstance(result_map["links"], Exception):
                logger.warning("Link resolution failed: %s", result_map["links"])

            if "images" in result_map and not isinstance(result_map["images"], Exception):
                package.analyzed_images = result_map["images"]
            elif "images" in result_map and isinstance(result_map["images"], Exception):
                logger.warning("Image analysis failed: %s", result_map["images"])

        # Follow sources revealed by images (sequential — depends on image results)
        if package.analyzed_images:
            await self._follow_image_sources(package)

        # Token estimation and truncation
        package.capture_duration_ms = int((time.perf_counter() - start) * 1000)
        package.token_estimate = self._estimate_package_tokens(package)

        if package.token_estimate > MAX_STAGE2_TOKENS:
            self._truncate_package(package)
            package.token_estimate = self._estimate_package_tokens(package)

        # Persist
        self._persist(package)

        return package

    # ── Bookmark enrichment ───────────────────────────────────────

    async def _enrich_bookmark(self, bookmark: "Bookmark") -> None:
        """Enrich a thin bookmark (backfill) with data from X API.

        Mutates the bookmark in place with tweet text, author info,
        links, media URLs, and conversation_id.
        """
        try:
            token = await self._x_api_auth.get_valid_token()
            data = await self._fetch_tweet_api(token, bookmark.id)
            if not data:
                logger.warning("Could not enrich bookmark %s via X API", bookmark.id)
                return

            # Tweet text (prefer note_tweet for long tweets)
            note_tweet = data.get("note_tweet")
            if note_tweet and note_tweet.get("text"):
                bookmark.text = note_tweet["text"]
            else:
                bookmark.text = data.get("text", "")

            bookmark.created_at = data.get("created_at", "")
            bookmark.conversation_id = data.get("conversation_id")

            # Author info from includes
            author_id = data.get("author_id")
            if author_id:
                bookmark.author_id = author_id
            includes = data.get("_includes", {})
            for user in includes.get("users", []):
                if user.get("id") == author_id:
                    bookmark.author_username = user.get("username", bookmark.author_username)
                    bookmark.author_name = user.get("name", bookmark.author_name)
                    break

            # Extract links and media from entities + includes
            bookmark.links = self._extract_links_from_api_tweet(data)
            bookmark.media_urls = self._extract_media_from_api_tweet(data, includes)

            # Detect thread
            if bookmark.conversation_id and bookmark.conversation_id == bookmark.id:
                bookmark.is_thread = True

            logger.info("Enriched bookmark %s: %d chars, %d links",
                        bookmark.id, len(bookmark.text), len(bookmark.links))

        except Exception as e:
            logger.warning("Enrichment failed for %s: %s", bookmark.id, e)

    def _enrich_from_legacy(self, bookmark: "Bookmark") -> None:
        """Enrich a bookmark from its legacy processed output file.

        Parses the YAML frontmatter and content section of the legacy
        Obsidian note to extract tweet text, author, and links.
        """
        from src.core.state_manager import StateManager

        try:
            # Find the legacy output file via state.json
            legacy_state_file = PACKAGES_DIR.parent / "state.json"
            if not legacy_state_file.exists():
                return

            state = StateManager(legacy_state_file)
            state.load()
            entry = state._state.get("processed", {}).get(bookmark.id)
            if not entry or not entry.get("output_path"):
                return

            output_path = Path(entry["output_path"])
            if not output_path.exists():
                return

            content = output_path.read_text(encoding="utf-8")

            # Parse YAML frontmatter
            if content.startswith("---"):
                end = content.index("---", 3)
                frontmatter = content[3:end]
                body = content[end + 3:]

                for line in frontmatter.split("\n"):
                    if line.startswith("author:"):
                        author = line.split(":", 1)[1].strip().strip("'\"@")
                        if author and bookmark.author_username == "unknown":
                            bookmark.author_username = author
                            bookmark.author_name = author
                    if line.startswith("tweet_date:"):
                        date_str = line.split(":", 1)[1].strip().strip("'\"")
                        if date_str:
                            bookmark.created_at = date_str

                # Extract tweet text from ## Content section
                if "## Content" in body:
                    content_section = body.split("## Content", 1)[1]
                    # Stop at next ## heading
                    if "\n## " in content_section:
                        content_section = content_section.split("\n## ", 1)[0]
                    # Clean up: remove author attribution lines
                    lines = content_section.strip().split("\n")
                    text_lines = [
                        l for l in lines
                        if l.strip() and not l.startswith("**") and not l.startswith("### ")
                    ]
                    if text_lines:
                        bookmark.text = "\n".join(text_lines).strip()

                # Extract URLs from body
                import re
                urls = re.findall(r'https?://[^\s\)\]"\'<>]+', body)
                for url in urls:
                    if self._is_safe_url(url) and url not in bookmark.links:
                        # Skip twitter/x URLs that are just the tweet itself
                        if f"/status/{bookmark.id}" in url:
                            continue
                        bookmark.links.append(url)

            if bookmark.text:
                logger.info("Enriched %s from legacy note: %d chars",
                            bookmark.id, len(bookmark.text))

        except Exception as e:
            logger.debug("Could not enrich from legacy for %s: %s", bookmark.id, e)

    # ── URL handling ────────────────────────────────────────────────

    def _extract_safe_urls(self, text: str) -> list[str]:
        """Extract URLs from text, filtering to http/https only."""
        urls = AsyncContentFetcher.extract_urls(text)
        return [u for u in urls if self._is_safe_url(u)]

    @staticmethod
    def _is_safe_url(url: str) -> bool:
        """Only allow http/https protocols."""
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https")

    # ── Link resolution ─────────────────────────────────────────────

    async def _resolve_links(self, urls: list[str]) -> list[ResolvedLink]:
        """Resolve and fetch content for all URLs."""
        resolved = []
        for url in urls:
            try:
                fetched = await self._fetcher.fetch_content(url)
                resolved.append(self._fetched_to_resolved(url, fetched))
            except Exception as e:
                logger.warning("Failed to fetch %s: %s", url, e)
                resolved.append(ResolvedLink(
                    original_url=url,
                    resolved_url=url,
                    fetch_error=str(e),
                ))
        return resolved

    @staticmethod
    def _fetched_to_resolved(original_url: str, fetched: FetchedContent) -> ResolvedLink:
        """Convert FetchedContent (core) to ResolvedLink (insight)."""
        content = fetched.main_content
        if content and len(content) > 15_000:
            content = content[:15_000]

        content_type_map = {
            "github": FetchedContentType.REPO,
            "youtube": FetchedContentType.VIDEO,
            "article": FetchedContentType.ARTICLE,
            "list/guide": FetchedContentType.ARTICLE,
            "code/tutorial": FetchedContentType.ARTICLE,
        }
        ct = content_type_map.get(fetched.content_type, FetchedContentType.OTHER)

        return ResolvedLink(
            original_url=original_url,
            resolved_url=fetched.expanded_url,
            title=fetched.title,
            content=content,
            fetch_error=fetched.fetch_error,
            content_type=ct,
        )

    # ── Thread expansion ────────────────────────────────────────────

    async def _expand_thread(self, bookmark: "Bookmark") -> list[ThreadTweet]:
        """Expand thread using X API v2 search."""
        token = await self._x_api_auth.get_valid_token()

        conversation_id = bookmark.conversation_id or bookmark.id
        author = bookmark.author_username

        # If no conversation_id, fetch the tweet first
        if not bookmark.conversation_id:
            tweet_data = await self._fetch_tweet_api(token, bookmark.id)
            if tweet_data:
                conversation_id = tweet_data.get("conversation_id", bookmark.id)

        tweets = await self._search_conversation(token, conversation_id, author)
        if not tweets:
            return []

        result = []
        for i, tweet in enumerate(tweets):
            text = tweet.get("text", "")
            note_tweet = tweet.get("note_tweet")
            if note_tweet and note_tweet.get("text"):
                text = note_tweet["text"]

            media_urls = self._extract_media_from_api_tweet(tweet)
            links = self._extract_links_from_api_tweet(tweet)

            result.append(ThreadTweet(
                order=i,
                text=text,
                media_urls=media_urls,
                links=links,
            ))

        return result

    async def _fetch_tweet_api(self, token: str, tweet_id: str) -> dict | None:
        """Fetch a single tweet by ID via X API.

        Returns the tweet data dict with an extra '_includes' key containing
        the response's includes (users, media) for resolving author and media.
        """
        try:
            async with create_client() as client:
                response = await client.get(
                    f"{X_API_BASE}/tweets/{tweet_id}",
                    params={
                        "tweet.fields": TWEET_FIELDS,
                        "expansions": EXPANSIONS,
                        "media.fields": MEDIA_FIELDS,
                        "user.fields": USER_FIELDS,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code != 200:
                    logger.warning("Failed to fetch tweet %s: %s", tweet_id, response.status_code)
                    return None
                body = response.json()
                data = body.get("data")
                if data and "includes" in body:
                    data["_includes"] = body["includes"]
                return data
        except Exception as e:
            logger.warning("Error fetching tweet %s: %s", tweet_id, e)
            return None

    async def _search_conversation(
        self, token: str, conversation_id: str, author: str
    ) -> list[dict]:
        """Search for all tweets in a conversation by the author."""
        query = f"conversation_id:{conversation_id} from:{author}"
        try:
            async with create_client() as client:
                response = await client.get(
                    f"{X_API_BASE}/tweets/search/recent",
                    params={
                        "query": query,
                        "max_results": "100",
                        "tweet.fields": TWEET_FIELDS,
                        "expansions": EXPANSIONS,
                        "media.fields": MEDIA_FIELDS,
                        "user.fields": USER_FIELDS,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code != 200:
                    logger.warning("Thread search failed: %s", response.status_code)
                    return []
                body = response.json()
                tweets = body.get("data", [])
                includes = body.get("includes", {})
                # Attach includes to each tweet for media resolution
                for tweet in tweets:
                    tweet["_includes"] = includes
                tweets.sort(key=lambda t: int(t.get("id", "0")))
                return tweets
        except Exception as e:
            logger.warning("Thread search error: %s", e)
            return []

    @staticmethod
    def _extract_media_from_api_tweet(tweet: dict, includes: dict | None = None) -> list[str]:
        """Extract media URLs from an API tweet response.

        Resolves media_keys via includes.media when available, falling back
        to pbs.twimg.com URLs in entities.
        """
        urls = []
        includes = includes or tweet.get("_includes", {})

        # Build media lookup from includes
        media_map: dict[str, dict] = {}
        for m in includes.get("media", []):
            media_map[m["media_key"]] = m

        # Resolve media_keys to actual URLs
        attachments = tweet.get("attachments", {})
        for key in attachments.get("media_keys", []):
            media = media_map.get(key)
            if media:
                url = media.get("url") or media.get("preview_image_url")
                if url:
                    urls.append(url)

        # Fallback: use entities for direct image URLs
        if not urls:
            entities = tweet.get("entities", {})
            for url_entity in entities.get("urls", []):
                expanded = url_entity.get("expanded_url", "")
                if "pbs.twimg.com" in expanded:
                    urls.append(expanded)
        return urls

    @staticmethod
    def _extract_links_from_api_tweet(tweet: dict) -> list[str]:
        """Extract external links from an API tweet."""
        links = []
        entities = tweet.get("entities", {})
        for url_entity in entities.get("urls", []):
            expanded = url_entity.get("expanded_url", "")
            if not expanded:
                continue
            if re.match(
                r"https?://(twitter\.com|x\.com)/\w+/status/\d+/(photo|video)",
                expanded,
            ):
                continue
            if "pbs.twimg.com" in expanded or "video.twimg.com" in expanded:
                continue
            if "twitter.com" in expanded or "x.com" in expanded:
                continue
            links.append(expanded)
        return links

    # ── Vision analysis ─────────────────────────────────────────────

    async def _analyze_images(self, media_urls: list[str]) -> list[AnalyzedImage]:
        """Analyze all images in parallel via vision model."""
        tasks = [self._analyze_single_image(url) for url in media_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        analyzed = []
        for url, result in zip(media_urls, results):
            if isinstance(result, Exception):
                logger.warning("Vision analysis failed for %s: %s", url, result)
                analyzed.append(AnalyzedImage(
                    url=url,
                    vision_analysis=f"[Analysis failed: {result}]",
                ))
            else:
                analyzed.append(result)
        return analyzed

    async def _analyze_single_image(self, url: str) -> AnalyzedImage:
        """Analyze a single image via vision model."""
        response = await self._vision.generate_with_vision(
            prompt=VISION_PROMPT,
            images=[url],
        )
        analysis = response.content

        # Check if the analysis reveals a followable source
        identified_source = self._identify_source_in_analysis(analysis)

        return AnalyzedImage(
            url=url,
            vision_analysis=analysis,
            identified_source=identified_source,
        )

    @staticmethod
    def _identify_source_in_analysis(analysis: str) -> str | None:
        """Check if vision analysis mentions a followable URL."""
        url_pattern = r'https?://[^\s\)\]\"\'<>]+'
        urls = re.findall(url_pattern, analysis)
        for url in urls:
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                return url
        return None

    async def _follow_image_sources(self, package: ContentPackage) -> None:
        """For images that reveal sources, fetch those sources too."""
        for image in package.analyzed_images:
            if not image.identified_source:
                continue
            # Don't re-fetch URLs already in resolved_links
            existing_urls = {rl.original_url for rl in package.resolved_links}
            existing_urls.update(rl.resolved_url for rl in package.resolved_links)
            if image.identified_source in existing_urls:
                continue

            try:
                fetched = await self._fetcher.fetch_content(image.identified_source)
                if fetched.main_content:
                    image.source_content = fetched.main_content[:15_000]
            except Exception as e:
                logger.warning(
                    "Failed to follow image source %s: %s",
                    image.identified_source, e,
                )

    # ── Token estimation & truncation ───────────────────────────────

    def _estimate_package_tokens(self, package: ContentPackage) -> int:
        """Estimate total tokens for the content package."""
        parts = [package.tweet_text]

        for tweet in package.thread_tweets:
            parts.append(tweet.text)

        for link in package.resolved_links:
            if link.content:
                parts.append(link.content)
            if link.title:
                parts.append(link.title)

        for image in package.analyzed_images:
            parts.append(image.vision_analysis)
            if image.source_content:
                parts.append(image.source_content)

        if package.video_transcript:
            parts.append(package.video_transcript)

        if package.quoted_content:
            parts.append(package.quoted_content.tweet_text)

        return estimate_tokens("\n".join(parts))

    def _truncate_package(self, package: ContentPackage) -> None:
        """Truncate package to fit within token budget.

        Priority order (truncate first → last):
        1. resolved_links[].content — longest articles first
        2. thread_tweets — keep first + last 5, summarize middle
        3. video_transcript — truncate to 10K chars
        4. vision_analysis — never truncate
        5. tweet_text — never truncate
        """
        # 1. Truncate longest linked articles first
        sorted_links = sorted(
            package.resolved_links,
            key=lambda rl: len(rl.content or ""),
            reverse=True,
        )
        for link in sorted_links:
            if self._estimate_package_tokens(package) <= MAX_STAGE2_TOKENS:
                break
            if link.content and len(link.content) > 2000:
                link.content = link.content[:2000] + "\n...[truncated]"

        # 2. Truncate thread to first + last 5 tweets
        if (
            len(package.thread_tweets) > 12
            and self._estimate_package_tokens(package) > MAX_STAGE2_TOKENS
        ):
            first_5 = package.thread_tweets[:5]
            last_5 = package.thread_tweets[-5:]
            middle_count = len(package.thread_tweets) - 10
            summary = ThreadTweet(
                order=5,
                text=f"[...{middle_count} tweets omitted for token budget...]",
            )
            package.thread_tweets = first_5 + [summary] + last_5

        # 3. Truncate video transcript
        if (
            package.video_transcript
            and len(package.video_transcript) > 10_000
            and self._estimate_package_tokens(package) > MAX_STAGE2_TOKENS
        ):
            package.video_transcript = package.video_transcript[:10_000] + "\n...[truncated]"

    # ── Persistence ─────────────────────────────────────────────────

    @staticmethod
    def _persist(package: ContentPackage) -> Path:
        """Persist ContentPackage to disk as JSON."""
        PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
        path = PACKAGES_DIR / f"{package.bookmark_id}.json"
        path.write_text(package.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Persisted content package: %s", path)
        return path

    @staticmethod
    def load_package(bookmark_id: str) -> ContentPackage | None:
        """Load a persisted ContentPackage by bookmark ID."""
        path = PACKAGES_DIR / f"{bookmark_id}.json"
        if not path.exists():
            return None
        return ContentPackage.model_validate_json(path.read_text(encoding="utf-8"))

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_date(date_str: str) -> datetime:
        """Parse a date string, falling back to now() on failure."""
        if not date_str:
            return datetime.now()
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return datetime.now()
