#!/usr/bin/env python3
"""Backfill author info and tweet text via fxtwitter (free, no auth).

Fills in author_username, author_name, and full tweet_text for packages
that are missing this data. Uses fxtwitter.com API which is free and
has no rate limits.

Limitations:
- Does NOT return thread_tweets, media_keys, or conversation_id
- Does NOT return full media URLs (only preview thumbnails)
- But resolves ~80% of the problem (author + text + thread detection)

Usage:
    python3 scripts/backfill_authors.py status    # Show how many need backfill
    python3 scripts/backfill_authors.py run       # Run backfill
    python3 scripts/backfill_authors.py run --limit 50  # Limit batch size
    python3 scripts/backfill_authors.py run --dry-run   # Preview without saving
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PACKAGES_DIR = Path("data/content_packages")
FXTWITTER_API = "https://api.fxtwitter.com"


def find_missing_packages() -> list[dict]:
    """Find all packages missing author_username."""
    missing = []
    for path in sorted(PACKAGES_DIR.glob("*.json")):
        try:
            pkg = json.loads(path.read_text())
            author = pkg.get("author_username", "")
            if not author or author == "unknown":
                missing.append(pkg)
        except (json.JSONDecodeError, KeyError):
            continue
    return missing


async def fetch_fxtwitter(tweet_id: str) -> dict | None:
    """Fetch tweet data from fxtwitter API."""
    url = f"{FXTWITTER_API}/i/status/{tweet_id}"
    headers = {"User-Agent": "TwitterBookmarkProcessor/1.0"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(url, headers=headers)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logger.warning("Tweet %s not found (deleted?)", tweet_id)
                return None
            else:
                logger.warning("fxtwitter %s: HTTP %d", tweet_id, response.status_code)
                return None
    except Exception as e:
        logger.warning("fxtwitter fetch failed for %s: %s", tweet_id, e)
        return None


def update_package(pkg: dict, fx_data: dict) -> dict:
    """Update package with fxtwitter data. Returns dict of changes made."""
    tweet = fx_data.get("tweet", {})
    if not tweet:
        return {}

    changes = {}
    author = tweet.get("author", {})

    if author.get("screen_name") and not pkg.get("author_username"):
        pkg["author_username"] = author["screen_name"]
        changes["author_username"] = author["screen_name"]

    if author.get("name") and (not pkg.get("author_name") or pkg["author_name"] == pkg.get("author_username")):
        pkg["author_name"] = author["name"]
        changes["author_name"] = author["name"]

    # Update tweet text if current is empty or very short
    fx_text = tweet.get("text", "")
    current_text = pkg.get("tweet_text", "")
    if fx_text and (not current_text or len(current_text) < len(fx_text)):
        pkg["tweet_text"] = fx_text
        changes["tweet_text"] = f"{len(fx_text)} chars"

    # Update author_name in case it was same as username
    if not pkg.get("author_name") and pkg.get("author_username"):
        pkg["author_name"] = pkg["author_username"]

    return changes


async def run_backfill(limit: int | None = None, dry_run: bool = False) -> dict:
    """Run the backfill process."""
    missing = find_missing_packages()
    if limit:
        missing = missing[:limit]

    logger.info("Backfilling %d packages via fxtwitter...", len(missing))

    stats = {"updated": 0, "not_found": 0, "no_change": 0, "error": 0}

    for i, pkg in enumerate(missing, 1):
        bid = pkg["bookmark_id"]
        fx_data = await fetch_fxtwitter(bid)

        if fx_data is None:
            stats["not_found"] += 1
            continue

        changes = update_package(pkg, fx_data)

        if changes:
            if not dry_run:
                path = PACKAGES_DIR / f"{bid}.json"
                path.write_text(json.dumps(pkg, indent=2, default=str))
            stats["updated"] += 1
            author = changes.get("author_username", "?")
            logger.info("[%d/%d] %s -> @%s %s", i, len(missing), bid, author,
                       "(dry-run)" if dry_run else "")
        else:
            stats["no_change"] += 1

        # Be polite — 100ms between requests
        await asyncio.sleep(0.1)

    return stats


def show_status():
    """Show current backfill status."""
    missing = find_missing_packages()
    total = len(list(PACKAGES_DIR.glob("*.json")))
    print(f"Total packages: {total}")
    print(f"Missing author: {len(missing)} ({len(missing)*100//total}%)")

    if missing:
        # Show a few sample IDs
        print(f"\nSample IDs (first 10):")
        for pkg in missing[:10]:
            bid = pkg["bookmark_id"]
            text = (pkg.get("tweet_text", "") or "")[:60]
            print(f"  {bid}  {text}")


def main():
    parser = argparse.ArgumentParser(description="Backfill author info via fxtwitter")
    parser.add_argument("command", choices=["status", "run"], help="Command to run")
    parser.add_argument("--limit", type=int, help="Limit number of packages to process")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    if args.command == "status":
        show_status()
    elif args.command == "run":
        stats = asyncio.run(run_backfill(limit=args.limit, dry_run=args.dry_run))
        print(f"\n=== Backfill Complete ===")
        print(f"Updated:   {stats['updated']}")
        print(f"Not found: {stats['not_found']}")
        print(f"No change: {stats['no_change']}")
        print(f"Errors:    {stats['error']}")

        if stats["updated"] > 0 and not args.dry_run:
            # Show remaining
            remaining = find_missing_packages()
            print(f"\nRemaining without author: {len(remaining)}")


if __name__ == "__main__":
    main()
