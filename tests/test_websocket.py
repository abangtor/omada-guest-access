"""History on HA's real authenticated WebSocket API, with no controller traffic."""

from dataclasses import replace
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.omada_guest_access.const import DOMAIN


@pytest.fixture
async def loaded(hass, entry, mock_client, socket_enabled):
    hass.config_entries.async_update_entry(entry, data={**entry.data, "portal_port": 0})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry.runtime_data
    await hass.config_entries.async_unload(entry.entry_id)


async def history(ws, entry_id, **filters):
    await ws.send_json_auto_id({"type": f"{DOMAIN}/history", "entry_id": entry_id, **filters})
    return await ws.receive_json()


async def test_history_pagination_filtering_and_private_fields(hass, loaded, context, hass_ws_client, freezer):
    loaded.config.update(require_terms=True, terms_text="No abuse.")
    first = await loaded.async_create_request("Alex", context, terms_accepted=True, terms_version=loaded.terms_version)
    await loaded.async_deny_request(first["request_id"], "Please ask Sam", user_id="admin-id")
    freezer.tick(timedelta(seconds=1))
    second = await loaded.async_create_request(
        "Sam",
        replace(context, client_mac="AA:BB:CC:DD:EE:01"),
        terms_accepted=True,
        terms_version=loaded.terms_version,
    )
    ws = await hass_ws_client(hass)
    result = (await history(ws, loaded.entry.entry_id, limit=1))["result"]
    assert result["total"] == 2
    assert result["requests"][0]["request_id"] == second["request_id"]
    page = (await history(ws, loaded.entry.entry_id, limit=1, offset=1))["result"]
    record = page["requests"][0]
    assert record["request_id"] == first["request_id"]
    assert record["decision_user_id"] == "admin-id"
    assert record["denial_reason"] == "Please ask Sam"
    assert record["terms_text"] == "No abuse."
    assert record["terms_accepted_at"] == first["terms_accepted_at"].isoformat()
    assert "portal_context" not in record
    assert "password" not in record
    assert "redirect_url" not in record
    for filters in ({"status": "denied"}, {"query": "aLeX"}, {"query": first["request_id"]}):
        match = (await history(ws, loaded.entry.entry_id, **filters))["result"]
        assert match["total"] == 1
        assert match["requests"][0]["request_id"] == first["request_id"]
    assert (await history(ws, loaded.entry.entry_id, query="nobody"))["result"]["total"] == 0
    await hass.async_block_till_done()
    # Detailed consent/history never goes into sensor attributes.
    for state in hass.states.async_all("sensor"):
        assert "terms_text" not in str(state.attributes)


async def test_history_rejects_non_admin(hass, loaded, hass_ws_client, hass_read_only_access_token):
    ws = await hass_ws_client(hass, access_token=hass_read_only_access_token)
    response = await history(ws, loaded.entry.entry_id)
    assert not response["success"]
    assert response["error"]["code"] == "unauthorized"
    assert "result" not in response


@pytest.mark.parametrize(
    "filters",
    [
        {"limit": 0},
        {"limit": 51},
        {"offset": -1},
        {"offset": "1"},
        {"query": "a" * 121},
        {"status": "unknown"},
    ],
)
async def test_history_validates_bounds(hass, loaded, hass_ws_client, filters):
    ws = await hass_ws_client(hass)
    response = await history(ws, loaded.entry.entry_id, **filters)
    assert not response["success"]
    assert response["error"]["code"] == "invalid_format"


async def test_history_cannot_read_other_entries(hass, loaded, hass_ws_client):
    ws = await hass_ws_client(hass)
    other = MockConfigEntry(domain="other", data={})
    other.add_to_hass(hass)
    unloaded = MockConfigEntry(domain=DOMAIN, data={})
    unloaded.add_to_hass(hass)
    for entry_id in ("missing", other.entry_id, unloaded.entry_id):
        response = await history(ws, entry_id)
        assert not response["success"]
        assert response["error"]["code"] == "not_found"


async def test_history_respects_retention(hass, loaded, context, hass_ws_client, freezer):
    ws = await hass_ws_client(hass)
    loaded.config["retention_days"] = 1
    item = await loaded.async_create_request("Alex", context)
    await loaded.async_deny_request(item["request_id"])
    freezer.tick(timedelta(days=2))
    await loaded.async_expire_requests()
    assert (await history(ws, loaded.entry.entry_id))["result"]["total"] == 0
