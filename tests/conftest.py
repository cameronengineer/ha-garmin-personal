"""Shared fixtures for Garmin Connect Personal API tests.

All payloads here are synthetic, shaped after Garmin's published OpenAPI spec.
They are not real account data.
"""

from __future__ import annotations

import base64
from collections.abc import Generator
import json
import re
import time
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.garmin_personal.const import (
    BASE_URLS,
    CONF_REGION,
    DOMAIN,
    REGION_GLOBAL,
    TOKEN_PATH,
)

BASE = BASE_URLS[REGION_GLOBAL]
API_KEY = "test-api-key-not-real"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: Any) -> Generator[None]:
    """Let Home Assistant load the integration from custom_components/."""
    yield


def make_jwt(lifetime_s: float = 24 * 3600, **claims: Any) -> str:
    """Build an unsigned JWT the way Garmin's token endpoint returns one."""

    def b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = int(time.time())
    payload = {"iat": now, "exp": now + int(lifetime_s), **claims}
    return f"{b64({'alg': 'RS256', 'typ': 'JWT'})}.{b64(payload)}.signature"


DAILY_SUMMARY: dict[str, Any] = {
    "data": [
        {
            "calendarDate": "2026-11-22",
            "movement": {
                "steps": {"goal": 8000, "value": 6543, "distanceInMeters": 4821.5},
                "floorsAscended": {"goal": 10, "value": 7},
            },
            "calories": {"burnedResting": 1650, "burnedActive": 420, "burnedTotal": 2070},
            "heartRate": {"minValue": 48, "maxValue": 151, "restingValue": 52},
            "intensityMinutes": {"goal": 150, "moderate": 22, "vigorous": 9},
            "stress": {"avgLevel": 31, "maxLevel": 88, "qualifier": "BALANCED"},
            "bodyBattery": {
                "minValue": 18,
                "maxValue": 86,
                "chargedValue": 61,
                "drainedValue": 49,
                "latestValue": 64,
                "dynamicFeedbackEvent": {"feedbackShortType": "RECOVERING_AND_READY"},
            },
            "hydration": {"consumedInFractionalMl": 1250.0},
            "respiration": {"avgValue": 14, "latestValue": 15},
            "pulseOx": {"avgValue": 96, "latestValue": -1},
        }
    ]
}

DAILY_SLEEP: dict[str, Any] = {
    "calendarDate": "2026-11-22",
    "sleepTimeSeconds": 26_100,
    "deepSleepSeconds": 5_400,
    "lightSleepSeconds": 14_400,
    "remSleepSeconds": 6_300,
    "awakeSleepSeconds": 1_200,
    # Spec says "date-time ... in ms": epoch milliseconds.
    "sleepStartTimestampGMT": 1_795_381_200_000,
    "sleepEndTimestampGMT": 1_795_408_500_000,
    "awakeCount": 2,
    "avgHeartRate": 51.5,
    "avgSleepStress": 14.0,
    "averageRespirationValue": 13.5,
    "sleepScores": {
        "overall": {"value": 82, "qualifierKey": "GOOD"},
        "deepPercentage": {"value": 21, "qualifierKey": "EXCELLENT"},
        "remPercentage": {"qualifierKey": "GOOD"},
    },
}

HRV: dict[str, Any] = {
    "hrvSummary": {
        "calendarDate": "2026-11-22",
        "weeklyAvg": 58,
        "lastNightAvg": 61,
        "status": "BALANCED",
        "baseline": {"lowUpper": 45, "balancedLow": 50, "balancedUpper": 66},
    }
}

WEIGHT: dict[str, Any] = {
    "dailyWeightSummaries": [
        {
            "summaryDate": "2026-11-20",
            "allWeightMetrics": [
                {"timestampGMT": 1_795_150_000_000, "weight": 80.4, "bmi": 24.1, "bodyFat": 19.5}
            ],
        },
        {
            "summaryDate": "2026-11-21",
            "allWeightMetrics": [
                {"timestampGMT": 1_795_240_000_000, "weight": 80.1, "bmi": 24.0, "bodyFat": 19.2}
            ],
        },
    ]
}

ACTIVITIES: list[dict[str, Any]] = [
    {
        "activityId": 12345,
        "activityName": "Morning Run",
        "activityType": {"typeKey": "running"},
        "beginTimestamp": 1_795_300_000_000,
        "startTimeGMT": "2026-11-21 06:13:20",
        "duration": 1830.0,
        "distance": 5012.3,
        "calories": 389.0,
        "averageHR": 148.0,
        "maxHR": 171.0,
        "startLatitude": -1.0,
        "startLongitude": 1.0,
    }
]

PROFILE: dict[str, Any] = {"displayName": "test-runner", "measurementSystem": "metric"}


def mock_garmin(
    aioclient_mock: AiohttpClientMocker,
    *,
    token: str | None = None,
    token_status: int = 200,
    summary: Any = DAILY_SUMMARY,
    sleep: Any = DAILY_SLEEP,
    hrv: Any = HRV,
    hrv_status: int = 200,
) -> None:
    """Register canned responses for every endpoint the integration calls."""
    aioclient_mock.post(
        f"{BASE}{TOKEN_PATH}",
        text=token or make_jwt(user_profile_pk=777),
        status=token_status,
    )
    aioclient_mock.get(f"{BASE}/userprofile-service/userprofile/settings", json=PROFILE)
    aioclient_mock.get(f"{BASE}/usersummary-service/v2/dailysummaries", json=summary)
    aioclient_mock.get(f"{BASE}/sleep-service/sleep/dailySleep", json=sleep)
    if hrv_status == 204:
        aioclient_mock.get(re.compile(r"/hrv-service/hrv/\d{4}-"), status=204, text="")
    else:
        aioclient_mock.get(re.compile(r"/hrv-service/hrv/\d{4}-"), json=hrv)
    aioclient_mock.get(re.compile(r"/weight-service/weight/range/"), json=WEIGHT)
    aioclient_mock.get(f"{BASE}/activitylist-service/activities/search/activities", json=ACTIVITIES)


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """A configured Garmin entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Garmin test-runner",
        unique_id=f"{REGION_GLOBAL}:777",
        data={"api_key": API_KEY, CONF_REGION: REGION_GLOBAL},
    )
