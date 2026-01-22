# Skill Integration Guide

How to integrate with existing Claude Code skills for content processing.

## Architecture Decision

**Primary data source:** Twillot JSON export (not bird CLI)

The project processes bookmarks exported from Twillot, which already contains all necessary data:
- Tweet content, media URLs, engagement metrics
- Thread indicators (`conversation_id`, `is_reply`)
- Author information

**Skills used for enrichment:**
- `youtube_processor.py` - Process YouTube videos found in bookmark links
- `thread_fetcher.py` - Fetch complete threads via ThreadReaderApp (from twitter-bookmarks-app)

**Note:** The `twitter_reader.py` skill requires `bird` CLI which only works on Mac host.
For thread fetching in the container, use `thread_fetcher.py` instead.

## YouTube Processor

### Location
```
/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py
```

### Requirements
- **Environment:** `GOOGLE_API_KEY` (Gemini API)
- **Python:** Uses dedicated venv at `/home/claude/.claude/skills/youtube-video/venv/`
- **Works in container:** Yes

### Usage as Library

```python
import sys
sys.path.insert(0, '/home/claude/.claude/skills/youtube-video/scripts')
from youtube_processor import YouTubeProcessor

processor = YouTubeProcessor()

# Transcript mode (default)
result = processor.process_video("https://youtube.com/watch?v=VIDEO_ID")

# Obsidian note mode
result = processor.process_video(
    "https://youtube.com/watch?v=VIDEO_ID",
    mode="note"
)
```

### Usage via CLI

```bash
# Transcript (default)
/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py "URL"

# Obsidian note format
/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py "URL" --note

# JSON output
/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py "URL" --json

# Clip specific section
/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py "URL" --clip 10:00-15:00
```

### Output Schema (transcript mode)

```json
{
  "title": "Video Title",
  "channel": "Channel Name",
  "duration": "03:33",
  "language": "English",
  "summary": "3-5 sentence summary",
  "key_points": [
    {"timestamp": "00:00", "content": "Key point description"}
  ],
  "transcript": [
    {"timestamp": "00:18", "speaker": "Speaker", "content": "Text"}
  ],
  "source_url": "https://...",
  "processed_at": "ISO timestamp",
  "mode": "transcript"
}
```

### Output Schema (note mode)

```json
{
  "title": "Video Title",
  "channel": "Channel Name",
  "tldr": "2-3 sentence summary",
  "key_points": ["Point 1", "Point 2"],
  "tags": ["topic/ai-coding", "person/someone"],
  "quotes": ["Notable quote"],
  "related_concepts": [
    {"concept": "Name", "importance": "core", "description": "..."}
  ],
  "suggested_moc": "AI-Coding",
  "mentioned_people": [
    {"name": "Person", "role": "Role"}
  ],
  "detailed_notes": "Markdown content"
}
```

## Thread Fetcher (Recommended for Threads)

### Location
```
/workspace/twitter-bookmarks-app/thread_fetcher.py
```

### Requirements
- **Backend:** ThreadReaderApp (free, no auth required)
- **Dependencies:** `requests`, `beautifulsoup4`
- **Works in container:** Yes
- **Cache:** SQLite database for avoiding repeated requests

### When to Use

Use ThreadFetcher when:
1. A bookmark is part of a thread but Twillot only captured the first tweet
2. You need the complete thread context for processing

### Availability

ThreadReaderApp works for **~70% of threads**. It may fail when:
- Author blocked ThreadReaderApp
- Thread is too new (not yet indexed)
- Account is private

### Usage

```python
import sys
sys.path.insert(0, '/workspace/twitter-bookmarks-app')
from thread_fetcher import ThreadFetcher, ThreadResult

fetcher = ThreadFetcher(cache_path='/tmp/thread_cache.db')

# Fetch thread
result = fetcher.fetch_thread(
    first_tweet_id='1999874571806355477',
    author='minchoi'
)

if result.success:
    print(f"Found {len(result.tweets)} tweets")
    for tweet in result.tweets:
        print(f"[{tweet.position}] {tweet.text[:100]}...")
else:
    print(f"Failed: {result.error}")

# Format for LLM prompt
formatted = fetcher.format_thread_for_prompt(result)
```

### Output Schema

