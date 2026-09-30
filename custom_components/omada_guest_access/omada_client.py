"""Omada's documented External Portal API (5.0.15–6.2.0).

Reference: https://support.omadanetworks.com/en/document/13080
The protocol authorizes clients; it does not document revocation or discovery.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientConnectionError, ClientError, ClientSession, ClientSSLError, ClientTimeout, CookieJar
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .const import CONF_CONTROLLER_ID, CONF_CONTROLLER_URL, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL


class OmadaApiError(Exception):
    """The controller could not complete a request."""


class OmadaTlsError(OmadaApiError):
    """The controller TLS certificate or handshake could not be verified."""


class OmadaAuthError(OmadaApiError):
    """The controller rejected authentication."""


@dataclass(frozen=True, slots=True)
class PortalContext:
    """Untrusted connection metadata from the browser's Omada redirect."""

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
        return self.ap_mac is not None


def normalize_controller_url(url: str, controller_id: str = "") -> tuple[str, str]:
    """Accept an origin or controller UI URL without duplicating the ID."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Use an HTTP(S) controller URL without embedded credentials")
    # Validate the port as well as the hostname.
    _ = parts.port
    segments = parts.path.strip("/").split("/") if parts.path.strip("/") else []
    inferred = segments[0] if segments else ""
    identifier = controller_id.strip() or inferred
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise ValueError("A valid Controller ID is required")
    if inferred and inferred != identifier:
        raise ValueError("Controller ID does not match the controller URL")
    return urlunsplit((parts.scheme, parts.netloc, "", "", "")), identifier


class OmadaExternalPortalClient:
    """Isolate cookies/CSRF per entry and serialize login and authorization."""

    supports_revoke = False

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self._base_url, self._controller_id = normalize_controller_url(
            config[CONF_CONTROLLER_URL], config.get(CONF_CONTROLLER_ID, "")
        )
        self._session: ClientSession = async_create_clientsession(
            hass,
            auto_cleanup=False,
            verify_ssl=config.get(CONF_VERIFY_SSL, True),
            cookie_jar=CookieJar(unsafe=True),  # Controllers commonly use IP addresses.
            timeout=ClientTimeout(total=15),
        )
        self._username = config[CONF_USERNAME]
        self._password = config[CONF_PASSWORD]
        self._csrf_token: str | None = None
        self._lock = asyncio.Lock()

    async def async_close(self) -> None:
        # HA owns the connector. Detach this isolated session without closing
        # that connector or affecting other integrations' HTTP requests.
        async with self._lock:
            self._csrf_token = None
            self._session.cookie_jar.clear()
            self._session.detach()

    async def async_test_connection(self) -> None:
        async with self._lock:
            await self._async_login()

    async def async_authorize(self, context: PortalContext, duration_hours: int) -> datetime:
        """Return the exact expiry sent to Omada, not a later local estimate."""
        if not 1 <= duration_hours <= 720:
            raise ValueError("Access duration must be between 1 and 720 hours")
        async with self._lock:
            if not self._csrf_token:
                await self._async_login()
            expires = dt_util.utcnow() + timedelta(hours=duration_hours)
            payload = _authorization_payload(context, duration_hours, expires=expires)
            try:
                await self._async_raw_request("/hotspot/extPortal/auth", payload, csrf=True)
            except OmadaAuthError:
                await self._async_login()
                await self._async_raw_request("/hotspot/extPortal/auth", payload, csrf=True)
            return expires

    async def async_revoke(self, context: PortalContext) -> None:
        """Never claim that an undocumented zero-duration grant revoked access."""
        raise OmadaApiError(
            "Revocation is not supported by the documented External Portal API. "
            "Disconnect/revoke the client in Omada Hotspot Manager or wait for its grant to expire."
        )

    async def _async_login(self) -> None:
        self._csrf_token = None
        self._session.cookie_jar.clear()
        response = await self._async_raw_request(
            "/hotspot/login", {"name": self._username, "password": self._password}, csrf=False
        )
        result = response.get("result")
        token = result.get("token") if isinstance(result, dict) else None
        if not isinstance(token, str) or not token:
            raise OmadaApiError("Hotspot Operator login did not return a CSRF token")
        self._csrf_token = token

    async def _async_raw_request(self, path: str, payload: dict, *, csrf: bool) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if csrf and self._csrf_token:
            headers["Csrf-Token"] = self._csrf_token
        url = f"{self._base_url}/{self._controller_id}/api/v2{path}"
        try:
            async with self._session.post(url, headers=headers, json=payload, allow_redirects=False) as response:
                if response.status in {401, 403}:
                    raise OmadaAuthError(f"Omada rejected authentication (HTTP {response.status})")
                if response.status != 200:
                    raise OmadaApiError(f"Omada API returned HTTP {response.status}")
                try:
                    result = await response.json(content_type=None)
                except ValueError as err:
                    raise OmadaApiError("Omada returned invalid JSON") from err
        except ClientSSLError as err:
            raise OmadaTlsError("Unable to establish a verified TLS connection to the Omada controller") from err
        except TimeoutError as err:
            raise OmadaApiError("Omada controller request timed out after 15 seconds") from err
        except ClientConnectionError as err:
            raise OmadaApiError("Unable to connect to the Omada controller from Home Assistant") from err
        except ClientError as err:
            raise OmadaApiError("Omada HTTP request failed") from err
        if not isinstance(result, dict) or "errorCode" not in result:
            raise OmadaApiError("Omada returned an invalid API response")
        code = result["errorCode"]
        if type(code) is not int:
            raise OmadaApiError("Omada returned an invalid API error code")
        if code != 0:
            # Omada uses -1005 for an expired/missing session. Do not expose
            # arbitrary controller response text (which can contain secrets).
            if not csrf or code == -1005:
                raise OmadaAuthError(f"Omada rejected the Hotspot Operator login/session (error code {code})")
            raise OmadaApiError(f"Omada rejected authorization (error code {code})")
        return result


def _authorization_payload(
    context: PortalContext, duration_hours: int, *, expires: datetime | None = None
) -> dict[str, str | int]:
    expires = expires or dt_util.utcnow() + timedelta(hours=duration_hours)
    payload: dict[str, str | int] = {
        "clientMac": context.client_mac,
        "site": context.site,
        "time": int(expires.timestamp() * 1_000_000),
        "authType": 4,
    }
    if context.is_wireless:
        payload.update(apMac=context.ap_mac or "", ssidName=context.ssid_name or "", radioId=context.radio_id or "")
    else:
        payload.update(gatewayMac=context.gateway_mac or "", vid=context.vlan_id or "")
    return payload
