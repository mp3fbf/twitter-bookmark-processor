#!/bin/bash
# sync-brain.sh - Copy processed notes from projects → brain vault
#
# Called after processing. Syncthing handles distribution to other machines.
# Exits silently if ~/brain/ doesn't exist (e.g., inside Docker container).

BRAIN="$HOME/brain"

if [[ ! -d "$BRAIN" ]]; then
    exit 0
fi

SOURCES="$HOME/projects/notes/Sources"
TARGET="$BRAIN/Sources"

# Rsync each source type that exists
for DIR in twitter Email "Zendesk Guide" videos; do
    if [[ -d "$SOURCES/$DIR" ]]; then
        mkdir -p "$TARGET/$DIR"
        rsync -a --delete "$SOURCES/$DIR/" "$TARGET/$DIR/"
    fi
done

# Count what we synced
TW=$(find "$TARGET/twitter" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')
echo "[sync-brain] Synced to brain: ${TW} twitter notes (Syncthing distributes)"
