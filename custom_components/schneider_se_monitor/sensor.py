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
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from . import SEMonitorConfigEntry
from .api import RealtimeData, Site
from .const import DOMAIN, MANUFACTURER
from .coordinator import EnergyData


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
        meter = site.meter or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(site.site_id))},
            name=site.name,
            manufacturer=meter.get("manufacturer") or MANUFACTURER,
            model=meter.get("model") or "Energy Monitor",
            serial_number=meter.get("serial_number"),
        )

    @property
    def native_value(self) -> float | None:
        """Return the sensor value."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
