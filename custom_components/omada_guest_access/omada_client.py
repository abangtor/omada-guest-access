"""Client for Omada's documented External Portal Server API.

This is intentionally separate from Omada's browser-management API.  It uses a
Hotspot Operator account and the External Portal endpoints documented by
TP-Link for Controller 5.0.15 through 6.2.x.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import CONF_CONTROLLER_ID, CONF_CONTROLLER_URL, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL

_LOGGER = logging.getLogger(__name__)


class OmadaApiError(Exception):
    """Raised when Omada rejects an External Portal request."""


@dataclass(frozen=True, slots=True)
class PortalContext:
    """Connection metadata supplied by Omada's external-portal redirect."""

    client_mac: str
    site: str
    ap_mac: str | None = None
    gateway_mac: str | None = None
    ssid_name: str | None = None
    radio_id: str | None = None
    vlan_id: str | None = None
    redirect_url: str | None = None

    @property
    def is_wireless(self) -> bool:
        """Return whether this is an AP (rather than gateway) portal context."""
        return self.ap_mac is not None


class OmadaExternalPortalClient:
    """Authorize guests through Omada's documented External Portal API."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self._session: ClientSession = async_get_clientsession(hass, verify_ssl=config.get(CONF_VERIFY_SSL, True))
        self._base_url = config[CONF_CONTROLLER_URL].rstrip("/")
        self._username = config[CONF_USERNAME]
        self._password = config[CONF_PASSWORD]
        self._controller_id = config.get(CONF_CONTROLLER_ID) or _controller_id_from_url(self._base_url)
        self._csrf_token: str | None = None

    @property
    def available(self) -> bool:
        """Return whether a controller ID is available for the API path."""
        return bool(self._controller_id)

    async def async_test_connection(self) -> None:
        """Authenticate the configured Hotspot Operator account."""
        await self._async_login()

    async def async_authorize(self, context: PortalContext, duration_hours: int) -> None:
        """Authorize one redirected client for a limited number of hours."""
        await self._async_request(
            "POST", "/hotspot/extPortal/auth", json=_authorization_payload(context, duration_hours)
        )

    async def async_revoke(self, context: PortalContext) -> None:
        """End a client's external-portal authorization immediately.

        Omada's documented External Portal API has no revoke endpoint. A zero
        duration authorization is the controller-supported way to invalidate
        the current portal grant.
        """
        await self._async_request("POST", "/hotspot/extPortal/auth", json=_authorization_payload(context, 0))

    async def _async_login(self) -> None:
        response = await self._async_raw_request(
            "POST", "/hotspot/login", json={"name": self._username, "password": self._password}, csrf=False
        )
        token = response.get("result", {}).get("token")
        if not token:
            raise OmadaApiError("Omada Hotspot Operator login did not return a CSRF token")
        self._csrf_token = token

    async def _async_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        """Issue an authenticated API request, retrying one expired session."""
        if not self._csrf_token:
            await self._async_login()
        try:
            return await self._async_raw_request(method, path, csrf=True, **kwargs)
        except OmadaApiError as err:
            if "401" not in str(err) and "session" not in str(err).lower():
                raise
        await self._async_login()
        return await self._async_raw_request(method, path, csrf=True, **kwargs)

    async def _async_raw_request(self, method: str, path: str, *, csrf: bool, **kwargs: Any) -> dict[str, Any]:
        if not self._controller_id:
            raise OmadaApiError("A Controller ID is required for Omada External Portal API")
        headers = {"Accept": "application/json"}
        if csrf and self._csrf_token:
            headers["Csrf-Token"] = self._csrf_token
        url = f"{self._base_url}/{self._controller_id}/api/v2{path}"
        try:
            async with self._session.request(method, url, headers=headers, **kwargs) as response:
                try:
                    payload = await response.json(content_type=None)
                except (ValueError, ClientError) as err:
                    raise OmadaApiError(f"Omada returned an invalid response (HTTP {response.status})") from err
        except ClientError as err:
            raise OmadaApiError(f"Unable to reach Omada controller: {err}") from err
        if response.status >= 400:
            raise OmadaApiError(f"Omada API returned HTTP {response.status}")
        if not isinstance(payload, dict) or payload.get("errorCode", 0) != 0:
            raise OmadaApiError(
                f"Omada rejected the request: {payload.get('msg', 'unknown error') if isinstance(payload, dict) else payload}"
            )
        return payload


def _controller_id_from_url(url: str) -> str | None:
    """Extract an Omada Controller ID from a normal controller URL."""
    path = urlparse(url).path.strip("/")
    return path.split("/")[0] if path else None


def _authorization_payload(context: PortalContext, duration_hours: int) -> dict[str, str | int]:
    """Create the EAP or gateway payload required by Omada External Portal."""
    expires = dt_util.utcnow() + timedelta(hours=duration_hours)
    payload: dict[str, str | int] = {
        "clientMac": context.client_mac,
        "site": context.site,
        "time": int(expires.timestamp() * 1_000_000),
        "authType": 4,
    }
    if context.is_wireless:
        payload.update(
            {"apMac": context.ap_mac or "", "ssidName": context.ssid_name or "", "radioId": context.radio_id or ""}
        )
    else:
        payload.update({"gatewayMac": context.gateway_mac or "", "vid": context.vlan_id or ""})
    return payload
