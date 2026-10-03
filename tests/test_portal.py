"""Real HTTP portal requests, proxy identity and browser input boundaries."""

import re
from datetime import timedelta

import pytest

from custom_components.omada_guest_access.const import EVENT_PORTAL_CONNECTED
from custom_components.omada_guest_access.portal import GuestPortal, _safe_redirect_url

QUERY = {
    "clientMac": "AA:BB:CC:DD:EE:FF",
    "apMac": "11:22:33:44:55:66",
    "ssidName": "Guest",
    "radioId": "1",
    "site": "Default",
    "redirectUrl": "https://example.com/",
}


async def landing(client, headers=None, query=None):
    response = await client.get("/", params=query or QUERY, headers=headers)
    assert response.status == 200
    page = await response.text()
    token = re.search(r'const token="([a-f0-9]+)"', page).group(1)
    assert f"nonce-{token}" in response.headers["Content-Security-Policy"]
    return {"X-Portal-Token": token, **(headers or {})}


async def test_request_status_approval_and_isolation(hass, coordinator, aiohttp_client, socket_enabled):
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    assert response.status == 201
    item = await response.json()
    assert item["status"] == "pending"
    assert "client_mac" not in item
    path = f"/api/request/{item['request_id']}"
    assert (await client.get(path)).status == 401
    other_headers = await landing(client)
    assert (await client.get(path, headers=other_headers)).status == 404
    await coordinator.async_approve_request(item["request_id"])
    response = await client.get(path, headers=headers)
    assert response.headers["Cache-Control"] == "no-store"
    approved = await response.json()
    assert approved["status"] == "approved"
    assert approved["redirect_url"] == "https://example.com/"


async def test_valid_portal_redirect_fires_connection_event_once_per_session(
    hass, coordinator, aiohttp_client, socket_enabled
):
    events = []
    hass.bus.async_listen(EVENT_PORTAL_CONNECTED, events.append)
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    query = {**QUERY, "clientIp": "192.168.9.133", "redirectUrl": "https://vpn.example.com/path"}

    first = await client.get("/", params=query)
    assert first.status == 200
    await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data == {
        "entry_id": coordinator.entry.entry_id,
        "client_mac": "AA:BB:CC:DD:EE:FF",
        "omada_client_ip": "192.168.9.133",
        "portal_client_ip": "127.0.0.1",
        "site": "Default",
        "connection_type": "wireless",
        "access_point_mac": "11:22:33:44:55:66",
        "gateway_mac": None,
        "ssid_name": "Guest",
        "radio_id": "1",
        "vlan_id": None,
        "redirect_hostname": "vpn.example.com",
    }

    # Re-opening the redirected URL with the session cookie is a refresh, not
    # another device connection notification.
    second = await client.get("/", params=query)
    assert second.status == 200
    await hass.async_block_till_done()
    assert len(events) == 1


@pytest.mark.parametrize(
    "body",
    [[], None, "hello", {"guest_name": None}, {"guest_name": {}}, {"guest_name": " "}, {"guest_name": "a", "note": 12}],
)
async def test_invalid_json_values_are_400(hass, coordinator, aiohttp_client, socket_enabled, body):
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json=body)
    assert response.status == 400
    assert not coordinator.requests


async def test_wrong_origin_and_oversized_body(hass, coordinator, aiohttp_client, socket_enabled):
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    headers = await landing(client)
    response = await client.post(
        "/api/request", headers={**headers, "Origin": "https://evil.example"}, json={"guest_name": "Alex"}
    )
    assert response.status == 403
    response = await client.post("/api/request", headers=headers, json={"guest_name": "a" * 9000})
    assert response.status == 413


async def test_trusted_proxy_clients_have_separate_limits_and_tokens(hass, coordinator, aiohttp_client, socket_enabled):
    coordinator.config.update(trusted_proxies="127.0.0.1", allowed_networks="192.168.50.0/24")
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    first = {"X-Forwarded-For": "192.168.50.10"}
    headers = await landing(client, first)
    for _ in range(9):
        await landing(client, first)
    assert (await client.get("/", params=QUERY, headers=first)).status == 429
    second = await landing(client, {"X-Forwarded-For": "192.168.50.11"})
    response = await client.post(
        "/api/request", headers={**second, "X-Portal-Token": headers["X-Portal-Token"]}, json={"guest_name": "Alex"}
    )
    assert response.status == 401
    assert (await client.get("/", params=QUERY, headers={"X-Forwarded-For": "203.0.113.1"})).status == 403
    # Leftmost spoofing must not override the first untrusted hop.
    assert (
        await client.get("/", params=QUERY, headers={"X-Forwarded-For": "192.168.50.10, 203.0.113.1"})
    ).status == 403


