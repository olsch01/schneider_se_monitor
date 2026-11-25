"""Config flow and setup tests."""
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.schneider_se_monitor import api
from custom_components.schneider_se_monitor.const import DOMAIN
from .conftest import APPLIANCES, SITE, make_jwt

P = "custom_components.schneider_se_monitor"


async def _start(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    url = result["description_placeholders"]["login_url"]
    state = dict(x.split("=", 1) for x in url.split("?", 1)[1].split("&"))["state"]
    return result, state


async def test_user_flow(hass: HomeAssistant):
    result, state = await _start(hass)
    with patch(f"{P}.config_flow.exchange_code", AsyncMock(return_value={"refresh_token": "rt", "id_token": make_jwt()})), \
         patch(f"{P}.config_flow.SEMonitorClient.async_get_sites", AsyncMock(return_value=[api._site(SITE)])), \
         patch(f"{P}.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": f"http://localhost/?code=c&state={state}"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Home"
    assert result["data"] == {"refresh_token": "rt", "site_id": 12345}


async def test_user_flow_errors(hass: HomeAssistant):
    result, state = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"redirect_url": "nonsense"})
    assert result["errors"] == {"base": "invalid_url"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": "http://localhost/?code=c&state=bad"})
    assert result["errors"] == {"base": "invalid_auth"}
    # a fresh login link is issued after a failed attempt
    assert state not in result["description_placeholders"]["login_url"]


async def test_setup_creates_sensors(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="12345", data={"refresh_token": "rt", "site_id": 12345})
    entry.add_to_hass(hass)
    usage = api.EnergyUsage("2026-11-24", 12.5, 12.5, 0.0, 12.5, {101: 1.25})
    appliances = [api.Appliance.from_api(a) for a in APPLIANCES]
    month = api.EnergyUsage("2026-11-01", 300.0, 300.0, 0.0, 300.0, {})

    async def fake_run(self):
        self.last_data = api.RealtimeData.from_payload({"epoch": 1, "consumption": 693.8, "devices": [
            {"appliance_id": 101, "w": 150.5}, {"appliance_id": 102, "w": None}]})
        self.first_data.set()

    with patch(f"{P}.SEMonitorClient.async_get_site", AsyncMock(return_value=api._site(SITE))), \
         patch(f"{P}.SEMonitorClient.async_get_appliances", AsyncMock(return_value=appliances)), \
         patch(f"{P}.coordinator.SEMonitorClient.async_get_energy_usage", AsyncMock(side_effect=[[usage], [month]])), \
         patch(f"{P}.SEMonitorRealtime.run", fake_run), \
         patch(f"{P}.coordinator.datetime") as dt:
        import datetime as _d
        dt.now.return_value = _d.datetime(2026, 11, 24, 12, 0)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await entry.runtime_data.realtime.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get("sensor.test_home_energy_today").state == "12.5"
    assert hass.states.get("sensor.test_home_energy_this_month").state == "300.0"
    assert hass.states.get("sensor.test_home_power").state == "693.8"
    assert hass.states.get("sensor.test_home_solar_power") is None

    # per-device entities (the "mains" pseudo device is skipped)
    assert hass.states.get("sensor.fridge_power").state == "150.5"
    assert hass.states.get("binary_sensor.fridge").state == "on"
    assert hass.states.get("sensor.fridge_energy_today").state == "1.25"
    assert hass.states.get("sensor.washer_power").state == "0.0"
    assert hass.states.get("binary_sensor.washer").state == "off"
    assert hass.states.get("sensor.washer_energy_today").state == "0.0"
    assert hass.states.get("sensor.always_on_power").state == "unknown"  # not in live frame
    assert hass.states.get("sensor.mains_power") is None


async def test_setup_auth_failure_starts_reauth(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="12345", data={"refresh_token": "rt", "site_id": 12345})
    entry.add_to_hass(hass)
    with patch(f"{P}.SEMonitorClient.async_get_site", AsyncMock(side_effect=api.SEMonitorAuthError("expired"))):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    flows = hass.config_entries.flow.async_progress()
    assert flows and flows[0]["context"]["source"] == "reauth"
