"""Binary sensors for Schneider SE-Monitor: whether each detected device is on."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from . import SEMonitorConfigEntry
from .api import Appliance, RealtimeData, Site
from .entity import appliance_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SEMonitorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one on/off sensor per detected device."""
    data = entry.runtime_data
    async_add_entities(
        SEMonitorDeviceOnSensor(data.realtime, data.site, appliance) for appliance in data.appliances
    )


class SEMonitorDeviceOnSensor(CoordinatorEntity[DataUpdateCoordinator[RealtimeData]], BinarySensorEntity):
    """On while the monitor sees the device drawing power."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = BinarySensorDeviceClass.POWER

    def __init__(
        self, coordinator: DataUpdateCoordinator[RealtimeData], site: Site, appliance: Appliance
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._appliance_id = appliance.appliance_id
        self._attr_unique_id = f"{site.site_id}_{appliance.appliance_id}_on"
        self._attr_device_info = appliance_device_info(site, appliance)

    @property
    def is_on(self) -> bool | None:
        """True when the device's live power is above zero."""
        if self.coordinator.data is None:
            return None
        watts = self.coordinator.data.device_power(self._appliance_id)
        return None if watts is None else watts > 0