async def test_untrusted_proxy_header_rejected(hass, coordinator, aiohttp_client, socket_enabled):
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    response = await client.get("/", params=QUERY, headers={"X-Forwarded-For": "192.168.50.10"})
    assert response.status == 400


async def test_session_lasts_through_configured_pending_deadline(
    hass, coordinator, aiohttp_client, socket_enabled, freezer
):
    coordinator.config["pending_timeout"] = 60
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    item = await response.json()
    freezer.tick(timedelta(minutes=25))
    path = f"/api/request/{item['request_id']}"
    assert (await client.get(path, headers=headers)).status == 200
    freezer.tick(timedelta(minutes=41))
    assert (await client.get(path, headers=headers)).status == 401


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "//evil.example", "https://user:pass@host", "http://", "https://host/\nfoo"]
)
def test_redirect_validation(url):
    assert _safe_redirect_url(url) is None


async def test_real_listener_releases_socket_for_reload(hass, coordinator, socket_enabled):
    portal = GuestPortal(hass, coordinator, 0)
    await portal.async_start()
    site = next(iter(portal._runner.sites))
    bound_port = site._server.sockets[0].getsockname()[1]
    assert portal.running
    await portal.async_stop()
    assert not portal.running
    replacement = GuestPortal(hass, coordinator, bound_port)
    try:
        await replacement.async_start()
        assert replacement.running
    finally:
        await replacement.async_stop()


@pytest.mark.parametrize("accepted", [None, False, "true", 1, [], {}])
async def test_required_terms_cannot_be_bypassed(hass, coordinator, aiohttp_client, socket_enabled, accepted):
    coordinator.config.update(require_terms=True, terms_text="No illegal activity.")
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    headers = await landing(client)
    response = await client.post(
        "/api/request", headers=headers, json={"guest_name": "Alex", "terms_accepted": accepted}
    )
    assert response.status == 409
    assert not coordinator.requests


async def test_consent_snapshot_comes_from_server(hass, coordinator, aiohttp_client, socket_enabled):
    coordinator.config.update(require_terms=True, terms_text="Be kind.\nNo abuse.")
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    headers = await landing(client)
    response = await client.post(
        "/api/request",
        headers=headers,
        json={
            "guest_name": "Alex",
            "terms_accepted": True,
            "terms_version": "forged",
            "terms_accepted_at": "fake",
        },
    )
    assert response.status == 201
    public = await response.json()
    record = coordinator.requests[public["request_id"]]
    assert record["terms_version"] == coordinator.terms_version
    assert record["terms_text"] == "Be kind.\nNo abuse."
    assert record["terms_accepted_at"].tzinfo is not None
    assert "terms_text" not in public
    # Even an idempotent submission cannot overwrite the original evidence.
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Other"})
    assert response.status == 200
    assert coordinator.requests[public["request_id"]] == record


async def test_terms_changed_since_landing_requires_new_session(hass, coordinator, aiohttp_client, socket_enabled):
    coordinator.config.update(require_terms=True, terms_text="Original terms")
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    headers = await landing(client)
    coordinator.config["terms_text"] = "Changed terms"
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex", "terms_accepted": True})
    assert response.status == 409
    assert not coordinator.requests


async def test_custom_portal_text_is_escaped_and_no_terms_by_default(hass, coordinator, aiohttp_client, socket_enabled):
    coordinator.config.update(
        portal_title="Sam's <Guest> Wi-Fi",
        portal_message="<script>alert(1)</script>",
        portal_accent="#123ABC",
        terms_text="<img src=x onerror=alert(1)> $title",
    )
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    response = await client.get("/", params=QUERY)
    text = await response.text()
    assert "Sam&#39;s &lt;Guest&gt; Wi-Fi" in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(1)&gt; $title" in text
    assert "background:#123ABC" in text
    assert "name='terms_accepted'" not in text
    assert "<script>alert" not in text


