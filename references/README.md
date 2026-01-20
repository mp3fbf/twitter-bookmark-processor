# Reference Files

Battle-tested implementations from the workspace that serve as **inspiration and patterns** (not direct dependencies) for the twitter-bookmark-processor.

> **Note:** The actual implementation will be custom-built, inspired by these patterns but not importing them directly. This allows flexibility to use only `httpx` (no `requests`) and implement robust thread detection.

## Files

### `twitter_reader.py`
**Source:** `~/.claude/skills/twitter/scripts/`

Twitter/X content reader with multiple backends (bird CLI, ThreadReaderApp, twitterapi.io).

**Patterns to learn from:**
- `Tweet` dataclass (lines 34-54) - data model structure with `video_urls`, `media_urls`, `links`, `is_thread`
- `_bird_to_tweet()` function - how to normalize data from multiple API sources
- `format_tweet_markdown()` - output formatting approach

**Note:** This file uses `requests` for fallbacks. Our implementation will use only `httpx`.

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
