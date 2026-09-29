"""Coordinator and in-memory request model for Omada Guest Access."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CONF_DEFAULT_DURATION, DEFAULT_DURATION_HOURS, DOMAIN


class OmadaGuestAccessCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Own guest-access request state and later coordinate Omada API access."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self.entry = entry
        self.requests: dict[str, dict[str, Any]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        """Return aggregate state; Omada client refresh lands here in a later release."""
        now = dt_util.utcnow()
        for request in self.requests.values():
            if request["status"] == "pending" and request["expires_at"] <= now:
                request["status"] = "expired"
        return {"requests": self.requests}

    async def async_approve_request(
        self, request_id: str, duration_hours: int | None = None
    ) -> Mapping[str, Any]:
        """Approve a pending request and reserve the authorization hook."""
        request = self._get_pending_request(request_id)
        duration = duration_hours or self.entry.data.get(
            CONF_DEFAULT_DURATION, DEFAULT_DURATION_HOURS
        )
        request["status"] = "approved"
        request["access_expires_at"] = dt_util.utcnow() + timedelta(hours=duration)
        # TODO: Call the Omada controller adapter to authorize the client MAC.
        await self.async_request_refresh()
        return request

    async def async_deny_request(self, request_id: str, reason: str | None = None) -> Mapping[str, Any]:
        """Deny a pending request."""
        request = self._get_pending_request(request_id)
        request["status"] = "denied"
        request["denial_reason"] = reason
        await self.async_request_refresh()
        return request

    async def async_revoke_access(self, request_id: str) -> Mapping[str, Any]:
        """Revoke a previously approved request."""
        request = self.requests[request_id]
        request["status"] = "revoked"
        # TODO: Call the Omada controller adapter to deauthorize the client MAC.
        await self.async_request_refresh()
        return request

    def _get_pending_request(self, request_id: str) -> dict[str, Any]:
        request = self.requests[request_id]
        if request["status"] != "pending":
            raise ValueError(f"Request {request_id} is not pending")
        return request

