"""Dedicated, controller-context-bound guest portal listener."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from datetime import timedelta
from html import escape
from ipaddress import ip_address, ip_network
from string import Template
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from aiohttp import web
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALLOWED_NETWORKS,
    CONF_PENDING_TIMEOUT,
    CONF_PORTAL_ACCENT,
    CONF_PORTAL_MESSAGE,
    CONF_PORTAL_TITLE,
    CONF_PORTAL_URL,
    CONF_REQUIRE_TERMS,
    CONF_SITE,
    CONF_TERMS_TEXT,
    CONF_TRUSTED_PROXIES,
    DEFAULT_PENDING_TIMEOUT_MINUTES,
    DEFAULT_PORTAL_ACCENT,
    DEFAULT_PORTAL_MESSAGE,
    DEFAULT_PORTAL_TITLE,
    PORTAL_MAX_SESSIONS,
    PORTAL_RATE_LIMIT,
    PORTAL_RATE_WINDOW_SECONDS,
    PORTAL_SESSION_TTL_SECONDS,
)
from .coordinator import OmadaGuestAccessCoordinator
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
        self._trusted = parse_networks(coordinator.config.get(CONF_TRUSTED_PROXIES, ""))
        self._allowed = parse_networks(coordinator.config.get(CONF_ALLOWED_NETWORKS, ""))

    @property
    def running(self) -> bool:
        return self._runner is not None

    def create_app(self) -> web.Application:
        app = web.Application(client_max_size=8 * 1024, middlewares=[self._security_headers])
        app.router.add_get("/", self._index)
        app.router.add_post("/api/request", self._create_request)
        app.router.add_get("/api/request/{request_id}", self._request_status)
        return app

    @web.middleware
    async def _security_headers(self, request: web.Request, handler) -> web.StreamResponse:
        headers = {
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        }
        try:
            response = await handler(request)
        except web.HTTPException as err:
            err.headers.update(headers)
            raise
        response.headers.update(headers)
        return response

    async def async_start(self) -> None:
        """Start the listener after the controller connection has been verified."""
        app = self.create_app()
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        try:
            await web.TCPSite(self._runner, host="0.0.0.0", port=self.port).start()
        except BaseException:
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
        context = _portal_context_from_query(request.query, self.coordinator.config[CONF_SITE])
        if len(self._sessions) >= PORTAL_MAX_SESSIONS:
            raise web.HTTPServiceUnavailable(text="Portal is busy. Please try again later.")
        session_id = uuid4().hex
        self._sessions[session_id] = {
            "context": context,
            "terms_version": self.coordinator.terms_version,
            "ip": self._client_ip(request),
            "expires_at": dt_util.utcnow()
            + timedelta(
                seconds=max(
                    PORTAL_SESSION_TTL_SECONDS,
                    (self.coordinator.config.get(CONF_PENDING_TIMEOUT, DEFAULT_PENDING_TIMEOUT_MINUTES) + 5) * 60,
                )
            ),
            "request_id": None,
        }
        return web.Response(
            text=_page(session_id, self.coordinator.config),
            content_type="text/html",
            headers={
                "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{session_id}'; "
                "style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
            },
        )

    async def _create_request(self, request: web.Request) -> web.Response:
        session = self._session_for(request)
        origin = request.headers.get("Origin")
        public_url = self.coordinator.config.get(CONF_PORTAL_URL) or f"{request.scheme}://{request.host}"
        public = urlsplit(public_url)
        if origin and origin != f"{public.scheme}://{public.netloc}":
            raise web.HTTPForbidden(text="Invalid portal origin")
        await self.coordinator.async_expire_requests()
        if session["request_id"]:
            item = self.coordinator.requests.get(session["request_id"])
            if item is not None:
                return web.json_response(_public_request(item, session["context"]))
        try:
            body: dict[str, Any] = await request.json()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Expected JSON body") from None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Expected a JSON object")
        name, note = body.get("guest_name", ""), body.get("note", "")
        if not isinstance(name, str) or not isinstance(note, str):
            raise web.HTTPBadRequest(text="Name and note must be text")
        name, note = name.strip(), note.strip()
        if not 1 <= len(name) <= 120 or len(note) > 500:
            raise web.HTTPBadRequest(
                text="A name of up to 120 characters and an optional 500-character note are required"
            )
        try:
            result = await self.coordinator.async_create_request(
                name,
                session["context"],
                note,
                terms_accepted=body.get("terms_accepted") is True,
                terms_version=session["terms_version"],
            )
        except ValueError as err:
            raise web.HTTPConflict(text=str(err)) from err
        session["request_id"] = result["request_id"]
        return web.json_response(_public_request(result, session["context"]), status=201)

    async def _request_status(self, request: web.Request) -> web.Response:
        session = self._session_for(request)
        if session["request_id"] != request.match_info["request_id"]:
            raise web.HTTPNotFound()
        await self.coordinator.async_expire_requests()
        item = self.coordinator.requests.get(request.match_info["request_id"])
        if item is None:
            raise web.HTTPNotFound()
        return web.json_response(_public_request(item, session["context"]))

    def _session_for(self, request: web.Request) -> dict[str, Any]:
        self._expire_sessions()
        token = request.headers.get("X-Portal-Token", "")
        session = self._sessions.get(token)
        if session is None or session["ip"] != self._client_ip(request):
            raise web.HTTPUnauthorized(text="Portal session has expired. Please reconnect to Guest Wi-Fi.")
        return session

    def _allow(self, request: web.Request) -> bool:
        now = dt_util.utcnow()
        ip = self._client_ip(request)
        if ip not in self._attempts and len(self._attempts) >= PORTAL_MAX_SESSIONS * 2:
            return False
        attempts = self._attempts[ip]
        cutoff = now - timedelta(seconds=PORTAL_RATE_WINDOW_SECONDS)
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= PORTAL_RATE_LIMIT:
            return False
        attempts.append(now)
        return True

    def _client_ip(self, request: web.Request) -> str:
        """Walk a trusted proxy chain from the socket peer toward the client."""
        try:
            peer = ip_address(request.remote or "")
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                if not any(peer in net for net in self._trusted):
                    raise web.HTTPBadRequest(text="Forwarding headers from an untrusted proxy")
                chain = [ip_address(value.strip()) for value in forwarded.split(",")]
                if len(chain) > 20:
                    raise ValueError("Proxy chain too long")
                for address in reversed(chain):
                    if not any(peer in net for net in self._trusted):
                        break
                    peer = address
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid client address") from None
        if self._allowed and not any(peer in net for net in self._allowed):
            raise web.HTTPForbidden(text="Connect to the guest network to use this portal")
        return str(peer)

    def _expire_sessions(self) -> None:
        now = dt_util.utcnow()
        for token, session in list(self._sessions.items()):
            if session["expires_at"] <= now:
                del self._sessions[token]
        cutoff = now - timedelta(seconds=PORTAL_RATE_WINDOW_SECONDS)
        for ip, attempts in list(self._attempts.items()):
            while attempts and attempts[0] < cutoff:
                attempts.popleft()
            if not attempts:
                del self._attempts[ip]


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
    if len(text) > 2048 or any(ord(char) < 32 for char in text) or "\\" in text:
        return None
    try:
        parsed = urlsplit(text)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            _ = parsed.port
            return text
    except ValueError:
        pass
    return None


def _public_request(item: dict[str, Any], context: PortalContext) -> dict[str, Any]:
    result = {
        key: value.isoformat() if hasattr(value := item.get(key), "isoformat") else value
        for key in ("request_id", "status", "expires_at", "access_expires_at", "denial_reason")
    }
    if item["status"] == "approved":
        result["redirect_url"] = context.redirect_url
    return result


def parse_networks(value: str) -> list:
    """Parse comma-separated IP addresses/CIDRs shared with the options flow."""
    return [ip_network(part.strip(), strict=False) for part in value.split(",") if part.strip()]


def _page(session_id: str, config: dict[str, Any] | None = None) -> str:
    config = config or {}
    terms = config.get(CONF_TERMS_TEXT, "").strip()
    terms_html = ""
    if terms:
        terms_html = (
            "<details id='terms' open><summary>Guest Wi-Fi terms</summary><p>" + escape(terms) + "</p></details>"
        )
    if config.get(CONF_REQUIRE_TERMS):
        terms_html += "<label class='consent'><input type='checkbox' name='terms_accepted' required> I agree to the guest Wi-Fi terms.</label>"
    accent = config.get(CONF_PORTAL_ACCENT, DEFAULT_PORTAL_ACCENT)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        accent = DEFAULT_PORTAL_ACCENT
    return Template(_PAGE).substitute(
        token=json.dumps(session_id),
        nonce=escape(session_id, quote=True),
        title=escape(config.get(CONF_PORTAL_TITLE, DEFAULT_PORTAL_TITLE)),
        message=escape(config.get(CONF_PORTAL_MESSAGE, DEFAULT_PORTAL_MESSAGE)),
        accent=accent,
        terms=terms_html,
    )


_PAGE = """<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>$title</title><style>
body{font-family:system-ui;max-width:440px;margin:8vh auto;padding:1rem;color:#15202b;background:#fff}
input,button{box-sizing:border-box;width:100%;padding:.8rem;margin:.4rem 0}
button{background:$accent;color:#fff;border:0;border-radius:.3rem;font:inherit;cursor:pointer}
button:disabled{opacity:.6;cursor:wait}input[type=checkbox]{width:auto;margin-right:.5rem}
#status{margin-top:1rem}#terms{margin:1rem 0}p{white-space:pre-wrap;overflow-wrap:anywhere}
.consent{display:block;margin:1rem 0}small{color:#52606d}
</style></head><body>
<h1>$title</h1><p>$message</p><form id='request'>
<label>Your name<input name='guest_name' required maxlength='120' autocomplete='name'></label>
<label>Optional note<input name='note' maxlength='500'></label>$terms
<button>Request access</button></form><p id='status' role='status' aria-live='polite'></p><script nonce='$nonce'>
const token=$token,f=document.querySelector('form'),s=document.querySelector('#status');let id,timer;
async function api(path,opts={}){let r=await fetch(path,{...opts,headers:{'Content-Type':'application/json','X-Portal-Token':token,...(opts.headers||{})}});if(!r.ok)throw new Error();return r.json()}
async function poll(){try{let x=await api('/api/request/'+id);if(x.status==='pending')return;clearInterval(timer);if(x.status==='approved'){s.textContent='Access approved. You can now use the internet.';if(x.redirect_url)location.assign(x.redirect_url)}else if(x.status==='denied')s.textContent=x.denial_reason||'Access was denied.';else s.textContent='This request has expired.'}catch(_){clearInterval(timer);s.textContent='Your portal session expired. Please reconnect to Guest Wi-Fi.'}}
f.onsubmit=async e=>{e.preventDefault();const button=f.querySelector('button');button.disabled=true;
try{const body=Object.fromEntries(new FormData(f));if(f.elements.terms_accepted)body.terms_accepted=f.elements.terms_accepted.checked;
let x=await api('/api/request',{method:'POST',body:JSON.stringify(body)});id=x.request_id;f.hidden=true;s.textContent='Request sent. Waiting for approval…';timer=setInterval(poll,3000);await poll()}
catch(_){s.textContent='Unable to send your request. Please reconnect and try again.'}finally{button.disabled=false}};
</script></body></html>"""
