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

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
log_err() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }

# Homebrew (not in launchd PATH)
export PATH="/opt/homebrew/bin:$PATH"

log "Script starting (PID $$)..."

# Auto-detect project directory (script is in deploy/)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"

# Validate venv exists
if [[ ! -f "$VENV_PYTHON" ]]; then
    log_err "venv not found at $VENV_PYTHON"
    exit 1
fi

log "Loading 1Password token..."

# Load 1Password service account token (from file if not in env)
if [[ -z "$OP_SERVICE_ACCOUNT_TOKEN" ]]; then
    for token_file in "$HOME/.secrets/op-token-yolo" "$HOME/.secrets/op-token" "$HOME/.op-token"; do
        if [[ -f "$token_file" ]]; then
            export OP_SERVICE_ACCOUNT_TOKEN="$(cat "$token_file")"
            log "Token loaded from $token_file (${#OP_SERVICE_ACCOUNT_TOKEN} chars)"
            break
        fi
    done
fi

if [[ -z "$OP_SERVICE_ACCOUNT_TOKEN" ]]; then
    log_err "OP_SERVICE_ACCOUNT_TOKEN not set and no token file found"
    exit 1
fi

# Fetch ANTHROPIC_API_KEY from 1Password (with 15s timeout to avoid silent hang)
log "Fetching Anthropic key from 1Password..."
OP_OUTPUT_FILE=$(mktemp)
if timeout 15 op read "op://Dev/twitter-bookmark-processor anthropic key/credential" > "$OP_OUTPUT_FILE" 2>&1; then
    ANTHROPIC_API_KEY=$(cat "$OP_OUTPUT_FILE")
else
    OP_EXIT=$?
    log_err "Failed to fetch ANTHROPIC_API_KEY (exit: $OP_EXIT, output: $(cat "$OP_OUTPUT_FILE"))"
    rm -f "$OP_OUTPUT_FILE"
    exit 1
fi
rm -f "$OP_OUTPUT_FILE"

if [[ -z "$ANTHROPIC_API_KEY" ]]; then
    log_err "ANTHROPIC_API_KEY is empty after 1Password fetch"
    exit 1
fi
export ANTHROPIC_API_KEY

# Fetch X API Client ID from 1Password
log "Fetching X API credentials..."
X_API_CLIENT_ID=$(timeout 15 op read "op://Dev/Claudexx X app API/Client Secret ID" 2>/dev/null || true)
if [[ -n "$X_API_CLIENT_ID" ]]; then
    export X_API_CLIENT_ID
    export BOOKMARK_SOURCE="${BOOKMARK_SOURCE:-both}"
fi

# Output directory: Brain vault (synced to MacBook via Gitea)
# Falls back to projects dir if brain vault doesn't exist
if [[ -d "$HOME/brain/Sources" ]]; then
    export TWITTER_OUTPUT_DIR="${TWITTER_OUTPUT_DIR:-$HOME/brain/Sources/twitter/}"
else
    export TWITTER_OUTPUT_DIR="${TWITTER_OUTPUT_DIR:-$HOME/projects/notes/twitter/}"
fi

# Log startup (without exposing keys)
log "Starting twitter-processor daemon..."
log "Anthropic key loaded (${#ANTHROPIC_API_KEY} chars)"
log "Output dir: $TWITTER_OUTPUT_DIR"
log "Bookmark source: ${BOOKMARK_SOURCE:-twillot}"
if [[ -n "$X_API_CLIENT_ID" ]]; then
    log "X API Client ID loaded (${#X_API_CLIENT_ID} chars)"
fi

# Ensure output directory exists
mkdir -p "$TWITTER_OUTPUT_DIR"

# Execute the processor (passes through any CLI arguments)
cd "$PROJECT_DIR"
"$VENV_PYTHON" -m src.main "$@"
EXIT_CODE=$?

# Sync brain vault if output dir is inside brain and notes were written
SYNC_SCRIPT="$SCRIPT_DIR/sync-brain.sh"
if [[ "$TWITTER_OUTPUT_DIR" == *"/brain/"* ]] && [[ -f "$SYNC_SCRIPT" ]]; then
    bash "$SYNC_SCRIPT" "$TWITTER_OUTPUT_DIR"
fi

exit $EXIT_CODE
