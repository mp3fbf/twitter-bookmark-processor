#!/bin/bash
# setup-macos.sh - Create persistent venv for twitter-bookmark-processor
#
# This script creates a Python virtual environment in the persistent mcp-tools
# directory, ensuring dependencies survive Docker container restarts.
#
# Usage:
#   ./setup-macos.sh
#
# After running:
#   source /workspace/.mcp-tools/twitter-processor/venv/bin/activate
#   python -c "import anthropic; print('OK')"

set -e

# Configuration
VENV_DIR="/workspace/.mcp-tools/twitter-processor/venv"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

echo_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

echo_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if requirements.txt exists
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    echo_error "requirements.txt not found at $REQUIREMENTS_FILE"
    exit 1
fi

# Create parent directory if needed
PARENT_DIR="$(dirname "$VENV_DIR")"
if [[ ! -d "$PARENT_DIR" ]]; then
    echo_info "Creating directory: $PARENT_DIR"
    mkdir -p "$PARENT_DIR"
fi

# Create venv
if [[ -d "$VENV_DIR" ]]; then
    echo_warn "Venv already exists at $VENV_DIR"
    read -p "Recreate? (y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo_info "Removing existing venv..."
        rm -rf "$VENV_DIR"
    else
        echo_info "Using existing venv"
    fi
fi

if [[ ! -d "$VENV_DIR" ]]; then
    echo_info "Creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# Upgrade pip
echo_info "Upgrading pip..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet

# Install dependencies
echo_info "Installing dependencies from requirements.txt..."
"$VENV_DIR/bin/pip" install -r "$REQUIREMENTS_FILE"

# Install project in editable mode (for imports to work)
echo_info "Installing project in editable mode..."
"$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR" --quiet 2>/dev/null || {
    echo_warn "Editable install skipped (no setup.py/pyproject.toml with build)"
}

# Add Jinja2 for templates
echo_info "Installing Jinja2 for templates..."
"$VENV_DIR/bin/pip" install jinja2 --quiet

# Verify installation
echo_info "Verifying installation..."
"$VENV_DIR/bin/python" -c "
import anthropic
import httpx
import aiohttp
print('  anthropic:', anthropic.__version__)
print('  httpx:', httpx.__version__)
print('  aiohttp:', aiohttp.__version__)
"

echo ""
echo_info "Setup complete!"
echo ""
echo "To activate the venv:"
echo "  source $VENV_DIR/bin/activate"
echo ""
echo "To run the processor:"
echo "  python -m src.main --once"
echo ""

# Check if user wants to install launchd plist
PLIST_SRC="$SCRIPT_DIR/deploy/com.mp3fbf.twitter-processor.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.mp3fbf.twitter-processor.plist"

if [[ -f "$PLIST_SRC" ]]; then
    echo ""
    echo_info "Optional: Install launchd daemon"
    echo "This will run the processor in the background, polling for new bookmarks."
    read -p "Install launchd plist? (y/N) " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Create LaunchAgents directory if needed
        mkdir -p "$HOME/Library/LaunchAgents"

        # Stop existing service if running
        if launchctl list | grep -q "com.mp3fbf.twitter-processor"; then
            echo_info "Stopping existing service..."
            launchctl stop com.mp3fbf.twitter-processor 2>/dev/null || true
            launchctl unload "$PLIST_DEST" 2>/dev/null || true
        fi

        # Generate plist with correct paths
        echo_info "Generating plist with your paths..."

        # Get the API key from environment or prompt
        if [[ -z "$ANTHROPIC_API_KEY" ]]; then
            echo_warn "ANTHROPIC_API_KEY not set in environment"
            read -p "Enter your Anthropic API key (or press Enter to skip): " API_KEY
        else
            API_KEY="$ANTHROPIC_API_KEY"
            echo_info "Using ANTHROPIC_API_KEY from environment"
        fi

        # Copy and customize the plist
        sed -e "s|/workspace/.mcp-tools/twitter-processor/venv/bin/python|$VENV_DIR/bin/python|g" \
            -e "s|/workspace/twitter-bookmark-processor|$SCRIPT_DIR|g" \
            -e "s|YOUR_API_KEY_HERE|${API_KEY:-YOUR_API_KEY_HERE}|g" \
            "$PLIST_SRC" > "$PLIST_DEST"

        echo_info "Plist installed to $PLIST_DEST"

        # Load the service
        echo_info "Loading launchd service..."
        launchctl load "$PLIST_DEST"

        # Verify it's running
        sleep 1
        if launchctl list | grep -q "com.mp3fbf.twitter-processor"; then
            echo_info "Service is running!"
            echo ""
            echo "Management commands:"
            echo "  launchctl stop com.mp3fbf.twitter-processor   # Stop"
            echo "  launchctl start com.mp3fbf.twitter-processor  # Start"
            echo "  launchctl unload $PLIST_DEST  # Remove"
            echo ""
            echo "View logs:"
            echo "  tail -f /tmp/twitter-processor.log"
            echo "  tail -f /tmp/twitter-processor.err"
        else
            echo_error "Service failed to start. Check logs at /tmp/twitter-processor.err"
        fi
    else
        echo_info "Skipped daemon installation"
    fi
