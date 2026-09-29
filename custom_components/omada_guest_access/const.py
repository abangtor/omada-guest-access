"""Constants for Omada Guest Access."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "omada_guest_access"
PLATFORMS: Final = ["sensor", "binary_sensor"]

CONF_CONTROLLER_URL: Final = "controller_url"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_SITE: Final = "site"
CONF_PORTAL_URL: Final = "portal_url"
CONF_PORTAL_PORT: Final = "portal_port"
CONF_DEFAULT_DURATION: Final = "default_duration"
CONF_PENDING_TIMEOUT: Final = "pending_timeout"
CONF_RETENTION_DAYS: Final = "retention_days"

DEFAULT_PORTAL_PORT: Final = 8088
DEFAULT_DURATION_HOURS: Final = 8
DEFAULT_PENDING_TIMEOUT_MINUTES: Final = 15
DEFAULT_RETENTION_DAYS: Final = 30

SERVICE_APPROVE_REQUEST: Final = "approve_request"
SERVICE_DENY_REQUEST: Final = "deny_request"
SERVICE_REVOKE_ACCESS: Final = "revoke_access"

EVENT_REQUEST_CREATED: Final = f"{DOMAIN}_request_created"
EVENT_REQUEST_APPROVED: Final = f"{DOMAIN}_request_approved"
EVENT_REQUEST_DENIED: Final = f"{DOMAIN}_request_denied"
EVENT_ACCESS_REVOKED: Final = f"{DOMAIN}_access_revoked"
EVENT_REQUEST_EXPIRED: Final = f"{DOMAIN}_request_expired"

REQUEST_STATUSES: Final = frozenset({"pending", "approved", "denied", "expired", "revoked"})
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.requests"

