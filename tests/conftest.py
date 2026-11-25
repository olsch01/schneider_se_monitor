"""Fixtures."""
import base64, json, time
import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def make_jwt(exp: float | None = None) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'none'})}.{enc({'exp': int(exp or time.time() + 3600)})}.sig"


SITE = {
    "site_id": 12345, "site_name": "Test Home", "timezone": "America/New_York", "has_solar": False,
    "devices": [{"device_type": "METER", "manufacturer": "Schneider Electric",
                 "model": "Schneider Energy Monitor", "serial_number": "W000000001"}],
}

APPLIANCES = [
    {"appliance_id": 101, "assignment": "load_refrigerator__1", "display_name": "Fridge"},
    {"appliance_id": 102, "assignment": "load_other__1", "display_name": "Washer"},
    {"appliance_id": 103, "assignment": "always_on__1", "display_name": "Rest of Home"},
    {"appliance_id": 104, "assignment": "consumption__1", "display_name": "mains"},
]
