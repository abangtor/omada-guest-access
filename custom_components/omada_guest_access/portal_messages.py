"""Guest-facing text, deliberately independent of internal error details."""

from typing import Any

# Empty strings intentionally suppress messages; never use truthiness fallbacks.
PORTAL_MESSAGES = {
    "portal_error_message": "Unable to complete your request. Please reconnect or contact your host.",
    "portal_session_error_message": "Your session is unavailable. Please reconnect to Guest Wi-Fi.",
    "portal_submit_error_message": "Unable to send your request. Please reconnect and try again.",
    "portal_denied_message": "Access was denied.",
    "portal_expired_message": "This request has expired.",
    "portal_revoked_message": "Your internet access has been cancelled.",
}


def portal_messages(config: dict[str, Any]) -> dict[str, str]:
    """Resolve defaults for existing entries while preserving explicit blanks."""
    return {key: config.get(key, default) for key, default in PORTAL_MESSAGES.items()}
