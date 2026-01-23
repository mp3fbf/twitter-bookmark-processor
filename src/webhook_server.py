"""Webhook Server for Twitter Bookmark Processor.

HTTP server that receives bookmark URLs and triggers processing.
Designed to be called from iOS Shortcuts or other automation tools.

Endpoints:
    GET /health - Health check endpoint, returns {"status": "ok"}
    POST /process - Accepts JSON with URL to process, returns 202 Accepted
"""

import json
from typing import Any

from aiohttp import web


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint.

    Returns:
        JSON response with status "ok" and 200 status code.
    """
    return web.json_response({"status": "ok"})


async def process_handler(request: web.Request) -> web.Response:
    """Process a bookmark URL.

    Expects JSON body with "url" field containing the Twitter/X URL to process.
    Returns 202 Accepted immediately; actual processing happens asynchronously.

    Args:
        request: The incoming HTTP request.

    Returns:
        202 Accepted if request is valid.
        400 Bad Request if JSON is invalid or missing required fields.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response(
            {"error": "Invalid JSON body"},
            status=400,
        )

    if not isinstance(body, dict):
        return web.json_response(
            {"error": "Request body must be a JSON object"},
            status=400,
        )

    url = body.get("url")
    if not url:
        return web.json_response(
            {"error": "Missing required field: url"},
            status=400,
        )

    # Return 202 Accepted - processing happens asynchronously (Issue #41)
    return web.json_response(
        {"status": "accepted", "url": url},
        status=202,
    )


def create_app() -> web.Application:
    """Create and configure the aiohttp application.

    Returns:
        Configured aiohttp Application with all routes registered.
    """
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_post("/process", process_handler)
    return app


async def run_server(host: str = "0.0.0.0", port: int = 8766) -> web.AppRunner:
    """Start the webhook server.

    Args:
        host: Host to bind to (default: 0.0.0.0).
        port: Port to listen on (default: 8766).

    Returns:
        The AppRunner instance (for testing/cleanup).
    """
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    return runner


def get_server_info() -> dict[str, Any]:
    """Get server configuration info.

    Returns:
        Dictionary with default host and port.
    """
    return {
        "default_host": "0.0.0.0",
        "default_port": 8766,
    }
