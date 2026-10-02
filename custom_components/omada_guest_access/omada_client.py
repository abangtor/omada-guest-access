"""Omada's documented External Portal API (5.0.15–6.2.0).

Reference: https://support.omadanetworks.com/en/document/13080
Deauthentication uses the separate Hotspot Manager web API observed on Omada 6.0.0.39.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from aiohttp import ClientConnectionError, ClientError, ClientSession, ClientSSLError, ClientTimeout, CookieJar
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONTROLLER_ID,
    CONF_CONTROLLER_URL,
    CONF_ENABLE_REVOKE,
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    FOREVER_DURATION_DAYS,
)


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
        self.supports_revoke = config.get(CONF_ENABLE_REVOKE, True)
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
        """Establish an operator session once; do not churn it on coordinator polls.

        Omada's Hotspot Operator endpoint creates a new controller session for
        every login. Repeating that login as a health check can invalidate an
        otherwise working session (and eventually creates a false reauth
        repair). API operations already retry once after a genuine expired
        session response, which is the appropriate time to log in again.
        """
        async with self._lock:
            if not self._csrf_token:
                await self._async_login()

    async def async_authorize(self, context: PortalContext, duration_hours: int) -> datetime | None:
        """Authorize for a relative duration and return the estimated local expiry."""
        if not 0 <= duration_hours <= 720:
            raise ValueError("Access duration must be 0 (Forever) or between 1 and 720 hours")
        async with self._lock:
            if not self._csrf_token:
                await self._async_login()
            # The controller consumes a relative duration, not Unix epoch time.
            payload = _authorization_payload(context, duration_hours)
            started = dt_util.utcnow()
            try:
                await self._async_raw_request("/hotspot/extPortal/auth", payload, csrf=True)
            except OmadaAuthError:
                await self._async_login()
                started = dt_util.utcnow()
                await self._async_raw_request("/hotspot/extPortal/auth", payload, csrf=True)
            return started + timedelta(microseconds=int(payload["time"]))

    async def async_revoke(self, context: PortalContext) -> None:
        """Deauthorize using Hotspot Manager, then confirm the record is inactive.

        Protocol source: Omada 6.0.0.39's authClients models/controllers (web UI).
        Do not use DELETE (history deletion) or a zero-duration authorization.
        """
        if not self.supports_revoke:
            raise OmadaApiError("Hotspot Manager deauthentication is disabled in integration options")
        async with self._lock:
            records = await self._async_hotspot_clients(context.site)
            matches = [row for row in records if self._matches_grant(row, context)]
            if len(matches) != 1:
                raise OmadaApiError(
                    "Cannot uniquely identify the active External Portal grant in Hotspot Manager. "
                    "Check operator site permissions and revoke manually if necessary."
                )
            identifier = matches[0]["id"]
            path = f"/hotspot/sites/{quote(context.site, safe='')}/cmd/clients/{quote(identifier, safe='')}/disconnect"
            await self._async_authenticated_request(path)
            for attempt in range(3):
                records = await self._async_hotspot_clients(context.site)
                if not any(
                    (row["id"] == identifier and row["valid"]) or self._matches_grant(row, context) for row in records
                ):
                    return
                if attempt < 2:
                    await asyncio.sleep(0.25)
            raise OmadaApiError(
                "Omada accepted deauthentication but still reports an active grant. "
                "The local record was not marked revoked; refresh Hotspot Manager before retrying."
            )

    @staticmethod
    def _matches_grant(row: dict, context: PortalContext) -> bool:
        mac = str(row.get("mac", "")).upper().replace("-", ":")
        return (
            mac == context.client_mac.upper().replace("-", ":")
            and row.get("valid") is True
            and row.get("authType") == 4
            and (not context.ssid_name or row.get("ssid") == context.ssid_name)
        )

    async def _async_hotspot_clients(self, site: str) -> list[dict]:
        """Read all pages; fail closed on malformed or truncated discovery."""
        rows: list[dict] = []
        identifiers: set[str] = set()
        expected_total: int | None = None
        path = f"/hotspot/sites/{quote(site, safe='')}/clients"
        for page in range(1, 101):
            response = await self._async_authenticated_request(
                path, method="GET", params={"currentPage": page, "currentPageSize": 100}
            )
            result = response.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("data"), list):
                raise OmadaApiError("Omada returned an invalid Hotspot Manager client list")
            total = result.get("totalRows")
            if type(total) is not int or total < 0 or total > 10000:
                raise OmadaApiError("Omada returned an invalid Hotspot Manager client count")
            if expected_total is not None and total != expected_total:
                raise OmadaApiError("Omada Hotspot Manager client count changed during pagination; retry")
            expected_total = total
            batch = result["data"]
            if len(rows) + len(batch) > total:
                raise OmadaApiError("Omada returned an inconsistent Hotspot Manager client count")
            for row in batch:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                    raise OmadaApiError("Omada returned an invalid Hotspot Manager client record")
                if row["id"] in identifiers or type(row.get("valid")) is not bool:
                    raise OmadaApiError("Omada returned inconsistent Hotspot Manager client records")
                identifiers.add(row["id"])
                rows.append(row)
            if len(rows) == total:
                return rows
            if not batch:
                break
        raise OmadaApiError("Omada Hotspot Manager client list was incomplete")

    async def _async_authenticated_request(self, path: str, *, method: str = "POST", params=None) -> dict:
        if not self._csrf_token:
            await self._async_login()
        try:
            return await self._async_raw_request(path, None, csrf=True, method=method, params=params)
        except OmadaAuthError:
            await self._async_login()
            return await self._async_raw_request(path, None, csrf=True, method=method, params=params)

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

    async def _async_raw_request(
        self, path: str, payload: dict | None, *, csrf: bool, method: str = "POST", params=None
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if csrf and self._csrf_token:
            headers["Csrf-Token"] = self._csrf_token
        url = f"{self._base_url}/{self._controller_id}/api/v2{path}"
        try:
            request = self._session.get if method == "GET" else self._session.post
            kwargs = {"headers": headers, "allow_redirects": False}
            if payload is not None:
                kwargs["json"] = payload
            if params is not None:
                kwargs["params"] = params
            async with request(url, **kwargs) as response:
                if csrf and response.status in {301, 302, 303, 307, 308}:
                    # Omada redirects expired operator sessions to its UI login.
                    # Never follow redirects or forward credentials to the target.
                    location = response.headers.get("Location", "")
                    try:
                        target = urlsplit(urljoin(url, location))
                        origin = urlsplit(self._base_url)
                        same_origin = (
                            target.scheme == origin.scheme
                            and target.hostname == origin.hostname
                            and (target.port or (443 if target.scheme == "https" else 80))
                            == (origin.port or (443 if origin.scheme == "https" else 80))
                            and not target.username
                            and not target.password
                        )
                    except ValueError:
                        same_origin = False
                    if same_origin and target.path.rstrip("/") in {
                        f"/{self._controller_id}/hotspot/login",
                        f"/{self._controller_id}/login",
                    }:
                        self._csrf_token = None
                        raise OmadaAuthError("Omada operator session expired (redirected to login)")
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
            raise OmadaApiError(f"Omada rejected the API operation (error code {code})")
        return result


def _authorization_payload(context: PortalContext, duration_hours: int) -> dict[str, str | int]:
    if not 0 <= duration_hours <= 720:
        raise ValueError("Access duration must be 0 (Forever) or between 1 and 720 hours")
    seconds = FOREVER_DURATION_DAYS * 86400 if duration_hours == 0 else duration_hours * 3600
    payload: dict[str, str | int] = {
        "clientMac": context.client_mac,
        "site": context.site,
        # Decimal-string duration in milliseconds, NOT an absolute timestamp.
        "time": str(seconds * 1_000),
        "authType": 4,
    }
    if context.is_wireless:
        payload.update(apMac=context.ap_mac or "", ssidName=context.ssid_name or "", radioId=context.radio_id or "")
    else:
        payload.update(gatewayMac=context.gateway_mac or "", vid=context.vlan_id or "")
    return payload
