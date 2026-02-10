#!/bin/bash
# sync-brain.sh - Commit and push new twitter notes to Brain vault (Gitea)
#
# Usage: Called by run-processor.sh after processing bookmarks
#   bash deploy/sync-brain.sh /path/to/brain/Sources/twitter/
#
# Only commits if there are actual changes. Silent on no-op.

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

echo "[sync-brain] Done"
