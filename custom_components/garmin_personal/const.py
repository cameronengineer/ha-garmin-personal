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
# For a token that ends when the key does, only renew right before it lapses.
TOKEN_FINAL_MARGIN: Final = timedelta(minutes=2)

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

# A heart-rate reading older than this isn't shown as the current value.
HEART_RATE_MAX_AGE: Final = timedelta(hours=1)
# VO2 max, fitness age, heart-rate zones and weight goal change rarely.
DAILY_DATA_INTERVAL: Final = timedelta(hours=6)

# How far back to look for the most recent weigh-in. People without a smart
# scale weigh in rarely, so a short window leaves the sensor empty.
WEIGHT_LOOKBACK_DAYS: Final = 365

# How far back to look for the latest VO2 max reading (only updated after
# qualifying runs, rides or rows).
VO2_LOOKBACK_DAYS: Final = 90

# Recent activities fetched hourly: enough for the latest activity, the
# new-activity event and this week's totals.
RECENT_ACTIVITIES_LIMIT: Final = 50

# Activities calendar: how many to fetch per page when browsing a date range.
CALENDAR_PAGE_SIZE: Final = 100
CALENDAR_MAX_PAGES: Final = 5

# Where the download_activity action saves FIT files (under the HA config dir).
DOWNLOAD_DIR: Final = "garmin_activities"

SERVICE_DOWNLOAD_ACTIVITY: Final = "download_activity"
ATTR_ACTIVITY_ID: Final = "activity_id"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"

EVENT_TYPE_NEW_ACTIVITY: Final = "new_activity"

REQUEST_TIMEOUT_SECONDS: Final = 30
