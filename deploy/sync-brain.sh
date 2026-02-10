#!/bin/bash
# sync-brain.sh - Sync twitter notes: commit → push Gitea → pull MacBook
#
# Usage: Called by run-processor.sh after processing bookmarks
#   bash deploy/sync-brain.sh /path/to/brain/Sources/twitter/
#
# Flow:
#   1. Detect changes in Sources/twitter/ (exit silently if none)
#   2. git add + commit + push to Gitea (origin)
#   3. Detect MacBook via Tailscale, SSH pull --ff-only for Obsidian sync
#
# The MacBook pull is best-effort: if Tailscale is unavailable, MacBook is
# offline, or SSH fails, the script logs a warning and exits 0.
#
# Environment overrides:
#   TAILSCALE      - Path to tailscale CLI (auto-detected)
#   MACBOOK_USER   - SSH username on MacBook (default: robertocunha)

TWITTER_DIR="${1:-$HOME/brain/Sources/twitter}"
BRAIN_DIR="$(cd "$TWITTER_DIR/../.." 2>/dev/null && pwd)"

if [[ ! -d "$BRAIN_DIR/.git" ]]; then
    echo "[sync-brain] Not a git repo: $BRAIN_DIR" >&2
    exit 0
fi

cd "$BRAIN_DIR"

# Check for changes in Sources/twitter/
if git diff --quiet HEAD -- "Sources/twitter/" 2>/dev/null && \
   [[ -z "$(git ls-files --others --exclude-standard Sources/twitter/)" ]]; then
    # No changes
    exit 0
fi

# Count new/modified files
NEW_COUNT=$(git ls-files --others --exclude-standard Sources/twitter/ | wc -l | tr -d ' ')
MOD_COUNT=$(git diff --name-only HEAD -- Sources/twitter/ 2>/dev/null | wc -l | tr -d ' ')

echo "[sync-brain] Syncing twitter notes to Brain vault (+${NEW_COUNT} new, ~${MOD_COUNT} modified)"

git add Sources/twitter/
git commit -m "twitter: +${NEW_COUNT} new, ~${MOD_COUNT} modified notes" --no-gpg-sign --quiet
git push --quiet 2>&1 || echo "[sync-brain] WARNING: push failed, will retry next cycle" >&2

# ── Pull on MacBook via Tailscale SSH ──────────────────────
TAILSCALE="${TAILSCALE:-$(command -v tailscale 2>/dev/null || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)}"
MACBOOK_HOST=""
if [ -x "$TAILSCALE" ]; then
    MACBOOK_HOST=$("$TAILSCALE" status 2>/dev/null | grep -i macbook | awk '{print $1}' | head -1)
fi

if [ -n "$MACBOOK_HOST" ]; then
    MACBOOK_USER="${MACBOOK_USER:-robertocunha}"
    if ssh -o ConnectTimeout=3 -o BatchMode=yes "$MACBOOK_USER@$MACBOOK_HOST" \
        "cd ~/brain && git pull --ff-only" >/dev/null 2>&1; then
        echo "[sync-brain] MacBook synced ($MACBOOK_HOST)"
    else
        echo "[sync-brain] WARNING: MacBook found ($MACBOOK_HOST) but SSH/pull failed" >&2
    fi
else
    echo "[sync-brain] MacBook not reachable via Tailscale (skipping)" >&2
fi

echo "[sync-brain] Done"
