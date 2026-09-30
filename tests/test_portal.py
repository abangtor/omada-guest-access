"""Real HTTP portal requests, proxy identity and browser input boundaries."""

import re
from datetime import timedelta

import pytest

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
    assert "Sam&#x27;s &lt;Guest&gt; Wi-Fi" in text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in text
    assert "&lt;img src=x onerror=alert(1)&gt; $title" in text
    assert "background:#123ABC" in text
    assert "type='checkbox'" not in text
    assert "<script>alert" not in text
