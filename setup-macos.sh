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
