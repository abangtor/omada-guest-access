"""Admin-only, paginated history via HA's authenticated WebSocket connection."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, REQUEST_STATUSES
from .coordinator import _serialize_request


@callback
def async_register_history(hass: HomeAssistant) -> None:
    """Registration is idempotent across entry reloads and multiple sites."""
    websocket_api.async_register_command(hass, async_history)


@websocket_api.websocket_command(
    {
        "type": f"{DOMAIN}/history",
        vol.Required("entry_id"): str,
        vol.Optional("status"): vol.In(REQUEST_STATUSES),
        vol.Optional("query", default=""): vol.All(str, vol.Length(max=120)),
        vol.Optional("offset", default=0): vol.All(int, vol.Range(min=0, max=1000000)),
        vol.Optional("limit", default=20): vol.All(int, vol.Range(min=1, max=50)),
    }
)
@websocket_api.require_admin
@callback
def async_history(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Return retained request records, never credentials or redirect/session context.

    This is a synchronous snapshot of published state: no controller I/O or awaits.
    Expiry follows the coordinator's independent 30-second timer.
    """
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], "not_found", "Guest access entry is not loaded")
        return
    coordinator = entry.runtime_data
    query = msg["query"].strip().casefold()
    rows = [
        item
        for item in coordinator.requests.values()
        if ("status" not in msg or item["status"] == msg["status"])
        and (
            not query
            or any(
                query in str(item.get(key, "")).casefold()
                for key in ("guest_name", "admin_label", "client_mac", "request_id")
            )
        )
    ]
    rows.sort(key=lambda item: (item["created_at"], item["request_id"]), reverse=True)
    offset, limit = msg["offset"], msg["limit"]
    fields = (
        "request_id",
        "guest_name",
        "admin_label",
        "client_mac",
        "note",
        "status",
        "created_at",
        "updated_at",
        "expires_at",
        "access_expires_at",
        "denial_reason",
        "decision_user_id",
        "terms_accepted_at",
        "terms_version",
        "terms_text",
    )
    connection.send_result(
        msg["id"],
        {
            "requests": [
                _serialize_request({key: item.get(key) for key in fields}) for item in rows[offset : offset + limit]
            ],
            "total": len(rows),
            "offset": offset,
            "limit": limit,
        },
    )
