"""Hotspot Manager deauthentication: discovery, permissions, confirmation and retries."""

from copy import deepcopy
from dataclasses import replace

import pytest
from aiohttp import web

from custom_components.omada_guest_access.omada_client import OmadaApiError, OmadaExternalPortalClient


@pytest.mark.parametrize("wireless", [True, False])
async def test_hotspot_disconnect_confirmed(hass, config, context, aiohttp_server, socket_enabled, wireless):
    if not wireless:
        context = replace(
            context, ap_mac=None, ssid_name=None, radio_id=None, gateway_mac="11:22:33:44:55:66", vlan_id="90"
        )
    logins, disconnects, pages = [], [], []
    row = {"id": "grant-id", "mac": context.client_mac.replace(":", "-"), "valid": True, "authType": 4, "ssid": "Guest"}

    async def login(request):
        logins.append(await request.json())
        result = web.json_response({"errorCode": 0, "result": {"token": f"token-{len(logins)}"}})
        result.set_cookie("TPOMADA_SESSIONID", "session")
        return result

    async def clients(request):
        pages.append(int(request.query["currentPage"]))
        assert request.query["currentPageSize"] == "100"
        assert request.cookies["TPOMADA_SESSIONID"] == "session"
        assert request.headers["Csrf-Token"] == f"token-{len(logins)}"
        if len(pages) == 1:
            return web.json_response({"errorCode": -1005})
        page = int(request.query["currentPage"])
        data = [{"id": "unrelated", "mac": "11-11-11-11-11-11", "valid": True, "authType": 4}] if page == 1 else [row]
        return web.json_response({"errorCode": 0, "result": {"totalRows": 2, "data": data}})

    async def disconnect(request):
        disconnects.append(request.path)
        assert request.headers["Csrf-Token"] == "token-2"
        assert not await request.read()
        row["valid"] = False
        return web.json_response({"errorCode": 0})

    app = web.Application()
    app.router.add_post("/controller/api/v2/hotspot/login", login)
    app.router.add_get("/controller/api/v2/hotspot/sites/Default/clients", clients)
    app.router.add_post("/controller/api/v2/hotspot/sites/Default/cmd/clients/grant-id/disconnect", disconnect)
    server = await aiohttp_server(app)
    config["controller_url"] = str(server.make_url("/"))
    client = OmadaExternalPortalClient(hass, config)
    try:
        assert client.supports_revoke
        await client.async_revoke(context)
        assert len(logins) == 2
        assert len(disconnects) == 1
        assert pages == [1, 1, 2, 1, 2]
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "scenario",
    [
        "missing",
        "duplicate",
        "wrong_auth",
        "wrong_ssid",
        "invalid_list",
        "repeat_page",
        "excess_records",
        "changing_count",
        "permission",
        "post_error",
        "still_active",
    ],
)
async def test_revoke_fails_closed(hass, config, context, aiohttp_server, socket_enabled, scenario):
    disconnects = []
    row = {"id": "grant-id", "mac": context.client_mac, "valid": True, "authType": 4, "ssid": "Guest"}

    async def login(request):
        return web.json_response({"errorCode": 0, "result": {"token": "token"}})

    async def clients(request):
        data = [deepcopy(row)]
        total = 1
        if scenario == "missing":
            data, total = [], 0
        elif scenario == "duplicate":
            data.append({**row, "id": "another"})
            total = 2
        elif scenario == "wrong_auth":
            data[0]["authType"] = 3
        elif scenario == "wrong_ssid":
            data[0]["ssid"] = "Other network"
        elif scenario == "invalid_list":
            return web.json_response({"errorCode": 0, "result": {"data": []}})
        elif scenario == "repeat_page":
            total = 2
        elif scenario == "excess_records":
            total = 0
        elif scenario == "changing_count":
            if request.query["currentPage"] == "1":
                total = 2
            else:
                total, data = 0, []
        elif scenario == "permission":
            return web.json_response({"errorCode": -2000})
        return web.json_response({"errorCode": 0, "result": {"data": data, "totalRows": total}})

    async def disconnect(request):
        disconnects.append(request.path)
        return web.json_response({"errorCode": -2001 if scenario == "post_error" else 0})

    app = web.Application()
    app.router.add_post("/controller/api/v2/hotspot/login", login)
    app.router.add_get("/controller/api/v2/hotspot/sites/Default/clients", clients)
    app.router.add_post("/controller/api/v2/hotspot/sites/Default/cmd/clients/grant-id/disconnect", disconnect)
    server = await aiohttp_server(app)
    config["controller_url"] = str(server.make_url("/"))
    client = OmadaExternalPortalClient(hass, config)
    try:
        with pytest.raises(OmadaApiError):
            await client.async_revoke(context)
        assert len(disconnects) == (1 if scenario in {"post_error", "still_active"} else 0)
    finally:
        await client.async_close()


async def test_cancel_persists_and_fires_event(coordinator, context, hass):
    events = []
    hass.bus.async_listen("omada_guest_access_access_revoked", lambda event: events.append(event))
    request = await coordinator.async_create_request("Guest", context)
    await coordinator.async_approve_request(request["request_id"])
    result = await coordinator.async_revoke_access(request["request_id"], user_id="administrator")
    await hass.async_block_till_done()
    coordinator.client.async_revoke.assert_awaited_once_with(context)
    assert result["status"] == "revoked"
    assert result["decision_user_id"] == "administrator"
    assert events
