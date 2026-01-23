"""Notification module for Twitter Bookmark Processor.

Sends notifications via external notification command (Telegram).
Gracefully degrades when notification command is not available.
"""

import logging
import os
import subprocess
from typing import Literal

logger = logging.getLogger(__name__)

# Default path to notification script (Mac Mini)
DEFAULT_NOTIFY_CMD = os.path.expanduser("~/projects/_scripts/notify")

# Notification types supported by the notify script
NotifyType = Literal["info", "done", "error", "wait"]


def get_notify_command() -> str:
    """Get path to notification command.

    Uses NOTIFY_CMD environment variable if set, otherwise defaults to
    ~/projects/_scripts/notify.

    Returns:
        Path to the notification command.
    """
    return os.environ.get("NOTIFY_CMD", DEFAULT_NOTIFY_CMD)


def notify(message: str, msg_type: NotifyType = "info") -> bool:
    """Send notification via external command.

    Uses the notify script to send Telegram notifications.
    Fails silently if the command doesn't exist or fails.

    Args:
        message: The message to send.
        msg_type: Type of notification - "info", "done", "error", or "wait".
            Affects icon/formatting in Telegram.

    Returns:
        True if notification was sent successfully, False otherwise.
    """
    cmd_path = get_notify_command()

    # Check if command exists
    if not os.path.exists(cmd_path):
        logger.debug("Notify command not found at %s, skipping notification", cmd_path)
        return False

    try:
        result = subprocess.run(
            [cmd_path, message, msg_type],
            timeout=10,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            logger.debug("Notification sent: %s", message[:50])
            return True
        else:
            logger.warning(
                "Notify command failed with code %d: %s",
                result.returncode,
                result.stderr[:100] if result.stderr else "no output",
            )
            return False

    except subprocess.TimeoutExpired:
        logger.warning("Notify command timed out after 10s")
        return False
    except Exception as e:
        logger.warning("Failed to send notification: %s", e)
        return False


def notify_success(tweet_id: str, content_type: str) -> bool:
    """Send success notification for processed bookmark.

    Args:
        tweet_id: The ID of the processed tweet.
        content_type: Type of content (TWEET, VIDEO, THREAD, LINK).

    Returns:
        True if notification was sent successfully.
    """
    message = f"Processed {content_type}: {tweet_id}"
    return notify(message, "done")


def notify_error(tweet_id: str, error: str) -> bool:
    """Send error notification for failed processing.

    Args:
        tweet_id: The ID of the failed tweet.
        error: Description of the error (truncated to 100 chars).

    Returns:
        True if notification was sent successfully.
    """
    # Truncate error message for notification
    error_short = error[:100] + "..." if len(error) > 100 else error
    message = f"Failed {tweet_id}: {error_short}"
    return notify(message, "error")


def notify_processing(tweet_id: str) -> bool:
    """Send notification that processing has started.

    Args:
        tweet_id: The ID of the tweet being processed.

    Returns:
        True if notification was sent successfully.
    """
    message = f"Processing bookmark: {tweet_id}"
    return notify(message, "wait")
