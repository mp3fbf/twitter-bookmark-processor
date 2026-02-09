"""Integration tests for Twitter Bookmark Processor.

End-to-end tests that verify the complete flow from export files
to generated Obsidian notes. All tests use temporary directories
to avoid polluting real workspace.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from src.core.bookmark import Bookmark
from src.core.pipeline import Pipeline
from src.webhook_server import create_app


class TestFullPipelineAllTypes:
    """Tests processing all content types through the pipeline."""

    @pytest.fixture
    def mock_video_skill_output(self):
        """Sample successful skill output for a video."""
        return {
            "success": True,
            "title": "Test Video Title",
            "channel": "Test Channel",
            "duration": "10:30",
            "source_url": "https://www.youtube.com/watch?v=test123",
            "tldr": "A test video summary.",
            "key_points": [
                {"timestamp": "1:00", "content": "First key point"},
                {"timestamp": "5:00", "content": "Second key point"},
            ],
            "tags": ["test", "video"],
        }

    @pytest.fixture
    def mock_thread_skill_output(self):
        """Sample successful skill output for a thread."""
        return {
            "success": True,
            "source": "bird",
            "author": "threadauthor",
            "tweet_count": 3,
            "tweets": [
                {
                    "id": "t001",
                    "text": "1/ First tweet in thread",
                    "author_username": "threadauthor",
                    "is_thread": True,
                    "thread_position": 1,
                    "media_urls": [],
                    "links": [],
                },
                {
                    "id": "t002",
                    "text": "2/ Second tweet continues",
                    "author_username": "threadauthor",
                    "is_thread": True,
                    "thread_position": 2,
                    "media_urls": [],
                    "links": [],
                },
                {
                    "id": "t003",
                    "text": "3/ Final tweet concludes",
                    "author_username": "threadauthor",
                    "is_thread": True,
                    "thread_position": 3,
                    "media_urls": [],
                    "links": [],
                },
            ],
        }

    @pytest.fixture
    def mock_link_llm_response(self):
        """Sample successful LLM extraction for a link."""
        return {
            "title": "Test Article",
            "tldr": "A test article summary.",
            "key_points": ["Point 1", "Point 2", "Point 3"],
            "tags": ["article", "test"],
        }

    @pytest.mark.asyncio
    async def test_full_pipeline_all_types(
        self,
        temp_output_dir: Path,
        temp_state_file: Path,
        tmp_path: Path,
        mock_video_skill_output,
        mock_thread_skill_output,
        mock_link_llm_response,
    ):
        """Process VIDEO, THREAD, LINK, TWEET in one export file."""
        # Create mock X API auth for thread processing
        mock_x_api_auth = AsyncMock()
        mock_x_api_auth.get_valid_token = AsyncMock(return_value="fake-token")

        pipeline = Pipeline(
            output_dir=temp_output_dir,
            state_file=temp_state_file,
            x_api_auth=mock_x_api_auth,
        )

        # Create export with all 4 content types
        # NOTE: Twillot reader extracts links from full_text, not from 'urls' field
        export_data = [
            # TWEET - simple text tweet (no external links)
            {
                "tweet_id": "int_tweet_001",
                "url": "https://twitter.com/user/status/int_tweet_001",
                "full_text": "Just a simple tweet for testing #test",
                "screen_name": "testuser",
            },
            # VIDEO - has YouTube URL in text
            {
                "tweet_id": "int_video_001",
                "url": "https://twitter.com/user/status/int_video_001",
                "full_text": "Check this video https://youtube.com/watch?v=test123",
                "screen_name": "videouser",
            },
            # THREAD - has thread signals (2+ heuristics: "1/", "🧵", "(thread)")
            {
                "tweet_id": "int_thread_001",
                "url": "https://x.com/threadauthor/status/int_thread_001",
                "full_text": "1/ A thread 🧵 (thread)",
                "screen_name": "threadauthor",
            },
            # LINK - has external URL in text (not Twitter/YouTube)
            {
                "tweet_id": "int_link_001",
                "url": "https://twitter.com/user/status/int_link_001",
                "full_text": "Great article here https://example.com/article",
                "screen_name": "linksharer",
            },
        ]
        export_path = tmp_path / "all_types_export.json"
        export_path.write_text(json.dumps(export_data))

        # Mock subprocess for VIDEO processor
        mock_video_result = MagicMock()
        mock_video_result.returncode = 0
        mock_video_result.stdout = json.dumps(mock_video_skill_output)
        mock_video_result.stderr = ""

        def subprocess_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
            if "youtube-video" in cmd_str or "yt" in cmd_str:
                return mock_video_result
            return MagicMock(returncode=1, stdout="", stderr="Unknown command")

        # Mock HTTP response for LINK processor
        mock_http_response = MagicMock()
        mock_http_response.text = """
        <html>
        <head><title>Test Article</title></head>
        <body><p>Article content here.</p></body>
        </html>
        """
        mock_http_response.raise_for_status = MagicMock()

        # Mock LLM client for LINK processor
        mock_llm = MagicMock()
        mock_llm.extract_structured.return_value = mock_link_llm_response

        # Create async context manager mock for httpx client
        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_http_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        # Mock httpx for thread processor's X API calls
        mock_search_response = MagicMock()
        mock_search_response.status_code = 200
        mock_search_response.json.return_value = {
            "data": [
                {
                    "id": "9000001",
                    "text": "1/ First tweet in thread",
                    "conversation_id": "9000001",
                    "author_id": "author1",
                },
                {
                    "id": "9000002",
                    "text": "2/ Second tweet continues",
                    "conversation_id": "9000001",
                    "author_id": "author1",
                },
                {
                    "id": "9000003",
                    "text": "3/ Final tweet concludes",
                    "conversation_id": "9000001",
                    "author_id": "author1",
                },
            ],
            "includes": {
                "users": [{"id": "author1", "username": "threadauthor"}],
            },
        }

        # Mock single tweet lookup (needed when conversation_id missing)
        mock_tweet_lookup_response = MagicMock()
        mock_tweet_lookup_response.status_code = 200
        mock_tweet_lookup_response.json.return_value = {
            "data": {
                "id": "int_thread_001",
                "text": "1/ A thread",
                "conversation_id": "9000001",
                "author_id": "author1",
            },
            "includes": {
                "users": [{"id": "author1", "username": "threadauthor"}],
            },
        }

        # Build httpx mock that handles thread API calls
        async def mock_httpx_get(url, **kwargs):
            url_str = str(url)
            if "tweets/search/recent" in url_str:
                return mock_search_response
            if "/tweets/" in url_str:
                return mock_tweet_lookup_response
            return mock_http_response

        mock_httpx_client = MagicMock()
        mock_httpx_client.get = AsyncMock(side_effect=mock_httpx_get)
        mock_httpx_client.__aenter__ = AsyncMock(return_value=mock_httpx_client)
        mock_httpx_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("subprocess.run", side_effect=subprocess_side_effect),
            patch(
                "src.processors.link_processor.create_client",
                return_value=mock_client,
            ),
            patch(
                "src.processors.link_processor.get_llm_client",
                return_value=mock_llm,
            ),
            patch(
                "src.processors.thread_processor.httpx.AsyncClient",
                return_value=mock_httpx_client,
            ),
        ):
            result = await pipeline.process_export(export_path)

        # All 4 should be processed
        assert result.processed == 4
        assert result.skipped == 0
        assert result.failed == 0
        assert result.errors == []

        # Verify 4 notes were created
        notes = list(temp_output_dir.glob("*.md"))
        assert len(notes) == 4

        # Verify each note has correct type in frontmatter
        note_types = set()
        for note in notes:
            content = note.read_text()
            if "type: tweet" in content:
                note_types.add("tweet")
            elif "type: video" in content:
                note_types.add("video")
            elif "type: thread" in content:
                note_types.add("thread")
            elif "type: link" in content:
                note_types.add("link")

        assert note_types == {"tweet", "video", "thread", "link"}

    @pytest.mark.asyncio
    async def test_integration_uses_temp_dirs(
        self,
        temp_output_dir: Path,
        temp_state_file: Path,
        tmp_path: Path,
    ):
        """Verify tests don't touch real /workspace/notes/ directory."""
        # This test verifies the fixture approach works
        assert "tmp" in str(temp_output_dir).lower() or "/tmp" in str(temp_output_dir)
        assert "workspace/notes" not in str(temp_output_dir)

        pipeline = Pipeline(output_dir=temp_output_dir, state_file=temp_state_file)

        # Create a simple export
        export_data = [
            {
                "tweet_id": "temp_test_001",
                "url": "https://twitter.com/user/status/temp_test_001",
                "full_text": "Testing temporary directories",
                "screen_name": "testuser",
            }
        ]
        export_path = tmp_path / "temp_test.json"
        export_path.write_text(json.dumps(export_data))

        result = await pipeline.process_export(export_path)

        assert result.processed == 1

        # Verify note was created in temp directory, not real workspace
        notes = list(temp_output_dir.glob("*.md"))
        assert len(notes) == 1
        assert temp_output_dir in notes[0].parents or notes[0].parent == temp_output_dir


