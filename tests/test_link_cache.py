"""Tests for LinkCache."""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src.core.link_cache import DEFAULT_TTL_DAYS, LinkCache, url_to_key


class TestUrlToKey:
    """Test URL to cache key conversion."""

    def test_url_to_key_returns_sha256_prefix(self):
        """url_to_key should return first 16 chars of SHA256 hash."""
        url = "https://example.com/article"
        expected = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        assert url_to_key(url) == expected

    def test_url_to_key_consistent(self):
        """Same URL should always produce the same key."""
        url = "https://example.com/test"
        assert url_to_key(url) == url_to_key(url)

    def test_url_to_key_different_urls(self):
        """Different URLs should produce different keys."""
        url1 = "https://example.com/a"
        url2 = "https://example.com/b"
        assert url_to_key(url1) != url_to_key(url2)

    def test_url_to_key_length(self):
        """Key should be exactly 16 characters."""
        url = "https://example.com/article"
        assert len(url_to_key(url)) == 16


class TestLinkCacheCreation:
    """Test LinkCache initialization."""

    def test_cache_creates_file_on_first_save(self, tmp_path: Path):
        """LinkCache should create cache file when data is saved."""
        cache_file = tmp_path / "cache.json"
        assert not cache_file.exists()

        cache = LinkCache(cache_file)
        cache.set("https://example.com", {"title": "Test"})

        assert cache_file.exists()

    def test_cache_creates_parent_directories(self, tmp_path: Path):
        """LinkCache should create parent directories if they don't exist."""
        cache_file = tmp_path / "deep" / "nested" / "cache.json"
        assert not cache_file.parent.exists()

        cache = LinkCache(cache_file)
        cache.set("https://example.com", {"title": "Test"})

        assert cache_file.exists()
        assert cache_file.parent.exists()

    def test_cache_accepts_string_path(self, tmp_path: Path):
        """LinkCache should accept both str and Path for cache_file."""
        cache_file = str(tmp_path / "cache.json")
        cache = LinkCache(cache_file)
        cache.set("https://example.com", {"title": "Test"})

        assert Path(cache_file).exists()

    def test_cache_default_ttl_30_days(self, tmp_path: Path):
        """LinkCache should default to 30 day TTL."""
        cache = LinkCache(tmp_path / "cache.json")
        assert cache.ttl == timedelta(days=30)
        assert DEFAULT_TTL_DAYS == 30

    def test_cache_custom_ttl(self, tmp_path: Path):
        """LinkCache should accept custom TTL."""
        cache = LinkCache(tmp_path / "cache.json", ttl_days=7)
        assert cache.ttl == timedelta(days=7)


class TestLinkCacheStoreRetrieve:
    """Test cache store and retrieve operations."""

    def test_cache_stores_by_url_hash(self, tmp_path: Path):
        """Cache should store entries keyed by URL hash."""
        cache_file = tmp_path / "cache.json"
        cache = LinkCache(cache_file)

        url = "https://example.com/article"
        data = {"title": "Test Article", "tldr": "A test"}
        cache.set(url, data)

        # Verify stored with hash key
        with open(cache_file) as f:
            stored = json.load(f)

        expected_key = url_to_key(url)
        assert expected_key in stored["entries"]
        assert stored["entries"][expected_key]["url"] == url

    def test_cache_retrieves_valid_entry(self, tmp_path: Path):
        """Cache should return stored data for valid (non-expired) entry."""
        cache = LinkCache(tmp_path / "cache.json")

        url = "https://example.com/article"
        data = {"title": "Test Article", "tldr": "A test", "key_points": ["point1"]}
        cache.set(url, data)

        retrieved = cache.get(url)
        assert retrieved == data

    def test_cache_returns_none_for_unknown_url(self, tmp_path: Path):
        """Cache should return None for URLs not in cache."""
        cache = LinkCache(tmp_path / "cache.json")
        assert cache.get("https://unknown.com") is None

    def test_cache_has_returns_true_for_cached(self, tmp_path: Path):
        """has() should return True for cached URLs."""
        cache = LinkCache(tmp_path / "cache.json")
        url = "https://example.com/article"
        cache.set(url, {"title": "Test"})

        assert cache.has(url) is True

    def test_cache_has_returns_false_for_uncached(self, tmp_path: Path):
        """has() should return False for uncached URLs."""
        cache = LinkCache(tmp_path / "cache.json")
        assert cache.has("https://unknown.com") is False


