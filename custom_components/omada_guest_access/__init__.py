"""Omada Guest Access integration lifecycle."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.event import async_track_time_interval

from .const import CONF_CONTROLLER_ID, CONF_CONTROLLER_URL, CONF_PORTAL_PORT, DEFAULT_PORTAL_PORT, PLATFORMS
from .coordinator import OmadaGuestAccessCoordinator
from .frontend import async_register_frontend
from .omada_client import OmadaApiError, OmadaAuthError, normalize_controller_url
from .portal import GuestPortal
from .services import async_register_services

type OmadaGuestAccessConfigEntry = ConfigEntry[OmadaGuestAccessCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: OmadaGuestAccessConfigEntry) -> bool:
    """Own and await all resources on both partial setup and normal shutdown."""
    try:
        coordinator = OmadaGuestAccessCoordinator(hass, entry)
    except ValueError as err:
        raise ConfigEntryAuthFailed("Reconfigure the controller URL and Controller ID") from err
    portal = None
    try:
        portal = GuestPortal(hass, coordinator, coordinator.config.get(CONF_PORTAL_PORT, DEFAULT_PORTAL_PORT))
        coordinator.portal = portal
        await coordinator.async_load()
        await coordinator.client.async_test_connection()
        coordinator.controller_available = True
        coordinator.async_set_updated_data({"requests": coordinator.requests, "controller_available": True})
        await portal.async_start()
        entry.runtime_data = coordinator
        await async_register_frontend(hass)
        await async_register_services(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException as err:
        if portal is not None:
            await portal.async_stop()
        await coordinator.async_close()
        if isinstance(err, OmadaAuthError):
            raise ConfigEntryAuthFailed(str(err)) from err
        if isinstance(err, (OmadaApiError, OSError)):
            raise ConfigEntryNotReady(f"Unable to start Omada Guest Access: {err}") from err
        raise

    async def stop(_event=None) -> None:
        await portal.async_stop()
        await coordinator.async_close()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop))
    entry.async_on_unload(async_track_time_interval(hass, coordinator.async_expire_requests, timedelta(seconds=30)))
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: OmadaGuestAccessConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = entry.runtime_data
    await coordinator.portal.async_stop()
    await coordinator.async_close()
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Retain old settings; missing legacy credentials are repaired via reauth."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        data = dict(entry.data)
        try:
            data[CONF_CONTROLLER_URL], data[CONF_CONTROLLER_ID] = normalize_controller_url(
                data[CONF_CONTROLLER_URL], data.get(CONF_CONTROLLER_ID, "")
            )
        except (ValueError, KeyError):
            pass
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: OmadaGuestAccessConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
