"""Tests for Stage 2: Insight Distillation."""

from datetime import datetime

from src.insight.distill import _build_user_prompt, SYSTEM_PROMPT
from src.insight.models import (
    AnalyzedImage,
    ContentPackage,
    ResolvedLink,
    ThreadTweet,
    ValueType,
)


class TestBuildUserPrompt:
    def _make_package(self, **kwargs) -> ContentPackage:
        defaults = dict(
            bookmark_id="123",
            tweet_text="Test tweet content",
            author_name="Test User",
            author_username="testuser",
            tweet_url="https://x.com/testuser/status/123",
            created_at=datetime(2026, 1, 15),
        )
        defaults.update(kwargs)
        return ContentPackage(**defaults)

    def test_basic_tweet(self):
        pkg = self._make_package()
        prompt = _build_user_prompt(pkg)
        assert "<tweet_content>" in prompt
        assert "Test tweet content" in prompt
        assert "@testuser" in prompt

    def test_includes_thread(self):
        pkg = self._make_package(
            thread_tweets=[
                ThreadTweet(order=0, text="First tweet in thread"),
                ThreadTweet(order=1, text="Second tweet"),
            ]
        )
        prompt = _build_user_prompt(pkg)
        assert "<thread_content>" in prompt
        assert "First tweet in thread" in prompt
        assert "Thread (2 tweets)" in prompt

    def test_includes_links(self):
        pkg = self._make_package(
            resolved_links=[
                ResolvedLink(
                    original_url="https://t.co/abc",
                    resolved_url="https://example.com/article",
                    title="Great Article",
                    content="Full article content here",
                )
            ]
        )
        prompt = _build_user_prompt(pkg)
        assert "<linked_content>" in prompt
        assert "Great Article" in prompt
        assert "Full article content here" in prompt

    def test_includes_images(self):
        pkg = self._make_package(
            analyzed_images=[
                AnalyzedImage(
                    url="https://pbs.twimg.com/media/test.jpg",
                    vision_analysis="Image shows a code snippet in Python",
                )
            ]
        )
        prompt = _build_user_prompt(pkg)
        assert "<image_analysis>" in prompt
        assert "code snippet in Python" in prompt

    def test_includes_image_source(self):
        pkg = self._make_package(
            analyzed_images=[
                AnalyzedImage(
                    url="https://pbs.twimg.com/media/test.jpg",
                    vision_analysis="Screenshot of YouTube video",
                    identified_source="https://youtube.com/watch?v=abc",
                    source_content="Video transcript...",
                )
            ]
        )
        prompt = _build_user_prompt(pkg)
        assert "Identified source: https://youtube.com" in prompt
        assert "Video transcript..." in prompt

    def test_includes_video_transcript(self):
        pkg = self._make_package(video_transcript="This is the video transcript")
        prompt = _build_user_prompt(pkg)
        assert "<video_transcript>" in prompt
        assert "This is the video transcript" in prompt

    def test_includes_quoted_tweet(self):
        quoted = ContentPackage(
            bookmark_id="789",
            tweet_text="Original quoted content",
            author_name="Original",
            author_username="original",
            tweet_url="https://x.com/original/status/789",
            created_at=datetime(2026, 1, 10),
        )
        pkg = self._make_package(quoted_content=quoted)
        prompt = _build_user_prompt(pkg)
        assert "<quoted_tweet>" in prompt
        assert "@original" in prompt
        assert "Original quoted content" in prompt

    def test_link_errors_shown(self):
        pkg = self._make_package(
            resolved_links=[
                ResolvedLink(
                    original_url="https://example.com",
                    resolved_url="https://example.com",
                    fetch_error="Connection timeout",
                )
            ]
        )
        prompt = _build_user_prompt(pkg)
        assert "Fetch error: Connection timeout" in prompt

    def test_xml_delimiters_present(self):
        """All untrusted content must be wrapped in XML tags."""
        pkg = self._make_package(
            thread_tweets=[ThreadTweet(order=0, text="test")],
            resolved_links=[ResolvedLink(
                original_url="a", resolved_url="b", content="c"
            )],
            analyzed_images=[AnalyzedImage(
                url="img", vision_analysis="test"
            )],
            video_transcript="transcript",
            quoted_content=ContentPackage(
                bookmark_id="q",
                tweet_text="quoted",
                author_name="q",
                author_username="q",
                tweet_url="https://x.com/q/status/q",
                created_at=datetime.now(),
            ),
        )
        prompt = _build_user_prompt(pkg)
        assert "<tweet_content>" in prompt
        assert "</tweet_content>" in prompt
        assert "<thread_content>" in prompt
        assert "</thread_content>" in prompt
        assert "<linked_content>" in prompt
        assert "</linked_content>" in prompt
        assert "<image_analysis>" in prompt
        assert "</image_analysis>" in prompt
        assert "<video_transcript>" in prompt
        assert "</video_transcript>" in prompt
        assert "<quoted_tweet>" in prompt
        assert "</quoted_tweet>" in prompt


class TestSystemPrompt:
    def test_all_value_types_documented(self):
        """System prompt should mention all 7 value types."""
        for vt in ValueType:
            assert vt.value in SYSTEM_PROMPT.lower(), f"Missing {vt.value} in system prompt"

    def test_mentions_xml_delimiters(self):
        assert "XML" in SYSTEM_PROMPT

    def test_knowledge_first_principle(self):
        assert "Knowledge first" in SYSTEM_PROMPT or "knowledge first" in SYSTEM_PROMPT.lower()
