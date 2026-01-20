# Reference Files

These are existing implementations from the workspace that serve as patterns and dependencies for the twitter-bookmark-processor.

## Files

### `twitter_reader.py`
**Source:** `~/.claude/skills/twitter/scripts/`

Twitter/X content reader with multiple backends (bird CLI, ThreadReaderApp, twitterapi.io).

**Key components to reuse:**
- `Tweet` dataclass (lines 34-54) - data model with `video_urls`, `media_urls`, `links`, `is_thread`
- `_bird_to_tweet()` function - normalizes data from multiple API sources
- `format_tweet_markdown()` - output formatting

### `youtube_processor.py`
**Source:** `~/.claude/skills/youtube-video/scripts/`

YouTube video processor using Gemini API for transcription and summarization.

**Key components:**
- Video URL validation patterns
- Obsidian note generation with YAML frontmatter
- Integration with Gemini API for content extraction

### `yt_webhook_server.py`
**Source:** `/workspace/_scripts/yt-webhook/server.py`

HTTP webhook server for processing YouTube videos from iOS Shortcuts.

**Key patterns to reuse:**
- `HTTPServer` + `BaseHTTPRequestHandler` pattern
- Async processing with `threading.Thread`
- 202 Accepted response for long-running tasks
- Telegram notification integration via `notify` command

### `kb_processor.py`
**Source:** `/workspace/_scripts/`

Knowledge base processor for email content.

**Key patterns to reuse:**
- JSON-based state management
- Noise filtering patterns
- Chronological processing with checkpoints
- Atomic file writes

## Usage

These files are for reference only. The actual implementation in `src/` will:
1. Import patterns and logic from these references
2. Adapt them for Twitter bookmark processing
3. Maintain compatibility with existing skills where possible
