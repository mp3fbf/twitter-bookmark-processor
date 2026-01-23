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


def _is_youtube_link(url: str) -> bool:
    """Check if URL is a YouTube link."""
    return any(pattern.match(url) for pattern in YOUTUBE_PATTERNS)


def _is_unsupported_video_platform(url: str) -> bool:
    """Check if URL is from an unsupported video platform."""
    return any(pattern.match(url) for pattern in UNSUPPORTED_VIDEO_PLATFORMS)


def classify(bookmark: "Bookmark") -> ContentType:
    """Classify a bookmark into its content type.

    Classification priority:
    1. VIDEO - if video_urls is populated or links contain YouTube URLs
    2. TWEET - default fallback

    Args:
        bookmark: The bookmark to classify

    Returns:
        ContentType indicating the classification result

    Note:
        Thread and Link detection will be added in subsequent issues.
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

    # Default to TWEET
    return ContentType.TWEET
