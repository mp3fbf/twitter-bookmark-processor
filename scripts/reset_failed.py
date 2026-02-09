#!/usr/bin/env python3
"""Reset failed bookmark entries in state.json for reprocessing.

Finds all entries with status "error" and removes them from state,
allowing the daemon to reprocess them on the next cycle.

Usage:
    python3 scripts/reset_failed.py              # Dry run (show what would be reset)
    python3 scripts/reset_failed.py --apply      # Actually reset the entries
    python3 scripts/reset_failed.py --filter thread  # Only reset thread failures
    python3 scripts/reset_failed.py --filter video   # Only reset video failures
"""

import argparse
import json
import sys
from pathlib import Path

STATE_FILE = Path(__file__).parent.parent / "data" / "state.json"


def main():
    parser = argparse.ArgumentParser(description="Reset failed state entries")
    parser.add_argument("--apply", action="store_true", help="Actually apply changes")
    parser.add_argument("--filter", type=str, help="Only reset entries with this text in error")
    parser.add_argument("--state-file", type=Path, default=STATE_FILE, help="Path to state.json")
    args = parser.parse_args()

    state_file = args.state_file
    if not state_file.exists():
        print(f"State file not found: {state_file}", file=sys.stderr)
        return 1

    with open(state_file) as f:
        state = json.load(f)

    # State format: {"processed": {tweet_id: {status, error, ...}}, "last_updated": "..."}
    processed = state.get("processed", {})

    # Find error entries
    errors = []
    for tweet_id, entry in processed.items():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "error":
            error_msg = entry.get("error", "")
            if args.filter and args.filter.lower() not in error_msg.lower():
                continue
            errors.append((tweet_id, error_msg))

    if not errors:
        print("No failed entries found.")
        return 0

    print(f"Found {len(errors)} failed entries:\n")
    for tweet_id, error_msg in errors:
        short_error = error_msg[:80] + "..." if len(error_msg) > 80 else error_msg
        print(f"  {tweet_id}: {short_error}")

    if not args.apply:
        print(f"\nDry run. Use --apply to reset these {len(errors)} entries.")
        return 0

    # Remove error entries from processed
    for tweet_id, _ in errors:
        del processed[tweet_id]

    # Write back
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    print(f"\nReset {len(errors)} entries. They will be reprocessed on next daemon cycle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
