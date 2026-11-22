"""Constants for the Garmin Connect Personal API integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "garmin_personal"

CONF_REGION: Final = "region"
REGION_GLOBAL: Final = "global"
REGION_CHINA: Final = "china"

BASE_URLS: Final = {
    REGION_GLOBAL: "https://personal.connectapi.garmin.com",
    REGION_CHINA: "https://personal.connectapi.garmin.cn",
}

# Where to manage Personal API keys (shown in the config flow).
KEY_SETTINGS_URL: Final = "https://connect.garmin.com/app/settings/personalApiToken"

TOKEN_PATH: Final = "/personalapitoken-service/personal-api/personal-api-key/token"

# Access tokens live for 24 h (or until the key expires). Refresh an hour early,
# the same window Garmin's own Python client uses.
TOKEN_REFRESH_MARGIN: Final = timedelta(hours=1)

# A token issued with a lifetime well short of 24 h means the API key itself
# expires when the token does (Garmin: "valid for 24 hours or until the
# Personal API key expires, whichever occurs first").
KEY_EXPIRY_DETECTION: Final = timedelta(hours=23)

# Polling. Garmin publishes no rate limits, so stay conservative: the daily
# summary (steps, heart rate, Body Battery, stress...) changes whenever the watch
# syncs; sleep, HRV, weight and activities change a few times a day at most.
CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=15)
MIN_SCAN_INTERVAL_MINUTES: Final = 5
MAX_SCAN_INTERVAL_MINUTES: Final = 240
SLOW_DATA_INTERVAL: Final = timedelta(hours=1)

# How far back to look for the most recent weigh-in.
WEIGHT_LOOKBACK_DAYS: Final = 30

REQUEST_TIMEOUT_SECONDS: Final = 30
