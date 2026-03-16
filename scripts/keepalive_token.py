#!/usr/bin/env python3
"""Token Keepalive — ensures X API OAuth token stays alive.

Standalone script that refreshes the token and validates it with a canary
request (GET /2/users/me). Designed to run via launchd every 12 hours,
completely independent of the main daemon.

Also serves as the recovery tool for Step 1 (revive expired access token).

Usage:
    python3 scripts/keepalive_token.py           # Refresh + validate
    python3 scripts/keepalive_token.py --status   # Show token status only
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sources.x_api_auth import XApiAuth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TOKEN_FILE = Path("data/x_api_tokens.json")
HEALTH_FILE = Path("data/token_health.json")
USERS_ME_URL = "https://api.twitter.com/2/users/me"


def notify(message: str, msg_type: str = "info") -> None:
    """Send Telegram notification via notify script."""
    from src.core.notifier import notify as _notify
    _notify(message, msg_type)


def save_health(status: str, username: str | None = None, error: str | None = None) -> None:
    """Persist health check result to data/token_health.json."""
    health = {
        "last_check": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "username": username,
        "error": error,
    }

    # Append to history (keep last 50)
    existing = []
    if HEALTH_FILE.exists():
        try:
            data = json.loads(HEALTH_FILE.read_text())
            existing = data.get("history", [])
        except (json.JSONDecodeError, KeyError):
            pass

    existing.append(health)
    existing = existing[-50:]

    HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
    HEALTH_FILE.write_text(json.dumps({
        "current": health,
        "history": existing,
    }, indent=2))


async def validate_token(token: str) -> str | None:
    """Validate token with GET /2/users/me. Returns username or None."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
            response = await client.get(
                USERS_ME_URL,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code == 200:
                data = response.json().get("data", {})
                return data.get("username")
            else:
                logger.error("Canary failed: HTTP %d — %s", response.status_code, response.text[:200])
                return None
    except Exception as e:
        logger.error("Canary request failed: %s", e)
        return None


async def keepalive(client_id: str) -> bool:
    """Main keepalive flow: refresh token if needed, validate with canary."""
    auth = XApiAuth(client_id=client_id, token_file=TOKEN_FILE)

    if not auth.has_tokens():
        msg = "X API: no tokens found. Run --authorize to set up."
        logger.error(msg)
        notify(msg, "error")
        save_health("no_tokens", error=msg)
        return False

    # Step 1: Get valid token (auto-refreshes if expired)
    try:
        token = await auth.get_valid_token()
        logger.info("Token obtained (refresh if needed: automatic)")
    except Exception as e:
        # Refresh failed — token might be completely dead
        msg = f"X API token refresh failed: {e}"
        logger.error(msg)
        notify(msg, "error")
        save_health("refresh_failed", error=str(e))
        return False

    # Step 2: Canary — validate token actually works
    username = await validate_token(token)
    if username:
        logger.info("Token valid, user: @%s", username)
        save_health("healthy", username=username)
        return True
    else:
        # Token refreshed but doesn't work — try one more explicit refresh
        logger.warning("Token exists but canary failed. Forcing refresh...")
        try:
            await auth.refresh_tokens()
            token = await auth.get_valid_token()
            username = await validate_token(token)
            if username:
                logger.info("Token valid after forced refresh, user: @%s", username)
                save_health("healthy", username=username)
                return True
        except Exception as e:
            pass

        msg = "X API token invalid after refresh. Manual --authorize may be needed."
        logger.error(msg)
        notify(msg, "error")
        save_health("invalid", error="Canary failed after refresh")
        return False


async def show_status(client_id: str) -> None:
    """Show current token status without modifying anything."""
    auth = XApiAuth(client_id=client_id, token_file=TOKEN_FILE)

    if not auth.has_tokens():
        print("Status: NO TOKENS")
        return

    tokens = auth._load_tokens()
    now = time.time()
    expires_in = tokens.expires_at - now

    print(f"Access token: {'EXPIRED' if tokens.is_expired else 'VALID'}")
    print(f"Expires in: {int(expires_in)}s ({int(expires_in / 3600)}h)")
    print(f"Scope: {tokens.scope}")

    if HEALTH_FILE.exists():
        health = json.loads(HEALTH_FILE.read_text())
        current = health.get("current", {})
        print(f"\nLast health check: {current.get('last_check', 'never')}")
        print(f"Status: {current.get('status', 'unknown')}")
        print(f"Username: @{current.get('username', '?')}")


def main():
    parser = argparse.ArgumentParser(description="X API Token Keepalive")
    parser.add_argument("--status", action="store_true", help="Show token status only")
    args = parser.parse_args()

    # Get client ID from env or Keychain
    client_id = os.environ.get("X_API_CLIENT_ID")
    if not client_id:
        # Try loading from macOS Keychain (for launchd context)
        import subprocess
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-a", "twitter-processor",
                 "-s", "x-api-client-id", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                client_id = result.stdout.strip()
        except Exception:
            pass

    if not client_id:
        print("ERROR: X_API_CLIENT_ID not set and not in Keychain", file=sys.stderr)
        sys.exit(1)

    if args.status:
        asyncio.run(show_status(client_id))
    else:
        ok = asyncio.run(keepalive(client_id))
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