class TestLinkCacheExpiration:
    """Test cache TTL and expiration."""

    def test_cache_misses_expired_entry(self, tmp_path: Path):
        """Cache should return None for expired entries."""
        cache = LinkCache(tmp_path / "cache.json", ttl_days=30)

        url = "https://example.com/old-article"
        cache.set(url, {"title": "Old Article"})

        # Simulate 31 days passing
        old_time = datetime.now() - timedelta(days=31)
        with patch("src.core.link_cache.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.now()
            mock_dt.fromisoformat.return_value = old_time

            assert cache.get(url) is None

    def test_cache_ttl_30_days(self, tmp_path: Path):
        """Entry exactly at 30 days should still be valid."""
        cache = LinkCache(tmp_path / "cache.json", ttl_days=30)

        url = "https://example.com/article"
        cache.set(url, {"title": "Article"})

        # Simulate exactly 30 days passing (still valid)
        cached_time = datetime.now() - timedelta(days=29, hours=23)
        with patch("src.core.link_cache.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.now()
            mock_dt.fromisoformat.return_value = cached_time

            assert cache.get(url) is not None

    def test_cache_expired_has_returns_false(self, tmp_path: Path):
        """has() should return False for expired entries."""
        cache = LinkCache(tmp_path / "cache.json", ttl_days=1)

        url = "https://example.com/article"
        cache.set(url, {"title": "Article"})

        # Simulate 2 days passing
        old_time = datetime.now() - timedelta(days=2)
        with patch("src.core.link_cache.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.now()
            mock_dt.fromisoformat.return_value = old_time

            assert cache.has(url) is False


class TestLinkCachePersistence:
    """Test cache persistence across instances."""

    def test_cache_persists_across_instances(self, tmp_path: Path):
        """Data saved by one instance should be readable by another."""
        cache_file = tmp_path / "cache.json"
        url = "https://example.com/article"
        data = {"title": "Test", "tags": ["test", "article"]}

        # Save with first instance
        cache1 = LinkCache(cache_file)
        cache1.set(url, data)

        # Load with second instance
        cache2 = LinkCache(cache_file)
        retrieved = cache2.get(url)

        assert retrieved == data

    def test_cache_atomic_write(self, tmp_path: Path):
        """Cache file should be written atomically."""
        cache_file = tmp_path / "cache.json"
        cache = LinkCache(cache_file)

        url = "https://example.com/article"
        cache.set(url, {"title": "Test"})

        # Check no temp files left behind
        temp_files = list(tmp_path.glob(".link_cache_*.tmp"))
        assert len(temp_files) == 0


class TestLinkCacheClear:
    """Test cache clearing."""

    def test_clear_removes_all_entries(self, tmp_path: Path):
        """clear() should remove all cached entries."""
        cache = LinkCache(tmp_path / "cache.json")
        cache.set("https://a.com", {"title": "A"})
        cache.set("https://b.com", {"title": "B"})

        cache.clear()

        assert cache.get("https://a.com") is None
        assert cache.get("https://b.com") is None


class TestLinkCacheStats:
    """Test cache statistics."""

    def test_get_stats_counts_entries(self, tmp_path: Path):
        """get_stats() should return entry counts."""
        cache = LinkCache(tmp_path / "cache.json")
        cache.set("https://a.com", {"title": "A"})
        cache.set("https://b.com", {"title": "B"})

        stats = cache.get_stats()
        assert stats["total"] == 2
        assert stats["valid"] == 2
        assert stats["expired"] == 0

    def test_get_stats_counts_expired(self, tmp_path: Path):
        """get_stats() should count expired entries separately."""
        cache = LinkCache(tmp_path / "cache.json", ttl_days=1)
        cache.set("https://a.com", {"title": "A"})

        # Simulate entry being old
        old_time = datetime.now() - timedelta(days=2)
        with patch("src.core.link_cache.datetime") as mock_dt:
            mock_dt.now.return_value = datetime.now()
            mock_dt.fromisoformat.return_value = old_time

            stats = cache.get_stats()
            assert stats["total"] == 1
            assert stats["expired"] == 1
            assert stats["valid"] == 0
