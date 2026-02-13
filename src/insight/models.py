"""Data models for the Insight Engine pipeline.

ContentPackage is the contract between Stage 1 (capture) and Stage 2 (distill).
Persisted as JSON to enable re-processing Stage 2 without refetching.

InsightNote is the output of Stage 2 — structured content for the Obsidian template.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class FetchedContentType(str, Enum):
    """Type of content fetched from a resolved link."""

    ARTICLE = "article"
    REPO = "repo"
    VIDEO = "video"
    TOOL = "tool"
    OTHER = "other"


class ValueType(str, Enum):
    """Drives output structure. Validated on 10 real bookmarks during brainstorm.

    Each type produces different sections in the final note:
    - TECHNIQUE: The Knowledge + The Technique (steps) + The Insight
    - PERSPECTIVE: The Knowledge + The Argument + The Tension + The Insight
    - TOOL: The Knowledge (specs, links, pricing) + The Insight
    - RESOURCE: The Knowledge (full content preserved) + The Insight
    - TIP: The Knowledge + The Insight
    - SIGNAL: Minimal note (genuinely thin content)
    - REFERENCE: Minimal note (bookmarked for later)
    """

    TECHNIQUE = "technique"
    PERSPECTIVE = "perspective"
    TOOL = "tool"
    RESOURCE = "resource"
    TIP = "tip"
    SIGNAL = "signal"
    REFERENCE = "reference"


class ThreadTweet(BaseModel):
    order: int
    text: str
    media_urls: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)


class ResolvedLink(BaseModel):
    original_url: str
    resolved_url: str
    title: str | None = None
    content: str | None = None  # max 15K chars
    fetch_error: str | None = None
    content_type: FetchedContentType = FetchedContentType.OTHER


class AnalyzedImage(BaseModel):
    url: str
    local_path: str | None = None
    vision_analysis: str
    identified_source: str | None = None  # YouTube URL, repo URL, etc.
    source_content: str | None = None


class ContentPackage(BaseModel):
    schema_version: int = SCHEMA_VERSION
    bookmark_id: str
    tweet_text: str
    author_name: str
    author_username: str
    tweet_url: str
    created_at: datetime

    thread_tweets: list[ThreadTweet] = Field(default_factory=list)
    resolved_links: list[ResolvedLink] = Field(default_factory=list)
    analyzed_images: list[AnalyzedImage] = Field(default_factory=list)
    video_transcript: str | None = None
    quoted_content: ContentPackage | None = None

    captured_at: datetime = Field(default_factory=datetime.now)
    capture_duration_ms: int = 0
    token_estimate: int = 0


class Section(BaseModel):
    heading: str
    content: str


class InsightNote(BaseModel):
    value_type: ValueType
    title: str
    sections: list[Section]
    tags: list[str]
    original_content: str
