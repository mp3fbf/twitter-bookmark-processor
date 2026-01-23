"""Bookmark data model for Twitter Bookmark Processor.

This module defines the core data structures used throughout the processor:
- ContentType: Classification of bookmark content
- ProcessingStatus: State tracking for processing pipeline
- Bookmark: Main dataclass representing a Twitter bookmark
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class ContentType(str, Enum):
    """Classification of bookmark content type.

    Determines which processor handles the bookmark:
    - VIDEO: Native Twitter video or YouTube links
    - THREAD: Multi-tweet thread from same author
    - LINK: External article/resource links
    - TWEET: Simple text or image tweets
    """

    VIDEO = "video"
    THREAD = "thread"
    LINK = "link"
    TWEET = "tweet"


class ProcessingStatus(str, Enum):
    """Processing state for bookmark pipeline.

    Tracks where a bookmark is in the processing lifecycle.
    """

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    ERROR = "error"


@dataclass
class Bookmark:
    """Represents a Twitter bookmark to be processed.

    Required fields:
        id: Tweet ID (unique identifier)
        url: Full URL to the tweet
        text: Tweet content
        author_username: Twitter handle (screen_name)

    Optional fields support thread detection, media handling,
    and processing state tracking.
    """

    # Required identification
    id: str
    url: str
    text: str
    author_username: str

    # Optional author info
    author_name: str = ""
    author_id: Optional[str] = None
    created_at: str = ""

    # Thread detection fields
    conversation_id: Optional[str] = None
    in_reply_to_user_id: Optional[str] = None

    # Classification (auto-detected by classifier)
    content_type: ContentType = ContentType.TWEET
    media_urls: list[str] = field(default_factory=list)
    video_urls: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    is_thread: bool = False

    # Processing metadata
    bookmarked_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    status: ProcessingStatus = ProcessingStatus.PENDING
    error_count: int = 0
    last_error: Optional[str] = None
    output_path: Optional[str] = None
