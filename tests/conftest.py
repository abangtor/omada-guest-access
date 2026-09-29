"""Real HA fixtures with controller I/O mocked at its boundary."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omada_guest_access.const import DOMAIN
from custom_components.omada_guest_access.coordinator import OmadaGuestAccessCoordinator
from custom_components.omada_guest_access.omada_client import PortalContext


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def config():
    return {
        "controller_url": "https://controller.example",
        "controller_id": "controller",
        "username": "operator",
        "password": "test-password",
        "site": "Default",
        "portal_port": 8088,
        "portal_url": "https://guest.example.com",
        "verify_ssl": True,
        "default_duration": 8,
        "pending_timeout": 15,
        "retention_days": 30,
    }


@pytest.fixture
def entry(hass, config):
    item = MockConfigEntry(domain=DOMAIN, version=2, data=config)
    item.add_to_hass(hass)
    return item


@pytest.fixture
def context():
    return PortalContext(
        client_mac="AA:BB:CC:DD:EE:FF",
        site="Default",
        ap_mac="11:22:33:44:55:66",
        ssid_name="Guest",
        radio_id="1",
        redirect_url="https://example.com/",
    )


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.async_test_connection = AsyncMock()
    client.async_authorize = AsyncMock(side_effect=lambda context, hours: dt_util.utcnow() + timedelta(hours=hours))
    client.async_revoke = AsyncMock()
    client.async_close = AsyncMock()
    client.supports_revoke = False
    with patch("custom_components.omada_guest_access.coordinator.OmadaExternalPortalClient", return_value=client):
        yield client


@pytest.fixture
async def coordinator(hass, entry, mock_client, hass_storage):
    coordinator = OmadaGuestAccessCoordinator(hass, entry)
    entry.runtime_data = coordinator
    await coordinator.async_load()
    yield coordinator
    await coordinator.async_close()
