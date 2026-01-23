"""Tests for base processor interface."""

import pytest

from src.core.bookmark import Bookmark
from src.processors.base import BaseProcessor, ProcessResult


def _make_bookmark(**kwargs) -> Bookmark:
    """Helper to create a bookmark with defaults."""
    defaults = {
        "id": "123456789",
        "url": "https://x.com/user/status/123456789",
        "text": "Test tweet",
        "author_username": "testuser",
    }
    defaults.update(kwargs)
    return Bookmark(**defaults)


class TestProcessResultDefaults:
    """Test ProcessResult default values."""

    def test_process_result_success_only(self):
        """Minimal ProcessResult with just success flag."""
        result = ProcessResult(success=True)

        assert result.success is True
        assert result.content is None
        assert result.title is None
        assert result.tags == []
        assert result.error is None
        assert result.duration_ms == 0

    def test_process_result_failure(self):
        """ProcessResult for failed processing."""
        result = ProcessResult(success=False, error="Something went wrong")

        assert result.success is False
        assert result.error == "Something went wrong"

    def test_process_result_full(self):
        """ProcessResult with all fields populated."""
        result = ProcessResult(
            success=True,
            content="# Title\n\nContent here",
            title="My Tweet Title",
            tags=["ai", "python", "thread"],
            error=None,
            duration_ms=150,
        )

        assert result.success is True
        assert result.content == "# Title\n\nContent here"
        assert result.title == "My Tweet Title"
        assert result.tags == ["ai", "python", "thread"]
        assert result.error is None
        assert result.duration_ms == 150


class TestProcessResultTags:
    """Test ProcessResult tags field behavior."""

    def test_tags_default_is_empty_list(self):
        """Tags defaults to empty list, not None."""
        result = ProcessResult(success=True)

        assert result.tags == []
        assert result.tags is not None

    def test_tags_mutable_default_isolation(self):
        """Each ProcessResult gets its own tags list."""
        result1 = ProcessResult(success=True)
        result2 = ProcessResult(success=True)

        result1.tags.append("test")

        assert result1.tags == ["test"]
        assert result2.tags == []

    def test_tags_can_be_provided(self):
        """Tags can be set explicitly."""
        result = ProcessResult(success=True, tags=["a", "b", "c"])

        assert result.tags == ["a", "b", "c"]


class TestProcessResultDuration:
    """Test ProcessResult duration_ms field."""

    def test_duration_default_zero(self):
        """Duration defaults to 0."""
        result = ProcessResult(success=True)

        assert result.duration_ms == 0

    def test_duration_positive_value(self):
        """Duration can be set to positive value."""
        result = ProcessResult(success=True, duration_ms=500)

        assert result.duration_ms == 500


class TestBaseProcessorAbstract:
    """Test BaseProcessor is properly abstract."""

    def test_cannot_instantiate_base_processor(self):
        """BaseProcessor cannot be instantiated directly."""
        with pytest.raises(TypeError, match="abstract"):
            BaseProcessor()  # type: ignore

    def test_subclass_must_implement_process(self):
        """Subclass without process method cannot be instantiated."""

        class IncompleteProcessor(BaseProcessor):
            pass

        with pytest.raises(TypeError, match="abstract"):
            IncompleteProcessor()  # type: ignore


class TestBaseProcessorSubclass:
    """Test concrete processor subclass behavior."""

    def test_subclass_with_process_can_be_instantiated(self):
        """Subclass implementing process() can be instantiated."""

        class ConcreteProcessor(BaseProcessor):
            async def process(self, bookmark: Bookmark) -> ProcessResult:
                return ProcessResult(success=True)

        processor = ConcreteProcessor()

        assert processor is not None
        assert isinstance(processor, BaseProcessor)

    @pytest.mark.asyncio
    async def test_subclass_process_returns_result(self):
        """Subclass process() returns ProcessResult."""

        class ConcreteProcessor(BaseProcessor):
            async def process(self, bookmark: Bookmark) -> ProcessResult:
                return ProcessResult(
                    success=True,
                    content=f"Processed: {bookmark.text}",
                    title="Test Title",
                )

        processor = ConcreteProcessor()
        bookmark = _make_bookmark(text="Hello world")

        result = await processor.process(bookmark)

        assert result.success is True
        assert result.content == "Processed: Hello world"
        assert result.title == "Test Title"

    @pytest.mark.asyncio
    async def test_subclass_can_use_bookmark_fields(self):
        """Subclass can access all bookmark fields during processing."""

        class ConcreteProcessor(BaseProcessor):
            async def process(self, bookmark: Bookmark) -> ProcessResult:
                tags = [f"author:{bookmark.author_username}"]
                return ProcessResult(
                    success=True,
                    title=f"Tweet by @{bookmark.author_username}",
                    tags=tags,
                )

        processor = ConcreteProcessor()
        bookmark = _make_bookmark(author_username="naval")

        result = await processor.process(bookmark)

        assert result.title == "Tweet by @naval"
        assert "author:naval" in result.tags

    @pytest.mark.asyncio
    async def test_subclass_can_return_error(self):
        """Subclass can return error result."""

        class FailingProcessor(BaseProcessor):
            async def process(self, bookmark: Bookmark) -> ProcessResult:
                return ProcessResult(
                    success=False,
                    error="Content deleted",
                )

        processor = FailingProcessor()
        bookmark = _make_bookmark()

        result = await processor.process(bookmark)

        assert result.success is False
        assert result.error == "Content deleted"


class TestProcessResultEquality:
    """Test ProcessResult equality comparisons."""

    def test_equal_results(self):
        """Two ProcessResults with same values are equal."""
        result1 = ProcessResult(success=True, title="Test", tags=["a"])
        result2 = ProcessResult(success=True, title="Test", tags=["a"])

        assert result1 == result2

    def test_different_results(self):
        """ProcessResults with different values are not equal."""
        result1 = ProcessResult(success=True, title="Test 1")
        result2 = ProcessResult(success=True, title="Test 2")

        assert result1 != result2
