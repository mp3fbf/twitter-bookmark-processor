"""Content classifier for Twitter Bookmark Processor.

This module classifies bookmarks into content types (VIDEO, THREAD, LINK, TWEET)
based on their media, links, and conversation metadata.
"""

import logging
import re
from typing import TYPE_CHECKING

from src.core.bookmark import ContentType

if TYPE_CHECKING:
    from src.core.bookmark import Bookmark

logger = logging.getLogger(__name__)

# YouTube URL patterns
YOUTUBE_PATTERNS = [
    re.compile(r"https?://(www\.)?youtube\.com/"),
    re.compile(r"https?://youtu\.be/"),
]

# Known video platforms (for logging warnings about unsupported ones)
UNSUPPORTED_VIDEO_PLATFORMS = [
    re.compile(r"https?://(www\.)?vimeo\.com/"),
    re.compile(r"https?://(www\.)?dailymotion\.com/"),
    re.compile(r"https?://(www\.)?twitch\.tv/"),
]

# Thread heuristic patterns
THREAD_NUMBER_PATTERN = re.compile(r"^\d+[/.]")  # Starts with "1/" or "1."
THREAD_EMOJI = "🧵"
THREAD_WORD_PATTERN = re.compile(r"\(thread\)", re.IGNORECASE)


def _is_youtube_link(url: str) -> bool:
    """Check if URL is a YouTube link."""
    return any(pattern.match(url) for pattern in YOUTUBE_PATTERNS)


def _is_unsupported_video_platform(url: str) -> bool:
    """Check if URL is from an unsupported video platform."""
    return any(pattern.match(url) for pattern in UNSUPPORTED_VIDEO_PLATFORMS)


def _is_thread_by_conversation(bookmark: "Bookmark") -> bool:
    """Check if bookmark is a thread via conversation_id.

    Definitive detection: conversation_id exists and differs from tweet id.
    """
    return (
        bookmark.conversation_id is not None
        and bookmark.conversation_id != bookmark.id
    )


def _is_thread_by_reply_chain(bookmark: "Bookmark") -> bool:
    """Check if bookmark is a thread via reply chain.

    Definitive detection: author is replying to themselves.
    """
    return (
        bookmark.in_reply_to_user_id is not None
        and bookmark.author_id is not None
        and bookmark.in_reply_to_user_id == bookmark.author_id
    )


def _count_thread_heuristic_signals(text: str) -> int:
    """Count thread heuristic signals in text.

    Returns count of signals found (0-3):
    - Starts with number pattern (e.g., "1/", "1.")
    - Contains thread emoji 🧵
    - Contains "(thread)" word
    """
    count = 0
    if THREAD_NUMBER_PATTERN.match(text):
        count += 1
    if THREAD_EMOJI in text:
        count += 1
    if THREAD_WORD_PATTERN.search(text):
        count += 1
    return count


def _is_thread_by_heuristics(bookmark: "Bookmark") -> bool:
    """Check if bookmark is a thread via text heuristics.

    Requires 2+ signals to classify as thread (to reduce false positives).
    """
    return _count_thread_heuristic_signals(bookmark.text) >= 2


def classify(bookmark: "Bookmark") -> ContentType:
    """Classify a bookmark into its content type.

    Classification priority:
    1. VIDEO - if video_urls is populated or links contain YouTube URLs
    2. THREAD - if conversation_id != id, or replying to self, or 2+ heuristics
    3. TWEET - default fallback

    Args:
        bookmark: The bookmark to classify

    Returns:
        ContentType indicating the classification result

    Note:
        Link detection will be added in subsequent issues.
    """
    # Check for native video (video_urls populated)
    if bookmark.video_urls:
        return ContentType.VIDEO

    # Check for YouTube links
    for link in bookmark.links:
        if _is_youtube_link(link):
            return ContentType.VIDEO

        # Log warning for unsupported video platforms
        if _is_unsupported_video_platform(link):
            logger.warning(
                "Unsupported video platform detected: %s (bookmark %s)",
                link,
                bookmark.id,
            )
            return ContentType.VIDEO

    # Check for thread (multiple detection methods)
    if _is_thread_by_conversation(bookmark):
        return ContentType.THREAD

    if _is_thread_by_reply_chain(bookmark):
        return ContentType.THREAD

    if _is_thread_by_heuristics(bookmark):
        return ContentType.THREAD

    # Default to TWEET
    return ContentType.TWEET
