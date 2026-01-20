# Twitter Bookmark Processor

Automatic Twitter/X bookmark processing system that extracts knowledge and generates Obsidian-compatible notes.

## Features

- **Hybrid detection**: Polling (every 2 min) + iOS Share Sheet (immediate)
- **Smart classification**: Videos, Threads, Links, Simple tweets
- **Quality-first**: LLM extraction for links (Claude Haiku)
- **Obsidian output**: Markdown with YAML frontmatter, tags, backlinks

## Architecture

```
Twillot export → classifier → processors → Obsidian notes
                     │
                     ├── VIDEO   → YouTube skill
                     ├── THREAD  → Twitter skill
                     ├── LINK    → LLM extraction
                     └── TWEET   → Basic extraction
```

## Quick Start

```bash
# 1. Clone
git clone git@github.com:mp3fbf/twitter-bookmark-processor.git
cd twitter-bookmark-processor

# 2. Setup venv
python3 -m venv /workspace/.mcp-tools/twitter-processor/venv
source /workspace/.mcp-tools/twitter-processor/venv/bin/activate
pip install -r requirements.txt

# 3. Configure
export ANTHROPIC_API_KEY="..."
export TWITTER_WEBHOOK_TOKEN="..."

# 4. Run
python src/main.py --once  # Process backlog once
python src/main.py         # Polling daemon
```

## Documentation

- **[SPEC.md](SPEC.md)** - Complete technical specification (v2.1)
- **[references/](references/)** - Battle-tested code patterns from existing skills

## Content Classification

| Type | Detection | Processing |
|------|-----------|------------|
| VIDEO | `video_urls` or YouTube in links | YouTube skill |
| THREAD | `conversation_id` / reply chain / heuristics | Twitter skill |
| LINK | External URLs (not twitter/youtube) | LLM extraction |
| TWEET | Default (text/images only) | Basic extraction |

## iOS Share Sheet Integration

1. Create iOS Shortcut with POST to `http://{tailscale-ip}:8766/process`
2. Add header: `Authorization: Bearer {token}`
3. Share tweet → processed in seconds → Telegram notification

## Output Example

```markdown
---
schema_version: 1
title: "How to Get Rich"
source: https://x.com/naval/status/123
author: "@naval"
type: thread
tags: [twitter, wealth, philosophy]
---

# How to Get Rich

## TL;DR
Naval's thread on wealth creation...

## Key Points
- Seek wealth, not money
- ...
```

## Dependencies

- Python 3.11+
- httpx, anthropic, jinja2, beautifulsoup4

## Related Projects

- [youtube-video skill](references/youtube_processor.py) - Video transcription
- [twitter skill](references/twitter_reader.py) - Tweet/thread reading
- [yt-webhook](references/yt_webhook_server.py) - Webhook pattern

## License

MIT
