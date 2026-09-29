"""Small dedicated HTTP portal for guest-access requests.

This is deliberately separate from Home Assistant's authenticated web UI. Place it
behind TLS termination and only expose it to the guest VLAN/reverse proxy.
"""

from __future__ import annotations

import re
from typing import Any

from aiohttp import web
from homeassistant.core import HomeAssistant

from .const import DOMAIN, EVENT_REQUEST_CREATED
from .coordinator import OmadaGuestAccessCoordinator

_MAC = re.compile(r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


class GuestPortal:
    """Own a per-entry guest portal listener."""

    def __init__(self, hass: HomeAssistant, coordinator: OmadaGuestAccessCoordinator, port: int) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.port = port
        self._runner: web.AppRunner | None = None

    async def async_start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._index)
        app.router.add_post("/api/request", self._create_request)
        app.router.add_get("/api/request/{request_id}", self._request_status)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, host="0.0.0.0", port=self.port)
        await site.start()

    async def async_stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _index(self, request: web.Request) -> web.Response:
        return web.Response(text=_PAGE, content_type="text/html")

    async def _create_request(self, request: web.Request) -> web.Response:
        try:
            body: dict[str, Any] = await request.json()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(text="Expected JSON body") from None
        name = str(body.get("guest_name", "")).strip()
        mac = str(body.get("client_mac", "")).strip().upper().replace("-", ":")
        if not 1 <= len(name) <= 120 or not _MAC.fullmatch(mac):
            raise web.HTTPBadRequest(text="A name and valid client MAC address are required")
        result = await self.coordinator.async_create_request(
            name, mac, str(body.get("note", "")), str(body.get("access_point", ""))
        )
        self.hass.bus.async_fire(EVENT_REQUEST_CREATED, dict(result))
        return web.json_response(_public_request(result), status=201)

    async def _request_status(self, request: web.Request) -> web.Response:
        item = self.coordinator.requests.get(request.match_info["request_id"])
        if item is None:
            raise web.HTTPNotFound()
        return web.json_response(_public_request(item))


def _public_request(request: dict[str, Any]) -> dict[str, Any]:
    """Return only portal-safe fields."""
    return {
        key: value.isoformat() if hasattr(value := request.get(key), "isoformat") else value
        for key in ("request_id", "guest_name", "status", "expires_at", "access_expires_at", "denial_reason")
    }


_PAGE = """<!doctype html><html lang='en'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Guest Wi-Fi</title><style>body{font-family:system-ui;max-width:440px;margin:12vh auto;padding:1rem}input,button{box-sizing:border-box;width:100%;padding:.8rem;margin:.4rem 0}button{background:#1769aa;color:white;border:0;border-radius:.3rem}#status{margin-top:1rem}</style>
<h1>Guest Wi-Fi</h1><p>Request internet access from your host.</p><form id='request'><input name='guest_name' placeholder='Your name' required maxlength='120'><input name='client_mac' placeholder='Device MAC address' required><input name='note' placeholder='Optional note'><button>Request access</button></form><p id='status'></p>
<script>const f=document.querySelector('form'),s=document.querySelector('#status'),q=new URLSearchParams(location.search),m=q.get('clientMac')||q.get('client_mac');if(m)f.client_mac.value=m;f.onsubmit=async e=>{e.preventDefault();let d=Object.fromEntries(new FormData(f));let r=await fetch('/api/request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});if(!r.ok){s.textContent='Please check your details and try again.';return}let x=await r.json();f.hidden=true;s.textContent='Request sent. Waiting for approval…';let t=setInterval(async()=>{let y=await(await fetch('/api/request/'+x.request_id)).json();if(y.status!=='pending'){s.textContent=y.status==='approved'?'Access approved. You can now use the internet.':y.status==='denied'?'Access was denied.':'This request has expired.';clearInterval(t)}},3000)}</script></html>"""
