"""Sensors for Omada Guest Access."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .coordinator import OmadaGuestAccessCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up Omada guest-access sensors."""
    coordinator: OmadaGuestAccessCoordinator = entry.runtime_data
    async_add_entities([PendingRequestsSensor(coordinator), ActiveSessionsSensor(coordinator)])


class _BaseSensor(CoordinatorEntity[OmadaGuestAccessCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _key: str

    @property
    def unique_id(self) -> str:
        """Keep entity identifiers distinct when multiple sites are configured."""
        return f"omada_guest_access_{self.coordinator.entry.entry_id}_{self._key}"


class PendingRequestsSensor(_BaseSensor):
    _attr_name = "Pending requests"
    _attr_icon = "mdi:account-clock"
    _key = "pending_requests"

    @property
    def native_value(self) -> int:
        return sum(item["status"] == "pending" for item in self.coordinator.requests.values())

    @property
    def extra_state_attributes(self) -> dict[str, list[dict[str, str | None]]]:
        """Expose the actionable pending queue for dashboard templates/cards."""
        return {
            "entry_id": self.coordinator.entry.entry_id,
            "requests": [
                _request_summary(item) for item in self.coordinator.requests.values() if item["status"] == "pending"
            ]
        }


class ActiveSessionsSensor(_BaseSensor):
    _attr_name = "Active sessions"
    _attr_icon = "mdi:wifi-check"
    _key = "active_sessions"

    @property
    def native_value(self) -> int:
        now = dt_util.utcnow()
        return sum(
            item["status"] == "approved" and item.get("access_expires_at", now) > now
            for item in self.coordinator.requests.values()
        )

    @property
    def extra_state_attributes(self) -> dict[str, list[dict[str, str | None]]]:
        """Expose currently authorized sessions for dashboard templates/cards."""
        now = dt_util.utcnow()
        return {
            "sessions": [
                _request_summary(item)
                for item in self.coordinator.requests.values()
                if item["status"] == "approved" and item.get("access_expires_at", now) > now
            ],
            "revoke_supported": self.coordinator.client.supports_revoke,
            "controller_confirmed": False,
        }


def _request_summary(item: dict) -> dict[str, str | None]:
    """Keep sensor attributes useful without publishing the internal portal context."""
    return {
        key: item.get(key).isoformat() if hasattr(item.get(key), "isoformat") else item.get(key)
        for key in (
            "request_id",
            "guest_name",
            "client_mac",
            "note",
            "status",
            "created_at",
            "expires_at",
            "access_expires_at",
        )
    }
