"""Sensors for Omada Guest Access."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import OmadaGuestAccessCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Omada guest-access sensors."""
    coordinator: OmadaGuestAccessCoordinator = entry.runtime_data
    async_add_entities([PendingRequestsSensor(coordinator), ActiveSessionsSensor(coordinator)])


class _BaseSensor(CoordinatorEntity[OmadaGuestAccessCoordinator], SensorEntity):
    _attr_has_entity_name = True


class PendingRequestsSensor(_BaseSensor):
    _attr_name = "Pending requests"
    _attr_unique_id = "omada_guest_access_pending_requests"
    _attr_icon = "mdi:account-clock"

    @property
    def native_value(self) -> int:
        return sum(item["status"] == "pending" for item in self.coordinator.requests.values())


class ActiveSessionsSensor(_BaseSensor):
    _attr_name = "Active sessions"
    _attr_unique_id = "omada_guest_access_active_sessions"
    _attr_icon = "mdi:wifi-check"

    @property
    def native_value(self) -> int:
        now = datetime.now().astimezone()
        return sum(
            item["status"] == "approved" and item.get("access_expires_at", now) > now
            for item in self.coordinator.requests.values()
        )

