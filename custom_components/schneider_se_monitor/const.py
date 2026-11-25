"""Constants for the Schneider SE-Monitor integration."""

from datetime import timedelta

DOMAIN = "schneider_se_monitor"
MANUFACTURER = "Schneider Electric"

CONF_REFRESH_TOKEN = "refresh_token"
CONF_SITE_ID = "site_id"
CONF_REDIRECT_URL = "redirect_url"

REALTIME_UPDATE_INTERVAL = timedelta(seconds=10)
REALTIME_STALE_AFTER = 180  # seconds without a websocket frame before power goes unavailable
ENERGY_UPDATE_INTERVAL = timedelta(minutes=5)
