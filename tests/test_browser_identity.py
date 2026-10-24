"""Persistent browser approval, exact remaining access and crash-safe MAC moves."""

import asyncio
import secrets
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from unittest.mock import AsyncMock, call

import pytest

from custom_components.omada_guest_access.const import STORAGE_KEY
from custom_components.omada_guest_access.coordinator import OmadaGuestAccessCoordinator, event_data
from custom_components.omada_guest_access.omada_client import OmadaApiError
from custom_components.omada_guest_access.portal import GuestPortal
from tests.test_portal import QUERY, landing


async def approved_browser(coordinator, context, hours=720):
    key = secrets.token_hex(32)
    item = await coordinator.async_create_request("Alex", context, browser_key_hash=sha256(key.encode()).hexdigest())
    approved = await coordinator.async_approve_request(item["request_id"], hours, user_id="host")
    coordinator.client.reset_mock()
    return key, approved


async def test_return_after_week_and_restart(hass, coordinator, context, freezer, hass_storage):
    key, original = await approved_browser(coordinator, context)
    freezer.tick(timedelta(days=7, minutes=13))
    restored = OmadaGuestAccessCoordinator(hass, coordinator.entry)
    try:
        await restored.async_load()
        new_context = replace(context, client_mac="AA:BB:CC:DD:EE:01", ap_mac="11:22:33:44:55:77")
        result = await restored.async_resume_browser(key, new_context)
        assert result["request_id"] == original["request_id"]
        assert result["access_expires_at"] == original["access_expires_at"]
        assert result["decision_user_id"] == "host"
        assert result["guest_name"] == "Alex"
        assert result["client_mac"] == new_context.client_mac
        assert restored.client.mock_calls[:2] == [
            call.async_revoke(context, allow_missing=True),
            call.async_authorize(new_context, 0, expires_at=original["access_expires_at"]),
        ]
        stored = hass_storage[f"{STORAGE_KEY}.{coordinator.entry.entry_id}"]["data"]["requests"]
        assert stored[result["request_id"]]["client_mac"] == new_context.client_mac
        assert key not in str(stored)
        assert "browser_key_hash" not in event_data(result)
    finally:
        await restored.async_close()


@pytest.mark.parametrize("status", ["pending", "denied", "expired", "revoked", "missing", "invalid_key"])
async def test_only_valid_approved_credentials_resume(coordinator, context, status):
    key, item = await approved_browser(coordinator, context)
    if status == "missing":
        coordinator.requests.clear()
    elif status != "invalid_key":
        coordinator.requests[item["request_id"]]["status"] = status
    else:
        key = secrets.token_hex(32)
    assert await coordinator.async_resume_browser(key, replace(context, client_mac="AA:BB:CC:DD:EE:02")) is None
    coordinator.client.async_authorize.assert_not_called()
    coordinator.client.async_revoke.assert_not_called()


async def test_expired_grant_never_restored(coordinator, context, freezer):
    key, _ = await approved_browser(coordinator, context, hours=1)
    freezer.tick(timedelta(hours=1))
    assert await coordinator.async_resume_browser(key, context) is None
    coordinator.client.async_authorize.assert_not_called()


@pytest.mark.parametrize("change", [{"site": "Other"}, {"ssid_name": "Other"}])
async def test_identity_is_network_scoped(coordinator, context, change):
    key, _ = await approved_browser(coordinator, context)
    assert await coordinator.async_resume_browser(key, replace(context, **change)) is None
    coordinator.client.async_authorize.assert_not_called()


async def test_changed_required_terms_need_new_consent(coordinator, context):
    key, _ = await approved_browser(coordinator, context)
    coordinator.config.update(require_terms=True, terms_text="New terms")
    assert await coordinator.async_resume_browser(key, context) is None
    coordinator.client.async_authorize.assert_not_called()


async def test_revoke_failure_does_not_authorize_new_mac(coordinator, context):
    key, item = await approved_browser(coordinator, context)
    coordinator.client.async_revoke.side_effect = OmadaApiError("not permitted")
    with pytest.raises(ValueError, match="restoration failed"):
        await coordinator.async_resume_browser(key, replace(context, client_mac="AA:BB:CC:DD:EE:03"))
    assert coordinator.requests[item["request_id"]] == item
    coordinator.client.async_authorize.assert_not_called()


