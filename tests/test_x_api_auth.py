"""Tests for X API OAuth 2.0 PKCE authentication."""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.sources.x_api_auth import (
    REFRESH_BUFFER_SECONDS,
    SCOPES,
    TokenData,
    XApiAuth,
    generate_pkce_pair,
)


class TestPKCEGeneration:
    """Tests for PKCE code_verifier/code_challenge generation."""

    def test_generates_verifier_and_challenge(self):
        verifier, challenge = generate_pkce_pair()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 40
        assert len(challenge) > 20

    def test_verifier_is_url_safe(self):
        verifier, _ = generate_pkce_pair()
        # URL-safe base64 only uses alphanumeric, -, _
        assert all(c.isalnum() or c in "-_" for c in verifier)

    def test_challenge_is_url_safe(self):
        _, challenge = generate_pkce_pair()
        assert all(c.isalnum() or c in "-_" for c in challenge)

    def test_challenge_has_no_padding(self):
        _, challenge = generate_pkce_pair()
        assert "=" not in challenge

    def test_different_pairs_each_call(self):
        pair1 = generate_pkce_pair()
        pair2 = generate_pkce_pair()
        assert pair1[0] != pair2[0]
        assert pair1[1] != pair2[1]


class TestTokenData:
    """Tests for TokenData dataclass."""

    def test_not_expired_when_fresh(self):
        token = TokenData(
            access_token="abc",
            refresh_token="xyz",
            expires_at=time.time() + 3600,
        )
        assert token.is_expired is False

    def test_expired_when_past(self):
        token = TokenData(
            access_token="abc",
            refresh_token="xyz",
            expires_at=time.time() - 100,
        )
        assert token.is_expired is True

    def test_expired_within_buffer(self):
        """Token should be considered expired if within the refresh buffer."""
        token = TokenData(
            access_token="abc",
            refresh_token="xyz",
            expires_at=time.time() + (REFRESH_BUFFER_SECONDS - 10),
        )
        assert token.is_expired is True

    def test_to_dict(self):
        token = TokenData(
            access_token="abc",
            refresh_token="xyz",
            expires_at=1000.0,
            scope="read write",
        )
        d = token.to_dict()
        assert d["access_token"] == "abc"
        assert d["refresh_token"] == "xyz"
        assert d["expires_at"] == 1000.0
        assert d["scope"] == "read write"

    def test_from_dict(self):
        d = {
            "access_token": "abc",
            "refresh_token": "xyz",
            "expires_at": 1000.0,
            "scope": "read write",
        }
        token = TokenData.from_dict(d)
        assert token.access_token == "abc"
        assert token.refresh_token == "xyz"
        assert token.expires_at == 1000.0

    def test_roundtrip(self):
        original = TokenData(
            access_token="abc",
            refresh_token="xyz",
            expires_at=2000.0,
            scope="a b",
            token_type="bearer",
        )
        restored = TokenData.from_dict(original.to_dict())
        assert restored.access_token == original.access_token
        assert restored.refresh_token == original.refresh_token
        assert restored.expires_at == original.expires_at


