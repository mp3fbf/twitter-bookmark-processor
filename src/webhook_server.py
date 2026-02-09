"""Webhook Server for Twitter Bookmark Processor.

HTTP server that receives bookmark URLs and triggers processing.
Designed to be called from iOS Shortcuts or other automation tools.

Endpoints:
    GET /health - Health check endpoint, returns {"status": "ok"}
    GET /metrics - Metrics endpoint with counters and uptime
    POST /process - Accepts JSON with URL to process, returns 202 Accepted
                   Requires Bearer token authentication when TWITTER_WEBHOOK_TOKEN is set.
                   Processing happens asynchronously in a background task.
                   Uses Pipeline to process bookmarks and sends notifications on completion.
"""

import asyncio
import hmac
import json
import logging
import os
import re
import time
import uuid
from html import escape
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aiohttp import web

from src.core.bookmark import Bookmark
from src.core.config import get_config
from src.core.notifier import notify_error, notify_processing, notify_success
from src.core.pipeline import Pipeline

logger = logging.getLogger(__name__)

@dataclass
class ServerMetrics:
    """Server metrics for monitoring.

    Tracks processing counts, errors, and uptime.
    All counters are thread-safe for use with asyncio.
    """

    start_time: float = field(default_factory=time.time)
    requests_total: int = 0
    processed_total: int = 0
    errors_total: int = 0

    def increment_requests(self) -> None:
        """Increment total requests counter."""
        self.requests_total += 1

    def increment_processed(self) -> None:
        """Increment successfully processed counter."""
        self.processed_total += 1

    def increment_errors(self) -> None:
        """Increment error counter."""
        self.errors_total += 1

    def get_uptime_seconds(self) -> float:
        """Get server uptime in seconds."""
        return time.time() - self.start_time

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary for JSON response."""
        return {
            "uptime_seconds": round(self.get_uptime_seconds(), 2),
            "requests_total": self.requests_total,
            "processed_total": self.processed_total,
            "errors_total": self.errors_total,
        }


# AppKey for storing background tasks (typed dict access)
BACKGROUND_TASKS_KEY = web.AppKey("background_tasks", dict[str, asyncio.Task])

# AppKey for storing the pipeline instance
PIPELINE_KEY = web.AppKey("pipeline", Pipeline)

# AppKey for storing server metrics
METRICS_KEY = web.AppKey("metrics", ServerMetrics)

# Regex pattern for Twitter/X status URLs
# Matches: twitter.com/user/status/123 or x.com/user/status/456
# Also supports mobile URLs (mobile.twitter.com) and variations
TWITTER_URL_PATTERN = re.compile(
    r"^https?://(?:(?:www|mobile)\.)?(?:twitter\.com|x\.com)"
    r"/[^/]+/status/(\d+)",
    re.IGNORECASE,
)


def validate_twitter_url(url: str) -> bool:
    """Check if URL is a valid Twitter/X status URL.

    Accepts URLs from twitter.com and x.com, including mobile and www subdomains.

    Args:
        url: The URL to validate.

    Returns:
        True if the URL is a valid Twitter/X status URL, False otherwise.
    """
    if not url:
        return False
    return TWITTER_URL_PATTERN.match(url) is not None


def extract_tweet_id(url: str) -> str | None:
    """Extract the tweet ID from a Twitter/X status URL.

    Args:
        url: A Twitter/X status URL.

    Returns:
        The tweet ID as a string, or None if the URL is invalid.
    """
    if not url:
        return None
    match = TWITTER_URL_PATTERN.match(url)
    if match:
        return match.group(1)
    return None


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
    return hmac.compare_digest(provided_token, token)


async def health_handler(request: web.Request) -> web.Response:
    """Health check endpoint.

    Returns:
        JSON response with status "ok" and 200 status code.
    """
    return web.json_response({"status": "ok"})


async def metrics_handler(request: web.Request) -> web.Response:
    """Metrics endpoint for monitoring.

    Returns:
        JSON response with server metrics including:
        - uptime_seconds: Server uptime in seconds
        - requests_total: Total number of /process requests received
        - processed_total: Number of successfully processed bookmarks
        - errors_total: Number of processing errors
    """
    metrics = request.app[METRICS_KEY]
    return web.json_response(metrics.to_dict())


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

    # Validate that URL is a Twitter/X status URL
    if not validate_twitter_url(url):
        return web.json_response(
            {"error": "Invalid URL - must be a Twitter/X status URL"},
            status=400,
        )

    # Extract tweet ID for tracking
    tweet_id = extract_tweet_id(url)

    # Increment requests counter
    request.app[METRICS_KEY].increment_requests()

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

    logger.info("Spawned background task %s for URL: %s (tweet_id=%s)", task_id, url, tweet_id)

    return web.json_response(
        {"status": "accepted", "url": url, "task_id": task_id, "tweet_id": tweet_id},
        status=202,
    )


def _create_bookmark_from_url(url: str, tweet_id: str) -> Bookmark:
    """Create a minimal Bookmark from a URL for webhook processing.

    Since we only have the URL (not full Twillot export data), we create
    a minimal Bookmark with just the essentials. The Pipeline and processors
    will fetch additional data as needed.

    Args:
        url: The Twitter/X URL.
        tweet_id: The extracted tweet ID.

    Returns:
        A Bookmark instance with minimal data.
    """
    return Bookmark(
        id=tweet_id,
        url=url,
        text="",  # Will be fetched by processor if needed
        author_username="",  # Will be extracted from URL or fetched
    )


async def _process_url_background(
    app: web.Application,
    task_id: str,
    url: str,
) -> None:
    """Process a URL in the background using the Pipeline.

    This function runs asynchronously after the HTTP response is sent.
    Uses the Pipeline to classify and process the bookmark, then sends
    notifications on completion or failure.

    Args:
        app: The aiohttp application instance.
        task_id: Unique identifier for this processing task.
        url: The Twitter/X URL to process.
    """
    tweet_id = extract_tweet_id(url)
    if not tweet_id:
        logger.error("Background task %s: invalid URL %s", task_id, url)
        return

    logger.info("Background task %s started for URL: %s (tweet_id=%s)", task_id, url, tweet_id)

    # Send processing notification
    notify_processing(tweet_id)

    try:
        # Get the pipeline from app state
        pipeline = app.get(PIPELINE_KEY)
        if pipeline is None:
            raise RuntimeError("Pipeline not initialized in app state")

        # Create bookmark from URL
        bookmark = _create_bookmark_from_url(url, tweet_id)

        # Process the bookmark
        output_path = await pipeline.process_bookmark(bookmark)

        if output_path:
            # Success - notify with content type and increment counter
            app[METRICS_KEY].increment_processed()
            content_type = bookmark.content_type.value.upper()
            notify_success(tweet_id, content_type)
            logger.info(
                "Background task %s completed successfully: %s -> %s",
                task_id,
                tweet_id,
                output_path,
            )
        else:
            # Skipped (already processed) or unsupported
            logger.info("Background task %s: bookmark %s was skipped", task_id, tweet_id)

    except Exception as e:
        # Log error, increment error counter, and send notification
        app[METRICS_KEY].increment_errors()
        error_msg = str(e)
        logger.error("Background task %s failed: %s", task_id, error_msg)
        notify_error(tweet_id, error_msg)


def _cleanup_task(app: web.Application, task_id: str) -> None:
    """Remove a completed task from tracking.

    Called as a done callback when the background task finishes.

    Args:
        app: The aiohttp application instance.
        task_id: The ID of the task to clean up.
    """
    app[BACKGROUND_TASKS_KEY].pop(task_id, None)
    logger.debug("Cleaned up task %s", task_id)


async def oauth_callback_handler(request: web.Request) -> web.Response:
    """Handle OAuth 2.0 callback from X API authorization.

    Captures the authorization code from the redirect and exchanges it
    for access + refresh tokens. This endpoint is used during the
    --authorize flow as an alternative to manual code pasting.

    Returns:
        HTML response indicating success or failure.
    """
    code = request.query.get("code")
    state = request.query.get("state")
    error = request.query.get("error")

    if error:
        return web.Response(
            text=f"<h1>Authorization Failed</h1><p>Error: {escape(error)}</p>",
            content_type="text/html",
        )

    if not code:
        return web.Response(
            text="<h1>Missing Code</h1><p>No authorization code received.</p>",
            content_type="text/html",
            status=400,
        )

    # Try to exchange the code for tokens
    try:
        from src.core.config import get_config
        from src.sources.x_api_auth import XApiAuth

        config = get_config(require_api_key=False)
        if not config.x_api_client_id:
            return web.Response(
                text="<h1>Error</h1><p>X_API_CLIENT_ID not configured.</p>",
                content_type="text/html",
                status=500,
            )

        auth = XApiAuth(
            client_id=config.x_api_client_id,
            token_file=config.x_api_token_file,
        )
        # Set the pending verifier from the authorization flow
        # Note: This only works if the auth flow was started in the same process
        tokens = await auth.exchange_code(code)

        return web.Response(
            text=(
                "<h1>Authorization Successful!</h1>"
                f"<p>Tokens saved. Access token expires at: {tokens.expires_at}</p>"
                "<p>You can close this window.</p>"
            ),
            content_type="text/html",
        )
    except Exception as e:
        logger.error("OAuth callback failed: %s", e)
        return web.Response(
            text=f"<h1>Authorization Failed</h1><p>Error: {escape(str(e))}</p>",
            content_type="text/html",
            status=500,
        )


def create_app(
    pipeline: Pipeline | None = None,
    output_dir: Path | None = None,
    state_file: Path | None = None,
) -> web.Application:
    """Create and configure the aiohttp application.

    Args:
        pipeline: Optional Pipeline instance. If not provided, creates one using
            output_dir and state_file (or defaults from config).
        output_dir: Directory for Obsidian notes output. Defaults to config value.
        state_file: Path to state JSON file. Defaults to config value.

    Returns:
        Configured aiohttp Application with all routes registered.
    """
    app = web.Application()

    # Initialize background task tracking
    app[BACKGROUND_TASKS_KEY] = {}

    # Initialize server metrics
    app[METRICS_KEY] = ServerMetrics()

    # Initialize pipeline
    if pipeline is not None:
        app[PIPELINE_KEY] = pipeline
    else:
        # Use provided paths or load from config
        x_api_auth = None
        try:
            config = get_config(require_api_key=False)
            output_dir = output_dir or config.output_dir
            state_file = state_file or config.state_file

            # Set up X API auth for thread processing if configured
            if config.x_api_client_id:
                from src.sources.x_api_auth import XApiAuth

                auth = XApiAuth(
                    client_id=config.x_api_client_id,
                    token_file=config.x_api_token_file,
                )
                if auth.has_tokens():
                    x_api_auth = auth
        except Exception:
            # Fallback defaults for testing without config
            output_dir = output_dir or Path("/workspace/notes/twitter/")
            state_file = state_file or Path("data/state.json")

        app[PIPELINE_KEY] = Pipeline(
            output_dir=output_dir,
            state_file=state_file,
            x_api_auth=x_api_auth,
        )

    app.router.add_get("/health", health_handler)
    app.router.add_get("/metrics", metrics_handler)
    app.router.add_post("/process", process_handler)
    app.router.add_get("/oauth/callback", oauth_callback_handler)
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
