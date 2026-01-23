"""Tests for Webhook Server."""

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from src.webhook_server import create_app, get_server_info


class TestHealthEndpoint(AioHTTPTestCase):
    """Test the /health endpoint."""

    async def get_application(self) -> web.Application:
        """Return the application for testing."""
        return create_app()

    async def test_health_endpoint_returns_200(self):
        """GET /health should return 200 OK."""
        resp = await self.client.get("/health")
        assert resp.status == 200

    async def test_health_endpoint_returns_json(self):
        """GET /health should return valid JSON."""
        resp = await self.client.get("/health")
        data = await resp.json()
        assert isinstance(data, dict)

    async def test_health_endpoint_returns_status_ok(self):
        """GET /health should return {"status": "ok"}."""
        resp = await self.client.get("/health")
        data = await resp.json()
        assert data == {"status": "ok"}


class TestProcessEndpoint(AioHTTPTestCase):
    """Test the /process endpoint."""

    async def get_application(self) -> web.Application:
        """Return the application for testing."""
        return create_app()

    async def test_process_requires_post(self):
        """GET /process should return 405 Method Not Allowed (only POST allowed)."""
        resp = await self.client.get("/process")
        # aiohttp returns 405 when the route exists but method is wrong
        assert resp.status == 405

    async def test_process_accepts_json(self):
        """POST /process with valid JSON body should be accepted."""
        resp = await self.client.post(
            "/process",
            json={"url": "https://twitter.com/user/status/123"},
        )
        assert resp.status == 202

    async def test_process_returns_202(self):
        """POST /process should return 202 Accepted for valid requests."""
        resp = await self.client.post(
            "/process",
            json={"url": "https://x.com/user/status/456"},
        )
        assert resp.status == 202
        data = await resp.json()
        assert data["status"] == "accepted"

    async def test_process_returns_url_in_response(self):
        """POST /process should echo the URL in response."""
        url = "https://twitter.com/elonmusk/status/789"
        resp = await self.client.post("/process", json={"url": url})
        data = await resp.json()
        assert data["url"] == url

    async def test_process_requires_json_body(self):
        """POST /process without JSON body should return 400."""
        resp = await self.client.post(
            "/process",
            data="not json",
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "error" in data

    async def test_process_requires_url_field(self):
        """POST /process without 'url' field should return 400."""
        resp = await self.client.post("/process", json={"foo": "bar"})
        assert resp.status == 400
        data = await resp.json()
        assert "url" in data["error"].lower()

    async def test_process_rejects_empty_url(self):
        """POST /process with empty url should return 400."""
        resp = await self.client.post("/process", json={"url": ""})
        assert resp.status == 400

    async def test_process_rejects_non_object_json(self):
        """POST /process with array instead of object should return 400."""
        resp = await self.client.post("/process", json=["url", "value"])
        assert resp.status == 400
        data = await resp.json()
        assert "object" in data["error"].lower()


class TestServerConfiguration:
    """Test server configuration utilities."""

    def test_get_server_info_returns_defaults(self):
        """get_server_info should return default host and port."""
        info = get_server_info()
        assert info["default_host"] == "0.0.0.0"
        assert info["default_port"] == 8766

    def test_create_app_has_health_route(self):
        """Application should have /health route registered."""
        app = create_app()
        routes = [r.resource.canonical for r in app.router.routes()]
        assert "/health" in routes

    def test_create_app_has_process_route(self):
        """Application should have /process route registered."""
        app = create_app()
        routes = [r.resource.canonical for r in app.router.routes()]
        assert "/process" in routes
