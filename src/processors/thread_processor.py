"""Thread processor for Twitter threads.

Processes bookmarks classified as THREAD content type.
Uses X API v2 search endpoint to fetch all tweets in a conversation,
then formats them into a structured Obsidian note.
"""

import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import httpx

from src.core.exceptions import ExtractionError, SkillError
from src.core.llm_client import LLMClient, get_llm_client
from src.processors.base import BaseProcessor, ProcessResult

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark
    from src.sources.x_api_auth import XApiAuth

logger = logging.getLogger(__name__)

# X API v2 base URL
BASE_URL = "https://api.twitter.com/2"

# Fields to request when fetching thread tweets
TWEET_FIELDS = "id,text,created_at,conversation_id,entities,attachments,author_id,note_tweet"
EXPANSIONS = "attachments.media_keys,author_id"
MEDIA_FIELDS = "media_key,type,url,preview_image_url"
USER_FIELDS = "id,username,name"


class ThreadProcessor(BaseProcessor):
    """Processor for thread content (Twitter threads).

    Uses X API v2 to fetch all tweets in a conversation by the thread author.
    Searches by conversation_id to reconstruct the full thread.
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        llm_client: Optional[LLMClient] = None,
        x_api_auth: Optional["XApiAuth"] = None,
    ):
        """Initialize thread processor.

        Args:
            output_dir: Directory where output should be saved
            llm_client: Optional LLMClient for key points extraction. If not provided,
                       will try to use global singleton (fails gracefully if unavailable).
            x_api_auth: XApiAuth instance for X API access. Required for thread fetching.
        """
        self.output_dir = output_dir
        self._llm_client = llm_client
        self._x_api_auth = x_api_auth

    async def process(self, bookmark: "Bookmark") -> ProcessResult:
        """Process a thread bookmark by fetching all tweets via X API.

        Args:
            bookmark: The thread bookmark to process

        Returns:
            ProcessResult with extracted thread content and metadata
        """
        start_time = time.perf_counter()

        if not self._x_api_auth:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error="X API auth not configured for thread processing",
                duration_ms=duration_ms,
            )

        try:
            # Fetch thread tweets via X API
            data = await self._fetch_thread(bookmark)

            # Parse output into ProcessResult (reuses same structure)
            process_result = self._parse_thread_data(data)
            process_result.duration_ms = int((time.perf_counter() - start_time) * 1000)

            return process_result

        except SkillError as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - start_time) * 1000)
            return ProcessResult(
                success=False,
                error=f"Unexpected error: {e}",
                duration_ms=duration_ms,
            )

    async def _fetch_thread(self, bookmark: "Bookmark") -> dict:
        """Fetch all tweets in a thread via X API v2.

        Strategy:
        1. If we have conversation_id and author, search directly
        2. If missing either, fetch the bookmarked tweet first to get them
        3. Use search/recent endpoint with conversation_id filter
        4. Fall back to single-tweet if search returns nothing (thread >7 days old)

        Args:
            bookmark: The thread bookmark

        Returns:
            Dict with keys: tweets (list), author (str), source (str)

        Raises:
            SkillError: If thread cannot be fetched
        """
        token = await self._x_api_auth.get_valid_token()

        conversation_id = bookmark.conversation_id
        author = bookmark.author_username

        # If missing conversation_id or author, fetch the tweet first
        if not conversation_id or not author:
            tweet_data, fetched_author = await self._fetch_single_tweet(
                token, bookmark.id
            )
            if not tweet_data:
                raise SkillError(f"Could not fetch tweet {bookmark.id}")

            conversation_id = tweet_data.get("conversation_id", bookmark.id)
            if not author:
                author = fetched_author or "unknown"

        # Search for all tweets in this conversation by the author
        tweets = await self._search_conversation(token, conversation_id, author)

        if not tweets:
            # Fallback: search returned nothing (thread older than 7 days
            # or search endpoint unavailable). Use just the bookmarked tweet.
            logger.warning(
                "Search returned no results for conversation %s. "
                "Falling back to single tweet.",
                conversation_id,
            )
            tweets = await self._build_fallback_tweets(token, bookmark, author)

        if not tweets:
            raise SkillError(
                f"Could not fetch any tweets for thread {bookmark.id}"
            )

        return {
            "tweets": tweets,
            "author": author,
            "source": "X API v2",
        }

    async def _fetch_single_tweet(
        self, token: str, tweet_id: str
    ) -> tuple[Optional[dict], Optional[str]]:
        """Fetch a single tweet by ID to get conversation_id and author.

        Args:
            token: Valid access token
            tweet_id: Tweet ID to fetch

        Returns:
            Tuple of (tweet_data dict, author_username) or (None, None) on error
        """
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(
                f"{BASE_URL}/tweets/{tweet_id}",
                params={
                    "tweet.fields": TWEET_FIELDS,
                    "expansions": EXPANSIONS,
                    "media.fields": MEDIA_FIELDS,
                    "user.fields": USER_FIELDS,
                },
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code != 200:
                logger.error(
                    "Failed to fetch tweet %s: %s %s",
                    tweet_id,
                    response.status_code,
                    response.text,
                )
                return None, None

            data = response.json()

        tweet = data.get("data", {})
        includes = data.get("includes", {})

        # Get author username from includes
        users = includes.get("users", [])
        author_id = tweet.get("author_id", "")
        author_username = None
        for user in users:
            if user.get("id") == author_id:
                author_username = user.get("username")
                break

        return tweet, author_username

    async def _search_conversation(
        self, token: str, conversation_id: str, author_username: str
    ) -> list[dict]:
        """Search for all tweets in a conversation by the author.

        Uses GET /2/tweets/search/recent with conversation_id filter.
        Note: Only covers last 7 days of tweets.

        Args:
            token: Valid access token
            conversation_id: The conversation ID (root tweet ID)
            author_username: Author's username for from: filter

        Returns:
            List of tweet dicts sorted chronologically, empty if search fails
        """
        query = f"conversation_id:{conversation_id} from:{author_username}"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
                response = await client.get(
                    f"{BASE_URL}/tweets/search/recent",
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

                if response.status_code == 429:
                    logger.warning("X API rate limited on search. Falling back.")
                    return []

                if response.status_code == 403:
                    logger.warning(
                        "X API search not available (403). "
                        "May need higher API tier."
                    )
                    return []

                if response.status_code != 200:
                    logger.error(
                        "X API search error %d: %s",
                        response.status_code,
                        response.text,
                    )
                    return []

                data = response.json()

        except httpx.HTTPError as e:
            logger.error("HTTP error during thread search: %s", e)
            return []

        raw_tweets = data.get("data", [])
        if not raw_tweets:
            return []

        includes = data.get("includes", {})
        media_map = {m["media_key"]: m for m in includes.get("media", [])}

        # Convert API tweets to the simple dict format used by formatting methods
        tweets = []
        for raw_tweet in raw_tweets:
            tweet_dict = self._api_tweet_to_dict(
                raw_tweet, author_username, media_map
            )
            tweets.append(tweet_dict)

        # Sort chronologically by ID (lower ID = older tweet)
        tweets.sort(key=lambda t: int(t.get("id", "0")))

        return tweets

    async def _build_fallback_tweets(
        self, token: str, bookmark: "Bookmark", author: str
    ) -> list[dict]:
        """Build a minimal tweet list when search is unavailable.

        Uses the bookmark's existing data, supplemented by a single API fetch
        if the bookmark text is empty (webhook-created bookmarks).

        Args:
            token: Valid access token
            bookmark: The original bookmark
            author: Author username

        Returns:
            List with a single tweet dict, or empty if nothing available
        """
        text = bookmark.text
        if not text:
            # Webhook bookmark with no text - fetch the tweet
            tweet_data, _ = await self._fetch_single_tweet(token, bookmark.id)
            if tweet_data:
                note_tweet = tweet_data.get("note_tweet")
                text = (
                    note_tweet.get("text", "")
                    if note_tweet and note_tweet.get("text")
                    else tweet_data.get("text", "")
                )

        if not text:
            return []

        return [
            {
                "id": bookmark.id,
                "text": text,
                "url": bookmark.url
                or f"https://twitter.com/{author}/status/{bookmark.id}",
                "media_urls": bookmark.media_urls,
                "links": [
                    link
                    for link in bookmark.links
                    if "twitter.com" not in link and "x.com" not in link
                ],
            }
        ]

    @staticmethod
    def _api_tweet_to_dict(
        raw_tweet: dict, author_username: str, media_map: dict
    ) -> dict:
        """Convert an X API tweet object to the simple dict format.

        The dict format matches what the formatting methods expect:
        {id, text, url, media_urls, links}

        Args:
            raw_tweet: Tweet data from API response
            author_username: Author's username for URL construction
            media_map: Media key → media data mapping

        Returns:
            Simplified tweet dict
        """
        tweet_id = raw_tweet["id"]

        # Use note_tweet for long tweets
        text = raw_tweet.get("text", "")
        note_tweet = raw_tweet.get("note_tweet")
        if note_tweet and note_tweet.get("text"):
            text = note_tweet["text"]

        # Media
        media_urls = []
        attachments = raw_tweet.get("attachments", {})
        for key in attachments.get("media_keys", []):
            media = media_map.get(key)
            if not media:
                continue
            if media.get("type") == "photo" and media.get("url"):
                media_urls.append(media["url"])
            elif media.get("preview_image_url"):
                media_urls.append(media["preview_image_url"])

        # External links from entities
        links = []
        entities = raw_tweet.get("entities", {})
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
            links.append(expanded)

        url = f"https://twitter.com/{author_username}/status/{tweet_id}"

        return {
            "id": tweet_id,
            "text": text,
            "url": url,
            "media_urls": media_urls,
            "links": links,
        }

    def _parse_thread_data(self, data: dict) -> ProcessResult:
        """Parse thread data into ProcessResult.

        Args:
            data: Dict with keys: tweets (list), author (str), source (str)

        Returns:
            ProcessResult with extracted content
        """
        tweets = data.get("tweets", [])
        author = data.get("author", "unknown")

        title = self._generate_title(tweets, author)
        tags = self._extract_tags(tweets)
        content = self._format_content(data, tweets)
        key_points = self._extract_key_points(tweets)

        metadata = {
            "tweets": tweets,
            "tweet_count": len(tweets),
            "author": author,
            "source": data.get("source", ""),
            "key_points": key_points,
        }

        return ProcessResult(
            success=True,
            content=content,
            title=title,
            tags=tags,
            metadata=metadata,
        )

    def _generate_title(self, tweets: list, author: str) -> str:
        """Generate a title from the first tweet.

        Args:
            tweets: List of tweet dicts
            author: Thread author username

        Returns:
            Generated title (max 8 words from first tweet)
        """
        if not tweets:
            return f"Thread by @{author}"

        first_tweet = tweets[0]
        text = first_tweet.get("text", "")

        # Clean text (remove URLs, mentions at start)
        clean_text = re.sub(r"https?://\S+", "", text)
        clean_text = re.sub(r"^(@\w+\s*)+", "", clean_text).strip()

        # Get first 8 words
        words = clean_text.split()[:8]
        if words:
            title = " ".join(words)
            if len(clean_text.split()) > 8:
                title += "..."
            return title

        return f"Thread by @{author}"

    def _extract_tags(self, tweets: list) -> list[str]:
        """Extract hashtags from all tweets as tags.

        Args:
            tweets: List of tweet dicts

        Returns:
            List of unique tags (without #)
        """
        tags = set()
        for tweet in tweets:
            text = tweet.get("text", "")
            hashtags = re.findall(r"#(\w+)", text)
            tags.update(tag.lower() for tag in hashtags)

        return list(tags)

    def _format_content(self, data: dict, tweets: list) -> str:
        """Format thread as markdown content.

        Args:
            data: Full thread data dict
            tweets: List of tweet dicts

        Returns:
            Formatted markdown content
        """
        lines = []
        author = data.get("author", "unknown")
        source = data.get("source", "")

        # Thread info
        lines.append(f"**Author**: @{author}")
        lines.append(f"**Tweets**: {len(tweets)}")
        if source:
            lines.append(f"**Source**: {source}")
        lines.append("")

        # Each tweet numbered
        for i, tweet in enumerate(tweets, 1):
            lines.append(f"### Tweet {i}")
            lines.append("")

            # Tweet text as blockquote
            text = tweet.get("text", "")
            if text:
                lines.append("> " + text.replace("\n", "\n> "))
                lines.append("")

            # Media
            media_urls = tweet.get("media_urls", [])
            if media_urls:
                for url in media_urls:
                    lines.append(f"![image]({url})")
                lines.append("")

            # Links (excluding twitter.com)
            links = tweet.get("links", [])
            external_links = [
                link for link in links
                if "twitter.com" not in link and "x.com" not in link
            ]
            if external_links:
                lines.append("**Links:**")
                for link in external_links:
                    lines.append(f"- {link}")
                lines.append("")

        # Original thread URL
        if tweets and tweets[0].get("url"):
            lines.append("---")
            lines.append(f"[View original thread]({tweets[0]['url']})")

        return "\n".join(lines)

    def _extract_key_points(self, tweets: list) -> list[str]:
        """Extract key points from thread content using LLM.

        Args:
            tweets: List of tweet dicts

        Returns:
            List of key points (3-5 bullet points), empty if LLM unavailable or fails
        """
        if not tweets:
            return []

        # Get LLM client (use injected or global singleton)
        llm_client = self._llm_client
        if llm_client is None:
            try:
                llm_client = get_llm_client()
            except Exception:
                # LLM not available (no API key, etc.) - return empty
                return []

        # Build thread text for analysis
        thread_text = "\n\n".join(
            f"Tweet {i}: {tweet.get('text', '')}"
            for i, tweet in enumerate(tweets, 1)
        )

        system_prompt = """You are analyzing a Twitter thread. Extract 3-5 key points that summarize the main ideas.

Return your response as a JSON object with a single key "key_points" containing an array of strings.
Each key point should be a concise sentence (under 100 characters).

Example response:
{
  "key_points": [
    "First key insight from the thread",
    "Second important point",
    "Third takeaway"
  ]
}"""

        try:
            result = llm_client.extract_structured(thread_text, system_prompt)
            key_points = result.get("key_points", [])
            # Validate: must be list of strings
            if isinstance(key_points, list) and all(isinstance(p, str) for p in key_points):
                return key_points[:5]  # Max 5 points
            return []
        except ExtractionError:
            # LLM extraction failed - return empty (graceful degradation)
            return []
