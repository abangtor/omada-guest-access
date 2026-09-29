"""Omada Guest Access integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PORTAL_PORT, DOMAIN, PLATFORMS
from .coordinator import OmadaGuestAccessCoordinator
from .portal import GuestPortal
from .services import async_register_services

type OmadaGuestAccessConfigEntry = ConfigEntry[OmadaGuestAccessCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: OmadaGuestAccessConfigEntry
) -> bool:
    """Set up Omada Guest Access from a config entry."""
    coordinator = OmadaGuestAccessCoordinator(hass, entry)
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    portal = GuestPortal(hass, coordinator, entry.data[CONF_PORTAL_PORT])
    await portal.async_start()

    await async_register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    entry.async_on_unload(portal.async_stop)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: OmadaGuestAccessConfigEntry
) -> bool:
    """Unload an Omada Guest Access config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: OmadaGuestAccessConfigEntry
) -> None:
    """Reload after config entry options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)

