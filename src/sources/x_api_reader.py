"""X API Bookmark Reader.

Reads bookmarks from the X API v2 and converts them to Bookmark dataclass
instances (same interface as TwillotReader). Handles pagination, rate limits,
and deduplication via StateManager.

Endpoint: GET /2/users/:id/bookmarks
Rate limit: 180 requests per 15 min window (app-level)
Max results per page: 100
"""

import logging
import re
from typing import Optional

import httpx

from src.core.bookmark import Bookmark
from src.core.state_manager import StateManager
from src.sources.x_api_auth import XApiAuth

logger = logging.getLogger(__name__)

# X API v2 base URL
BASE_URL = "https://api.twitter.com/2"

# Fields to request from the API
TWEET_FIELDS = [
    "id",
    "text",
    "created_at",
    "conversation_id",
    "in_reply_to_user_id",
    "entities",
    "attachments",
    "author_id",
    "public_metrics",
    "note_tweet",
]

EXPANSIONS = ["attachments.media_keys", "author_id"]

MEDIA_FIELDS = ["media_key", "type", "url", "preview_image_url", "variants"]

USER_FIELDS = ["id", "username", "name"]


class XApiReader:
    """Reads bookmarks from X API and produces Bookmark instances.

    Uses XApiAuth for token management and StateManager for dedup.
    """

    def __init__(
        self,
        auth: XApiAuth,
        state_manager: Optional[StateManager] = None,
        max_results_per_page: int = 100,
    ):
        """Initialize X API bookmark reader.

        Args:
            auth: XApiAuth instance for token management
            state_manager: Optional StateManager for skipping already-processed bookmarks
            max_results_per_page: Results per API page (max 100)
        """
        self.auth = auth
        self.state_manager = state_manager
        self.max_results_per_page = min(max_results_per_page, 100)

    async def fetch_new_bookmarks(
        self,
        max_bookmarks: int = 200,
    ) -> list[Bookmark]:
        """Fetch bookmarks from X API, skipping already-processed ones.

        Args:
            max_bookmarks: Maximum total bookmarks to return (across pages)

        Returns:
            List of new (unprocessed) Bookmark instances
        """
        token = await self.auth.get_valid_token()

        # Get authenticated user ID
        user_id = await self._get_user_id(token)
        if not user_id:
            logger.error("Failed to get authenticated user ID")
            return []

        bookmarks: list[Bookmark] = []
        pagination_token: Optional[str] = None

        while len(bookmarks) < max_bookmarks:
            page_bookmarks, pagination_token = await self._fetch_page(
                user_id=user_id,
                token=token,
                pagination_token=pagination_token,
            )

            if not page_bookmarks:
                break

            # Filter out already-processed bookmarks
            for bm in page_bookmarks:
                if self.state_manager and self.state_manager.is_processed(bm.id):
                    logger.debug("Skipping already-processed bookmark: %s", bm.id)
                    continue
                bookmarks.append(bm)
                if len(bookmarks) >= max_bookmarks:
                    break

            if not pagination_token:
                break

        logger.info("Fetched %d new bookmarks from X API", len(bookmarks))
        return bookmarks

    async def _get_user_id(self, token: str) -> Optional[str]:
        """Get the authenticated user's ID.

        Args:
            token: Valid access token

        Returns:
            User ID string, or None on error
        """
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(
                f"{BASE_URL}/users/me",
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code != 200:
                logger.error("Failed to get user ID: %s", response.text)
                return None

            data = response.json()
            return data.get("data", {}).get("id")

    async def _fetch_page(
        self,
        user_id: str,
        token: str,
        pagination_token: Optional[str] = None,
    ) -> tuple[list[Bookmark], Optional[str]]:
        """Fetch one page of bookmarks from the API.

        Args:
            user_id: Authenticated user's ID
            token: Valid access token
            pagination_token: Cursor for next page (None for first page)

        Returns:
            Tuple of (bookmarks, next_pagination_token)
        """
        params = {
            "max_results": str(self.max_results_per_page),
            "tweet.fields": ",".join(TWEET_FIELDS),
            "expansions": ",".join(EXPANSIONS),
            "media.fields": ",".join(MEDIA_FIELDS),
            "user.fields": ",".join(USER_FIELDS),
        }
        if pagination_token:
            params["pagination_token"] = pagination_token

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.get(
                f"{BASE_URL}/users/{user_id}/bookmarks",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 429:
                logger.warning("X API rate limited (429). Stopping pagination.")
                return [], None

            if response.status_code != 200:
                logger.error(
                    "X API error %d: %s", response.status_code, response.text
                )
                return [], None

            data = response.json()

        tweets = data.get("data", [])
        includes = data.get("includes", {})
        next_token = data.get("meta", {}).get("next_token")

        # Build lookup maps from includes
        users_map = {u["id"]: u for u in includes.get("users", [])}
        media_map = {m["media_key"]: m for m in includes.get("media", [])}

        bookmarks = []
        for tweet in tweets:
            bookmark = self._tweet_to_bookmark(tweet, users_map, media_map)
            bookmarks.append(bookmark)

        return bookmarks, next_token

    def _tweet_to_bookmark(
        self,
        tweet: dict,
        users_map: dict,
        media_map: dict,
    ) -> Bookmark:
        """Convert an X API tweet object to a Bookmark instance.

        Args:
            tweet: Tweet data from API response
            users_map: User ID → user data mapping from includes
            media_map: Media key → media data mapping from includes

        Returns:
            Bookmark instance
        """
        tweet_id = tweet["id"]
        text = tweet.get("text", "")

        # Use note_tweet for long tweets (>280 chars)
        note_tweet = tweet.get("note_tweet")
        if note_tweet and note_tweet.get("text"):
            text = note_tweet["text"]

        # Author info
        author_id = tweet.get("author_id", "")
        author_data = users_map.get(author_id, {})
        author_username = author_data.get("username", "")
        author_name = author_data.get("name", "")

        # Media handling
        media_urls: list[str] = []
        video_urls: list[str] = []

        attachments = tweet.get("attachments", {})
        media_keys = attachments.get("media_keys", [])

        for key in media_keys:
            media = media_map.get(key)
            if not media:
                continue

            media_type = media.get("type", "")
            if media_type == "photo":
                url = media.get("url", "")
                if url:
                    media_urls.append(url)
            elif media_type in ("video", "animated_gif"):
                # Get best quality video variant
                variants = media.get("variants", [])
                best_video = self._get_best_video_variant(variants)
                if best_video:
                    video_urls.append(best_video)
                # Also store preview image
                preview = media.get("preview_image_url", "")
                if preview:
                    media_urls.append(preview)

        # Extract URLs from entities
        links = self._extract_links(tweet)

        # Build URL
        url = f"https://twitter.com/{author_username}/status/{tweet_id}"

        return Bookmark(
            id=tweet_id,
            url=url,
            text=text,
            author_username=author_username,
            author_name=author_name,
            author_id=author_id,
            created_at=tweet.get("created_at", ""),
            conversation_id=tweet.get("conversation_id"),
            in_reply_to_user_id=tweet.get("in_reply_to_user_id"),
            media_urls=media_urls,
            video_urls=video_urls,
            links=links,
        )

    @staticmethod
    def _extract_links(tweet: dict) -> list[str]:
        """Extract external URLs from tweet entities.

        Args:
            tweet: Tweet data with entities

        Returns:
            List of expanded URLs (excluding Twitter/X media URLs)
        """
        entities = tweet.get("entities", {})
        urls_entities = entities.get("urls", [])

        links = []
        for url_entity in urls_entities:
            expanded = url_entity.get("expanded_url", "")
            if not expanded:
                continue

            # Skip Twitter/X internal links (media, status links from same tweet)
            if re.match(r"https?://(twitter\.com|x\.com)/\w+/status/\d+/(photo|video)", expanded):
                continue
            if "pbs.twimg.com" in expanded or "video.twimg.com" in expanded:
                continue

            links.append(expanded)

        return links

    @staticmethod
    def _get_best_video_variant(variants: list[dict]) -> Optional[str]:
        """Pick the highest quality video variant.

        Args:
            variants: List of video variants from media object

        Returns:
            URL of the best quality video, or None
        """
        mp4_variants = [
            v for v in variants
            if v.get("content_type") == "video/mp4" and v.get("url")
        ]
        if not mp4_variants:
            return None

        # Sort by bitrate descending, pick highest
        mp4_variants.sort(key=lambda v: v.get("bit_rate", 0), reverse=True)
        return mp4_variants[0]["url"]
