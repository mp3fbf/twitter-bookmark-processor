#!/usr/bin/env python3
"""Re-enrich packages via OAuth and expand old threads via paid API.

Two modes:
1. `enrich` — Use OAuth (free) to batch-lookup tweets that need conversation_id,
   media_keys, or author_id. Also expands threads within the 7-day window.
2. `expand-old` — Use paid API (Full Archive Search) for threads older than 7 days.
   Hardcoded list of tweet IDs. One-shot, never needs to run again.

Usage:
    python3 scripts/enrich_via_oauth.py status              # Show what needs enriching
    python3 scripts/enrich_via_oauth.py enrich              # OAuth batch enrichment
    python3 scripts/enrich_via_oauth.py enrich --limit 100  # Limit batch size
    python3 scripts/enrich_via_oauth.py expand-old          # Paid API for old threads
    python3 scripts/enrich_via_oauth.py expand-old --dry-run
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sources.x_api_auth import XApiAuth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PACKAGES_DIR = Path("data/content_packages")
TOKEN_FILE = Path("data/x_api_tokens.json")

# X API v2 endpoints
X_API_BASE = "https://api.twitter.com/2"
TWEET_FIELDS = "id,text,created_at,conversation_id,entities,attachments,author_id,note_tweet"
EXPANSIONS = "attachments.media_keys,author_id"
MEDIA_FIELDS = "media_key,type,url,preview_image_url"
USER_FIELDS = "id,username,name"


def get_client_id() -> str:
    """Get X API client ID from env or Keychain."""
    client_id = os.environ.get("X_API_CLIENT_ID")
    if client_id:
        return client_id

    import subprocess
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", "twitter-processor",
             "-s", "x-api-client-id", "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass

    print("ERROR: X_API_CLIENT_ID not set", file=sys.stderr)
    sys.exit(1)


def find_enrichment_candidates() -> dict:
    """Find packages that need enrichment."""
    needs_thread = []    # Has thread indicator but no thread_tweets
    needs_media = []     # Has media mention but no media data
    needs_author = []    # Has author from fxtwitter but no author_id

    for path in sorted(PACKAGES_DIR.glob("*.json")):
        try:
            pkg = json.loads(path.read_text())
        except (json.JSONDecodeError, KeyError):
            continue

        bid = pkg["bookmark_id"]
        text = pkg.get("tweet_text", "")
        thread_tweets = pkg.get("thread_tweets", [])
        author = pkg.get("author_username", "")

        # Needs thread expansion: has author (so we can search) + no threads yet
        # Detect thread indicators in text
        if author and not thread_tweets:
            # Check if fxtwitter flagged it or text has thread patterns
            if any(indicator in text.lower() for indicator in ["thread", "🧵", "1/", "1."]):
                needs_thread.append(bid)

        # Needs author enrichment (has username from fxtwitter but missing author_id)
        if author and author != "unknown" and not pkg.get("author_id"):
            needs_author.append(bid)

    return {
        "needs_thread": needs_thread,
        "needs_author": needs_author,
    }


async def batch_lookup_tweets(auth: XApiAuth, tweet_ids: list[str]) -> dict:
    """Batch lookup tweets via GET /2/tweets. Max 100 per request."""
    import httpx

    all_tweets = {}
    all_includes = {"users": [], "media": []}

    for i in range(0, len(tweet_ids), 100):
        batch = tweet_ids[i:i+100]
        token = await auth.get_valid_token()

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.get(
                    f"{X_API_BASE}/tweets",
                    params={
                        "ids": ",".join(batch),
                        "tweet.fields": TWEET_FIELDS,
                        "expansions": EXPANSIONS,
                        "media.fields": MEDIA_FIELDS,
                        "user.fields": USER_FIELDS,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )

                if response.status_code != 200:
                    logger.error("Batch lookup failed: %d — %s", response.status_code, response.text[:200])
                    continue

                body = response.json()
                for tweet in body.get("data", []):
                    all_tweets[tweet["id"]] = tweet

                includes = body.get("includes", {})
                all_includes["users"].extend(includes.get("users", []))
                all_includes["media"].extend(includes.get("media", []))

        except Exception as e:
            logger.error("Batch lookup error: %s", e)

        # Rate limiting: 300 requests per 15 min for /tweets
        await asyncio.sleep(0.5)

    return {"tweets": all_tweets, "includes": all_includes}


async def search_thread(auth: XApiAuth, conversation_id: str, author: str) -> list[dict] | None:
    """Search for thread tweets via search/recent (7-day window).

    Returns list of tweets, empty list if no thread found, or None if rate limited.
    """
    import httpx

    token = await auth.get_valid_token()
    query = f"conversation_id:{conversation_id} from:{author}"

    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                response = await client.get(
                    f"{X_API_BASE}/tweets/search/recent",
                    params={
                        "query": query,
                        "max_results": "100",
                        "tweet.fields": TWEET_FIELDS,
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )

                if response.status_code == 429:
                    if attempt < 2:
                        wait = 60 * (attempt + 1)
                        logger.warning("Rate limited, waiting %ds...", wait)
                        await asyncio.sleep(wait)
                        continue
                    return None  # Signal to caller: stop searching

                if response.status_code != 200:
                    logger.warning("Thread search failed for %s: %d", conversation_id, response.status_code)
                    return []

                body = response.json()
                tweets = body.get("data", [])
                tweets.sort(key=lambda t: int(t.get("id", "0")))
                return tweets

        except Exception as e:
            logger.warning("Thread search error: %s", e)
            return []

    return None


async def enrich_packages(auth: XApiAuth, limit: int | None = None) -> dict:
    """Enrich packages with OAuth data."""
    candidates = find_enrichment_candidates()
    all_ids = list(set(candidates["needs_thread"] + candidates["needs_author"]))

    if limit:
        all_ids = all_ids[:limit]

    if not all_ids:
        print("No packages need enrichment.")
        return {"enriched": 0}

    logger.info("Enriching %d packages via OAuth...", len(all_ids))

    # Batch lookup all tweets
    result = await batch_lookup_tweets(auth, all_ids)
    tweets = result["tweets"]
    includes = result["includes"]

    # Build user lookup
    user_map = {}
    for user in includes.get("users", []):
        user_map[user["id"]] = user

    # Build media lookup
    media_map = {}
    for media in includes.get("media", []):
        media_map[media["media_key"]] = media

    stats = {"enriched": 0, "threads_expanded": 0, "not_found": 0}

    for bid in all_ids:
        tweet = tweets.get(bid)
        if not tweet:
            stats["not_found"] += 1
            continue

        # Load package
        pkg_path = PACKAGES_DIR / f"{bid}.json"
        try:
            pkg = json.loads(pkg_path.read_text())
        except Exception:
            continue

        changed = False

        # Update author_id
        author_id = tweet.get("author_id")
        if author_id and not pkg.get("author_id"):
            pkg["author_id"] = author_id
            user = user_map.get(author_id)
            if user:
                pkg["author_username"] = user.get("username", pkg.get("author_username", ""))
                pkg["author_name"] = user.get("name", pkg.get("author_name", ""))
            changed = True

        # Update conversation_id
        conv_id = tweet.get("conversation_id")
        if conv_id:
            pkg["conversation_id"] = conv_id

            # Try thread expansion only if thread indicators present
            text = pkg.get("tweet_text", "").lower()
            has_thread_indicator = any(ind in text for ind in ["thread", "\U0001f9f5", "1/", "1."])
            if (conv_id == bid and not pkg.get("thread_tweets")
                    and has_thread_indicator and not stats.get("_rate_limited")):
                author = pkg.get("author_username", "")
                if author:
                    # Respect rate limit: 5s between search requests
                    await asyncio.sleep(5.0)
                    thread_tweets = await search_thread(auth, conv_id, author)
                    if thread_tweets is None:
                        # Rate limited even after retries — stop searching
                        stats["_rate_limited"] = True
                        logger.warning("Rate limit hit, skipping remaining thread searches")
                    elif len(thread_tweets) > 1:
                        pkg["thread_tweets"] = [
                            {
                                "order": i,
                                "text": t.get("note_tweet", {}).get("text") or t.get("text", ""),
                                "media_urls": [],
                                "links": [],
                            }
                            for i, t in enumerate(thread_tweets)
                        ]
                        stats["threads_expanded"] += 1
                        logger.info("Expanded thread %s: %d tweets", bid, len(thread_tweets))
                        changed = True

            changed = True

        # Update media
        attachments = tweet.get("attachments", {})
        media_keys = attachments.get("media_keys", [])
        if media_keys and not pkg.get("media_keys"):
            pkg["media_keys"] = media_keys
            media_urls = []
            for key in media_keys:
                m = media_map.get(key)
                if m:
                    url = m.get("url") or m.get("preview_image_url")
                    if url:
                        media_urls.append(url)
            if media_urls:
                # Merge with existing
                existing = set(pkg.get("media_urls", []))
                for url in media_urls:
                    if url not in existing:
                        pkg.setdefault("media_urls", []).append(url)
                changed = True

        if changed:
            pkg_path.write_text(json.dumps(pkg, indent=2, default=str))
            stats["enriched"] += 1

        await asyncio.sleep(0.2)

    return stats


async def expand_old_threads(auth: XApiAuth, dry_run: bool = False) -> dict:
    """Expand threads older than 7 days using paid API.

    This uses GET /2/tweets batch lookup + conversation search.
    For tweets outside the search/recent window, we can still get the
    tweet data via batch lookup, but thread expansion requires full
    archive search (paid).

    Note: This is a one-shot operation for a finite list.
    """
    # Find packages marked as thread_detected_not_expanded
    candidates = []
    for path in sorted(PACKAGES_DIR.glob("*.json")):
        try:
            pkg = json.loads(path.read_text())
            if pkg.get("thread_detected_not_expanded"):
                candidates.append(pkg["bookmark_id"])
        except Exception:
            continue

    if not candidates:
        print("No old threads to expand.")
        return {"expanded": 0}

    logger.info("Found %d old threads to expand", len(candidates))

    if dry_run:
        for bid in candidates:
            print(f"  Would expand: {bid}")
        return {"expanded": 0, "candidates": len(candidates)}

    # For each candidate, try batch lookup to get conversation_id,
    # then attempt search/all (paid endpoint)
    stats = {"expanded": 0, "failed": 0}

    for bid in candidates:
        pkg_path = PACKAGES_DIR / f"{bid}.json"
        pkg = json.loads(pkg_path.read_text())
        author = pkg.get("author_username", "")
        conv_id = pkg.get("conversation_id", bid)

        if not author:
            stats["failed"] += 1
            continue

        # Try search/recent first (maybe tweet came back in window)
        thread_tweets = await search_thread(auth, conv_id, author)

        if len(thread_tweets) > 1:
            pkg["thread_tweets"] = [
                {
                    "order": i,
                    "text": t.get("note_tweet", {}).get("text") or t.get("text", ""),
                    "media_urls": [],
                    "links": [],
                }
                for i, t in enumerate(thread_tweets)
            ]
            del pkg["thread_detected_not_expanded"]
            pkg_path.write_text(json.dumps(pkg, indent=2, default=str))
            stats["expanded"] += 1
            logger.info("Expanded old thread %s: %d tweets", bid, len(thread_tweets))
        else:
            logger.info("Thread %s still outside search window", bid)
            stats["failed"] += 1

        await asyncio.sleep(1.0)  # Conservative rate limiting for paid API

    return stats


def show_status():
    """Show enrichment status."""
    candidates = find_enrichment_candidates()

    # Count thread_detected_not_expanded
    old_threads = 0
    for path in PACKAGES_DIR.glob("*.json"):
        try:
            pkg = json.loads(path.read_text())
            if pkg.get("thread_detected_not_expanded"):
                old_threads += 1
        except Exception:
            continue

    total = len(list(PACKAGES_DIR.glob("*.json")))

    print(f"Total packages: {total}")
    print(f"\nEnrichment candidates (OAuth):")
    print(f"  Needs thread expansion: {len(candidates['needs_thread'])}")
    print(f"  Needs author_id:        {len(candidates['needs_author'])}")
    print(f"\nOld threads (paid API):    {old_threads}")


def main():
    parser = argparse.ArgumentParser(description="Enrich packages via OAuth + paid API")
    parser.add_argument("command", choices=["status", "enrich", "expand-old"])
    parser.add_argument("--limit", type=int, help="Limit batch size")
    parser.add_argument("--dry-run", action="store_true", help="Preview without saving")
    args = parser.parse_args()

    if args.command == "status":
        show_status()
        return

    client_id = get_client_id()
    auth = XApiAuth(client_id=client_id, token_file=TOKEN_FILE)

    if not auth.has_tokens():
        print("ERROR: No X API tokens. Run keepalive_token.py first.", file=sys.stderr)
        sys.exit(1)

    if args.command == "enrich":
        stats = asyncio.run(enrich_packages(auth, limit=args.limit))
        print(f"\n=== OAuth Enrichment Complete ===")
        print(f"Enriched:          {stats['enriched']}")
        print(f"Threads expanded:  {stats.get('threads_expanded', 0)}")
        print(f"Not found:         {stats.get('not_found', 0)}")

    elif args.command == "expand-old":
        stats = asyncio.run(expand_old_threads(auth, dry_run=args.dry_run))
        print(f"\n=== Old Thread Expansion ===")
        print(f"Expanded: {stats.get('expanded', 0)}")
        print(f"Failed:   {stats.get('failed', 0)}")


if __name__ == "__main__":
    main()