```python
@dataclass
class ThreadTweet:
    tweet_id: str
    text: str
    author: str
    position: int  # 1, 2, 3...
    created_at: Optional[str]
    media_urls: List[str]

@dataclass
class ThreadResult:
    success: bool
    tweets: List[ThreadTweet]
    error: Optional[str]
    source: str  # "threadreaderapp"
    cached: bool
```

## Twitter Reader (Mac Host Only)

### Location
```
/home/claude/.claude/skills/twitter/scripts/twitter_reader.py
```

### Requirements
- **Primary backend:** `bird` CLI (Mac host only, uses browser cookies)
- **Fallback:** twitterapi.io (requires `TWITTER_API_KEY`)
- **Works in container:** No

### When to Use

Only use when ThreadFetcher fails and you have access to:
1. Mac host with `bird` CLI installed, OR
2. `TWITTER_API_KEY` for twitterapi.io ($0.15/1000 tweets)

For most cases, prefer ThreadFetcher.

## Integration Strategy

### Recommended Approach

```
┌──────────────────────────────────────────────────────────────┐
│                    Twillot JSON Export                       │
│           (Primary source - all bookmark data)               │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                   Bookmark Classifier                         │
│        Detects: VIDEO | THREAD | LINK | TWEET                │
└─────────┬────────────┬────────────────┬──────────────────────┘
          │            │                │
          ▼            ▼                ▼
    ┌─────────┐  ┌──────────┐    ┌───────────┐
    │ YouTube │  │  Thread  │    │  Generic  │
    │Processor│  │ Fetcher  │    │ Processor │
    │ (Gemini)│  │ (TRA)    │    │  (LLM)    │
    └─────────┘  └──────────┘    └───────────┘
```

### Thread Detection & Fetching

When Twillot exports a bookmark that's part of a thread:

1. **Check indicators:** `is_thread`, `conversation_id != tweet_id`, or heuristics (1/, 2/, "thread")
2. **Try ThreadFetcher:** Free, works ~70% of the time
3. **Fallback options:**
   - twitterapi.io ($0.15/1000 tweets) if API key available
   - Process only the bookmarked tweet if all else fails

### Code Example

```python
from pathlib import Path
import sys

# Add skill paths
sys.path.insert(0, '/home/claude/.claude/skills/youtube-video/scripts')

from youtube_processor import YouTubeProcessor

def process_bookmark(bookmark: dict) -> dict:
    """Process a single bookmark based on content type."""

    # Check for YouTube links
    youtube_url = None
    for link in bookmark.get('media_items', []) + [bookmark.get('url', '')]:
        if 'youtube.com' in link or 'youtu.be' in link:
            youtube_url = link
            break

    if youtube_url or bookmark.get('has_video'):
        # Process with YouTube skill
        processor = YouTubeProcessor()
        return processor.process_video(youtube_url, mode="note")

    # Default: return bookmark data as-is for text processing
    return {
        'type': 'tweet',
        'content': bookmark.get('full_text'),
        'author': bookmark.get('screen_name'),
        'url': bookmark.get('url')
    }
```

## Environment Variables

| Variable | Required | Used By | Description |
|----------|----------|---------|-------------|
| `GOOGLE_API_KEY` | Yes | youtube_processor | Gemini API access |
| `OP_SERVICE_ACCOUNT_TOKEN` | Optional | youtube_processor | 1Password fallback |
| `TWITTER_API_KEY` | Optional | twitter_reader | twitterapi.io fallback |

## Limitations

1. **YouTube videos > 3 hours:** Use `--clip` to process sections
2. **Thread fetching:** ThreadReaderApp works ~70% of threads; some authors block it
3. **Rate limits:** Gemini API has quotas; batch processing recommended
4. **bird CLI:** Only works on Mac host (not in Docker container)

## Testing

### Verify YouTube Integration

```bash
# Should return JSON with video info
/home/claude/.claude/skills/youtube-video/scripts/youtube_processor.py \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --json
```

### Verify Twillot Data Sufficiency

```python
import json

data = json.load(open('tests/fixtures/twillot_sample.json'))
for item in data:
    print(f"Type: video={item['has_video']}, image={item['has_image']}, link={item['has_link']}")
    print(f"Text preview: {item['full_text'][:50]}...")
```