async def test_authorization_failure_recovery_tracks_possible_new_grant(hass, coordinator, context):
    key, original = await approved_browser(coordinator, context)
    second = replace(context, client_mac="AA:BB:CC:DD:EE:02")
    third = replace(context, client_mac="AA:BB:CC:DD:EE:03")
    coordinator.client.async_authorize.side_effect = OmadaApiError("timeout after possible success")
    with pytest.raises(ValueError):
        await coordinator.async_resume_browser(key, second)
    assert coordinator.requests[original["request_id"]]["browser_restore_pending"] is True
    restored = OmadaGuestAccessCoordinator(hass, coordinator.entry)
    try:
        await restored.async_load()
        restored.client.reset_mock()
        restored.client.async_authorize.side_effect = None
        result = await restored.async_resume_browser(key, third)
        restored.client.async_revoke.assert_awaited_once_with(second, allow_missing=True)
        restored.client.async_authorize.assert_awaited_once_with(third, 0, expires_at=original["access_expires_at"])
        assert result["browser_restore_pending"] is False
    finally:
        await restored.async_close()


async def test_journal_save_failure_never_authorizes_untracked_mac(coordinator, context):
    key, item = await approved_browser(coordinator, context)
    coordinator._store.async_save = AsyncMock(side_effect=OSError("disk full"))
    with pytest.raises(OSError):
        await coordinator.async_resume_browser(key, replace(context, client_mac="AA:BB:CC:DD:EE:02"))
    assert coordinator.requests[item["request_id"]] == item
    coordinator.client.async_authorize.assert_not_called()


async def test_conflicting_mac_request_not_silently_overwritten(coordinator, context):
    key, _ = await approved_browser(coordinator, context)
    other = replace(context, client_mac="AA:BB:CC:DD:EE:02")
    await coordinator.async_create_request("Other", other)
    with pytest.raises(ValueError, match="another guest request"):
        await coordinator.async_resume_browser(key, other)
    coordinator.client.async_revoke.assert_not_called()


async def test_concurrent_moves_are_serialized(coordinator, context):
    key, item = await approved_browser(coordinator, context)
    second = replace(context, client_mac="AA:BB:CC:DD:EE:02")
    third = replace(context, client_mac="AA:BB:CC:DD:EE:03")
    await asyncio.gather(coordinator.async_resume_browser(key, second), coordinator.async_resume_browser(key, third))
    assert coordinator.client.mock_calls == [
        call.async_revoke(context, allow_missing=True),
        call.async_authorize(second, 0, expires_at=item["access_expires_at"]),
        call.async_revoke(second, allow_missing=True),
        call.async_authorize(third, 0, expires_at=item["access_expires_at"]),
    ]


async def test_browser_cookie_roundtrip_automatic_transfer(hass, coordinator, aiohttp_client, socket_enabled, freezer):
    coordinator.config["portal_url"] = "http://guest.example.com"
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    public = await response.json()
    cookie = response.cookies[portal._browser_cookie]
    assert cookie["httponly"] and cookie["samesite"] == "Lax"
    assert int(cookie["max-age"]) == 400 * 86400
    assert cookie.value not in await response.text()
    item = await coordinator.async_approve_request(public["request_id"], 720)
    coordinator.client.reset_mock()
    freezer.tick(timedelta(days=7))
    portal._sessions.clear()  # HA/portal restart; persistent browser cookie survives.
    response = await client.get("/", params={**QUERY, "clientMac": "AA:BB:CC:DD:EE:02"})
    assert response.status == 200
    assert f'let id="{item["request_id"]}"' in await response.text()
    assert "<form id='request' hidden>" in await response.text()
    assert len(coordinator.requests) == 1
    coordinator.client.async_revoke.assert_awaited_once()
    coordinator.client.async_authorize.assert_awaited_once()
    assert coordinator.requests[item["request_id"]]["access_expires_at"] == item["access_expires_at"]
    # Same short-lived session refresh does not deauthenticate/re-authorize again.
    assert (await client.get("/", params={**QUERY, "clientMac": "AA:BB:CC:DD:EE:02"})).status == 200
    coordinator.client.async_authorize.assert_awaited_once()


async def test_duplicate_mac_cannot_claim_another_browsers_key(hass, coordinator, aiohttp_client, socket_enabled):
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    original_hash = next(iter(coordinator.requests.values()))["browser_key_hash"]
    headers = await landing(client)  # HTTPS cookies deliberately not sent over test HTTP.
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Impersonator"})
    assert portal._browser_cookie not in response.cookies
    assert next(iter(coordinator.requests.values()))["browser_key_hash"] == original_hash


async def test_secure_identity_cookie_and_no_key_in_page(hass, coordinator, aiohttp_client, socket_enabled):
    portal = GuestPortal(hass, coordinator, 0)
    client = await aiohttp_client(portal.create_app())
    headers = await landing(client)
    response = await client.post("/api/request", headers=headers, json={"guest_name": "Alex"})
    cookie = response.cookies[portal._browser_cookie]
    assert cookie["secure"] and cookie["httponly"] and cookie["path"] == "/"
    assert cookie.value not in await response.text()
