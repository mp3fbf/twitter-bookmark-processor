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
- `twitter_reader.py` - Optional, for fetching complete threads not in export

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

## Twitter Reader

### Location
```
/home/claude/.claude/skills/twitter/scripts/twitter_reader.py
```

### Requirements
- **Primary backend:** `bird` CLI (Mac host only, uses browser cookies)
- **Fallback 1:** ThreadReaderApp (currently blocked by Twitter)
- **Fallback 2:** twitterapi.io (requires `TWITTER_API_KEY`)
- **Works in container:** No (bird CLI on host only)

### When to Use

For this project, **twitter_reader.py is optional**. Use it only when:
1. You need to fetch a complete thread not captured in Twillot export
2. You need real-time tweet data beyond bookmarks

For bookmark processing, use the Twillot JSON directly.

### Usage (if needed)

```python
import sys
sys.path.insert(0, '/home/claude/.claude/skills/twitter/scripts')
from twitter_reader import TwitterReader, Tweet, ThreadResult

reader = TwitterReader(verbose=True)

# Single tweet
tweet = reader.read_tweet("https://x.com/user/status/123")

# Thread (requires bird CLI or fallback)
result = reader.read_thread("https://x.com/user/status/123")
```

### CLI Usage

```bash
# Single tweet
python3 /path/to/twitter_reader.py "https://x.com/user/status/123"

# Thread
python3 /path/to/twitter_reader.py --thread "https://x.com/user/status/123"

# JSON output
python3 /path/to/twitter_reader.py --json "URL"
```

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
    │  (API)  │  │(optional)│    │           │
    └─────────┘  └──────────┘    └───────────┘
```

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
2. **Twitter threads:** bird CLI only works on Mac host; ThreadReaderApp blocked
3. **Rate limits:** Gemini API has quotas; batch processing recommended

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
