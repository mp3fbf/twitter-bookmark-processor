"""Webhook Server for Twitter Bookmark Processor.

HTTP server that receives bookmark URLs and triggers processing.
Designed to be called from iOS Shortcuts or other automation tools.

Endpoints:
    GET /health - Health check endpoint, returns {"status": "ok"}
    POST /process - Accepts JSON with URL to process, returns 202 Accepted
                   Requires Bearer token authentication when TWITTER_WEBHOOK_TOKEN is set.
                   Processing happens asynchronously in a background task.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)

# AppKey for storing background tasks (typed dict access)
BACKGROUND_TASKS_KEY = web.AppKey("background_tasks", dict[str, asyncio.Task])


def get_auth_token() -> str | None:
    """Get the configured authentication token from environment.

    Returns:
        The token string if set, None otherwise (auth disabled in dev).
    """
    return os.environ.get("TWITTER_WEBHOOK_TOKEN")


def check_auth(request: web.Request) -> bool:
    """Check if the request has valid authentication.

    If TWITTER_WEBHOOK_TOKEN is not set, authentication is disabled (dev mode).
    If set, the request must include a valid Authorization header.

    Args:
        request: The incoming HTTP request.

    Returns:
        True if authentication is valid or disabled, False otherwise.
    """
    token = get_auth_token()

    # No token configured = auth disabled (dev mode)
    if not token:
        return True

    auth_header = request.headers.get("Authorization", "")

    # Expect "Bearer <token>"
    if not auth_header.startswith("Bearer "):
        return False

    provided_token = auth_header[7:]  # Remove "Bearer " prefix
    return provided_token == token


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

    When TWITTER_WEBHOOK_TOKEN is set, requires Authorization: Bearer <token> header.

    Args:
        request: The incoming HTTP request.

    Returns:
        202 Accepted if request is valid and authenticated.
        400 Bad Request if JSON is invalid or missing required fields.
        401 Unauthorized if authentication fails.
    """
    # Check authentication first
    if not check_auth(request):
        return web.json_response(
            {"error": "Unauthorized - invalid or missing token"},
            status=401,
        )

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

    # Generate a unique task ID for tracking
    task_id = str(uuid.uuid4())[:8]

    # Spawn background processing task
    task = asyncio.create_task(
        _process_url_background(request.app, task_id, url),
        name=f"process-{task_id}",
    )

    # Track the task in app state
    request.app[BACKGROUND_TASKS_KEY][task_id] = task

    # Clean up completed task when done
    task.add_done_callback(
        lambda t: _cleanup_task(request.app, task_id)
    )

    logger.info("Spawned background task %s for URL: %s", task_id, url)

    return web.json_response(
        {"status": "accepted", "url": url, "task_id": task_id},
        status=202,
    )


async def _process_url_background(
    app: web.Application,
    task_id: str,
    url: str,
) -> None:
    """Process a URL in the background.

    This function runs asynchronously after the HTTP response is sent.
    Actual pipeline integration will be added in Issue #44.

    Args:
        app: The aiohttp application instance.
        task_id: Unique identifier for this processing task.
        url: The Twitter/X URL to process.
    """
    logger.info("Background task %s started for URL: %s", task_id, url)
    try:
        # TODO: Issue #44 will integrate with Pipeline
        # For now, just log that processing would happen here
        logger.info("Background task %s completed successfully", task_id)
    except Exception as e:
        # Log error but don't re-raise - task is already detached
        logger.error("Background task %s failed: %s", task_id, e)


def _cleanup_task(app: web.Application, task_id: str) -> None:
    """Remove a completed task from tracking.

    Called as a done callback when the background task finishes.

    Args:
        app: The aiohttp application instance.
        task_id: The ID of the task to clean up.
    """
    app[BACKGROUND_TASKS_KEY].pop(task_id, None)
    logger.debug("Cleaned up task %s", task_id)


def create_app() -> web.Application:
    """Create and configure the aiohttp application.

    Returns:
        Configured aiohttp Application with all routes registered.
    """
    app = web.Application()

    # Initialize background task tracking
    app[BACKGROUND_TASKS_KEY] = {}

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
