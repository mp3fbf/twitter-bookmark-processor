"""Shared test fixtures for Twitter Bookmark Processor.

Provides reusable fixtures for integration and unit tests,
especially for temporary directory management to avoid
polluting real workspace directories.
"""

from pathlib import Path

import pytest


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Path:
    """Create a temporary output directory for Obsidian notes.

    This fixture ensures tests don't write to real /workspace/notes/
    or any production directories.

    Args:
        tmp_path: pytest's built-in temp directory fixture.

    Returns:
        Path to temporary output directory (created but empty).
    """
    output_dir = tmp_path / "notes"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


@pytest.fixture
def temp_state_file(tmp_path: Path) -> Path:
    """Create a path for temporary state file.

    Args:
        tmp_path: pytest's built-in temp directory fixture.

    Returns:
        Path for state file (not created, just the path).
    """
    return tmp_path / "state.json"


@pytest.fixture
def temp_backlog_dir(tmp_path: Path) -> Path:
    """Create a temporary backlog directory for export files.

    Args:
        tmp_path: pytest's built-in temp directory fixture.

    Returns:
        Path to temporary backlog directory (created but empty).
    """
    backlog_dir = tmp_path / "backlog"
    backlog_dir.mkdir(parents=True, exist_ok=True)
    return backlog_dir


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create a temporary cache directory for link cache.

    Args:
        tmp_path: pytest's built-in temp directory fixture.

    Returns:
        Path to temporary cache directory (created but empty).
    """
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir
