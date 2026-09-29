"""Dedicated, controller-context-bound guest portal listener."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from datetime import timedelta
from typing import Any
from uuid import uuid4

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SITE,
    EVENT_REQUEST_CREATED,
    PORTAL_RATE_LIMIT,
    PORTAL_RATE_WINDOW_SECONDS,
    PORTAL_SESSION_TTL_SECONDS,
)
from .coordinator import OmadaGuestAccessCoordinator, event_data
from .omada_client import PortalContext

_MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class GuestPortal:
    """Serve one guest portal listener with opaque, short-lived portal sessions."""

    def __init__(self, hass: HomeAssistant, coordinator: OmadaGuestAccessCoordinator, port: int) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.port = port
        self._runner: web.AppRunner | None = None
        self._sessions: dict[str, dict[str, Any]] = {}
        self._attempts: defaultdict[str, deque] = defaultdict(deque)

    async def async_start(self) -> None:
        """Start the listener after the controller connection has been verified."""
        app = web.Application(client_max_size=8 * 1024)
        app.router.add_get("/", self._index)
        app.router.add_post("/api/request", self._create_request)
        app.router.add_get("/api/request/{request_id}", self._request_status)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, host="0.0.0.0", port=self.port).start()
        except OSError:
            await self._runner.cleanup()
            self._runner = None
            raise

    async def async_stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._sessions.clear()
        self._attempts.clear()

    async def _index(self, request: web.Request) -> web.Response:
        self._expire_sessions()
        if not self._allow(request):
            raise web.HTTPTooManyRequests(text="Too many portal attempts. Please wait and try again.")
        context = _portal_context_from_query(request.query, self.coordinator.entry.data[CONF_SITE])
        session_id = uuid4().hex
        self._sessions[session_id] = {
            "context": context,
            "ip": request.remote,
            "expires_at": dt_util.utcnow() + timedelta(seconds=PORTAL_SESSION_TTL_SECONDS),
            "request_id": None,
        }
        return web.Response(text=_page(session_id), content_type="text/html", headers={"Cache-Control": "no-store"})

    async def _create_request(self, request: web.Request) -> web.Response:
        session = self._session_for(request)
        if session["request_id"]:
            item = self.coordinator.requests.get(session["request_id"])
            if item is not None:
                return web.json_response(_public_request(item, session["context"]))
        try:
            body: dict[str, Any] = await request.json()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Expected JSON body") from None
        name = str(body.get("guest_name", "")).strip()
        note = str(body.get("note", "")).strip()
        if not 1 <= len(name) <= 120 or len(note) > 500:
            raise web.HTTPBadRequest(
                text="A name of up to 120 characters and an optional 500-character note are required"
            )
        result = await self.coordinator.async_create_request(name, session["context"], note)
        session["request_id"] = result["request_id"]
        self.hass.bus.async_fire(EVENT_REQUEST_CREATED, event_data(result))
        return web.json_response(_public_request(result, session["context"]), status=201)

    async def _request_status(self, request: web.Request) -> web.Response:
        session = self._session_for(request)
        if session["request_id"] != request.match_info["request_id"]:
            raise web.HTTPNotFound()
        item = self.coordinator.requests.get(request.match_info["request_id"])
        if item is None:
            raise web.HTTPNotFound()
        return web.json_response(_public_request(item, session["context"]))

    def _session_for(self, request: web.Request) -> dict[str, Any]:
        self._expire_sessions()
        token = request.headers.get("X-Portal-Token", "")
        session = self._sessions.get(token)
        if session is None or session["ip"] != request.remote:
            raise web.HTTPUnauthorized(text="Portal session has expired. Please reconnect to Guest Wi-Fi.")
        return session

    def _allow(self, request: web.Request) -> bool:
        now = dt_util.utcnow()
        ip = request.remote or "unknown"
        attempts = self._attempts[ip]
        cutoff = now - timedelta(seconds=PORTAL_RATE_WINDOW_SECONDS)
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= PORTAL_RATE_LIMIT:
            return False
        attempts.append(now)
        return True

    def _expire_sessions(self) -> None:
        now = dt_util.utcnow()
        for token, session in list(self._sessions.items()):
            if session["expires_at"] <= now:
                del self._sessions[token]


def _portal_context_from_query(query: Any, expected_site: str) -> PortalContext:
    """Validate and normalize the context Omada injects into its redirect URL."""
    client_mac = str(query.get("clientMac", "")).upper().replace("-", ":")
    site = str(query.get("site", ""))
    ap_mac = _mac_or_none(query.get("apMac"))
    gateway_mac = _mac_or_none(query.get("gatewayMac"))
    if not _MAC.fullmatch(client_mac) or not site or site != expected_site or bool(ap_mac) == bool(gateway_mac):
        raise web.HTTPBadRequest(text="Missing or invalid Omada portal redirect context")
    if ap_mac and (not query.get("ssidName") or query.get("radioId") is None):
        raise web.HTTPBadRequest(text="Incomplete Omada wireless portal context")
    if gateway_mac and query.get("vid") is None:
        raise web.HTTPBadRequest(text="Incomplete Omada gateway portal context")
    return PortalContext(
        client_mac=client_mac,
        site=site,
        ap_mac=ap_mac,
        gateway_mac=gateway_mac,
        ssid_name=str(query.get("ssidName")) if query.get("ssidName") else None,
        radio_id=str(query.get("radioId")) if query.get("radioId") is not None else None,
        vlan_id=str(query.get("vid")) if query.get("vid") is not None else None,
        redirect_url=_safe_redirect_url(query.get("redirectUrl")),
    )


def _mac_or_none(value: Any) -> str | None:
    normalized = str(value or "").upper().replace("-", ":")
    return normalized if _MAC.fullmatch(normalized) else None


def _safe_redirect_url(value: Any) -> str | None:
    text = str(value or "")
    return text if text.startswith(("https://", "http://")) and len(text) <= 2048 else None


def _public_request(item: dict[str, Any], context: PortalContext) -> dict[str, Any]:
    result = {
        key: value.isoformat() if hasattr(value := item.get(key), "isoformat") else value
        for key in ("request_id", "status", "expires_at", "access_expires_at", "denial_reason")
    }
    if item["status"] == "approved":
        result["redirect_url"] = context.redirect_url
    return result


def _page(session_id: str) -> str:
    return _PAGE.replace("__TOKEN__", json.dumps(session_id))


_PAGE = """<!doctype html><html lang='en'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Guest Wi-Fi</title><style>body{font-family:system-ui;max-width:440px;margin:12vh auto;padding:1rem;color:#15202b}input,button{box-sizing:border-box;width:100%;padding:.8rem;margin:.4rem 0}button{background:#1769aa;color:#fff;border:0;border-radius:.3rem;font:inherit}#status{margin-top:1rem}small{color:#52606d}</style>
<h1>Guest Wi-Fi</h1><p>Request internet access from your host.</p><form id='request'><label>Your name<input name='guest_name' required maxlength='120' autocomplete='name'></label><label>Optional note<input name='note' maxlength='500'></label><button>Request access</button></form><p id='status' role='status'></p><script>
const token=__TOKEN__,f=document.querySelector('form'),s=document.querySelector('#status');let id;
async function api(path,opts={}){let r=await fetch(path,{...opts,headers:{'Content-Type':'application/json','X-Portal-Token':token,...(opts.headers||{})}});if(!r.ok)throw new Error();return r.json()}
async function poll(){try{let x=await api('/api/request/'+id);if(x.status==='pending')return;clearInterval(timer);if(x.status==='approved'){s.textContent='Access approved. Connecting you now…';if(x.redirect_url)location.assign(x.redirect_url)}else if(x.status==='denied')s.textContent=x.denial_reason||'Access was denied.';else s.textContent='This request has expired.'}catch(_){clearInterval(timer);s.textContent='Your portal session expired. Please reconnect to Guest Wi-Fi.'}}
f.onsubmit=async e=>{e.preventDefault();try{let x=await api('/api/request',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(f)))});id=x.request_id;f.hidden=true;s.textContent='Request sent. Waiting for approval…';window.timer=setInterval(poll,3000)}catch(_){s.textContent='Unable to send your request. Please reconnect and try again.'}};
</script></html>"""
