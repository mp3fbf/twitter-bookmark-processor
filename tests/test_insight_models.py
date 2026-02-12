"""Tests for insight data models."""

import json
from datetime import datetime

from src.insight.models import (
    AnalyzedImage,
    ContentPackage,
    FetchedContentType,
    InsightNote,
    ResolvedLink,
    Section,
    ThreadTweet,
    ValueType,
)


class TestContentPackage:
    def test_create_minimal(self):
        pkg = ContentPackage(
            bookmark_id="123",
            tweet_text="hello world",
            author_name="Test User",
            author_username="testuser",
            tweet_url="https://x.com/testuser/status/123",
            created_at=datetime(2026, 1, 1),
        )
        assert pkg.bookmark_id == "123"
        assert pkg.tweet_text == "hello world"
        assert pkg.thread_tweets == []
        assert pkg.resolved_links == []
        assert pkg.analyzed_images == []
        assert pkg.quoted_content is None

    def test_json_round_trip(self):
        pkg = ContentPackage(
            bookmark_id="456",
            tweet_text="test tweet",
            author_name="Author",
            author_username="author",
            tweet_url="https://x.com/author/status/456",
            created_at=datetime(2026, 2, 1),
            resolved_links=[
                ResolvedLink(
                    original_url="https://t.co/abc",
                    resolved_url="https://example.com/article",
                    title="An Article",
                    content="Article content here",
                    content_type=FetchedContentType.ARTICLE,
                )
            ],
            thread_tweets=[
                ThreadTweet(order=0, text="First tweet"),
                ThreadTweet(order=1, text="Second tweet", links=["https://example.com"]),
            ],
        )

        json_str = pkg.model_dump_json()
        loaded = ContentPackage.model_validate_json(json_str)

        assert loaded.bookmark_id == "456"
        assert len(loaded.resolved_links) == 1
        assert loaded.resolved_links[0].title == "An Article"
        assert len(loaded.thread_tweets) == 2
        assert loaded.thread_tweets[1].links == ["https://example.com"]

    def test_self_referential_quoted_content(self):
        """ContentPackage can contain a nested ContentPackage for quote-tweets."""
        quoted = ContentPackage(
            bookmark_id="789",
            tweet_text="original tweet",
            author_name="Original",
            author_username="original",
            tweet_url="https://x.com/original/status/789",
            created_at=datetime(2026, 1, 15),
        )
        pkg = ContentPackage(
            bookmark_id="101",
            tweet_text="quoting this",
            author_name="Quoter",
            author_username="quoter",
            tweet_url="https://x.com/quoter/status/101",
            created_at=datetime(2026, 1, 16),
            quoted_content=quoted,
        )

        json_str = pkg.model_dump_json()
        loaded = ContentPackage.model_validate_json(json_str)
        assert loaded.quoted_content is not None
        assert loaded.quoted_content.bookmark_id == "789"

    def test_schema_version(self):
        pkg = ContentPackage(
            bookmark_id="1",
            tweet_text="t",
            author_name="a",
            author_username="a",
            tweet_url="https://x.com/a/status/1",
            created_at=datetime.now(),
        )
        assert pkg.schema_version == 1


class TestInsightNote:
    def test_create(self):
        note = InsightNote(
            value_type=ValueType.TECHNIQUE,
            title="How to X by doing Y",
            sections=[
                Section(heading="The Knowledge", content="Details here"),
                Section(heading="The Technique", content="Step 1..."),
                Section(heading="The Insight", content="Why this matters"),
            ],
            tags=["productivity", "workflow"],
            original_content="Original tweet text",
        )
        assert note.value_type == ValueType.TECHNIQUE
        assert len(note.sections) == 3

    def test_json_schema(self):
        schema = InsightNote.model_json_schema()
        assert "properties" in schema
        assert "value_type" in schema["properties"]
        assert "sections" in schema["properties"]

    def test_all_value_types(self):
        """All 7 value types should be valid."""
        for vt in ValueType:
            note = InsightNote(
                value_type=vt,
                title=f"Test {vt.value}",
                sections=[Section(heading="Test", content="Content")],
                tags=["test"],
                original_content="test",
            )
            assert note.value_type == vt


class TestValueType:
    def test_all_types_exist(self):
        expected = {"technique", "perspective", "tool", "resource", "tip", "signal", "reference"}
        actual = {vt.value for vt in ValueType}
        assert actual == expected


class TestAnalyzedImage:
    def test_with_identified_source(self):
        img = AnalyzedImage(
            url="https://pbs.twimg.com/media/abc.jpg",
            vision_analysis="This shows a YouTube video titled 'How to Code'",
            identified_source="https://youtube.com/watch?v=abc123",
            source_content="Video transcript here...",
        )
        assert img.identified_source == "https://youtube.com/watch?v=abc123"
        assert img.source_content is not None
