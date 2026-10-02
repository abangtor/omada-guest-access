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
CONF_CONTROLLER_ID: Final = "controller_id"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_DEFAULT_DURATION: Final = "default_duration"
CONF_PENDING_TIMEOUT: Final = "pending_timeout"
CONF_RETENTION_DAYS: Final = "retention_days"

DEFAULT_PORTAL_PORT: Final = 8088
DEFAULT_VERIFY_SSL: Final = True
DEFAULT_DURATION_HOURS: Final = 8
DEFAULT_PENDING_TIMEOUT_MINUTES: Final = 15
DEFAULT_RETENTION_DAYS: Final = 30
# Forever remains a finite 3650-day duration; zero expires immediately.
# Controller acceptance of such long grants still needs live verification.
FOREVER_DURATION_DAYS: Final = 3650

SERVICE_APPROVE_REQUEST: Final = "approve_request"
SERVICE_DENY_REQUEST: Final = "deny_request"
SERVICE_REVOKE_ACCESS: Final = "revoke_access"
SERVICE_SET_GUEST_LABEL: Final = "set_guest_label"

EVENT_REQUEST_CREATED: Final = f"{DOMAIN}_request_created"
EVENT_REQUEST_APPROVED: Final = f"{DOMAIN}_request_approved"
EVENT_REQUEST_DENIED: Final = f"{DOMAIN}_request_denied"
EVENT_ACCESS_REVOKED: Final = f"{DOMAIN}_access_revoked"
EVENT_REQUEST_EXPIRED: Final = f"{DOMAIN}_request_expired"
EVENT_OMADA_API_ERROR: Final = f"{DOMAIN}_omada_api_error"

REQUEST_STATUSES: Final = frozenset({"pending", "approved", "denied", "expired", "revoked"})
STORAGE_VERSION: Final = 1
STORAGE_KEY: Final = f"{DOMAIN}.requests"

PORTAL_SESSION_TTL_SECONDS: Final = 20 * 60
PORTAL_RATE_LIMIT: Final = 10
PORTAL_RATE_WINDOW_SECONDS: Final = 10 * 60

CONF_TRUSTED_PROXIES: Final = "trusted_proxies"
CONF_ALLOWED_NETWORKS: Final = "allowed_networks"
PORTAL_MAX_SESSIONS: Final = 2000
PORTAL_MAX_PENDING: Final = 500

CONF_PORTAL_TITLE: Final = "portal_title"
CONF_PORTAL_MESSAGE: Final = "portal_message"
CONF_PORTAL_ACCENT: Final = "portal_accent"
CONF_TERMS_TEXT: Final = "terms_text"
CONF_REQUIRE_TERMS: Final = "require_terms"
DEFAULT_PORTAL_TITLE: Final = "Guest Wi-Fi"
DEFAULT_PORTAL_MESSAGE: Final = "Request internet access from your host."
DEFAULT_PORTAL_ACCENT: Final = "#1769aa"

CONF_DURATION_OPTIONS: Final = "duration_options"
DEFAULT_DURATION_OPTIONS: Final = "1,2,4,8,12,24,48,72,168,0"
CONF_ENABLE_REVOKE: Final = "enable_revoke"
CONF_PORTAL_CSS: Final = "portal_css"
CONF_PORTAL_TEMPLATE: Final = "portal_template"
CONF_SHOW_REMEMBER_CHECKBOX: Final = "show_remember_checkbox"
CONF_SHOW_FORGET_BUTTON: Final = "show_forget_button"
CONF_FORGET_ALL_RECORDS: Final = "forget_all_records"
CONF_DECISION_USER_IDS: Final = "decision_user_ids"
