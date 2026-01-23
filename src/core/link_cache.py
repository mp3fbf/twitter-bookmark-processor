"""Link extraction cache for avoiding redundant LLM calls.

Caches LLM extraction results by URL hash with configurable TTL.
Uses JSON file for persistence with atomic writes for data integrity.
"""

import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# Default TTL of 30 days for cached entries
DEFAULT_TTL_DAYS = 30


def url_to_key(url: str) -> str:
    """Convert URL to cache key using SHA256 hash.

    Uses first 16 characters of hex digest for compact but unique keys.

    Args:
        url: The URL to hash.

    Returns:
        First 16 characters of SHA256 hex digest.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


class LinkCache:
    """Cache for link extraction results.

    Stores extraction data (title, tldr, key_points, tags) by URL hash.
    Entries expire after TTL (default 30 days).

    Attributes:
        cache_file: Path to the JSON cache file.
        ttl: Timedelta for entry expiration.
    """

    def __init__(
        self,
        cache_file: str | Path,
        *,
        ttl_days: int = DEFAULT_TTL_DAYS,
    ):
        """Initialize LinkCache with a cache file path.

        Args:
            cache_file: Path to the JSON file for cache persistence.
            ttl_days: Days until cached entries expire (default: 30).
        """
        self.cache_file = Path(cache_file)
        self.ttl = timedelta(days=ttl_days)
        self._cache: dict[str, Any] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        """Load cache from file if not already loaded."""
        if self._loaded:
            return
        self._load()

    def _load(self) -> None:
        """Load cache from JSON file.

        Creates the file with empty cache if it doesn't exist.
        """
        if not self.cache_file.exists():
            self._cache = {"entries": {}, "last_updated": None}
            self._loaded = True
            return

        with open(self.cache_file, encoding="utf-8") as f:
            self._cache = json.load(f)

        # Ensure required keys exist
        if "entries" not in self._cache:
            self._cache["entries"] = {}

        self._loaded = True

    def _save(self) -> None:
        """Save current cache to JSON file atomically.

        Uses temp file + rename for atomic writes.
        Creates parent directories if they don't exist.
        """
        self._cache["last_updated"] = datetime.now().isoformat()

        # Ensure parent directory exists
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file first, then atomically rename
        fd, temp_path = tempfile.mkstemp(
            dir=self.cache_file.parent,
            prefix=".link_cache_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2, ensure_ascii=False)
            # Atomic rename - ensures file is never partially written
            os.replace(temp_path, self.cache_file)
        except Exception:
            # Clean up temp file on failure
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def _is_expired(self, entry: dict[str, Any]) -> bool:
        """Check if a cache entry has expired.

        Args:
            entry: Cache entry with 'cached_at' timestamp.

        Returns:
            True if entry is older than TTL.
        """
        cached_at_str = entry.get("cached_at")
        if not cached_at_str:
            return True

        cached_at = datetime.fromisoformat(cached_at_str)
        return datetime.now() - cached_at > self.ttl

    def get(self, url: str) -> dict[str, Any] | None:
        """Get cached extraction data for a URL.

        Args:
            url: The URL to look up.

        Returns:
            Cached data dict if found and not expired, None otherwise.
        """
        self._ensure_loaded()

        key = url_to_key(url)
        entry = self._cache["entries"].get(key)

        if entry is None:
            return None

        if self._is_expired(entry):
            return None

        # Return the data portion, not the metadata
        return entry.get("data")

    def set(self, url: str, data: dict[str, Any]) -> None:
        """Cache extraction data for a URL.

        Args:
            url: The URL being cached.
            data: Extraction data (title, tldr, key_points, tags).
        """
        self._ensure_loaded()

        key = url_to_key(url)
        self._cache["entries"][key] = {
            "url": url,
            "data": data,
            "cached_at": datetime.now().isoformat(),
        }
        self._save()

    def has(self, url: str) -> bool:
        """Check if URL has a valid (non-expired) cache entry.

        Args:
            url: The URL to check.

        Returns:
            True if URL is cached and not expired.
        """
        return self.get(url) is not None

    def clear(self) -> None:
        """Clear all cached entries."""
        self._ensure_loaded()
        self._cache["entries"] = {}
        self._save()

    def get_stats(self) -> dict[str, int]:
        """Get cache statistics.

        Returns:
            Dictionary with total entries and expired count.
        """
        self._ensure_loaded()

        total = len(self._cache["entries"])
        expired = sum(
            1 for entry in self._cache["entries"].values()
            if self._is_expired(entry)
        )

        return {
            "total": total,
            "expired": expired,
            "valid": total - expired,
        }
