"""Shared entity helpers for Schneider SE-Monitor."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .api import Appliance, Site
from .const import DOMAIN, MANUFACTURER


def site_device_info(site: Site) -> DeviceInfo:
    """Device for the energy monitor itself."""
    meter = site.meter or {}
    return DeviceInfo(
        identifiers={(DOMAIN, str(site.site_id))},
        name=site.name,
        manufacturer=meter.get("manufacturer") or MANUFACTURER,
        model=meter.get("model") or "Energy Monitor",
        serial_number=meter.get("serial_number"),
    )


def appliance_device_info(site: Site, appliance: Appliance) -> DeviceInfo:
    """Device for an appliance the monitor detected."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{site.site_id}_{appliance.appliance_id}")},
        name=appliance.name,
        manufacturer=MANUFACTURER,
        model="Detected device",
        via_device=(DOMAIN, str(site.site_id)),
    )
