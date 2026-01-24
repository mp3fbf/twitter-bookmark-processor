# Configuration

All configuration is done via environment variables. The application validates configuration at startup and fails fast on invalid values.

## Required Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | API key for Claude/Anthropic LLM. Required for link content extraction. Get yours at https://console.anthropic.com/ |

## Optional Variables

### Authentication

| Variable | Default | Description |
|----------|---------|-------------|
| `TWITTER_WEBHOOK_TOKEN` | *(none)* | Bearer token for webhook authentication. When not set, webhook runs in dev mode without auth. |

### Paths

| Variable | Default | Description |
|----------|---------|-------------|
| `TWITTER_OUTPUT_DIR` | `/workspace/notes/twitter/` | Directory where Obsidian notes are written. |
| `TWITTER_STATE_FILE` | `data/state.json` | Path to JSON file tracking processed bookmarks. |
| `TWITTER_CACHE_FILE` | `data/link_cache.json` | Path to link extraction cache (30-day TTL). |

### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `TWITTER_RATE_LIMIT_VIDEO` | `1.0` | Minimum seconds between YouTube API calls. |
| `TWITTER_RATE_LIMIT_THREAD` | `0.5` | Minimum seconds between Twitter thread fetches. |
| `TWITTER_RATE_LIMIT_LINK` | `0.2` | Minimum seconds between link content fetches. |

### Processing

| Variable | Default | Description |
|----------|---------|-------------|
| `TWITTER_MAX_WORKERS` | `5` | Maximum parallel processing workers. |
| `LOG_LEVEL` | `INFO` | Logging verbosity. Options: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

### Notifications

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTIFY_CMD` | `notify` | Command to call for notifications. Called as: `{cmd} "message" {type}` where type is `info`, `done`, `error`, or `wait`. |

## Example Configuration

### Development (minimal)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Production (Mac Mini)

```bash
# Required
export ANTHROPIC_API_KEY="sk-ant-..."

# Webhook auth
export TWITTER_WEBHOOK_TOKEN="your-secret-token"

# Paths (optional, defaults work for standard setup)
export TWITTER_OUTPUT_DIR="/workspace/notes/twitter/"

# Logging
export LOG_LEVEL="INFO"
```

### Using with launchd

When running as a launchd service, set environment variables in the plist:

```xml
<key>EnvironmentVariables</key>
<dict>
    <key>ANTHROPIC_API_KEY</key>
    <string>sk-ant-...</string>
    <key>TWITTER_WEBHOOK_TOKEN</key>
    <string>your-secret-token</string>
</dict>
```

## Validation

Configuration is validated at startup. Invalid values cause immediate failure with descriptive error messages:

- `ANTHROPIC_API_KEY`: Must be set (unless running tests)
- `LOG_LEVEL`: Must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL
- Rate limits: Must be non-negative numbers
- `TWITTER_MAX_WORKERS`: Must be at least 1

## Loading Priority

1. Environment variables (always checked)
2. Default values (from `src/core/config.py`)

The application does not read from `.env` files automatically. Use your shell or a process manager to set environment variables.
