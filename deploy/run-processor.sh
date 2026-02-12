#!/bin/bash
# run-processor.sh - Wrapper script for twitter-processor daemon
#
# Usage: Called by launchd via com.mp3fbf.twitter-processor.plist
#
# Secrets: Loaded from macOS Keychain (encrypted, no plaintext on disk)
#
# Setup (one-time, on Mac Mini):
#   security add-generic-password -a "twitter-processor" -s "anthropic-api-key" \
#     -w "$(op read 'op://Dev/twitter-bookmark-processor anthropic key/credential')"
#   security add-generic-password -a "twitter-processor" -s "x-api-client-id" \
#     -w "$(op read 'op://Dev/Claudexx X app API/Client Secret ID')"
#   security add-generic-password -a "twitter-processor" -s "google-api-key" \
#     -w "YOUR_GOOGLE_API_KEY"

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

if [[ ! -f "$VENV_PYTHON" ]]; then
    log_err "venv not found at $VENV_PYTHON"
    exit 1
fi

# Load secrets from macOS Keychain (encrypted, local, no network)
log "Loading secrets from Keychain..."

ANTHROPIC_API_KEY=$(security find-generic-password -a "twitter-processor" -s "anthropic-api-key" -w 2>&1)
if [[ $? -ne 0 ]] || [[ -z "$ANTHROPIC_API_KEY" ]]; then
    log_err "Failed to read anthropic-api-key from Keychain: $ANTHROPIC_API_KEY"
    exit 1
fi
export ANTHROPIC_API_KEY
log "Anthropic key loaded (${#ANTHROPIC_API_KEY} chars)"

GOOGLE_API_KEY=$(security find-generic-password -a "twitter-processor" -s "google-api-key" -w 2>/dev/null || true)
if [[ -n "$GOOGLE_API_KEY" ]]; then
    export GOOGLE_API_KEY
    log "Google API key loaded (${#GOOGLE_API_KEY} chars)"
else
    log "WARNING: google-api-key not in Keychain (video processing will fail)"
fi

X_API_CLIENT_ID=$(security find-generic-password -a "twitter-processor" -s "x-api-client-id" -w 2>/dev/null || true)
if [[ -n "$X_API_CLIENT_ID" ]]; then
    export X_API_CLIENT_ID
    export BOOKMARK_SOURCE="${BOOKMARK_SOURCE:-both}"
    log "X API Client ID loaded (${#X_API_CLIENT_ID} chars)"
fi

# Output directory
if [[ -d "$HOME/brain/Sources" ]]; then
    export TWITTER_OUTPUT_DIR="${TWITTER_OUTPUT_DIR:-$HOME/brain/Sources/twitter/}"
else
    export TWITTER_OUTPUT_DIR="${TWITTER_OUTPUT_DIR:-$HOME/projects/notes/Sources/twitter/}"
fi

log "Starting twitter-processor daemon..."
log "Output dir: $TWITTER_OUTPUT_DIR"
log "Bookmark source: ${BOOKMARK_SOURCE:-twillot}"

mkdir -p "$TWITTER_OUTPUT_DIR"

# Execute the processor
cd "$PROJECT_DIR"
"$VENV_PYTHON" -m src.main "$@"
EXIT_CODE=$?

# Sync brain vault if output dir is inside brain
SYNC_SCRIPT="$SCRIPT_DIR/sync-brain.sh"
if [[ "$TWITTER_OUTPUT_DIR" == *"/brain/"* ]] && [[ -f "$SYNC_SCRIPT" ]]; then
    bash "$SYNC_SCRIPT" "$TWITTER_OUTPUT_DIR"
fi

exit $EXIT_CODE
