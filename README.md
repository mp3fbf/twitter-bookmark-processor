# Twitter Bookmark Processor

Transform Twitter/X bookmarks into structured Obsidian notes with automatic content extraction.

## Features

- **Hybrid Input**: Polling mode (2-min intervals) + iOS Share Sheet (instant)
- **Smart Classification**: Automatically detects videos, threads, links, and simple tweets
- **LLM Extraction**: Claude Haiku extracts TL;DR, key points, and tags from web links
- **Obsidian Output**: Markdown with YAML frontmatter, tags, and backlinks
- **Rate Limiting**: Built-in throttling for external APIs
- **Insight Engine**: Two-stage AI pipeline (capture → distill) for richer notes
- **X API Integration**: OAuth 2.0 PKCE for fetching bookmarks directly from X/Twitter
- **Brain Sync**: Automatic sync to Obsidian vault via launchd + Syncthing
- **Production Ready**: 630+ tests, structured logging, graceful shutdown

## Architecture

```
                    ┌──────────────────┐
                    │  Input Sources   │
                    │                  │
                    │  • Twillot JSON  │
                    │  • iOS Webhook   │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │   Classifier     │
                    │                  │
                    │  VIDEO|THREAD    │
                    │  LINK|TWEET      │
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼──────┐    ┌───────▼──────┐     ┌───────▼──────┐
│ VideoProcessor│    │ThreadProcessor│     │ LinkProcessor│
│              │    │              │     │              │
│ YouTube skill│    │Twitter skill │     │ LLM extract  │
└───────┬──────┘    └───────┬──────┘     └───────┬──────┘
        │                    │                    │
        └────────────────────┼────────────────────┘
                             │
                    ┌────────▼─────────┐
                    │  ObsidianWriter  │
                    │                  │
                    │  Jinja2 templates│
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │  Obsidian Vault  │
                    └──────────────────┘
```

## Quick Start

### Prerequisites

- Python 3.11+
- Anthropic API key (for link extraction)

### Installation

```bash
# Clone the repository
git clone git@github.com:mp3fbf/twitter-bookmark-processor.git
cd twitter-bookmark-processor

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Basic Usage

```bash
# Set required environment variable
export ANTHROPIC_API_KEY="sk-ant-..."

# Process backlog once and exit
python -m src.main --once

# Run as daemon (polls every 2 minutes)
python -m src.main

# Enable debug logging
python -m src.main --once --verbose
```

## Configuration

All configuration via environment variables. See [docs/configuration.md](docs/configuration.md) for full reference.

### Essential Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for LLM extraction |
| `TWITTER_WEBHOOK_TOKEN` | No | Bearer token for webhook auth (dev mode if unset) |
| `TWITTER_OUTPUT_DIR` | No | Output directory (default: `/workspace/notes/Sources/twitter/`) |

### Example .env

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-api03-...

# Optional - webhook authentication
TWITTER_WEBHOOK_TOKEN=your-secret-token

# Optional - paths
TWITTER_OUTPUT_DIR=/Users/me/notes/twitter/

# Optional - logging
LOG_LEVEL=INFO
```

## Content Classification

| Type | Detection Method | Processor |
|------|------------------|-----------|
| VIDEO | `video_urls` field or YouTube link | Extracts transcript + summary |
| THREAD | `conversation_id` / reply chain / heuristics | Fetches full thread with key points |
| LINK | External URLs (non-Twitter/YouTube) | LLM extracts TL;DR + key points |
| TWEET | Default (text/images only) | Basic title + content extraction |

## Deployment

### Option 1: Polling Daemon (Recommended)

The simplest deployment - watches a directory for new Twillot exports.

```bash
# Create backlog directory
mkdir -p data/backlog

# Run daemon
python -m src.main
```

Export your bookmarks with Twillot and drop the JSON file in `data/backlog/`. The daemon will process it within 2 minutes.

### Option 2: Webhook Server

For instant processing via iOS Share Sheet:

```bash
# Run webhook server on port 8766
python -m src.main --port 8766

# Or with systemd/launchd for production
```

See [docs/ios-shortcut.md](docs/ios-shortcut.md) for iOS Shortcut setup.

### Brain Vault Sync (Obsidian)

Notes are written to `~/projects/notes/Sources/twitter/` and automatically synced to the Obsidian brain vault via a launchd job:

```
Container writes .md → /workspace/notes/Sources/twitter/
                       (= ~/projects/notes/Sources/twitter/ on Mac Mini)
                                    │
                           launchd WatchPaths detects change
                                    │
                           sync-brain.sh (rsync)
                                    │
                           ~/brain/Sources/twitter/
                                    │
                           Syncthing distributes
                                    │
                           MacBook, iPhone, etc
```

