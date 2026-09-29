"""Coordinator, request storage and Omada authorization lifecycle."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_PENDING_TIMEOUT,
    CONF_RETENTION_DAYS,
    DEFAULT_DURATION_HOURS,
    DEFAULT_PENDING_TIMEOUT_MINUTES,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    EVENT_OMADA_API_ERROR,
    EVENT_REQUEST_EXPIRED,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .omada_client import OmadaApiError, OmadaExternalPortalClient, PortalContext

_LOGGER = logging.getLogger(__name__)


class OmadaGuestAccessCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Own persistent requests and synchronize their access state with Omada."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, logger=_LOGGER, name=DOMAIN, update_interval=timedelta(seconds=30))
        self.entry = entry
        self.requests: dict[str, dict[str, Any]] = {}
        self.config = dict(entry.data) | dict(entry.options)
        self.client = OmadaExternalPortalClient(hass, self.config)
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")

    async def async_load(self) -> None:
        """Load persisted request history before the first refresh."""
        stored = await self._store.async_load() or {}
        self.requests = {key: _deserialize_request(value) for key, value in stored.get("requests", {}).items()}

    async def _async_save(self) -> None:
        await self._store.async_save(
            {"requests": {key: _serialize_request(value) for key, value in self.requests.items()}}
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Verify controller availability and apply local request expiry/retention."""
        try:
            await self.client.async_test_connection()
        except OmadaApiError as err:
            self.hass.bus.async_fire(EVENT_OMADA_API_ERROR, {"entry_id": self.entry.entry_id, "error": str(err)})
            raise UpdateFailed(str(err)) from err

        now = dt_util.utcnow()
        changed = False
        cutoff = now - timedelta(days=self.config.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
        for request_id, request in list(self.requests.items()):
            if request["updated_at"] < cutoff:
                del self.requests[request_id]
                changed = True
                continue
            if request["status"] == "pending" and request["expires_at"] <= now:
                request["status"] = "expired"
                request["updated_at"] = now
                self.hass.bus.async_fire(EVENT_REQUEST_EXPIRED, event_data(request))
                changed = True
            elif request["status"] == "approved" and request["access_expires_at"] <= now:
                request["status"] = "expired"
                request["updated_at"] = now
                self.hass.bus.async_fire(EVENT_REQUEST_EXPIRED, event_data(request))
                changed = True
        if changed:
            await self._async_save()
        return {"requests": self.requests, "controller_available": True}

    async def async_create_request(
        self, guest_name: str, context: PortalContext, note: str | None = None
    ) -> Mapping[str, Any]:
        """Create or return an active request bound to an Omada redirect context."""
        now = dt_util.utcnow()
        for request in self.requests.values():
            if request["client_mac"] == context.client_mac and request["status"] == "pending":
                return request
        request_id = uuid4().hex
        timeout = self.config.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT_MINUTES)
        request = {
            "request_id": request_id,
            "guest_name": guest_name.strip(),
            "client_mac": context.client_mac,
            "note": (note or "").strip() or None,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "expires_at": now + timedelta(minutes=timeout),
            "access_expires_at": None,
            "denial_reason": None,
            "portal_context": _serialize_context(context),
        }
        self.requests[request_id] = request
        await self._async_save()
        await self.async_request_refresh()
        return request

    async def async_approve_request(self, request_id: str, duration_hours: int | None = None) -> Mapping[str, Any]:
        """Authorize in Omada first, then make the approval visible to the guest."""
        request = self._get_pending_request(request_id)
        duration = duration_hours or self.config.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_HOURS)
        try:
            await self.client.async_authorize(_deserialize_context(request["portal_context"]), duration)
        except OmadaApiError as err:
            self.hass.bus.async_fire(EVENT_OMADA_API_ERROR, {"request_id": request_id, "error": str(err)})
            raise ValueError(f"Omada authorization failed: {err}") from err
        now = dt_util.utcnow()
        request.update(status="approved", access_expires_at=now + timedelta(hours=duration), updated_at=now)
        await self._async_save()
        await self.async_request_refresh()
        return request

    async def async_deny_request(self, request_id: str, reason: str | None = None) -> Mapping[str, Any]:
        """Deny a pending request without contacting the controller."""
        request = self._get_pending_request(request_id)
        request.update(status="denied", denial_reason=(reason or "").strip() or None, updated_at=dt_util.utcnow())
        await self._async_save()
        await self.async_request_refresh()
        return request

    async def async_revoke_access(self, request_id: str) -> Mapping[str, Any]:
        """Revoke an active controller grant, retaining an auditable request record."""
        request = self.requests.get(request_id)
        if request is None:
            raise ValueError("Unknown guest access request")
        if request["status"] != "approved":
            raise ValueError(f"Request {request_id} is not approved")
        try:
            await self.client.async_revoke(_deserialize_context(request["portal_context"]))
        except OmadaApiError as err:
            self.hass.bus.async_fire(EVENT_OMADA_API_ERROR, {"request_id": request_id, "error": str(err)})
            raise ValueError(f"Omada revoke failed: {err}") from err
        request.update(status="revoked", updated_at=dt_util.utcnow())
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


def _serialize_context(context: PortalContext) -> dict[str, str | None]:
    return {
        "client_mac": context.client_mac,
        "site": context.site,
        "ap_mac": context.ap_mac,
        "gateway_mac": context.gateway_mac,
        "ssid_name": context.ssid_name,
        "radio_id": context.radio_id,
        "vlan_id": context.vlan_id,
        "redirect_url": context.redirect_url,
    }


def _deserialize_context(value: Mapping[str, Any]) -> PortalContext:
    return PortalContext(**dict(value))


def _serialize_request(request: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in request.items()}


def _deserialize_request(request: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(request)
    for key in ("created_at", "updated_at", "expires_at", "access_expires_at"):
        if isinstance(result.get(key), str):
            result[key] = dt_util.parse_datetime(result[key])
    return result


def event_data(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return event-safe request details without redirect URLs or session context."""
    return {
        key: request.get(key)
        for key in ("request_id", "guest_name", "client_mac", "status", "created_at", "access_expires_at")
    }