@pytest.mark.parametrize("status", ["pending", "approved", "denied", "expired"])
async def test_reload_restores_request_without_duplicate(
    hass, coordinator, aiohttp_client, socket_enabled, freezer, status
):
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    token = headers["X-Portal-Token"]
    note = "Visiting Sam\nSecond line\nThird line"
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex", "note": note})
    item = await response.json()
    request_id = item["request_id"]
    assert coordinator.requests[request_id]["note"] == note
    if status == "approved":
        await coordinator.async_approve_request(request_id)
    elif status == "denied":
        await coordinator.async_deny_request(request_id, "Please contact your host")
    elif status == "expired":
        freezer.tick(timedelta(minutes=16))
    cookie = {"Cookie": f"omada_guest_portal={token}"}
    # A full Omada redirect and a clean URL both recover the same request.
    for query in (QUERY, None):
        response = await client.get("/", params=query, headers=cookie)
        assert response.status == 200
        page = await response.text()
        assert f'let id="{request_id}"' in page
        assert "<form id='request' hidden>" in page
        assert "<textarea name='note' maxlength='500' rows='4'>" in page
        assert len(portal._sessions) == 1
        session_cookie = response.cookies["omada_guest_portal"]
        assert session_cookie.value == token
        assert session_cookie["httponly"]
        assert session_cookie["secure"]  # public HTTPS even behind an HTTP proxy
        assert session_cookie["samesite"] == "Lax"
        assert session_cookie["path"] == "/"
    response = await client.get(f"/api/request/{request_id}", headers=headers)
    assert (await response.json())["status"] == status
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Duplicate"})
    assert response.status == 200
    assert len(coordinator.requests) == 1


async def test_reconnect_after_revocation_shows_a_new_request_form(
    hass, coordinator, aiohttp_client, socket_enabled
):
    """A cancelled guest must be able to create a fresh access request."""
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    request_id = (await response.json())["request_id"]
    # The controller call itself is covered in test_revoke; this isolates the
    # portal's handling of the resulting local lifecycle state.
    coordinator.requests[request_id]["status"] = "revoked"

    response = await client.get(
        "/",
        params=QUERY,
        headers={"Cookie": f"omada_guest_portal={headers['X-Portal-Token']}"},
    )
    assert response.status == 200
    page = await response.text()
    assert "let id=null" in page
    assert "<form id='request' hidden>" not in page
    assert portal._sessions[headers["X-Portal-Token"]]["request_id"] is None


async def test_cookie_cannot_recover_other_ip_or_device(hass, coordinator, aiohttp_client, socket_enabled):
    coordinator.config.update(trusted_proxies="127.0.0.1")
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client, {"X-Forwarded-For": "192.168.9.10"})
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    request_id = (await response.json())["request_id"]
    cookie = {"Cookie": f'omada_guest_portal={headers["X-Portal-Token"]}'}
    for query, ip in ((QUERY, "192.168.9.11"), ({**QUERY, "clientMac": "00:11:22:33:44:55"}, "192.168.9.10")):
        response = await client.get("/", params=query, headers={**cookie, "X-Forwarded-For": ip})
        assert response.status == 200
        assert request_id not in await response.text()
        token = response.cookies["omada_guest_portal"].value
        response = await client.get(
            f"/api/request/{request_id}", headers={"X-Portal-Token": token, "X-Forwarded-For": ip}
        )
        assert response.status == 404
    response = await client.get("/", params={**QUERY, "site": "wrong"}, headers={**headers, **cookie})
    assert response.status == 400
    assert (await client.get("/", headers={**cookie, "X-Forwarded-For": "192.168.9.11"})).status == 400


async def test_expired_or_unknown_cookie_does_not_recover_request(
    hass, coordinator, aiohttp_client, socket_enabled, freezer
):
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    request_id = (await response.json())["request_id"]
    freezer.tick(timedelta(minutes=21))
    for token in (headers["X-Portal-Token"], "unknown"):
        cookie = {"Cookie": f"omada_guest_portal={token}"}
        assert (await client.get("/", headers=cookie)).status == 400
        response = await client.get("/", params=QUERY, headers=cookie)
        assert response.status == 200
        assert request_id not in await response.text()
        assert response.cookies["omada_guest_portal"].value != token


