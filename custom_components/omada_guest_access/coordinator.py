"""Persistent, serialized guest request lifecycle independent of controller health."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DEFAULT_DURATION,
    CONF_PENDING_TIMEOUT,
    CONF_REQUIRE_TERMS,
    CONF_RETENTION_DAYS,
    CONF_TERMS_TEXT,
    DEFAULT_DURATION_HOURS,
    DEFAULT_PENDING_TIMEOUT_MINUTES,
    DEFAULT_RETENTION_DAYS,
    DOMAIN,
    EVENT_ACCESS_REVOKED,
    EVENT_OMADA_API_ERROR,
    EVENT_PORTAL_CONNECTED,
    EVENT_REQUEST_APPROVED,
    EVENT_REQUEST_CREATED,
    EVENT_REQUEST_DENIED,
    EVENT_REQUEST_EXPIRED,
    PORTAL_MAX_PENDING,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .omada_client import OmadaApiError, OmadaAuthError, OmadaExternalPortalClient, PortalContext

_LOGGER = logging.getLogger(__name__)
_ACTIVE = {"pending", "approved"}


class OmadaGuestAccessCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Serialize decisions, persistence and expiry; never poll on guest submission."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, logger=_LOGGER, name=DOMAIN, update_interval=timedelta(seconds=30))
        self.entry = entry
        self.requests: dict[str, dict[str, Any]] = {}
        self.config = dict(entry.data) | dict(entry.options)
        self.client = OmadaExternalPortalClient(hass, self.config)
        self.portal = None
        self.controller_available = False
        self._stopping = False
        self._lock = asyncio.Lock()
        self._store = Store[dict[str, Any]](hass, STORAGE_VERSION, f"{STORAGE_KEY}.{entry.entry_id}")

    @property
    def terms_version(self) -> str | None:
        text = self.config.get(CONF_TERMS_TEXT, "").strip()
        return sha256(text.encode("utf-8")).hexdigest() if text else None

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self.requests = {key: _deserialize_request(value) for key, value in stored.get("requests", {}).items()}
        # Pre-1.0 requests were not controller-bound and cannot be approved safely.
        now = dt_util.utcnow()
        for item in self.requests.values():
            if item["status"] in _ACTIVE and not item.get("portal_context"):
                item.update(status="expired", updated_at=now, denial_reason="Legacy request; reconnect to Wi-Fi")
        await self._async_save()
        await self.async_expire_requests()

    async def async_close(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        await self.async_shutdown()
        async with self._lock:
            await self.client.async_close()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {"requests": {key: _serialize_request(value) for key, value in self.requests.items()}}
        )

    def _publish(self) -> None:
        self.async_set_updated_data({"requests": self.requests, "controller_available": self.controller_available})

    async def _async_update_data(self) -> dict[str, Any]:
        await self.async_expire_requests()
        try:
            await self.client.async_test_connection()
        except OmadaApiError as err:
            if self.controller_available:
                self.hass.bus.async_fire(EVENT_OMADA_API_ERROR, {"entry_id": self.entry.entry_id, "error": str(err)})
            self.controller_available = False
            if isinstance(err, OmadaAuthError):
                self.entry.async_start_reauth(self.hass)
        else:
            self.controller_available = True
        return {"requests": self.requests, "controller_available": self.controller_available}

    async def async_expire_requests(self, _now: datetime | None = None) -> None:
        """Also called by an independent timer, even if entity polling is disabled."""
        async with self._lock:
            if not self._stopping:
                await self._expire_locked()

    async def _expire_locked(self) -> None:
        now = dt_util.utcnow()
        cutoff = now - timedelta(days=self.config.get(CONF_RETENTION_DAYS, DEFAULT_RETENTION_DAYS))
        expired = []
        deleted = False
        for request_id, item in list(self.requests.items()):
            deadline = item.get("expires_at") if item["status"] == "pending" else item.get("access_expires_at")
            if item["status"] in _ACTIVE and deadline is not None and deadline <= now:
                item.update(status="expired", updated_at=now)
                expired.append(item)
            # Retention never removes a pending request or an unexpired grant.
            if item["status"] not in _ACTIVE and item["updated_at"] < cutoff:
                del self.requests[request_id]
                deleted = True
        if expired or deleted:
            await self._async_save()
            self._publish()
            for item in expired:
                self._event(EVENT_REQUEST_EXPIRED, item)

    def _event(self, event: str, item: Mapping[str, Any]) -> None:
        self.hass.bus.async_fire(event, {"entry_id": self.entry.entry_id, **event_data(item)})

    def portal_connected(self, context: PortalContext, portal_client_ip: str) -> None:
        """Notify when a device first opens a valid Omada portal redirect.

        External Portal Server has no controller push/webhook for a mere AP
        association. This is deliberately emitted only once per portal browser
        session, rather than on every refresh or polling request.
        """
        redirect_hostname = None
        if context.redirect_url:
            redirect_hostname = urlsplit(context.redirect_url).hostname
        self.hass.bus.async_fire(
            EVENT_PORTAL_CONNECTED,
            {
                "entry_id": self.entry.entry_id,
                "client_mac": context.client_mac,
                "omada_client_ip": context.client_ip,
                "portal_client_ip": portal_client_ip,
                "site": context.site,
                "connection_type": "wireless" if context.is_wireless else "gateway",
                "access_point_mac": context.ap_mac,
                "gateway_mac": context.gateway_mac,
                "ssid_name": context.ssid_name,
                "radio_id": context.radio_id,
                "vlan_id": context.vlan_id,
                "redirect_hostname": redirect_hostname,
            },
        )

    def _ensure_running(self) -> None:
        if self._stopping:
            raise ValueError("Guest access integration is shutting down")

    async def async_create_request(
        self,
        guest_name: str,
        context: PortalContext,
        note: str | None = None,
        *,
        terms_accepted: bool = False,
        terms_version: str | None = None,
    ) -> Mapping[str, Any]:
        if not isinstance(guest_name, str) or not 1 <= len(guest_name.strip()) <= 120:
            raise ValueError("A name of up to 120 characters is required")
        if note is not None and (not isinstance(note, str) or len(note) > 500):
            raise ValueError("Note must be at most 500 characters")
        terms_text = self.config.get(CONF_TERMS_TEXT, "").strip()
        required = self.config.get(CONF_REQUIRE_TERMS, False)
        if required and (terms_accepted is not True or not terms_text or terms_version != self.terms_version):
            raise ValueError("Accept the current guest Wi-Fi terms before requesting access")
        async with self._lock:
            self._ensure_running()
            await self._expire_locked()
            for item in self.requests.values():
                if item["client_mac"] == context.client_mac and item["status"] in _ACTIVE:
                    original = dict(item["portal_context"])
                    current = asdict(context)
                    original.pop("redirect_url", None)
                    current.pop("redirect_url", None)
                    if original != current:
                        raise ValueError("This device already has a request on another connection")
                    return deepcopy(item)
            if sum(item["status"] == "pending" for item in self.requests.values()) >= PORTAL_MAX_PENDING:
                raise ValueError("Guest request queue is full; please contact your host")
            now = dt_util.utcnow()
            request_id = uuid4().hex
            item = {
                "request_id": request_id,
                "guest_name": guest_name.strip(),
                "admin_label": None,
                "client_mac": context.client_mac,
                "note": (note or "").strip() or None,
                "status": "pending",
                "created_at": now,
                "updated_at": now,
                "expires_at": now
                + timedelta(minutes=self.config.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT_MINUTES)),
                "access_expires_at": None,
                "denial_reason": None,
                "decision_user_id": None,
                "portal_context": asdict(context),
                "terms_accepted_at": now if required else None,
                "terms_version": self.terms_version if required else None,
                "terms_text": terms_text if required else None,
            }
            self.requests[request_id] = item
            try:
                await self._async_save()
            except Exception:
                del self.requests[request_id]
                raise
            self._publish()
            self._event(EVENT_REQUEST_CREATED, item)
            return deepcopy(item)

    async def async_approve_request(
        self, request_id: str, duration_hours: int | None = None, *, user_id: str | None = None
    ) -> Mapping[str, Any]:
        async with self._lock:
            self._ensure_running()
            await self._expire_locked()
            item = self._get_request(request_id, "pending")
            duration = (
                self.config.get(CONF_DEFAULT_DURATION, DEFAULT_DURATION_HOURS)
                if duration_hours is None
                else duration_hours
            )
            if not isinstance(duration, int) or isinstance(duration, bool) or not 0 <= duration <= 720:
                raise ValueError("Access duration must be 0 (Forever) or between 1 and 720 hours")
            try:
                expires = await self.client.async_authorize(PortalContext(**item["portal_context"]), duration)
            except OmadaApiError as err:
                self._api_error(request_id, err)
                raise ValueError(f"Omada authorization failed: {err}") from err
            item.update(
                status="approved", access_expires_at=expires, updated_at=dt_util.utcnow(), decision_user_id=user_id
            )
            await self._async_save()
            self._publish()
            self._event(EVENT_REQUEST_APPROVED, item)
            return deepcopy(item)

    async def async_deny_request(
        self, request_id: str, reason: str | None = None, *, user_id: str | None = None
    ) -> Mapping[str, Any]:
        async with self._lock:
            self._ensure_running()
            await self._expire_locked()
            item = self._get_request(request_id, "pending")
            item.update(
                status="denied",
                denial_reason=(reason or "").strip() or None,
                updated_at=dt_util.utcnow(),
                decision_user_id=user_id,
            )
            await self._async_save()
            self._publish()
            self._event(EVENT_REQUEST_DENIED, item)
            return deepcopy(item)

    async def async_revoke_access(self, request_id: str, *, user_id: str | None = None) -> Mapping[str, Any]:
        async with self._lock:
            self._ensure_running()
            await self._expire_locked()
            item = self._get_request(request_id, "approved")
            try:
                await self.client.async_revoke(PortalContext(**item["portal_context"]))
            except OmadaApiError as err:
                self._api_error(request_id, err)
                raise ValueError(str(err)) from err
            item.update(status="revoked", updated_at=dt_util.utcnow(), decision_user_id=user_id)
            await self._async_save()
            self._publish()
            self._event(EVENT_ACCESS_REVOKED, item)
            return deepcopy(item)

    async def async_set_guest_label(
        self, request_id: str, label: str | None, *, user_id: str | None = None
    ) -> Mapping[str, Any]:
        """Set an administrator-only display label without changing the submitted name."""
        if label is not None and (not isinstance(label, str) or len(label.strip()) > 120):
            raise ValueError("Guest label must be at most 120 characters")
        async with self._lock:
            self._ensure_running()
            item = self.requests.get(request_id)
            if item is None:
                raise ValueError("Unknown guest access request")
            item.update(
                admin_label=(label or "").strip() or None,
                updated_at=dt_util.utcnow(),
                label_user_id=user_id,
            )
            await self._async_save()
            self._publish()
            return deepcopy(item)

    async def async_forget_all_records(self) -> int:
        """Delete local request history without changing Omada authorizations.

        This is intentionally separate from deauthorization: records can be
        stale when a guest was removed directly in Omada Hotspot Manager.
        """
        async with self._lock:
            self._ensure_running()
            count = len(self.requests)
            self.requests.clear()
            await self._async_save()
            self._publish()
            return count

    def _api_error(self, request_id: str, err: OmadaApiError) -> None:
        self.hass.bus.async_fire(
            EVENT_OMADA_API_ERROR, {"entry_id": self.entry.entry_id, "request_id": request_id, "error": str(err)}
        )

    def _get_request(self, request_id: str, status: str) -> dict[str, Any]:
        item = self.requests.get(request_id)
        if item is None:
            raise ValueError("Unknown guest access request")
        if item["status"] != status:
            raise ValueError(f"Request is {item['status']}, not {status}")
        return item


def _serialize_request(request: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in request.items()}


def _deserialize_request(request: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(request)
    for key in ("created_at", "updated_at", "expires_at", "access_expires_at", "terms_accepted_at"):
        if isinstance(result.get(key), str):
            parsed = dt_util.parse_datetime(result[key])
            if parsed is None:
                raise ValueError(f"Invalid stored request timestamp: {key}")
            result[key] = dt_util.as_utc(parsed)
    return result


def event_data(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return JSON-safe audit details, excluding internal redirect context."""
    return _serialize_request(
        {
            key: request.get(key)
            for key in (
                "request_id",
                "guest_name",
                "admin_label",
                "client_mac",
                "status",
                "created_at",
                "access_expires_at",
                "decision_user_id",
            )
        }
    )
