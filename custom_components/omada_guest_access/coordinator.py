"""Coordinator and persistent request model for Omada Guest Access."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import logging
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_PENDING_TIMEOUT,
    CONF_RETENTION_DAYS,
    DEFAULT_DURATION_HOURS,
    DEFAULT_PENDING_TIMEOUT_MINUTES,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    EVENT_REQUEST_EXPIRED,
    STORAGE_KEY,
    STORAGE_VERSION,
)

_LOGGER = logging.getLogger(__name__)


class OmadaGuestAccessCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Own guest-access request state and later coordinate Omada API access."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),
        )
        self.entry = entry
        self.requests: dict[str, dict[str, Any]] = {}
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")

    async def async_load(self) -> None:
        """Load persisted requests before the first coordinator refresh."""
        stored = await self._store.async_load() or {}
        self.requests = {
            request_id: _deserialize_request(request)
            for request_id, request in stored.get("requests", {}).items()
        }

    async def _async_save(self) -> None:
        await self._store.async_save(
            {"requests": {request_id: _serialize_request(request) for request_id, request in self.requests.items()}}
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Return aggregate state; Omada client refresh lands here in a later release."""
        now = dt_util.utcnow()
        changed = False
        retention_cutoff = now - timedelta(days=self.entry.data.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
        removed = [request_id for request_id, request in self.requests.items() if request["updated_at"] < retention_cutoff]
        for request_id in removed:
            del self.requests[request_id]
            changed = True
        for request in self.requests.values():
            if request["status"] == "pending" and request["expires_at"] <= now:
                request["status"] = "expired"
                request["updated_at"] = now
                self.hass.bus.async_fire(EVENT_REQUEST_EXPIRED, dict(request))
                changed = True
            if request["status"] == "approved" and request.get("access_expires_at") <= now:
                request["status"] = "expired"
                request["updated_at"] = now
                changed = True
        if changed:
            await self._async_save()
        return {"requests": self.requests}

    async def async_create_request(
        self, guest_name: str, client_mac: str, note: str | None = None, access_point: str | None = None
    ) -> Mapping[str, Any]:
        """Create a request for a guest client identified by the portal."""
        now = dt_util.utcnow()
        timeout = self.entry.data.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT_MINUTES)
        normalized_mac = client_mac.strip().upper().replace("-", ":")
        for request in self.requests.values():
            if request["client_mac"] == normalized_mac and request["status"] == "pending":
                return request
        request_id = uuid4().hex
        request = {
            "request_id": request_id,
            "guest_name": guest_name.strip(),
            "client_mac": normalized_mac,
            "note": (note or "").strip() or None,
            "access_point": (access_point or "").strip() or None,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(minutes=timeout),
            "access_expires_at": None,
            "denial_reason": None,
        }
        self.requests[request_id] = request
        await self._async_save()
        await self.async_request_refresh()
        return request

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
        request["updated_at"] = dt_util.utcnow()
        await self._async_save()
        await self.async_request_refresh()
        return request

    async def async_deny_request(self, request_id: str, reason: str | None = None) -> Mapping[str, Any]:
        """Deny a pending request."""
        request = self._get_pending_request(request_id)
        request["status"] = "denied"
        request["denial_reason"] = reason
        request["updated_at"] = dt_util.utcnow()
        await self._async_save()
        await self.async_request_refresh()
        return request

    async def async_revoke_access(self, request_id: str) -> Mapping[str, Any]:
        """Revoke a previously approved request."""
        request = self.requests.get(request_id)
        if request is None:
            raise ValueError("Unknown guest access request")
        if request["status"] != "approved":
            raise ValueError(f"Request {request_id} is not approved")
        request["status"] = "revoked"
        request["updated_at"] = dt_util.utcnow()
        await self._async_save()
        await self.async_request_refresh()
        return request

    def _get_pending_request(self, request_id: str) -> dict[str, Any]:
        request = self.requests.get(request_id)
        if request is None:
            raise ValueError("Unknown guest access request")
        if request["status"] != "pending":
            raise ValueError(f"Request {request_id} is not pending")
        return request


def _serialize_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Convert Home Assistant datetime values to JSON-compatible strings."""
    return {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in request.items()}


def _deserialize_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Restore stored datetime strings."""
    result = dict(request)
    for key in ("created_at", "updated_at", "expires_at", "access_expires_at"):
        if isinstance(result.get(key), str):
            result[key] = dt_util.parse_datetime(result[key])
    return result

