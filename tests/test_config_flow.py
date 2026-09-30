"""HA flow manager coverage: options, migration, reauthentication and validation."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omada_guest_access import async_migrate_entry
from custom_components.omada_guest_access.const import DOMAIN
from custom_components.omada_guest_access.omada_client import OmadaAuthError


@pytest.fixture
def flow_client(mock_client):
    with patch("custom_components.omada_guest_access.config_flow.OmadaExternalPortalClient", return_value=mock_client):
        yield mock_client


async def test_user_flow_normalizes_and_closes_client(hass, config, flow_client):
    config["controller_url"] += "/controller/"
    with patch("custom_components.omada_guest_access.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}, data=config
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["controller_url"] == "https://controller.example"
    flow_client.async_close.assert_awaited_once()


async def test_invalid_auth_is_recoverable(hass, config, flow_client):
    flow_client.async_test_connection.side_effect = OmadaAuthError("invalid credentials")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=config
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    flow_client.async_close.assert_awaited_once()


async def test_options_flow_uses_readonly_config_entry(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "portal_port": 8090,
            "portal_url": "https://guest.example.com",
            "default_duration": 4,
            "pending_timeout": 60,
            "retention_days": 7,
            "trusted_proxies": "10.0.0.2/32",
            "allowed_networks": "192.168.50.0/24",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["portal_port"] == 8090


async def test_options_reject_invalid_network(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "portal_port": 8088,
            "default_duration": 8,
            "pending_timeout": 15,
            "retention_days": 30,
            "trusted_proxies": "not-an-ip",
        },
    )
    assert result["errors"] == {"base": "invalid_config"}


async def test_duplicate_listen_port_rejected(hass, entry, config, flow_client):
    config["site"] = "Other"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}, data=config
    )
    assert result["errors"] == {"portal_port": "port_in_use"}
    flow_client.async_test_connection.assert_not_called()


async def test_legacy_migration_preserves_options(hass, config):
    config["controller_url"] += "/controller/"
    entry = MockConfigEntry(domain=DOMAIN, version=1, data=config, options={"default_duration": 4})
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.data["controller_url"] == "https://controller.example"
    assert entry.options["default_duration"] == 4


async def test_reauth_updates_and_reloads(hass, entry, config, flow_client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_REAUTH,
            "entry_id": entry.entry_id,
        },
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    config["password"] = "replacement-password"
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input=config)
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "replacement-password"
    reload.assert_awaited_once()


@pytest.mark.parametrize(
    "settings",
    [
        {"require_terms": True, "terms_text": " "},
        {"portal_accent": "red; background:url(https://example.com)"},
        {"portal_title": " "},
        {"portal_message": "x" * 501},
        {"terms_text": "x" * 4001},
    ],
)
async def test_options_reject_invalid_portal_branding(hass, entry, settings):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={
            "portal_port": 8088,
            "default_duration": 8,
            "pending_timeout": 15,
            "retention_days": 30,
            **settings,
        },
    )
    assert result["errors"] == {"base": "invalid_config"}


async def test_options_store_branding_and_terms(hass, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    settings = {
        "portal_port": 8088,
        "default_duration": 8,
        "pending_timeout": 15,
        "retention_days": 30,
        "portal_title": "Garden Wi-Fi",
        "portal_message": "Welcome!",
        "portal_accent": "#ABC123",
        "terms_text": "No abuse.\nEnjoy your visit.",
        "require_terms": True,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input=settings)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert all(entry.options[key] == value for key, value in settings.items())


@pytest.fixture
async def flow_http(hass, hass_client):
    """Exercise the real API serialization used by the HA frontend."""
    from homeassistant.components.config import config_entries as config_api
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "http", {})
    assert await async_setup_component(hass, "websocket_api", {})
    assert config_api.async_setup(hass)
    return await hass_client()


async def test_user_form_loads_over_http(flow_http):
    response = await flow_http.post("/api/config/config_entries/flow", json={"handler": DOMAIN})
    assert response.status == 200
    result = await response.json()
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    fields = {field["name"]: field for field in result["data_schema"]}
    assert fields["controller_url"]["type"] == "string"
    assert fields["controller_url"]["required"]


@pytest.mark.parametrize("source", [config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE])
async def test_existing_entry_forms_load_over_http(hass, entry, flow_http, source):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": source, "entry_id": entry.entry_id}, data=None
    )
    response = await flow_http.get(f"/api/config/config_entries/flow/{result['flow_id']}")
    assert response.status == 200
    payload = await response.json()
    assert payload["step_id"] == ("reauth_confirm" if source == config_entries.SOURCE_REAUTH else "reconfigure")
    assert isinstance(payload["data_schema"], list)


async def test_options_form_loads_over_http(entry, flow_http):
    response = await flow_http.post("/api/config/config_entries/options/flow", json={"handler": entry.entry_id})
    assert response.status == 200
    assert (await response.json())["step_id"] == "init"


@pytest.mark.parametrize("url", ["not-a-url", "ftp://controller.example", "https://controller.example:bad"])
async def test_invalid_controller_url_returns_form_over_http(flow_http, config, flow_client, url):
    response = await flow_http.post("/api/config/config_entries/flow", json={"handler": DOMAIN})
    assert response.status == 200
    flow_id = (await response.json())["flow_id"]
    response = await flow_http.post(
        f"/api/config/config_entries/flow/{flow_id}", json={**config, "controller_url": url}
    )
    assert response.status == 200
    assert (await response.json())["errors"] == {"base": "invalid_config"}
    flow_client.async_test_connection.assert_not_called()


async def test_auth_error_form_loads_over_http(flow_http, config, flow_client):
    flow_client.async_test_connection.side_effect = OmadaAuthError("invalid credentials")
    response = await flow_http.post("/api/config/config_entries/flow", json={"handler": DOMAIN})
    assert response.status == 200
    flow_id = (await response.json())["flow_id"]
    response = await flow_http.post(f"/api/config/config_entries/flow/{flow_id}", json=config)
    assert response.status == 200
    assert (await response.json())["errors"] == {"base": "invalid_auth"}