**Setup (one-time):**
```bash
bash ~/projects/_inbox/install-sync-brain.sh
```

This installs `com.mp3fbf.sync-brain` launchd job that:
- Watches `~/projects/notes/Sources/` for changes (event-driven)
- Runs `sync-brain.sh` to rsync → `~/brain/Sources/`
- Also runs every 15 min as safety net
- Log: `/tmp/sync-brain.log`

### Option 3: macOS launchd

Create `~/Library/LaunchAgents/com.user.twitter-processor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.user.twitter-processor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/.venv/bin/python</string>
        <string>-m</string>
        <string>src.main</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/twitter-bookmark-processor</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ANTHROPIC_API_KEY</key>
        <string>sk-ant-...</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/twitter-processor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/twitter-processor.err</string>
</dict>
</plist>
```

Load with:
```bash
launchctl load ~/Library/LaunchAgents/com.user.twitter-processor.plist
```

### Option 4: Insight Engine

The Insight Engine is a two-stage pipeline that produces richer notes using AI distillation:

1. **Capture**: Fetches tweet content, resolves links, analyzes images, downloads video transcripts
2. **Distill**: LLM extracts structured insights with title, tags, value type, and original content

```bash
# Process new bookmarks through insight pipeline
python3 -m src.main --insight

# Limit to N bookmarks
python3 -m src.main --insight --limit 10

# Reprocess stage 2 (re-distill existing content packages)
python3 -m src.main --reprocess-stage2

# Retry bookmarks flagged for review
python3 -m src.main --retry-reviews
```

**X API OAuth (required for fetching bookmarks):**
```bash
# One-time authorization
python3 -m src.main --authorize

# Tokens stored in data/x_api_tokens.json (refresh tokens last 6 months)
```

State is tracked in `data/insight_state.json`, separate from the legacy `data/state.json`.

## Troubleshooting

### Common Issues

**"ANTHROPIC_API_KEY not set"**
```bash
# Verify the key is exported
echo $ANTHROPIC_API_KEY
# Should show sk-ant-...
```

**No notes generated after processing**
```bash
# Check the state file for processed IDs
cat data/state.json | head

# Check logs for errors
LOG_LEVEL=DEBUG python -m src.main --once
```

**Webhook returns 401 Unauthorized**
- Verify `TWITTER_WEBHOOK_TOKEN` matches your iOS Shortcut header
- Header format must be: `Authorization: Bearer <token>`

**Rate limit errors**
- Default rate limits are conservative (1/sec for video, 5/sec for links)
- Adjust via `TWITTER_RATE_LIMIT_*` env vars if needed

### Health Checks

```bash
# Check webhook health
curl http://localhost:8766/health
# Returns: {"status": "ok"}

# Check metrics
curl http://localhost:8766/metrics
# Returns: {"requests_total": 10, "processed_total": 8, ...}
```

### Logs

Logs are JSON-formatted for structured parsing:

```bash
# View logs with jq
python -m src.main --once 2>&1 | jq '.'

# Filter errors only
python -m src.main --once 2>&1 | jq 'select(.level == "ERROR")'
```

## Development

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src

# Run specific test file
pytest tests/test_pipeline.py -v
```

### Code Quality

```bash
# Lint check
ruff check .

# Format code
ruff format .
```

### Project Structure

```
twitter-bookmark-processor/
├── src/
│   ├── core/           # Core modules (config, pipeline, state)
│   ├── insight/        # Insight Engine (capture, distill, pipeline)
│   ├── processors/     # Content type processors
│   ├── output/         # Obsidian writer + templates
│   ├── sources/        # Input readers (Twillot, X API)
│   ├── main.py         # CLI entry point
│   └── webhook_server.py
├── deploy/             # launchd plists, sync scripts, run scripts
├── tests/              # 630+ test cases
├── docs/               # Documentation
├── data/               # Runtime data (state, cache, backlog)
└── references/         # Reference implementations
```

## Documentation

- **[docs/configuration.md](docs/configuration.md)** - Environment variables reference
- **[docs/ios-shortcut.md](docs/ios-shortcut.md)** - iOS Share Sheet setup guide
- **[docs/twillot-schema.md](docs/twillot-schema.md)** - Twillot export format
- **[SPEC.md](SPEC.md)** - Technical specification

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Write tests for new functionality
4. Ensure all tests pass: `pytest`
5. Ensure code is formatted: `ruff format . && ruff check .`
6. Commit with descriptive message
7. Open a Pull Request

### Commit Message Format

```
<type>: <short description>

<optional body>

Co-Authored-By: Your Name <email>
```

Types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`

## License

MIT