fi

# Check if user wants to install webhook server plist
WEBHOOK_PLIST_SRC="$SCRIPT_DIR/deploy/com.mp3fbf.twitter-webhook.plist"
WEBHOOK_PLIST_DEST="$HOME/Library/LaunchAgents/com.mp3fbf.twitter-webhook.plist"

if [[ -f "$WEBHOOK_PLIST_SRC" ]]; then
    echo ""
    echo_info "Optional: Install launchd webhook server"
    echo "This will run an HTTP server on port 8766 for iOS Shortcuts integration."
    echo "Endpoints: /health, /metrics, /process"
    read -p "Install webhook server plist? (y/N) " -n 1 -r
    echo

    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # Create LaunchAgents directory if needed
        mkdir -p "$HOME/Library/LaunchAgents"

        # Stop existing service if running
        if launchctl list | grep -q "com.mp3fbf.twitter-webhook"; then
            echo_info "Stopping existing webhook service..."
            launchctl stop com.mp3fbf.twitter-webhook 2>/dev/null || true
            launchctl unload "$WEBHOOK_PLIST_DEST" 2>/dev/null || true
        fi

        # Generate plist with correct paths
        echo_info "Generating webhook plist with your paths..."

        # Reuse API_KEY from daemon section if available, otherwise prompt
        if [[ -z "$API_KEY" ]]; then
            if [[ -z "$ANTHROPIC_API_KEY" ]]; then
                echo_warn "ANTHROPIC_API_KEY not set in environment"
                read -p "Enter your Anthropic API key (or press Enter to skip): " API_KEY
            else
                API_KEY="$ANTHROPIC_API_KEY"
                echo_info "Using ANTHROPIC_API_KEY from environment"
            fi
        else
            echo_info "Using API key from daemon setup"
        fi

        # Ask for optional webhook token
        echo ""
        echo_info "Optional: Set webhook authentication token"
        echo "If set, requests must include 'Authorization: Bearer <token>' header."
        read -p "Enter webhook token (or press Enter for no auth): " WEBHOOK_TOKEN

        # Copy and customize the plist
        sed -e "s|/workspace/.mcp-tools/twitter-processor/venv/bin/python|$VENV_DIR/bin/python|g" \
            -e "s|/workspace/twitter-bookmark-processor|$SCRIPT_DIR|g" \
            -e "s|YOUR_API_KEY_HERE|${API_KEY:-YOUR_API_KEY_HERE}|g" \
            -e "s|<key>TWITTER_WEBHOOK_TOKEN</key>\n        <string></string>|<key>TWITTER_WEBHOOK_TOKEN</key>\n        <string>${WEBHOOK_TOKEN}</string>|g" \
            "$WEBHOOK_PLIST_SRC" > "$WEBHOOK_PLIST_DEST"

        echo_info "Webhook plist installed to $WEBHOOK_PLIST_DEST"

        # Load the service
        echo_info "Loading webhook service..."
        launchctl load "$WEBHOOK_PLIST_DEST"

        # Verify it's running
        sleep 1
        if launchctl list | grep -q "com.mp3fbf.twitter-webhook"; then
            echo_info "Webhook server is running!"
            echo ""
            echo "Test the server:"
            echo "  curl http://localhost:8766/health"
            echo ""
            echo "Management commands:"
            echo "  launchctl stop com.mp3fbf.twitter-webhook   # Stop"
            echo "  launchctl start com.mp3fbf.twitter-webhook  # Start"
            echo "  launchctl unload $WEBHOOK_PLIST_DEST  # Remove"
            echo ""
            echo "View logs:"
            echo "  tail -f /tmp/twitter-webhook.log"
            echo "  tail -f /tmp/twitter-webhook.err"
        else
            echo_error "Webhook server failed to start. Check logs at /tmp/twitter-webhook.err"
        fi
    else
        echo_info "Skipped webhook installation"
    fi
fi

echo ""
echo_info "All done!"
echo ""
echo "Manual plist installation (if skipped above):"
echo "  Daemon: cp $PLIST_SRC ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.mp3fbf.twitter-processor.plist"
echo "  Webhook: cp $WEBHOOK_PLIST_SRC ~/Library/LaunchAgents/ && launchctl load ~/Library/LaunchAgents/com.mp3fbf.twitter-webhook.plist"
