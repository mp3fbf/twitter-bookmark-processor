#!/usr/bin/env python3
"""
Twitter/X Reader - Wrapper for bird CLI with fallbacks

Reads tweets, threads, and searches Twitter using:
1. bird CLI (primary) - uses browser cookies
2. ThreadReaderApp (fallback for threads)
3. twitterapi.io (fallback if API key available)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

# Optional imports for fallbacks
try:
    import requests
    from bs4 import BeautifulSoup
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


@dataclass
class Tweet:
    """Structured tweet data"""
    id: str
    text: str
    author_username: str
    author_name: str = ""
    created_at: str = ""
    likes: int = 0
    retweets: int = 0
    replies: int = 0
    quotes: int = 0
    views: int = 0
    media_urls: List[str] = field(default_factory=list)
    video_urls: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)
    quoted_tweet: Optional[Dict] = None
    is_thread: bool = False
    thread_position: int = 0
    url: str = ""


@dataclass
class ThreadResult:
    """Result of reading a thread"""
    success: bool
    tweets: List[Tweet] = field(default_factory=list)
    error: Optional[str] = None
    source: str = "unknown"
    author: str = ""
    title: str = ""


class TwitterReader:
    """Main Twitter reader with multiple backends"""

    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.bird_path = self._find_bird()
        self.api_key = os.getenv('TWITTER_API_KEY') or os.getenv('x-api-key')

    def _find_bird(self) -> Optional[str]:
        """Find bird CLI executable"""
        # Check local
        bird = shutil.which('bird')
        if bird:
            return bird

        # Common Mac locations
        for path in ['/opt/homebrew/bin/bird', '/usr/local/bin/bird']:
            if os.path.exists(path):
                return path

        return None

    def _log(self, msg: str):
        """Log if verbose"""
        if self.verbose:
            print(f"[twitter] {msg}", file=sys.stderr)

    def _extract_tweet_id(self, url: str) -> Optional[str]:
        """Extract tweet ID from URL"""
        patterns = [
            r'/status/(\d+)',
            r'/i/web/status/(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _run_bird(self, args: List[str]) -> Optional[Dict]:
        """Run bird CLI and parse JSON output"""
        if not self.bird_path:
            self._log("bird CLI not found")
            return None

        cmd = [self.bird_path] + args + ['--json']
        self._log(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                self._log(f"bird error: {result.stderr}")
                return None

            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            self._log("bird timed out")
            return None
        except json.JSONDecodeError as e:
            self._log(f"Failed to parse bird output: {e}")
            return None
        except Exception as e:
            self._log(f"bird failed: {e}")
            return None

    def _bird_to_tweet(self, data: Dict) -> Tweet:
        """Convert bird JSON to Tweet object"""
        # bird returns different structures depending on command
        # Normalize to our Tweet format

        author = data.get('author', {})
        if isinstance(author, str):
            author = {'username': author, 'name': author}

        metrics = data.get('metrics', {})
        if not metrics:
            # Try flat structure
            metrics = {
                'likes': data.get('likeCount', data.get('likes', 0)),
                'retweets': data.get('retweetCount', data.get('retweets', 0)),
                'replies': data.get('replyCount', data.get('replies', 0)),
                'quotes': data.get('quoteCount', data.get('quotes', 0)),
                'views': data.get('viewCount', data.get('views', 0)),
            }

        # Extract media
        media_urls = []
        video_urls = []
        for media in data.get('media', []):
            if isinstance(media, str):
                media_urls.append(media)
            elif isinstance(media, dict):
                if media.get('type') == 'video':
                    video_urls.append(media.get('url', ''))
                else:
                    media_urls.append(media.get('url', ''))

        # Extract links
        links = []
        for url_obj in data.get('urls', data.get('entities', {}).get('urls', [])):
            if isinstance(url_obj, str):
                links.append(url_obj)
            elif isinstance(url_obj, dict):
                links.append(url_obj.get('expanded_url', url_obj.get('url', '')))

        # Build URL if not present
        tweet_id = data.get('id', data.get('rest_id', ''))
        username = author.get('username', author.get('userName', 'unknown'))
        url = data.get('url', f"https://x.com/{username}/status/{tweet_id}")

        return Tweet(
            id=str(tweet_id),
            text=data.get('text', data.get('full_text', '')),
            author_username=username,
            author_name=author.get('name', ''),
            created_at=data.get('createdAt', data.get('created_at', '')),
            likes=metrics.get('likes', 0),
            retweets=metrics.get('retweets', 0),
            replies=metrics.get('replies', 0),
            quotes=metrics.get('quotes', 0),
            views=metrics.get('views', 0),
            media_urls=media_urls,
            video_urls=video_urls,
            links=links,
            quoted_tweet=data.get('quoted_tweet'),
            url=url
        )

    def read_tweet(self, url: str) -> Optional[Tweet]:
        """Read a single tweet"""
        # Try bird first
        if self.bird_path:
            data = self._run_bird(['read', url])
            if data:
                return self._bird_to_tweet(data)

        # Fallback to twitterapi.io
        if self.api_key:
            tweet = self._read_via_api(url)
            if tweet:
                return tweet

        self._log("No backend available to read tweet")
        return None

    def read_thread(self, url: str, use_fallback: bool = False) -> ThreadResult:
        """Read a full thread"""
        # Try bird first (unless fallback requested)
        if self.bird_path and not use_fallback:
            data = self._run_bird(['thread', url])
            if data:
                tweets = []
                thread_data = data if isinstance(data, list) else data.get('tweets', [data])
                for i, t in enumerate(thread_data):
                    tweet = self._bird_to_tweet(t)
                    tweet.thread_position = i + 1
                    tweet.is_thread = True
                    tweets.append(tweet)

                if tweets:
                    return ThreadResult(
                        success=True,
                        tweets=tweets,
                        source='bird',
                        author=tweets[0].author_username
                    )

        # Fallback to ThreadReaderApp
        if HAS_REQUESTS:
            result = self._read_thread_via_threadreader(url)
            return result  # Return result whether success or failure (to propagate error message)

        return ThreadResult(success=False, error="No backend available. Install bird CLI: brew install steipete/tap/bird")

    def _read_thread_via_threadreader(self, url: str) -> ThreadResult:
        """Read thread via ThreadReaderApp (scraping)"""
        tweet_id = self._extract_tweet_id(url)
        if not tweet_id:
            return ThreadResult(success=False, error="Could not extract tweet ID")

        threadreader_url = f"https://threadreaderapp.com/thread/{tweet_id}.html"
        self._log(f"Fetching from ThreadReaderApp: {threadreader_url}")

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            }
            response = requests.get(threadreader_url, headers=headers, timeout=30)

            if response.status_code == 404:
                return ThreadResult(success=False, error="Thread not found on ThreadReaderApp")

            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Check if ThreadReaderApp blocked access
            page_text = soup.get_text()
            if "can't access this thread" in page_text or "API restrictions" in page_text:
                self._log("ThreadReaderApp blocked by Twitter API restrictions")
                return ThreadResult(
                    success=False,
                    error="ThreadReaderApp blocked by Twitter API restrictions. Install bird CLI for full access."
                )

            tweets = []

            # Try multiple selectors (ThreadReaderApp changes structure)
            tweet_divs = (
                soup.find_all('div', class_='content-tweet') or
                soup.find_all('div', class_='tweet-content') or
                soup.find_all('div', {'data-tweet': True})
            )

            for i, div in enumerate(tweet_divs):
                text_div = div.find('div', class_='tweet-text') or div.find('p')
                text = text_div.get_text(strip=True) if text_div else div.get_text(strip=True)

                # Extract author from first tweet
                author = ""
                author_link = div.find('a', class_='tweet-avatar') or div.find('a', href=re.compile(r'twitter\.com/\w+'))
                if author_link:
                    href = author_link.get('href', '')
                    match = re.search(r'(?:twitter|x)\.com/(\w+)', href)
                    author = match.group(1) if match else href.split('/')[-1]

                # Extract media
                media_urls = []
                for img in div.find_all('img'):
                    src = img.get('src', '')
                    if src and 'pbs.twimg.com' in src:
                        media_urls.append(src)

                if text:  # Only add if we have text
                    tweet = Tweet(
                        id=f"{tweet_id}_{i}",
                        text=text,
                        author_username=author or "unknown",
                        media_urls=media_urls,
                        is_thread=True,
                        thread_position=i + 1,
                        url=f"https://x.com/{author}/status/{tweet_id}" if i == 0 else ""
                    )
                    tweets.append(tweet)

            if tweets:
                return ThreadResult(
                    success=True,
                    tweets=tweets,
                    source='threadreaderapp',
                    author=tweets[0].author_username
                )

            return ThreadResult(
                success=False,
                error="No tweets found. ThreadReaderApp may be blocked or thread was deleted."
            )

        except Exception as e:
            return ThreadResult(success=False, error=str(e))

    def _read_via_api(self, url: str) -> Optional[Tweet]:
        """Read tweet via twitterapi.io"""
        if not self.api_key or not HAS_REQUESTS:
            return None

        tweet_id = self._extract_tweet_id(url)
        if not tweet_id:
            return None

        try:
            headers = {
                'X-API-Key': self.api_key,
                'Content-Type': 'application/json'
            }
            response = requests.get(
                f"https://api.twitterapi.io/twitter/tweets",
                params={'tweet_ids': tweet_id},
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                data = response.json()
                if data.get('status') == 'success' and data.get('tweets'):
                    return self._bird_to_tweet(data['tweets'][0])

            return None
        except Exception as e:
            self._log(f"API error: {e}")
            return None

    def search(self, query: str, count: int = 10) -> List[Tweet]:
        """Search tweets"""
        if not self.bird_path:
            self._log("bird required for search")
            return []

        data = self._run_bird(['search', query, '-n', str(count)])
        if not data:
            return []

        tweets = []
        results = data if isinstance(data, list) else data.get('tweets', [])
        for t in results:
            tweets.append(self._bird_to_tweet(t))

        return tweets

    def get_user(self, username: str) -> Optional[Dict]:
        """Get user info"""
        username = username.lstrip('@')

        if self.bird_path:
            # bird doesn't have direct user command, use user-tweets
            data = self._run_bird(['user-tweets', username, '-n', '1'])
            if data:
                tweets = data if isinstance(data, list) else data.get('tweets', [])
                if tweets:
                    author = tweets[0].get('author', {})
                    return {
                        'username': author.get('username', username),
                        'name': author.get('name', ''),
                        'verified': author.get('verified', False),
                    }

        return None


def format_tweet_markdown(tweet: Tweet) -> str:
    """Format a tweet as Markdown"""
    lines = []

    # Header
    if tweet.is_thread and tweet.thread_position:
        lines.append(f"### Tweet {tweet.thread_position}")
    else:
        lines.append(f"## Tweet by @{tweet.author_username}")

    lines.append("")

    # Metadata
    meta = []
    if tweet.author_name:
        meta.append(f"**Author:** {tweet.author_name} (@{tweet.author_username})")
    if tweet.created_at:
        meta.append(f"**Posted:** {tweet.created_at}")
    if tweet.likes or tweet.retweets:
        engagement = []
        if tweet.likes:
            engagement.append(f"{tweet.likes:,} likes")
        if tweet.retweets:
            engagement.append(f"{tweet.retweets:,} retweets")
        if tweet.replies:
            engagement.append(f"{tweet.replies:,} replies")
        if tweet.views:
            engagement.append(f"{tweet.views:,} views")
        meta.append(f"**Engagement:** {', '.join(engagement)}")

    if meta:
        lines.extend(meta)
        lines.append("")

    # Tweet text as blockquote
    lines.append("> " + tweet.text.replace('\n', '\n> '))
    lines.append("")

    # Media
    if tweet.media_urls:
        lines.append("**Media:**")
        for url in tweet.media_urls:
            lines.append(f"- ![image]({url})")
        lines.append("")

    if tweet.video_urls:
        lines.append("**Videos:**")
        for url in tweet.video_urls:
            lines.append(f"- {url}")
        lines.append("")

    # Links
    if tweet.links:
        lines.append("**Links:**")
        for link in tweet.links:
            lines.append(f"- {link}")
        lines.append("")

    # Quoted tweet
    if tweet.quoted_tweet:
        qt = tweet.quoted_tweet
        qt_author = qt.get('author', {}).get('username', 'unknown')
        qt_text = qt.get('text', '')
        lines.append(f"**Quoted Tweet (@{qt_author}):**")
        lines.append(f"> {qt_text}")
        lines.append("")

    # URL
    if tweet.url and not tweet.is_thread:
        lines.append(f"**URL:** {tweet.url}")

    return '\n'.join(lines)


def format_thread_markdown(result: ThreadResult) -> str:
    """Format a thread as Markdown"""
    if not result.success:
        return f"Error: {result.error}"

    lines = []
    lines.append(f"# Thread by @{result.author}")
    lines.append(f"*{len(result.tweets)} tweets | Source: {result.source}*")
    lines.append("")
    lines.append("---")
    lines.append("")

    for tweet in result.tweets:
        lines.append(format_tweet_markdown(tweet))
        lines.append("---")
        lines.append("")

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(
        description='Read tweets and threads from Twitter/X',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s https://x.com/user/status/123
  %(prog)s --thread https://x.com/user/status/123
  %(prog)s --search "Claude AI" --count 10
  %(prog)s --user @naval
        """
    )

    parser.add_argument('url', nargs='?', help='Tweet or thread URL')
    parser.add_argument('--thread', '-t', action='store_true', help='Read as thread')
    parser.add_argument('--search', '-s', help='Search query')
    parser.add_argument('--user', '-u', help='Get user info')
    parser.add_argument('--count', '-n', type=int, default=10, help='Number of results for search')
    parser.add_argument('--json', '-j', action='store_true', help='Output JSON')
    parser.add_argument('--fallback', '-f', action='store_true', help='Use fallback (ThreadReaderApp)')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')

    args = parser.parse_args()

    reader = TwitterReader(verbose=args.verbose)

    # Search mode
    if args.search:
        tweets = reader.search(args.search, args.count)
        if args.json:
            print(json.dumps([asdict(t) for t in tweets], indent=2, ensure_ascii=False))
        else:
            for tweet in tweets:
                print(format_tweet_markdown(tweet))
                print("---\n")
        return

    # User mode
    if args.user:
        user = reader.get_user(args.user)
        if user:
            if args.json:
                print(json.dumps(user, indent=2))
            else:
                print(f"**User:** @{user.get('username')}")
                print(f"**Name:** {user.get('name', 'N/A')}")
                print(f"**Verified:** {'Yes' if user.get('verified') else 'No'}")
        else:
            print("User not found or bird CLI unavailable", file=sys.stderr)
            sys.exit(1)
        return

    # URL required for tweet/thread
    if not args.url:
        parser.print_help()
        sys.exit(1)

    # Thread mode
    if args.thread:
        result = reader.read_thread(args.url, use_fallback=args.fallback)
        if args.json:
            output = {
                'success': result.success,
                'source': result.source,
                'author': result.author,
                'tweet_count': len(result.tweets),
                'tweets': [asdict(t) for t in result.tweets],
                'error': result.error
            }
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            print(format_thread_markdown(result))

        if not result.success:
            sys.exit(1)
        return

    # Single tweet mode
    tweet = reader.read_tweet(args.url)
    if tweet:
        if args.json:
            print(json.dumps(asdict(tweet), indent=2, ensure_ascii=False))
        else:
            print(format_tweet_markdown(tweet))
    else:
        print("Failed to read tweet. Check if bird CLI is installed and authenticated.", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