async def test_reload_refreshes_changed_terms_before_submission(hass, coordinator, aiohttp_client, socket_enabled):
    coordinator.config.update(require_terms=True, terms_text="Original terms")
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    headers = await landing(client)
    coordinator.config["terms_text"] = "New terms"
    response = await client.get("/", params=QUERY, headers={"Cookie": f'omada_guest_portal={headers["X-Portal-Token"]}'})
    token = response.cookies["omada_guest_portal"].value
    assert token != headers["X-Portal-Token"]
    response = await client.post(
        "/api/request", headers={"X-Portal-Token": token}, json={"guest_name": "Alex", "terms_accepted": True}
    )
    assert response.status == 201
    item = await response.json()
    assert coordinator.requests[item["request_id"]]["terms_text"] == "New terms"


async def test_browser_cookie_round_trip_on_direct_http(hass, coordinator, aiohttp_client, socket_enabled):
    coordinator.config["portal_url"] = "http://guest.example.com"
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    request_id = (await response.json())["request_id"]
    # No manually supplied Cookie header: the HTTP client behaves like a browser.
    response = await client.get("/")
    assert response.status == 200
    assert f'let id="{request_id}"' in await response.text()
    assert not response.cookies["omada_guest_portal"]["secure"]
    assert len(portal._sessions) == 1
    # A pruned record must not leave the browser stuck polling a missing request.
    del coordinator.requests[request_id]
    response = await client.get("/")
    assert "let id=null" in await response.text()


async def test_recovery_at_capacity_does_not_extend_session(
    hass, coordinator, aiohttp_client, socket_enabled, freezer, monkeypatch
):
    monkeypatch.setattr("custom_components.omada_guest_access.portal.PORTAL_MAX_SESSIONS", 1)
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    token = headers["X-Portal-Token"]
    deadline = portal._sessions[token]["expires_at"]
    freezer.tick(timedelta(minutes=5))
    response = await client.get("/", params=QUERY, headers={"Cookie": f"omada_guest_portal={token}"})
    assert response.status == 200
    assert portal._sessions[token]["expires_at"] == deadline
    assert int(response.cookies["omada_guest_portal"]["max-age"]) <= 15 * 60
    assert (await client.get("/", params=QUERY)).status == 503


async def test_custom_template_over_http(hass, coordinator, aiohttp_client, socket_enabled):
    from custom_components.omada_guest_access.portal_render import DEFAULT_TEMPLATE

    coordinator.config.update(portal_template=DEFAULT_TEMPLATE.replace("<main>", "<main><h2>Our home</h2>"),
                              portal_css="body{color:navy}")
    client = await aiohttp_client(GuestPortal(hass, coordinator, 0).create_app())
    response = await client.get("/", params=QUERY)
    assert response.status == 200
    assert "<h2>Our home</h2>" in await response.text()
    assert "img-src 'self' data:" in response.headers["Content-Security-Policy"]
    coordinator.config["portal_template"] = "{{ missing_secret }}"
    response = await client.get("/", params=QUERY)
    assert response.status == 503
    assert "missing_secret" not in await response.text()


async def test_repeated_valid_reloads_do_not_consume_session_creation_limit(
    hass, coordinator, aiohttp_client, socket_enabled
):
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    token = headers['X-Portal-Token']
    response = await client.post('/api/request', headers=headers, json={'guest_name': 'Alex'})
    request_id = (await response.json())['request_id']
    await coordinator.async_approve_request(request_id)
    for index in range(25):
        # Android connectivity checks may return with a different original URL.
        response = await client.get('/', params={**QUERY, 'redirectUrl': f'http://check.example/{index}'},
                                    headers={'Cookie': f'omada_guest_portal={token}'})
        assert response.status == 200
        assert response.cookies['omada_guest_portal'].value == token
        assert request_id in await response.text()
    assert len(portal._sessions) == 1
    assert len(portal._attempts['127.0.0.1']) == 1
    assert len(coordinator.requests) == 1
    assert 'location.assign' not in await response.text()
    # Changing the MAC is a new context, not an exemption from rate limiting.
    for index in range(9):
        response = await client.get('/', params={**QUERY, 'clientMac': f'AA:BB:CC:DD:00:{index:02X}'},
                                    headers={'Cookie': f'omada_guest_portal={token}'})
        assert response.status == 200
    response = await client.get('/', params=QUERY)
    assert response.status == 429
    assert int(response.headers['Retry-After']) > 0
    # Even when new-session budget is exhausted, existing sessions still load.
    response = await client.get('/', params=QUERY, headers={'Cookie': f'omada_guest_portal={token}'})
    assert response.status == 200
