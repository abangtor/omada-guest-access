"""Setup/unload failures must release sockets and owned HTTP sessions."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.omada_guest_access import async_setup_entry, async_unload_entry
from custom_components.omada_guest_access.omada_client import OmadaAuthError


async def test_setup_unload_awaits_resources(hass, entry, mock_client, hass_storage, socket_enabled):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "portal_port": 0})
    with (
        patch("custom_components.omada_guest_access.async_register_frontend", new=AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch.object(hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)),
    ):
        assert await async_setup_entry(hass, entry)
        portal = entry.runtime_data.portal
        assert portal.running
        assert await async_unload_entry(hass, entry)
        assert not portal.running
        mock_client.async_close.assert_awaited_once()


async def test_auth_failure_closes_resources(hass, entry, mock_client, hass_storage):
    mock_client.async_test_connection.side_effect = OmadaAuthError("bad credentials")
    with pytest.raises(ConfigEntryAuthFailed):
        await async_setup_entry(hass, entry)
    mock_client.async_close.assert_awaited_once()


async def test_port_bind_failure_closes_resources(hass, entry, mock_client, hass_storage):
    with patch(
        "custom_components.omada_guest_access.portal.web.TCPSite.start", new=AsyncMock(side_effect=OSError("in use"))
    ):
        with pytest.raises(ConfigEntryNotReady, match="in use"):
            await async_setup_entry(hass, entry)
    mock_client.async_close.assert_awaited_once()


async def test_platform_failure_closes_started_listener(hass, entry, mock_client, hass_storage, socket_enabled):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "portal_port": 0})
    with (
        patch("custom_components.omada_guest_access.async_register_frontend", new=AsyncMock()),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock(side_effect=RuntimeError("setup failed"))
        ),
    ):
        with pytest.raises(RuntimeError, match="setup failed"):
            await async_setup_entry(hass, entry)
    assert not entry.runtime_data.portal.running
    mock_client.async_close.assert_awaited_once()


async def test_invalid_proxy_settings_dont_leak_client(hass, entry, mock_client, hass_storage):
    hass.config_entries.async_update_entry(entry, options={"trusted_proxies": "invalid"})
    with pytest.raises(ValueError):
        await async_setup_entry(hass, entry)
    mock_client.async_close.assert_awaited_once()


async def test_full_ha_setup_static_resource_entities_and_reload(
    hass, entry, mock_client, hass_client, socket_enabled, context
):
    """Use the real config-entry manager/platforms/HTTP registration together."""
    hass.config_entries.async_update_entry(entry, data={**entry.data, "portal_port": 0})
    assert await hass.config_entries.async_setup(entry.entry_id)
    http = await hass_client()
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    resources = hass.data["lovelace"].resources
    assert any(
            item["url"] == "/omada_guest_access/omada-guest-access-card.js?v=1.4.7"
        and item["type"] == "module"
        for item in resources.async_items()
    )
    response = await http.get("/omada_guest_access/omada-guest-access-card.js")
    assert response.status == 200
    assert "class OmadaGuestAccessCard" in await response.text()
    await coordinator.async_create_request("Alex", context)
    await hass.async_block_till_done()
    states = hass.states.async_all("sensor")
    assert any(state.state == "1" and state.attributes.get("requests") for state in states)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not coordinator.portal.running
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.portal.running
    assert len(entry.runtime_data.requests) == 1
    assert sum(item["url"].startswith("/omada_guest_access/") for item in resources.async_items()) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
