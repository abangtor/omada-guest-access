"""Binary sensors for Omada Guest Access."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import OmadaGuestAccessCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Omada guest-access binary sensors."""
    async_add_entities([PortalOnlineBinarySensor(entry.runtime_data)])


class PortalOnlineBinarySensor(CoordinatorEntity[OmadaGuestAccessCoordinator], BinarySensorEntity):
    """Report whether the integration coordinator is responding."""

    _attr_has_entity_name = True
    _attr_name = "Portal online"
    _attr_device_class = "connectivity"

    @property
    def unique_id(self) -> str:
        """Keep entity identifiers distinct when multiple sites are configured."""
        return f"omada_guest_access_{self.coordinator.entry.entry_id}_portal_online"

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success
