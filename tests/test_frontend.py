"""Use HA's real resource collection to verify registration and upgrade behavior."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.lovelace.dashboard import LovelaceStorage
from homeassistant.components.lovelace.resources import ResourceStorageCollection, ResourceYAMLCollection

from custom_components.omada_guest_access.frontend import CARD_PATH, async_register_frontend


@pytest.fixture
async def resources(hass, hass_storage):
    collection = ResourceStorageCollection(hass, LovelaceStorage(hass, None))
    hass.data["lovelace"] = SimpleNamespace(resources=collection)
    return collection


@pytest.fixture
async def register(hass):
    # Real collection/storage with only the HTTP static-path boundary mocked.
    hass.http = SimpleNamespace(async_register_static_paths=AsyncMock())
    with patch(
        "custom_components.omada_guest_access.frontend.async_get_integration",
        new=AsyncMock(return_value=SimpleNamespace(version="1.3.1")),
    ):
        yield lambda: async_register_frontend(hass)


async def test_new_resource_concurrent_setup_and_reload(hass, resources, register):
    await asyncio.gather(register(), register())
    await register()
    assert resources.loaded
    assert len(resources.async_items()) == 1
    item = resources.async_items()[0]
    assert item["url"] == f"{CARD_PATH}?v=1.3.1"
    assert item["type"] == "module"
    hass.http.async_register_static_paths.assert_awaited_once()
    # Check the resource survives an actual storage reload, not just memory.
    await resources.store.async_save({"items": resources.async_items()})
    restored = ResourceStorageCollection(hass, LovelaceStorage(hass, None))
    await restored.async_load()
    assert restored.async_items() == [item]


async def test_upgrade_manual_resources_preserves_id_and_other_resources(hass, resources, register):
    await resources.async_load()
    resources.loaded = True
    original = await resources.async_create_item({"url": f"{CARD_PATH}?v=1.2.0", "res_type": "js"})
    await resources.async_create_item({"url": CARD_PATH, "res_type": "module"})
    external = await resources.async_create_item({"url": f"https://other.example{CARD_PATH}", "res_type": "module"})
    other = await resources.async_create_item({"url": "/local/another-card.js", "res_type": "module"})
    await register()
    items = resources.async_items()
    assert len(items) == 3
    assert external in items and other in items
    assert {**original, "url": f"{CARD_PATH}?v=1.3.1", "type": "module"} in items
    with patch.object(resources, "async_update_item", new=AsyncMock()) as update:
        await register()
        update.assert_not_called()


async def test_old_dict_lovelace_data(hass, resources, register):
    hass.data["lovelace"] = {"resources": resources}
    await register()
    assert len(resources.async_items()) == 1


async def test_yaml_resources_untouched(hass, register, caplog):
    items = [{"url": f"{CARD_PATH}?v=old", "type": "module"}]
    hass.data["lovelace"] = SimpleNamespace(resources=ResourceYAMLCollection(items))
    await register()
    assert hass.data["lovelace"].resources.async_items() == items
    assert "add" in caplog.text and "in YAML" in caplog.text


async def test_resource_failure_does_not_break_portal_and_can_retry(hass, resources, register, caplog):
    with patch.object(resources, "async_load", new=AsyncMock(side_effect=OSError("storage unavailable"))):
        await register()
    assert "Unable to register the guest card automatically" in caplog.text
    await register()
    assert len(resources.async_items()) == 1
    hass.http.async_register_static_paths.assert_awaited_once()
