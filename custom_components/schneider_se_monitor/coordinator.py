"""Coordinators for Schneider SE-Monitor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    EnergyUsage,
    RealtimeData,
    SEMonitorAuthError,
    SEMonitorClient,
    SEMonitorConnectionError,
    SEMonitorRealtime,
    Site,
)
from .const import ENERGY_UPDATE_INTERVAL, REALTIME_STALE_AFTER, REALTIME_UPDATE_INTERVAL

if TYPE_CHECKING:
    from . import SEMonitorConfigEntry

_LOGGER = logging.getLogger(__name__)


class SEMonitorRealtimeCoordinator(DataUpdateCoordinator[RealtimeData]):
    """Publishes the latest websocket frame to entities at a fixed rate.

    The websocket pushes a frame every couple of seconds; sampling it every
    REALTIME_UPDATE_INTERVAL keeps the recorder from being flooded.
    """

    config_entry: SEMonitorConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SEMonitorConfigEntry, realtime: SEMonitorRealtime
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="SE-Monitor realtime",
            update_interval=REALTIME_UPDATE_INTERVAL,
        )
        self.realtime = realtime
        self.auth_failed: SEMonitorAuthError | None = None

    async def _async_update_data(self) -> RealtimeData:
        if self.auth_failed:
            raise ConfigEntryAuthFailed(str(self.auth_failed))
        data = self.realtime.last_data
        if data is None:
            raise UpdateFailed("Waiting for realtime data")
        if time.time() - data.received > REALTIME_STALE_AFTER:
            raise UpdateFailed("Realtime data is stale")
        return data


@dataclass
class EnergyData:
    """Energy totals for the current day and month (site-local)."""

    day: EnergyUsage | None
    month: EnergyUsage | None


class SEMonitorEnergyCoordinator(DataUpdateCoordinator[EnergyData]):
    """Polls energy totals."""

    config_entry: SEMonitorConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SEMonitorConfigEntry, client: SEMonitorClient, site: Site
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="SE-Monitor energy",
            update_interval=ENERGY_UPDATE_INTERVAL,
        )
        self.client = client
        self.site = site
        self._tz = ZoneInfo(site.timezone)

    async def _async_update_data(self) -> EnergyData:
        today = datetime.now(self._tz).date()
        month_start = today.replace(day=1)
        month_end = date(
            month_start.year + month_start.month // 12, month_start.month % 12 + 1, 1
        ) - timedelta(days=1)
        try:
            days = await self.client.async_get_energy_usage(
                self.site.site_id, today.isoformat(), today.isoformat(), "day"
            )
            months = await self.client.async_get_energy_usage(
                self.site.site_id, month_start.isoformat(), month_end.isoformat(), "month"
            )
        except SEMonitorAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SEMonitorConnectionError as err:
            raise UpdateFailed(str(err)) from err

        day = next((d for d in days if d.date.startswith(today.isoformat())), None)
        month = next((m for m in months if m.date.startswith(month_start.isoformat()[:7])), None)
        # No row yet just after midnight / the 1st: the period has started from zero.
        empty = EnergyUsage(date="", consumed=0.0, imported=0.0, exported=0.0, grid=0.0, appliances={})
        return EnergyData(day=day or empty, month=month or empty)
