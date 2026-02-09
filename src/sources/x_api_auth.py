"""X API OAuth 2.0 PKCE Authentication.

Handles the OAuth 2.0 Authorization Code Flow with PKCE (Proof Key for Code
Exchange) for X API access. PKCE is required for public clients (no client
secret) which is the recommended pattern for CLI tools.

Flow:
1. Generate PKCE code_verifier + code_challenge pair
2. Build authorization URL → user opens in browser
3. X redirects to localhost callback with authorization code
4. Exchange code + code_verifier for access_token + refresh_token
5. Auto-refresh before token expiry (access tokens last 2 hours)

Token storage is in a local JSON file (data/x_api_tokens.json).
"""

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

# X API OAuth 2.0 endpoints
AUTHORIZE_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"

# Required scopes for bookmark reading
SCOPES = ["bookmark.read", "tweet.read", "users.read", "offline.access"]

# Token refresh buffer — refresh 5 minutes before expiry
REFRESH_BUFFER_SECONDS = 300


@dataclass
class TokenData:
    """Stored OAuth token data."""

    access_token: str
    refresh_token: str
    expires_at: float  # Unix timestamp when access_token expires
    scope: str = ""
    token_type: str = "bearer"

    @property
    def is_expired(self) -> bool:
        """Check if the access token is expired or about to expire."""
        return time.time() >= (self.expires_at - REFRESH_BUFFER_SECONDS)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "token_type": self.token_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TokenData":
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "bearer"),
        )


def generate_pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge pair.

    Returns:
        Tuple of (code_verifier, code_challenge) where:
        - code_verifier: 128-char random string (URL-safe base64)
        - code_challenge: SHA-256 hash of verifier (URL-safe base64, no padding)
    """
    # Generate 96 random bytes → 128 chars in base64
    code_verifier = secrets.token_urlsafe(96)

    # SHA-256 hash of the verifier
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    return code_verifier, code_challenge


class XApiAuth:
    """OAuth 2.0 PKCE authentication manager for X API.

    Handles the full auth lifecycle: authorization, token exchange,
    token storage, and auto-refresh.
    """

    def __init__(
        self,
        client_id: str,
        redirect_uri: str = "http://localhost:8766/oauth/callback",
        token_file: Optional[Path] = None,
    ):
        """Initialize X API auth manager.

        Args:
            client_id: X API OAuth 2.0 Client ID
            redirect_uri: Redirect URI registered in X Developer Portal
            token_file: Path for token persistence (default: data/x_api_tokens.json)
        """
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.token_file = token_file or Path("data/x_api_tokens.json")
        self._tokens: Optional[TokenData] = None
        self._pending_verifier: Optional[str] = None

    def get_authorization_url(self) -> tuple[str, str]:
        """Build the authorization URL for user to visit.

        Returns:
            Tuple of (authorization_url, state) where state should be
            verified in the callback for CSRF protection.
        """
        code_verifier, code_challenge = generate_pkce_pair()
        self._pending_verifier = code_verifier

        state = secrets.token_urlsafe(32)

        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        url = f"{AUTHORIZE_URL}?{urlencode(params)}"

        return url, state

    async def exchange_code(self, code: str) -> TokenData:
        """Exchange authorization code for access + refresh tokens.

        Args:
            code: Authorization code from the callback

        Returns:
            TokenData with fresh tokens

        Raises:
            RuntimeError: If no pending code_verifier (call get_authorization_url first)
            httpx.HTTPStatusError: On API error
        """
        if not self._pending_verifier:
            raise RuntimeError(
                "No pending code_verifier. Call get_authorization_url() first."
            )

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect_uri,
                    "client_id": self.client_id,
                    "code_verifier": self._pending_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()

        self._pending_verifier = None

        tokens = TokenData(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=time.time() + data.get("expires_in", 7200),
            scope=data.get("scope", ""),
            token_type=data.get("token_type", "bearer"),
        )

        self._tokens = tokens
        self._save_tokens()
        logger.info("Successfully obtained X API tokens")

        return tokens

    async def refresh_tokens(self) -> TokenData:
        """Refresh the access token using the refresh token.

        Returns:
            TokenData with refreshed tokens

        Raises:
            RuntimeError: If no tokens are loaded
            httpx.HTTPStatusError: On API error
        """
        tokens = self._load_tokens()
        if not tokens:
            raise RuntimeError("No tokens available. Run authorization flow first.")

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens.refresh_token,
                    "client_id": self.client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            data = response.json()

        new_tokens = TokenData(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token", tokens.refresh_token),
            expires_at=time.time() + data.get("expires_in", 7200),
            scope=data.get("scope", tokens.scope),
            token_type=data.get("token_type", "bearer"),
        )

        self._tokens = new_tokens
        self._save_tokens()
        logger.info("Successfully refreshed X API tokens")

        return new_tokens

    async def get_valid_token(self) -> str:
        """Get a valid access token, refreshing if needed.

        Returns:
            Valid access token string

        Raises:
            RuntimeError: If no tokens and can't refresh
        """
        tokens = self._load_tokens()
        if not tokens:
            raise RuntimeError("No tokens available. Run --authorize first.")

        if tokens.is_expired:
            logger.info("Access token expired, refreshing...")
            tokens = await self.refresh_tokens()

        return tokens.access_token

    def has_tokens(self) -> bool:
        """Check if valid tokens exist (loaded or on disk)."""
        return self._load_tokens() is not None

    def _load_tokens(self) -> Optional[TokenData]:
        """Load tokens from memory cache or file."""
        if self._tokens is not None:
            return self._tokens

        if not self.token_file.exists():
            return None

        try:
            with open(self.token_file, encoding="utf-8") as f:
                data = json.load(f)
            self._tokens = TokenData.from_dict(data)
            return self._tokens
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Failed to load tokens from %s: %s", self.token_file, e)
            return None

    def _save_tokens(self) -> None:
        """Persist tokens to file."""
        if self._tokens is None:
            return

        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_file, "w", encoding="utf-8") as f:
            json.dump(self._tokens.to_dict(), f, indent=2)

        logger.debug("Saved tokens to %s", self.token_file)
