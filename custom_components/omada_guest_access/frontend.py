"""Serve the card on HA and maintain its storage-managed Lovelace resource."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlsplit

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
CARD_PATH = f"/{DOMAIN}/omada-guest-access-card.js"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register once per HA instance; serialize concurrent site setup/reloads."""
    state = hass.data.setdefault(DOMAIN, {"frontend_lock": asyncio.Lock(), "frontend_registered": False})
    async with state["frontend_lock"]:
        if not state["frontend_registered"]:
            await hass.http.async_register_static_paths(
                [StaticPathConfig(CARD_PATH, str(Path(__file__).parent / "frontend" / "omada-guest-access-card.js"), False)]
            )
            state["frontend_registered"] = True
        # Failure to manage a dashboard resource must not take guest Wi-Fi offline.
        # Retry on the next entry setup/reload; the static path remains usable.
        try:
            await _async_register_resource(hass)
        except Exception:
            _LOGGER.exception("Unable to register the guest card automatically; add %s as a module manually", CARD_PATH)


async def _async_register_resource(hass: HomeAssistant) -> None:
    integration = await async_get_integration(hass, DOMAIN)
    url = f"{CARD_PATH}?v={integration.version}"
    lovelace = hass.data.get("lovelace")
    # Older HA versions used a dict before LovelaceData was introduced.
    resources = lovelace.get("resources") if isinstance(lovelace, dict) else getattr(lovelace, "resources", None)
    if not isinstance(resources, ResourceStorageCollection):
        _LOGGER.info("Lovelace resources are not storage-managed; add %s as a module in YAML", url)
        return
    if not resources.loaded:
        await resources.async_load()
        resources.loaded = True
    matching = []
    for item in resources.async_items():
        parsed = urlsplit(item.get("url", ""))
        if not parsed.scheme and not parsed.netloc and parsed.path == CARD_PATH:
            matching.append(item)
    values = {"url": url, "res_type": "module"}
    if not matching:
        await resources.async_create_item(values)
        return
    current, *duplicates = matching
    if current["url"] != url or current.get("type") != "module":
        await resources.async_update_item(current["id"], values)
    # Only exact local card paths belong to us; leave other resources untouched.
    for duplicate in duplicates:
        await resources.async_delete_item(duplicate["id"])
