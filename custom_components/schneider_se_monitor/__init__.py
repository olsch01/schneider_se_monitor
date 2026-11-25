"""The Schneider SE-Monitor integration."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    Appliance,
    SEMonitorAuth,
    SEMonitorAuthError,
    SEMonitorClient,
    SEMonitorConnectionError,
    SEMonitorRealtime,
    Site,
)
from .const import CONF_REFRESH_TOKEN, CONF_SITE_ID
from .coordinator import SEMonitorEnergyCoordinator, SEMonitorRealtimeCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


@dataclass
class SEMonitorData:
    """Runtime data for a config entry."""

    site: Site
    appliances: list[Appliance]
    client: SEMonitorClient
    realtime: SEMonitorRealtimeCoordinator
    energy: SEMonitorEnergyCoordinator


type SEMonitorConfigEntry = ConfigEntry[SEMonitorData]


async def async_setup_entry(hass: HomeAssistant, entry: SEMonitorConfigEntry) -> bool:
    """Set up SE-Monitor from a config entry."""
    session = async_get_clientsession(hass)
    auth = SEMonitorAuth(session, entry.data[CONF_REFRESH_TOKEN])
    client = SEMonitorClient(session, auth)
    site_id = entry.data[CONF_SITE_ID]

    try:
        site = await client.async_get_site(site_id)
        appliances = [a for a in await client.async_get_appliances(site_id) if a.is_device]
    except SEMonitorAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except SEMonitorConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    @callback
    def _persist_refresh_token() -> None:
        """Save the refresh token if Cognito ever rotates it."""
        if auth.refresh_token != entry.data[CONF_REFRESH_TOKEN]:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: auth.refresh_token}
            )

    realtime_coordinator: SEMonitorRealtimeCoordinator

    @callback
    def _on_auth_error(err: SEMonitorAuthError) -> None:
        realtime_coordinator.auth_failed = err

    ws = SEMonitorRealtime(session, auth, site_id, on_data=lambda _d: None, on_auth_error=_on_auth_error)
    realtime_coordinator = SEMonitorRealtimeCoordinator(hass, entry, ws)
    energy_coordinator = SEMonitorEnergyCoordinator(
        hass, entry, client, site, {a.appliance_id for a in appliances}
    )

    await energy_coordinator.async_config_entry_first_refresh()
    _persist_refresh_token()

    entry.runtime_data = SEMonitorData(
        site, appliances, client, realtime_coordinator, energy_coordinator
    )
    entry.async_create_background_task(hass, ws.run(), f"se_monitor_ws_{site_id}")
    entry.async_on_unload(energy_coordinator.async_add_listener(_persist_refresh_token))
    # The first realtime frame usually arrives within a few seconds; wait briefly so the
    # power sensor starts with a value, but never block setup on it.
    with suppress(TimeoutError):
        async with asyncio.timeout(20):
            await ws.first_data.wait()
    await realtime_coordinator.async_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SEMonitorConfigEntry) -> bool:
    """Unload a config entry (background websocket task is cancelled automatically)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
