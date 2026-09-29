"""Serve the optional Lovelace resource through HA, never the guest listener."""

from __future__ import annotations

import asyncio
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_register_frontend(hass: HomeAssistant) -> None:
    state = hass.data.setdefault(DOMAIN, {"frontend_lock": asyncio.Lock(), "frontend_registered": False})
    async with state["frontend_lock"]:
        if not state["frontend_registered"]:
            await hass.http.async_register_static_paths(
                [
                    StaticPathConfig(
                        f"/{DOMAIN}/omada-guest-access-card.js",
                        str(Path(__file__).parent / "frontend" / "omada-guest-access-card.js"),
                        False,
                    )
                ]
            )
            state["frontend_registered"] = True
