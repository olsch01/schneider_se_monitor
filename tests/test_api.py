"""API client tests."""
import json, time
import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.schneider_se_monitor import api
from .conftest import SITE, make_jwt


def test_login_request_and_parse():
    login = api.create_login_request()
    assert "code_challenge_method=S256" in login.url and "identity_provider=EIM" in login.url
    code = api.parse_redirect_url(f"http://localhost/?code=abc&state={login.state}", login)
    assert code == "abc"
    with pytest.raises(api.SEMonitorAuthError):
        api.parse_redirect_url("http://localhost/?code=abc&state=wrong", login)
    with pytest.raises(api.SEMonitorAuthError):
        api.parse_redirect_url("http://localhost/?error=access_denied", login)


def test_jwt_exp():
    assert abs(api._jwt_exp(make_jwt(1234)) - 1234) < 1
    assert api._jwt_exp("garbage") == 0.0


async def test_refresh_and_get(hass, aioclient_mock):
    s = async_get_clientsession(hass)
    if True:
        auth = api.SEMonitorAuth(s, "refresh")
        client = api.SEMonitorClient(s, auth)
        m = aioclient_mock
        if True:
            m.post(api.COGNITO_IDP, json={"AuthenticationResult": {"IdToken": make_jwt(), "ExpiresIn": 3600}})
            m.get(f"{api.API_URL}sites", json={"item_count": 1, "data": [SITE]})
            sites = await client.async_get_sites()
        assert sites[0].site_id == 12345 and sites[0].meter["serial_number"] == "W000000001"


async def test_refresh_not_authorized(hass, aioclient_mock):
    s = async_get_clientsession(hass)
    if True:
        auth = api.SEMonitorAuth(s, "refresh")
        m = aioclient_mock
        if True:
            m.post(api.COGNITO_IDP, status=400, json={"__type": "NotAuthorizedException", "message": "Refresh Token has expired"})
            with pytest.raises(api.SEMonitorAuthError):
                await auth.async_get_id_token()


async def test_401_retries_once_then_auth_error(hass, aioclient_mock):
    s = async_get_clientsession(hass)
    if True:
        auth = api.SEMonitorAuth(s, "refresh", make_jwt())
        client = api.SEMonitorClient(s, auth)
        m = aioclient_mock
        if True:
            from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
            calls = []

            async def user(method, url, data):
                calls.append(1)
                if len(calls) == 1:
                    return AiohttpClientMockResponse(method, url, status=401)
                return AiohttpClientMockResponse(method, url, json={"user_id": 1})

            m.get(f"{api.API_URL}user", side_effect=user)
            m.post(api.COGNITO_IDP, json={"AuthenticationResult": {"IdToken": make_jwt()}})
            assert (await client.async_get_user())["user_id"] == 1


async def test_energy_usage_parse(hass, aioclient_mock):
    s = async_get_clientsession(hass)
    if True:
        client = api.SEMonitorClient(s, api.SEMonitorAuth(s, "r", make_jwt()))
        m = aioclient_mock
        if True:
            m.get(f"{api.API_URL}sites/12345/energy_usage", json=
                  [{"reading_date": "2026-11-24", "grid": 33.5, "consumed": 33.5, "imported": 33.5,
                            "exported": 0.0, "appliances": [{"id": 0, "energy": 28.4}]}])
            rows = await client.async_get_energy_usage(12345, "2026-11-24", "2026-11-24")
        assert rows[0].consumed == 33.5 and rows[0].appliances == {0: 28.4}


def test_realtime_parse():
    d = api.RealtimeData.from_payload({"epoch": 1, "consumption": 693.8, "solar": 0.0, "devices": [{"w": 1}]})
    assert d.consumption == 693.8 and d.devices == [{"w": 1}]


async def test_energy_usage_uses_inclusive_dates(hass, aioclient_mock):
    s = async_get_clientsession(hass)
    client = api.SEMonitorClient(s, api.SEMonitorAuth(s, "r", make_jwt()))
    aioclient_mock.get(f"{api.API_URL}sites/12345/energy_usage", json=[])
    await client.async_get_energy_usage(12345, "2026-11-25", "2026-11-25")
    url = str(aioclient_mock.mock_calls[-1][1])
    assert "start_date=2026-11-25" in url and "end_date=2026-11-25" in url and "from_date" not in url


async def test_http_error_includes_detail(hass, aioclient_mock):
    s = async_get_clientsession(hass)
    client = api.SEMonitorClient(s, api.SEMonitorAuth(s, "r", make_jwt()))
    aioclient_mock.get(f"{api.API_URL}user", status=400, text="start_date cannot be after end_date")
    with pytest.raises(api.SEMonitorConnectionError, match="start_date cannot be after"):
        await client.async_get_user()


def test_appliance_parsing_and_device_power():
    from .conftest import APPLIANCES
    apps = [api.Appliance.from_api(x) for x in APPLIANCES]
    assert [a.name for a in apps] == ["Fridge", "Washer", "Always On", "mains"]
    assert [a.is_device for a in apps] == [True, True, True, False]
    rt = api.RealtimeData.from_payload({"devices": [{"appliance_id": 101, "w": 42.0}, {"appliance_id": 102, "w": None}]})
    assert rt.device_power(101) == 42.0 and rt.device_power(102) == 0.0 and rt.device_power(103) is None
