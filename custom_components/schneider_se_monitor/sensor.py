"""Sensors for Schneider SE-Monitor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from . import SEMonitorConfigEntry
from .api import Appliance, RealtimeData, Site
from .coordinator import EnergyData
from .entity import appliance_device_info, site_device_info


@dataclass(frozen=True, kw_only=True)
class SEMonitorSensorDescription(SensorEntityDescription):
    """Describes an SE-Monitor sensor."""

    value_fn: Callable[[Any], float | None]
    solar_only: bool = False


REALTIME_SENSORS: tuple[SEMonitorSensorDescription, ...] = (
    SEMonitorSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.consumption,
    ),
    SEMonitorSensorDescription(
        key="solar_power",
        translation_key="solar_power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda d: d.solar,
        solar_only=True,
    ),
)


def _energy(desc_key: str, translation_key: str, fn: Callable[[EnergyData], float | None], solar_only: bool = False) -> SEMonitorSensorDescription:
    return SEMonitorSensorDescription(
        key=desc_key,
        translation_key=translation_key,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        value_fn=fn,
        solar_only=solar_only,
    )


ENERGY_SENSORS: tuple[SEMonitorSensorDescription, ...] = (
    _energy("energy_today", "energy_today", lambda d: d.day.consumed if d.day else None),
    _energy("import_today", "import_today", lambda d: d.day.imported if d.day else None),
    _energy("export_today", "export_today", lambda d: d.day.exported if d.day else None, solar_only=True),
    _energy("energy_month", "energy_month", lambda d: d.month.consumed if d.month else None),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SEMonitorConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    data = entry.runtime_data
    has_solar = data.site.has_solar
    entities: list[SEMonitorSensor] = [
        SEMonitorSensor(data.realtime, data.site, desc)
        for desc in REALTIME_SENSORS
        if has_solar or not desc.solar_only
    ]
    entities += [
        SEMonitorSensor(data.energy, data.site, desc)
        for desc in ENERGY_SENSORS
        if has_solar or not desc.solar_only
    ]
    for appliance in data.appliances:
        entities.append(SEMonitorDevicePowerSensor(data.realtime, data.site, appliance))
        entities.append(SEMonitorDeviceEnergySensor(data.energy, data.site, appliance))
    async_add_entities(entities)


class SEMonitorSensor(CoordinatorEntity[DataUpdateCoordinator[Any]], SensorEntity):
    """An SE-Monitor sensor."""

    _attr_has_entity_name = True
    entity_description: SEMonitorSensorDescription

    def __init__(
        self,
        coordinator: DataUpdateCoordinator[RealtimeData] | DataUpdateCoordinator[EnergyData],
        site: Site,
        description: SEMonitorSensorDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{site.site_id}_{description.key}"
        self._attr_device_info = site_device_info(site)

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)


class SEMonitorDevicePowerSensor(CoordinatorEntity[DataUpdateCoordinator[RealtimeData]], SensorEntity):
    """Live power of one detected device."""

    _attr_has_entity_name = True
    _attr_translation_key = "device_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: DataUpdateCoordinator[RealtimeData], site: Site, appliance: Appliance
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._appliance_id = appliance.appliance_id
        self._attr_unique_id = f"{site.site_id}_{appliance.appliance_id}_power"
        self._attr_device_info = appliance_device_info(site, appliance)

    @property
    def native_value(self) -> float | None:
        """Watts, 0 when off; unknown if the live feed doesn't include this device."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.device_power(self._appliance_id)


class SEMonitorDeviceEnergySensor(CoordinatorEntity[DataUpdateCoordinator[EnergyData]], SensorEntity):
    """Energy used today by one detected device."""

    _attr_has_entity_name = True
    _attr_translation_key = "device_energy_today"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 2

    def __init__(
        self, coordinator: DataUpdateCoordinator[EnergyData], site: Site, appliance: Appliance
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._appliance_id = appliance.appliance_id
        self._attr_unique_id = f"{site.site_id}_{appliance.appliance_id}_energy_today"
        self._attr_device_info = appliance_device_info(site, appliance)

    @property
    def native_value(self) -> float | None:
        """kWh today; devices missing from today's totals haven't used anything yet."""
        day = self.coordinator.data.day if self.coordinator.data else None
        if day is None:
            return None
        return day.appliances.get(self._appliance_id) or 0.0
