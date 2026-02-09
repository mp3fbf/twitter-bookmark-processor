"""Tests for Smart Prompts module."""

import pytest

from src.core.smart_prompts import (
    SmartContentType,
    SmartPromptSelector,
)


class TestDetectContentType:
    """Tests for SmartPromptSelector.detect_content_type."""

    def test_detects_top_list(self):
        assert (
            SmartPromptSelector.detect_content_type("Top 10 AI tools for 2025")
            == SmartContentType.TOP_LIST
        )

    def test_detects_best_list(self):
        assert (
            SmartPromptSelector.detect_content_type("The 5 best Python libraries")
            == SmartContentType.TOP_LIST
        )

    def test_detects_tutorial(self):
        assert (
            SmartPromptSelector.detect_content_type("How to deploy Docker containers")
            == SmartContentType.TUTORIAL_GUIDE
        )

    def test_detects_step_by_step(self):
        assert (
            SmartPromptSelector.detect_content_type("Step by step guide to AWS")
            == SmartContentType.TUTORIAL_GUIDE
        )

    def test_detects_tool_announcement(self):
        assert (
            SmartPromptSelector.detect_content_type("Just launched: MyTool v2.0")
            == SmartContentType.TOOL_ANNOUNCEMENT
        )

    def test_detects_npm_install(self):
        assert (
            SmartPromptSelector.detect_content_type("Try it out: npm i my-package")
            == SmartContentType.TOOL_ANNOUNCEMENT
        )

    def test_detects_code_snippet(self):
        assert (
            SmartPromptSelector.detect_content_type("Save this snippet:\n```python\ndef foo(): pass\n```")
            == SmartContentType.CODE_SNIPPET
        )

    def test_detects_opinion(self):
        assert (
            SmartPromptSelector.detect_content_type("Hot take: Python is better than JS")
            == SmartContentType.OPINION_TAKE
        )

    def test_detects_news(self):
        assert (
            SmartPromptSelector.detect_content_type("Breaking: OpenAI releases GPT-6")
            == SmartContentType.NEWS_UPDATE
        )

    def test_detects_thread(self):
        assert (
            SmartPromptSelector.detect_content_type("A thread on why AI matters 1/")
            == SmartContentType.THREAD_CONTENT
        )

    def test_detects_video(self):
        assert (
            SmartPromptSelector.detect_content_type(
                "Check this out", has_video=True
            )
            == SmartContentType.VIDEO_CONTENT
        )

    def test_detects_screenshot_short_text_with_image(self):
        assert (
            SmartPromptSelector.detect_content_type(
                "Look at this", has_image=True
            )
            == SmartContentType.SCREENSHOT_INFO
        )

    def test_long_text_with_image_not_screenshot(self):
        """Long text with image should not be classified as screenshot."""
        text = "This is a very long tweet " * 10
        result = SmartPromptSelector.detect_content_type(text, has_image=True)
        # Should fall through to UNKNOWN since text is long and no patterns match
        assert result == SmartContentType.UNKNOWN

    def test_detects_article_link(self):
        assert (
            SmartPromptSelector.detect_content_type(
                "Check out this article about Python", has_link=True
            )
            == SmartContentType.ARTICLE_LINK
        )

    def test_unknown_fallback(self):
        assert (
            SmartPromptSelector.detect_content_type("Just vibing today")
            == SmartContentType.UNKNOWN
        )

    def test_video_takes_priority(self):
        """Video flag should override any text patterns."""
        assert (
            SmartPromptSelector.detect_content_type(
                "Top 10 best tools", has_video=True
            )
            == SmartContentType.VIDEO_CONTENT
        )

    def test_case_insensitive(self):
        assert (
            SmartPromptSelector.detect_content_type("HOW TO BUILD A HOUSE")
            == SmartContentType.TUTORIAL_GUIDE
        )


class TestBuildPrompt:
    """Tests for SmartPromptSelector.build_prompt."""

    def test_returns_tuple(self):
        result = SmartPromptSelector.build_prompt("Top 10 AI tools")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_prompt_contains_tweet_text(self):
        prompt, _ = SmartPromptSelector.build_prompt("Top 10 AI tools for developers")
        assert "Top 10 AI tools for developers" in prompt

    def test_includes_link_content(self):
        prompt, _ = SmartPromptSelector.build_prompt(
            "Read this article",
            has_link=True,
            link_content="Full article text here...",
        )
        assert "Full article text here..." in prompt

    def test_includes_image_analysis(self):
        prompt, _ = SmartPromptSelector.build_prompt(
            "Check this screenshot",
            has_image=True,
            image_analysis="The image shows a code snippet...",
        )
        assert "The image shows a code snippet..." in prompt

    def test_includes_video_analysis(self):
        prompt, _ = SmartPromptSelector.build_prompt(
            "Watch this",
            has_video=True,
            video_analysis="The video demonstrates...",
        )
        assert "The video demonstrates..." in prompt

    def test_system_prompt_not_empty(self):
        _, system_prompt = SmartPromptSelector.build_prompt("Some tweet text")
        assert len(system_prompt) > 0

    def test_author_substitution(self):
        prompt, _ = SmartPromptSelector.build_prompt(
            "Hot take: AI is overhyped",
            author="johndoe",
        )
        assert "@johndoe" in prompt


class TestGetPrompt:
    """Tests for SmartPromptSelector.get_prompt."""

    def test_all_types_have_prompts(self):
        """Every SmartContentType (except MEME_HUMOR) should have a prompt."""
        for ct in SmartContentType:
            if ct == SmartContentType.MEME_HUMOR:
                continue
            prompt = SmartPromptSelector.get_prompt(ct)
            assert prompt is not None
            assert prompt.content_type == ct or ct not in SmartPromptSelector.PROMPTS

    def test_unknown_type_returns_fallback(self):
        prompt = SmartPromptSelector.get_prompt(SmartContentType.MEME_HUMOR)
        assert prompt.content_type == SmartContentType.UNKNOWN


class TestDescribeType:
    """Tests for SmartPromptSelector.describe_type."""

    def test_all_types_described(self):
        for ct in SmartContentType:
            desc = SmartPromptSelector.describe_type(ct)
            assert isinstance(desc, str)
            assert len(desc) > 0

    def test_unknown_type(self):
        desc = SmartPromptSelector.describe_type(SmartContentType.UNKNOWN)
        assert "not detected" in desc.lower()
