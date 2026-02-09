#!/bin/bash
# setup-launchd.sh - Non-interactive setup for twitter-bookmark-processor daemon
#
# Run on Mac Mini (NOT inside Docker container):
#   bash ~/projects/twitter-bookmark-processor/deploy/setup-launchd.sh
#
# What it does:
#   1. Creates .venv and installs Python dependencies
#   2. Reads OP_SERVICE_ACCOUNT_TOKEN from environment
#   3. Injects token + paths into launchd plists
#   4. Installs and loads both services (processor daemon + webhook server)
#
# Prerequisites:
#   - Python 3.11+ installed
#   - op CLI installed (brew install 1password-cli)
#   - OP_SERVICE_ACCOUNT_TOKEN set in shell environment

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!!]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# --- Resolve paths ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$PROJECT_DIR/.venv"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"

echo ""
echo "=== Twitter Bookmark Processor - Daemon Setup ==="
echo "Project: $PROJECT_DIR"
echo ""

# --- Step 1: Validate 1Password token file ---
TOKEN_FILE=""
for f in "$HOME/.secrets/op-token-yolo" "$HOME/.secrets/op-token" "$HOME/.op-token"; do
    [[ -f "$f" ]] && TOKEN_FILE="$f" && break
done

if [[ -z "$TOKEN_FILE" ]]; then
    fail "No 1Password token file found. Expected one of:
    ~/.secrets/op-token-yolo
    ~/.secrets/op-token
    ~/.op-token"
fi

# Validate token works
export OP_SERVICE_ACCOUNT_TOKEN="$(cat "$TOKEN_FILE")"
export PATH="/opt/homebrew/bin:$PATH"

if ! op read "op://Dev/twitter-bookmark-processor anthropic key/credential" >/dev/null 2>&1; then
    fail "1Password token from $TOKEN_FILE is invalid or can't access Dev vault."
fi
info "1Password token valid (from $TOKEN_FILE)"

# --- Step 2: Create venv + install deps ---
if [[ ! -f "$VENV_DIR/bin/python" ]]; then
    info "Creating venv at $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
else
    info "Venv exists at $VENV_DIR"
fi

info "Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$PROJECT_DIR/requirements.txt" --quiet
"$VENV_DIR/bin/pip" install jinja2 --quiet

# Verify
"$VENV_DIR/bin/python" -c "import anthropic; print('  anthropic OK')"
info "Dependencies installed"

# --- Step 3: Make wrapper scripts executable ---
chmod +x "$SCRIPT_DIR/run-processor.sh"
chmod +x "$SCRIPT_DIR/run-webhook.sh"

# --- Step 4: Generate and install plists ---
mkdir -p "$LAUNCH_AGENTS"

# Resolve output dir (notes/twitter/ is sibling to project dir)
OUTPUT_DIR="$PROJECT_DIR/../notes/twitter"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

install_plist() {
    local src="$1"
    local label="$2"
    local dest="$LAUNCH_AGENTS/$label.plist"

    # Stop existing service
    if launchctl list 2>/dev/null | grep -q "$label"; then
        launchctl stop "$label" 2>/dev/null || true
        launchctl unload "$dest" 2>/dev/null || true
    fi

    # Generate plist with real paths (no secrets in plist - wrapper loads from file)
    sed \
        -e "s|/workspace/twitter-bookmark-processor|$PROJECT_DIR|g" \
        -e "s|__PROJECT_DIR__/..|$(dirname "$PROJECT_DIR")|g" \
        "$src" > "$dest"

    # Load service
    launchctl load "$dest"
    sleep 1

    if launchctl list 2>/dev/null | grep -q "$label"; then
        info "$label loaded and running"
    else
        warn "$label loaded but may not be running yet (check logs)"
    fi
}

install_plist "$SCRIPT_DIR/com.mp3fbf.twitter-processor.plist" "com.mp3fbf.twitter-processor"
install_plist "$SCRIPT_DIR/com.mp3fbf.twitter-webhook.plist" "com.mp3fbf.twitter-webhook"

# --- Step 5: Summary ---
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Services installed:"
echo "  - com.mp3fbf.twitter-processor (daemon, polls X API every 15min)"
echo "  - com.mp3fbf.twitter-webhook   (HTTP server on port 8766)"
echo ""
echo "Logs:"
echo "  tail -f /tmp/twitter-processor.log"
echo "  tail -f /tmp/twitter-processor.err"
echo "  tail -f /tmp/twitter-webhook.log"
echo "  tail -f /tmp/twitter-webhook.err"
echo ""
echo "Management:"
echo "  launchctl stop com.mp3fbf.twitter-processor"
echo "  launchctl start com.mp3fbf.twitter-processor"
echo "  launchctl list | grep twitter"
echo ""
