#!/bin/bash
# run-processor.sh - Wrapper script for twitter-processor daemon
# Fetches secrets from 1Password at runtime via service account
#
# Usage: Called by launchd via com.mp3fbf.twitter-processor.plist
# Security: API keys are only in memory, never written to disk
#
# Requirements:
#   - op CLI installed (brew install 1password-cli)
#   - OP_SERVICE_ACCOUNT_TOKEN set in launchd plist EnvironmentVariables
#   - Secrets stored in 1Password Dev vault

set -e

# Homebrew (not in launchd PATH)
export PATH="/opt/homebrew/bin:$PATH"

# Auto-detect project directory (script is in deploy/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

# Validate venv exists
if [[ ! -f "$VENV_PYTHON" ]]; then
    echo "ERROR: venv not found at $VENV_PYTHON" >&2
    echo "Run setup-macos.sh first to create the virtual environment" >&2
    exit 1
fi

# Load 1Password service account token (from file if not in env)
if [[ -z "$OP_SERVICE_ACCOUNT_TOKEN" ]]; then
    for token_file in "$HOME/.secrets/op-token-yolo" "$HOME/.secrets/op-token" "$HOME/.op-token"; do
        [[ -f "$token_file" ]] && export OP_SERVICE_ACCOUNT_TOKEN="$(cat "$token_file")" && break
    done
fi

if [[ -z "$OP_SERVICE_ACCOUNT_TOKEN" ]]; then
    echo "ERROR: OP_SERVICE_ACCOUNT_TOKEN not set and no token file found" >&2
    exit 1
fi

# Fetch ANTHROPIC_API_KEY from 1Password
ANTHROPIC_API_KEY=$(op read "op://Dev/twitter-bookmark-processor anthropic key/credential" 2>&1)
OP_EXIT=$?

if [[ $OP_EXIT -ne 0 ]] || [[ -z "$ANTHROPIC_API_KEY" ]]; then
    echo "ERROR: Failed to fetch ANTHROPIC_API_KEY from 1Password (exit: $OP_EXIT)" >&2
    exit 1
fi
export ANTHROPIC_API_KEY

# Fetch X API Client ID from 1Password
X_API_CLIENT_ID=$(op read "op://Dev/Claudexx X app API/Client Secret ID" 2>/dev/null || true)
if [[ -n "$X_API_CLIENT_ID" ]]; then
    export X_API_CLIENT_ID
    export BOOKMARK_SOURCE="${BOOKMARK_SOURCE:-both}"
fi

# Set output directory for Mac Mini (maps to /workspace/notes/twitter/ inside container)
export TWITTER_OUTPUT_DIR="${TWITTER_OUTPUT_DIR:-$HOME/projects/notes/twitter/}"

# Log startup (without exposing keys)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting twitter-processor daemon..."
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Anthropic key loaded from 1Password (${#ANTHROPIC_API_KEY} chars)"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Output dir: $TWITTER_OUTPUT_DIR"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Bookmark source: ${BOOKMARK_SOURCE:-twillot}"
if [[ -n "$X_API_CLIENT_ID" ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] X API Client ID loaded (${#X_API_CLIENT_ID} chars)"
fi

# Ensure output directory exists
mkdir -p "$TWITTER_OUTPUT_DIR"

# Execute the processor (passes through any CLI arguments)
cd "$PROJECT_DIR"
exec "$VENV_PYTHON" -m src.main "$@"