class TestWebhookE2EHealthEndpoint(AioHTTPTestCase):
    """Test webhook health endpoint."""

    async def get_application(self) -> web.Application:
        """Return the application for testing."""
        return create_app()

    async def test_webhook_health_returns_ok(self):
        """GET /health returns status ok."""
        resp = await self.client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"

    async def test_webhook_metrics_returns_counters(self):
        """GET /metrics returns uptime and counters."""
        resp = await self.client.get("/metrics")
        assert resp.status == 200
        data = await resp.json()
        assert "uptime_seconds" in data
        assert "requests_total" in data
        assert "processed_total" in data
        assert "errors_total" in data


class TestWebhookE2EProcessEndpoint(AioHTTPTestCase):
    """Test webhook process endpoint."""

    async def get_application(self) -> web.Application:
        """Return the application for testing."""
        return create_app()

    async def test_webhook_process_accepts_valid_url(self):
        """POST /process with valid Twitter URL returns 202."""
        resp = await self.client.post(
            "/process",
            json={"url": "https://twitter.com/testuser/status/1234567890123456789"},
        )
        assert resp.status == 202
        data = await resp.json()
        assert data["status"] == "accepted"
        assert "task_id" in data
        assert data["tweet_id"] == "1234567890123456789"

    async def test_webhook_process_rejects_invalid_url(self):
        """POST /process with non-Twitter URL returns 400."""
        resp = await self.client.post(
            "/process",
            json={"url": "https://example.com/not-a-tweet"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data


class TestWebhookE2EAuth(AioHTTPTestCase):
    """Test webhook authentication."""

    async def get_application(self) -> web.Application:
        """Return the application for testing."""
        return create_app()

    async def test_webhook_auth_required_when_token_set(self):
        """POST /process requires auth when TWITTER_WEBHOOK_TOKEN is set."""
        import os

        # Set token for this test
        original_token = os.environ.get("TWITTER_WEBHOOK_TOKEN")
        os.environ["TWITTER_WEBHOOK_TOKEN"] = "test-secret-token"

        try:
            # Without auth - should fail
            resp_no_auth = await self.client.post(
                "/process",
                json={"url": "https://x.com/user/status/9999999999"},
            )
            assert resp_no_auth.status == 401

            # With correct auth - should succeed
            resp_with_auth = await self.client.post(
                "/process",
                json={"url": "https://x.com/user/status/9999999999"},
                headers={"Authorization": "Bearer test-secret-token"},
            )
            assert resp_with_auth.status == 202
        finally:
            # Restore original state
            if original_token is None:
                os.environ.pop("TWITTER_WEBHOOK_TOKEN", None)
            else:
                os.environ["TWITTER_WEBHOOK_TOKEN"] = original_token


class TestBacklogProcessing:
    """Tests for backlog file processing (drop file → processed)."""

    @pytest.mark.asyncio
    async def test_backlog_processing(
        self,
        temp_output_dir: Path,
        temp_state_file: Path,
        temp_backlog_dir: Path,
    ):
        """Drop export file in backlog → file processed → archived."""
        from src.core.backlog_manager import BacklogManager
        from src.core.pipeline import Pipeline
        from src.core.state_manager import StateManager
        from src.core.watcher import DirectoryWatcher

        # Initialize components
        backlog_manager = BacklogManager(temp_backlog_dir)
        state_manager = StateManager(temp_state_file)
        watcher = DirectoryWatcher(backlog_manager, state_manager)
        pipeline = Pipeline(temp_output_dir, temp_state_file)

        # "Drop" an export file in backlog
        export_data = [
            {
                "tweet_id": "backlog_001",
                "url": "https://twitter.com/user/status/backlog_001",
                "full_text": "Tweet from backlog processing test",
                "screen_name": "backloguser",
            },
            {
                "tweet_id": "backlog_002",
                "url": "https://twitter.com/user/status/backlog_002",
                "full_text": "Second tweet in backlog",
                "screen_name": "backloguser",
            },
        ]
        export_file = temp_backlog_dir / "test_export.json"
        export_file.write_text(json.dumps(export_data))

        # Verify file is detected as pending
        pending_files = watcher.get_new_files()
        assert len(pending_files) == 1
        assert pending_files[0] == export_file

        # Process the file
        result = await pipeline.process_export(export_file)
        assert result.processed == 2
        assert result.failed == 0

        # Archive the processed file
        archived = backlog_manager.archive_file(export_file)
        assert archived is not None
        assert archived.exists()
        assert not export_file.exists()

        # Mark as processed in watcher
        watcher.mark_file_processed(export_file)

        # Verify watcher doesn't return it anymore
        pending_files_after = watcher.get_new_files()
        assert len(pending_files_after) == 0

        # Verify notes were created
        notes = list(temp_output_dir.glob("*.md"))
        assert len(notes) == 2

    @pytest.mark.asyncio
    async def test_backlog_multiple_files(
        self,
        temp_output_dir: Path,
        temp_state_file: Path,
        temp_backlog_dir: Path,
    ):
        """Multiple export files in backlog are processed in order."""
        from src.core.backlog_manager import BacklogManager
        from src.core.pipeline import Pipeline
        from src.core.state_manager import StateManager
        from src.core.watcher import DirectoryWatcher

        # Initialize components
        backlog_manager = BacklogManager(temp_backlog_dir)
        state_manager = StateManager(temp_state_file)
        watcher = DirectoryWatcher(backlog_manager, state_manager)
        pipeline = Pipeline(temp_output_dir, temp_state_file)

        # Create multiple export files
        for i in range(3):
            export_data = [
                {
                    "tweet_id": f"multi_{i}_001",
                    "url": f"https://twitter.com/user/status/multi_{i}_001",
                    "full_text": f"Tweet from export file {i}",
                    "screen_name": f"user{i}",
                }
            ]
            export_file = temp_backlog_dir / f"export_{i}.json"
            export_file.write_text(json.dumps(export_data))

        # Get pending files
        pending_files = watcher.get_new_files()
        assert len(pending_files) == 3

        # Process all files
        total_processed = 0
        for export_file in pending_files:
            result = await pipeline.process_export(export_file)
            total_processed += result.processed
            backlog_manager.archive_file(export_file)
            watcher.mark_file_processed(export_file)

        assert total_processed == 3

        # Verify all notes created
        notes = list(temp_output_dir.glob("*.md"))
        assert len(notes) == 3

        # Verify all files archived
        archived_files = list(backlog_manager.processed_dir.glob("*.json"))
        assert len(archived_files) == 3


class TestMainEntryPoint:
    """Tests for main.py run_once integration."""

    @pytest.mark.asyncio
    async def test_run_once_integration(
        self,
        temp_output_dir: Path,
        temp_state_file: Path,
        temp_backlog_dir: Path,
        monkeypatch,
    ):
        """run_once processes backlog and returns correct stats."""
        from src.main import run_once

        # Create export file in backlog
        export_data = [
            {
                "tweet_id": "main_001",
                "url": "https://twitter.com/user/status/main_001",
                "full_text": "Tweet for main entry point test",
                "screen_name": "mainuser",
            }
        ]
        export_file = temp_backlog_dir / "main_test.json"
        export_file.write_text(json.dumps(export_data))

        # Run once
        result = await run_once(
            backlog_dir=temp_backlog_dir,
            output_dir=temp_output_dir,
            state_file=temp_state_file,
        )

        assert result.processed == 1
        assert result.skipped == 0
        assert result.failed == 0

        # Verify note created
        notes = list(temp_output_dir.glob("*.md"))
        assert len(notes) == 1

        # Verify file archived
        archived_dir = temp_backlog_dir / "processed"
        archived_files = list(archived_dir.glob("*.json"))
        assert len(archived_files) == 1

    @pytest.mark.asyncio
    async def test_run_once_empty_backlog(
        self,
        temp_output_dir: Path,
        temp_state_file: Path,
        temp_backlog_dir: Path,
    ):
        """run_once with empty backlog returns zero counts."""
        from src.main import run_once

        # Run once on empty backlog
        result = await run_once(
            backlog_dir=temp_backlog_dir,
            output_dir=temp_output_dir,
            state_file=temp_state_file,
        )

        assert result.processed == 0
        assert result.skipped == 0
        assert result.failed == 0

        # No notes should be created
        notes = list(temp_output_dir.glob("*.md"))
        assert len(notes) == 0


class TestDeduplicationIntegration:
    """Tests for deduplication across processing runs."""

    @pytest.mark.asyncio
    async def test_duplicate_detection_across_files(
        self,
        temp_output_dir: Path,
        temp_state_file: Path,
        tmp_path: Path,
    ):
        """Same bookmark in two exports is only processed once."""
        pipeline = Pipeline(output_dir=temp_output_dir, state_file=temp_state_file)

        # First export
        export1_data = [
            {
                "tweet_id": "dup_001",
                "url": "https://twitter.com/user/status/dup_001",
                "full_text": "This tweet appears in both exports",
                "screen_name": "dupuser",
            }
        ]
        export1 = tmp_path / "export1.json"
        export1.write_text(json.dumps(export1_data))

        # Second export with same tweet ID
        export2_data = [
            {
                "tweet_id": "dup_001",  # Same ID
                "url": "https://twitter.com/user/status/dup_001",
                "full_text": "This tweet appears in both exports (again)",
                "screen_name": "dupuser",
            }
        ]
        export2 = tmp_path / "export2.json"
        export2.write_text(json.dumps(export2_data))

        # Process first export
        result1 = await pipeline.process_export(export1)
        assert result1.processed == 1
        assert result1.skipped == 0

        # Process second export - should skip the duplicate
        result2 = await pipeline.process_export(export2)
        assert result2.processed == 0
        assert result2.skipped == 1

        # Only one note should exist
        notes = list(temp_output_dir.glob("*.md"))
        assert len(notes) == 1


class TestCacheIntegration:
    """Tests for link cache integration."""

    @pytest.mark.asyncio
    async def test_link_cache_persists_across_calls(
        self,
        temp_output_dir: Path,
        temp_state_file: Path,
        tmp_path: Path,
    ):
        """Link cache saves extractions and reuses them."""
        from src.core.link_cache import LinkCache
        from src.processors.link_processor import LinkProcessor

        # Create cache file (LinkCache expects a file path, not a directory)
        cache_file = tmp_path / "link_cache.json"
        cache = LinkCache(cache_file)
        processor = LinkProcessor(cache=cache)

        # Create a bookmark with link
        bookmark = Bookmark(
            id="cache_test_001",
            url="https://twitter.com/user/status/cache_test_001",
            text="Check out this article https://example.com/cached-article",
            author_username="cacheuser",
            links=["https://example.com/cached-article"],
        )

        mock_llm_response = {
            "title": "Cached Article",
            "tldr": "A cached article summary.",
            "key_points": ["Point 1"],
            "tags": ["cached"],
        }

        # Mock HTTP response with enough content (>50 chars required for LLM)
        mock_http_response = MagicMock()
        mock_http_response.text = """
        <html>
        <head><title>Cached Article</title></head>
        <body>
            <p>This is a comprehensive article about caching strategies in web applications.</p>
            <p>We explore various techniques including in-memory caching, distributed caches,
            and CDN-based solutions. Performance improvements can be significant when properly
            implemented, often reducing response times by 50% or more.</p>
        </body>
        </html>
        """
        mock_http_response.raise_for_status = MagicMock()

        # Create a fresh mock for LLM that persists across both with blocks
        # Note: extract_structured is called with (text, prompt)
        llm_call_count = [0]

        def track_llm_call(text, prompt):
            llm_call_count[0] += 1
            return mock_llm_response

        mock_llm = MagicMock()
        mock_llm.extract_structured.side_effect = track_llm_call

        mock_client = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_http_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "src.processors.link_processor.create_client",
                return_value=mock_client,
            ),
            patch(
                "src.processors.link_processor.get_llm_client",
                return_value=mock_llm,
            ),
        ):
            # First call - should use LLM
            result1 = await processor.process(bookmark)
            assert result1.success
            assert llm_call_count[0] == 1

            # Verify cache now has the entry
            assert cache.has("https://example.com/cached-article")

            # Second call with fresh processor but same cache
            processor2 = LinkProcessor(cache=cache)
            bookmark2 = Bookmark(
                id="cache_test_002",
                url="https://twitter.com/user/status/cache_test_002",
                text="Same article again https://example.com/cached-article",
                author_username="cacheuser",
                links=["https://example.com/cached-article"],  # Same URL
            )

            # Second call - should use cache, not LLM
            result2 = await processor2.process(bookmark2)
            assert result2.success
            # LLM should NOT have been called again (still 1)
            assert llm_call_count[0] == 1
