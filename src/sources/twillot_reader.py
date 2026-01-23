"""Twillot export parser for Twitter Bookmark Processor.

This module parses JSON exports from the Twillot browser extension,
converting them to Bookmark dataclass instances for processing.

Twillot exports contain bookmark data in a structured JSON format with
fields like tweet_id, full_text, screen_name, media_items, etc.
"""

import json
import re
from pathlib import Path
from typing import Union

from src.core.bookmark import Bookmark
from src.core.exceptions import ParseError


def _extract_links_from_text(text: str) -> list[str]:
    """Extract URLs from tweet text.

    Args:
        text: Tweet content that may contain URLs

    Returns:
        List of extracted URLs (excluding t.co shortened links)
    """
    # Match URLs in text - basic pattern for http/https URLs
    url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
    urls = re.findall(url_pattern, text)
    # Filter out t.co links (Twitter's URL shortener) as they're just redirects
    # Use regex to match t.co as a domain, not as part of other domains
    return [url for url in urls if not re.match(r"https?://t\.co/", url)]


def parse_twillot_export(source: Union[str, Path, list[dict]]) -> list[Bookmark]:
    """Parse a Twillot JSON export into Bookmark instances.

    Args:
        source: Either a file path (str or Path), or a list of dicts
                representing the parsed JSON data

    Returns:
        List of Bookmark instances

    Raises:
        ParseError: If the JSON is malformed or required fields are missing
        FileNotFoundError: If the source file doesn't exist
    """
    # Handle different input types
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Export file not found: {path}")
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ParseError(f"Invalid JSON in export file: {e}")
    else:
        data = source

    if not isinstance(data, list):
        raise ParseError("Twillot export must be a JSON array")

    bookmarks = []
    for i, item in enumerate(data):
        try:
            bookmark = _parse_single_bookmark(item)
            bookmarks.append(bookmark)
        except (KeyError, TypeError) as e:
            raise ParseError(f"Failed to parse bookmark at index {i}: {e}")

    return bookmarks


def _parse_single_bookmark(item: dict) -> Bookmark:
    """Parse a single Twillot bookmark entry.

    Args:
        item: Dictionary from Twillot JSON export

    Returns:
        Bookmark instance

    Raises:
        KeyError: If required fields are missing
        TypeError: If field types are incorrect
    """
    # Required fields - will raise KeyError if missing
    tweet_id = str(item["tweet_id"])
    url = item["url"]
    text = item["full_text"]
    author_username = item["screen_name"]

    # Optional author info
    author_name = item.get("username", "")
    author_id = item.get("user_id")
    if author_id is not None:
        author_id = str(author_id)
    created_at = item.get("created_at", "")

    # Media handling
    media_items = item.get("media_items", [])
    # media_items is a list of URLs (strings) for images
    media_urls = media_items if isinstance(media_items, list) else []

    # Video URLs - Twillot doesn't provide direct video URLs,
    # but has_video flag in the export indicates video presence
    video_urls: list[str] = []

    # Extract links from text
    links = _extract_links_from_text(text)

    # Thread detection fields
    conversation_id = item.get("conversation_id")
    if conversation_id is not None:
        conversation_id = str(conversation_id)

    # in_reply_to_user_id is not directly in Twillot export,
    # but we can infer from is_reply
    in_reply_to_user_id = None

    return Bookmark(
        id=tweet_id,
        url=url,
        text=text,
        author_username=author_username,
        author_name=author_name,
        author_id=author_id,
        created_at=created_at,
        conversation_id=conversation_id,
        in_reply_to_user_id=in_reply_to_user_id,
        media_urls=media_urls,
        video_urls=video_urls,
        links=links,
    )