class TestXApiAuthInit:
    """Tests for XApiAuth initialization."""

    def test_sets_client_id(self):
        auth = XApiAuth(client_id="test123")
        assert auth.client_id == "test123"

    def test_default_redirect_uri(self):
        auth = XApiAuth(client_id="test")
        assert "localhost" in auth.redirect_uri

    def test_custom_token_file(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        auth = XApiAuth(client_id="test", token_file=token_file)
        assert auth.token_file == token_file


class TestGetAuthorizationUrl:
    """Tests for authorization URL generation."""

    def test_returns_url_and_state(self):
        auth = XApiAuth(client_id="test123")
        url, state = auth.get_authorization_url()
        assert "twitter.com" in url
        assert "test123" in url
        assert len(state) > 10

    def test_url_contains_scopes(self):
        auth = XApiAuth(client_id="test")
        url, _ = auth.get_authorization_url()
        for scope in SCOPES:
            assert scope in url

    def test_url_contains_pkce_challenge(self):
        auth = XApiAuth(client_id="test")
        url, _ = auth.get_authorization_url()
        assert "code_challenge=" in url
        assert "code_challenge_method=S256" in url

    def test_stores_pending_verifier(self):
        auth = XApiAuth(client_id="test")
        assert auth._pending_verifier is None
        auth.get_authorization_url()
        assert auth._pending_verifier is not None


class TestExchangeCode:
    """Tests for authorization code exchange."""

    @pytest.mark.asyncio
    async def test_raises_without_verifier(self):
        auth = XApiAuth(client_id="test")
        with pytest.raises(RuntimeError, match="No pending code_verifier"):
            await auth.exchange_code("some-code")

    @pytest.mark.asyncio
    async def test_exchanges_code_for_tokens(self, tmp_path):
        auth = XApiAuth(
            client_id="test",
            token_file=tmp_path / "tokens.json",
        )
        auth.get_authorization_url()  # Sets _pending_verifier

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_in": 7200,
            "scope": "bookmark.read",
            "token_type": "bearer",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("src.sources.x_api_auth.httpx.AsyncClient", return_value=mock_client):
            tokens = await auth.exchange_code("auth-code")

        assert tokens.access_token == "new_access"
        assert tokens.refresh_token == "new_refresh"
        assert auth._pending_verifier is None  # Cleared after exchange

    @pytest.mark.asyncio
    async def test_saves_tokens_to_file(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        auth = XApiAuth(client_id="test", token_file=token_file)
        auth.get_authorization_url()

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "a",
            "refresh_token": "r",
            "expires_in": 7200,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("src.sources.x_api_auth.httpx.AsyncClient", return_value=mock_client):
            await auth.exchange_code("code")

        assert token_file.exists()
        saved = json.loads(token_file.read_text())
        assert saved["access_token"] == "a"
        assert saved["refresh_token"] == "r"


class TestRefreshTokens:
    """Tests for token refresh."""

    @pytest.mark.asyncio
    async def test_raises_without_tokens(self, tmp_path):
        auth = XApiAuth(
            client_id="test",
            token_file=tmp_path / "missing.json",
        )
        with pytest.raises(RuntimeError, match="No tokens available"):
            await auth.refresh_tokens()

    @pytest.mark.asyncio
    async def test_refreshes_with_stored_tokens(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({
            "access_token": "old_access",
            "refresh_token": "old_refresh",
            "expires_at": time.time() - 100,  # Expired
        }))

        auth = XApiAuth(client_id="test", token_file=token_file)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_in": 7200,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("src.sources.x_api_auth.httpx.AsyncClient", return_value=mock_client):
            tokens = await auth.refresh_tokens()

        assert tokens.access_token == "new_access"
        assert tokens.refresh_token == "new_refresh"


class TestGetValidToken:
    """Tests for get_valid_token."""

    @pytest.mark.asyncio
    async def test_returns_token_when_fresh(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({
            "access_token": "fresh_token",
            "refresh_token": "refresh",
            "expires_at": time.time() + 3600,
        }))

        auth = XApiAuth(client_id="test", token_file=token_file)
        token = await auth.get_valid_token()
        assert token == "fresh_token"

    @pytest.mark.asyncio
    async def test_refreshes_when_expired(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({
            "access_token": "old",
            "refresh_token": "refresh",
            "expires_at": time.time() - 100,
        }))

        auth = XApiAuth(client_id="test", token_file=token_file)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "refreshed",
            "refresh_token": "new_refresh",
            "expires_in": 7200,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post.return_value = mock_response

        with patch("src.sources.x_api_auth.httpx.AsyncClient", return_value=mock_client):
            token = await auth.get_valid_token()

        assert token == "refreshed"

    @pytest.mark.asyncio
    async def test_raises_when_no_tokens(self, tmp_path):
        auth = XApiAuth(
            client_id="test",
            token_file=tmp_path / "missing.json",
        )
        with pytest.raises(RuntimeError, match="No tokens available"):
            await auth.get_valid_token()


class TestHasTokens:
    """Tests for has_tokens."""

    def test_false_when_no_file(self, tmp_path):
        auth = XApiAuth(
            client_id="test",
            token_file=tmp_path / "missing.json",
        )
        assert auth.has_tokens() is False

    def test_true_when_file_exists(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({
            "access_token": "a",
            "refresh_token": "r",
            "expires_at": time.time() + 3600,
        }))
        auth = XApiAuth(client_id="test", token_file=token_file)
        assert auth.has_tokens() is True

    def test_false_when_file_corrupt(self, tmp_path):
        token_file = tmp_path / "tokens.json"
        token_file.write_text("not json")
        auth = XApiAuth(client_id="test", token_file=token_file)
        assert auth.has_tokens() is False
