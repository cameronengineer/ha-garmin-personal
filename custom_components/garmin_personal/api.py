"""Async client for the Garmin Connect Personal API.

Endpoints and the auth flow follow Garmin's published OpenAPI spec and FAQ
(https://developer.garmin.com/gc-developer-program/personal/) and the behaviour
of Garmin's official Python client (garmin/connect-personal-api-python,
Apache-2.0): the token endpoint takes ``Authorization: ApiKey <key>`` and
returns the access token as a plain-text JWT whose ``exp`` claim is the expiry.
There are no refresh tokens; a new access token is simply requested again.

This module has no Home Assistant imports so it can be tested and reused on
its own.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import json
import logging
from typing import Any

import aiohttp

from .const import (
    KEY_EXPIRY_DETECTION,
    REQUEST_TIMEOUT_SECONDS,
    TOKEN_FINAL_MARGIN,
    TOKEN_PATH,
    TOKEN_REFRESH_MARGIN,
)

_LOGGER = logging.getLogger(__name__)


class GarminApiError(Exception):
    """Base error for the Garmin Personal API."""


class GarminAuthError(GarminApiError):
    """The API key is invalid, expired or revoked."""


class GarminConnectionError(GarminApiError):
    """Garmin could not be reached, or answered with a server error."""


class GarminRateLimitError(GarminConnectionError):
    """Garmin asked us to slow down (HTTP 429)."""


class GarminForbiddenError(GarminApiError):
    """The key works, but Garmin refused this particular endpoint.

    Deliberately *not* an auth error: a dead key fails at the token endpoint.
    Treating a per-endpoint refusal as auth failure would trigger a reauth
    that a new key can't fix, looping the user forever.
    """


@dataclass(frozen=True, slots=True)
class AccessToken:
    """A bearer token and when it stops working."""

    value: str
    expires_at: datetime
    issued_at: datetime
    claims: dict[str, Any]

    @property
    def key_expires_at(self) -> datetime | None:
        """When the API key itself expires, if this token reveals it.

        Tokens last 24 h or until the key expires, whichever is first, so a
        token issued with a noticeably shorter life ends when the key does.
        """
        if self.expires_at - self.issued_at < KEY_EXPIRY_DETECTION:
            return self.expires_at
        return None

    def expires_within(self, margin: timedelta, now: datetime | None = None) -> bool:
        """Return True if the token is expired or will be within ``margin``."""
        now = now or datetime.now(UTC)
        return self.expires_at - now <= margin


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Return a JWT's payload claims without verifying the signature.

    We only need ``exp`` (and an account identifier, if present) for local
    bookkeeping; Garmin verifies the token on every request.
    """
    try:
        payload = token.split(".")[1]
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(decoded)
    except (IndexError, ValueError, UnicodeDecodeError) as err:
        raise GarminApiError("Token endpoint did not return a JWT") from err
    if not isinstance(claims, dict) or "exp" not in claims:
        raise GarminApiError("Access token has no exp claim")
    return claims


def _iso(day: date) -> str:
    return day.isoformat()


class GarminPersonalApi:
    """Minimal async wrapper around the Personal API endpoints we use."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        api_key: str,
        base_url: str,
    ) -> None:
        """Initialise with a shared aiohttp session."""
        self._session = session
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._token: AccessToken | None = None
        self._token_lock = asyncio.Lock()

    @property
    def token(self) -> AccessToken | None:
        """The current access token, if one has been fetched."""
        return self._token

    # ------------------------------------------------------------------ auth

    async def async_get_token(self, *, force: bool = False) -> AccessToken:
        """Return a valid access token, exchanging the API key if needed."""
        async with self._token_lock:
            if not force and self._token is not None:
                # Refresh an hour early normally. A token capped by the key's
                # own expiry can't be renewed past it, so keep using it until
                # it's actually about to lapse instead of re-fetching every call.
                margin = (
                    TOKEN_FINAL_MARGIN
                    if self._token.key_expires_at is not None
                    else TOKEN_REFRESH_MARGIN
                )
                if not self._token.expires_within(margin):
                    return self._token

            url = f"{self._base_url}{TOKEN_PATH}"
            _LOGGER.debug("Requesting a new Garmin access token")
            try:
                async with self._session.post(
                    url,
                    headers={"Accept": "*/*", "Authorization": f"ApiKey {self._api_key}"},
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    text = await resp.text()
                    status = resp.status
            except (aiohttp.ClientError, TimeoutError) as err:
                raise GarminConnectionError(f"Could not reach Garmin: {err}") from err

            if status in (400, 401, 403):
                # Garmin answers a wrong, expired or revoked key with a 4xx here.
                raise GarminAuthError(f"Garmin rejected the API key (HTTP {status})")
            if status == 429:
                raise GarminRateLimitError("Garmin rate limit hit while fetching a token")
            if status >= 400:
                raise GarminConnectionError(f"Token endpoint returned HTTP {status}")

            value = text.strip()
            # Be tolerant if Garmin ever wraps the token in JSON.
            if value.startswith("{"):
                try:
                    body = json.loads(value)
                    value = str(body.get("access_token") or body.get("accessToken") or "")
                except ValueError:
                    pass
            claims = decode_jwt_claims(value)
            issued = claims.get("iat")
            self._token = AccessToken(
                value=value,
                expires_at=datetime.fromtimestamp(float(claims["exp"]), UTC),
                issued_at=(
                    datetime.fromtimestamp(float(issued), UTC)
                    if isinstance(issued, (int, float))
                    else datetime.now(UTC)
                ),
                claims=claims,
            )
            return self._token

    # ------------------------------------------------------------------ http

    async def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> tuple[int, bytes]:
        """GET a path with a valid token; return (status, body) for a usable response."""
        for attempt in (1, 2):
            token = await self.async_get_token(force=attempt == 2)
            try:
                async with self._session.get(
                    f"{self._base_url}{path}",
                    params=params,
                    headers={"Accept": accept, "Authorization": f"Bearer {token.value}"},
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    status = resp.status
                    body = await resp.read()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise GarminConnectionError(f"Could not reach Garmin: {err}") from err

            if status == 401 and attempt == 1:
                # Token revoked or clock skew: try once more with a fresh token.
                # A key that is actually dead fails at the token endpoint instead.
                continue
            if status in (401, 403):
                raise GarminForbiddenError(f"Garmin refused {path} (HTTP {status})")
            if status == 429:
                raise GarminRateLimitError(f"Garmin rate limit hit on {path}")
            if status >= 400 and status != 404:
                raise GarminConnectionError(f"{path} returned HTTP {status}")
            return status, body
        raise GarminForbiddenError(f"Garmin refused {path}")  # pragma: no cover

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a path and return decoded JSON (None for an empty body)."""
        status, body = await self._request(path, params)
        if status in (204, 404) or not body:
            # No data for that day (e.g. HRV before the first night's sleep).
            return None
        try:
            return json.loads(body)
        except ValueError as err:
            raise GarminConnectionError(f"{path} returned invalid JSON") from err

    # ------------------------------------------------------------- endpoints

    async def async_get_profile_settings(self) -> dict[str, Any] | None:
        """User profile settings (display name, units, locale)."""
        return await self._get("/userprofile-service/userprofile/settings")

    async def async_get_daily_summary(self, day: date) -> dict[str, Any] | None:
        """The daily summary for one day: steps, HR, stress, Body Battery..."""
        data = await self._get(
            "/usersummary-service/v2/dailysummaries",
            {"startDate": _iso(day), "endDate": _iso(day)},
        )
        if isinstance(data, dict):
            items = data.get("data") or []
            if items:
                return items[-1]
        return None

    async def async_get_daily_sleep(self, day: date) -> dict[str, Any] | None:
        """The sleep that ended on ``day`` (i.e. last night, for today)."""
        return await self._get("/sleep-service/sleep/dailySleep", {"date": _iso(day)})

    async def async_get_hrv(self, day: date) -> dict[str, Any] | None:
        """Overnight HRV summary for ``day``."""
        return await self._get(f"/hrv-service/hrv/{_iso(day)}")

    async def async_get_weight_range(self, start: date, end: date) -> dict[str, Any] | None:
        """All weigh-ins in an inclusive date range."""
        return await self._get(
            f"/weight-service/weight/range/{_iso(start)}/{_iso(end)}",
            {"includeAll": "true"},
        )

    async def async_get_latest_activity(self) -> dict[str, Any] | None:
        """The most recent recorded activity, if any."""
        data = await self.async_search_activities(limit=1)
        return data[0] if data else None

    async def async_search_activities(
        self,
        *,
        start: int = 0,
        limit: int = 20,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[dict[str, Any]]:
        """Activity summaries, newest first, optionally limited to a date range."""
        params: dict[str, Any] = {"start": start, "limit": limit}
        if start_date is not None:
            params["startDate"] = _iso(start_date)
        if end_date is not None:
            params["endDate"] = _iso(end_date)
        data = await self._get("/activitylist-service/activities/search/activities", params)
        return data if isinstance(data, list) else []

    async def async_get_activity(self, activity_id: int) -> dict[str, Any] | None:
        """Full detail for one activity."""
        return await self._get(f"/activity-service/activity/{int(activity_id)}")

    async def async_get_activity_workouts(self, activity_id: int) -> list[dict[str, Any]]:
        """Structured workouts attached to an activity (usually none)."""
        data = await self._get(f"/activity-service/activity/{int(activity_id)}/workouts")
        return data if isinstance(data, list) else []

    async def async_download_activity(self, activity_id: int) -> bytes:
        """The original activity file, as ZIP bytes (contains the .fit file).

        Garmin answers 406 to ``Accept: application/octet-stream`` alone, so ask
        for the ZIP types explicitly (verified live).
        """
        if int(activity_id) <= 0:
            raise GarminApiError("activity_id must be a positive integer")
        status, body = await self._request(
            f"/download-service/files/activity/{int(activity_id)}",
            accept="application/zip, application/x-zip-compressed, application/octet-stream",
        )
        if status == 404 or not body:
            raise GarminApiError(f"No original file for activity {activity_id}")
        return body

    async def async_get_daily_heart_rate(self, day: date) -> dict[str, Any] | None:
        """All-day heart rate: min/max/resting plus [timestamp_ms, bpm] readings."""
        return await self._get("/wellness-service/wellness/dailyHeartRate", {"date": _iso(day)})

    async def async_get_body_battery_report(self, day: date) -> dict[str, Any] | None:
        """Body Battery readings through the day and what charged/drained it."""
        data = await self._get(
            "/wellness-service/wellness/bodyBattery/reports/daily",
            {"startDate": _iso(day), "endDate": _iso(day)},
        )
        return data[-1] if isinstance(data, list) and data else None

    async def async_get_spo2(self, day: date) -> dict[str, Any] | None:
        """Daily Pulse Ox (SpO2) summary, including sleep and 7-day averages."""
        return await self._get(f"/wellness-service/wellness/daily/spo2acclimation/{_iso(day)}")

    async def async_get_vo2_max(self, start: date, end: date) -> list[dict[str, Any]]:
        """Daily VO2 max entries (generic/running, cycling, rowing) in a range."""
        data = await self._get(f"/metrics-service/metrics/maxmet/daily/{_iso(start)}/{_iso(end)}")
        return data if isinstance(data, list) else []

    async def async_get_fitness_age(self, day: date) -> dict[str, Any] | None:
        """Fitness age, achievable fitness age and the factors behind them."""
        return await self._get(f"/fitnessage-service/fitnessage/v2/{_iso(day)}")

    async def async_get_heart_rate_zones(self) -> list[dict[str, Any]]:
        """Configured heart-rate zones, one entry per sport (DEFAULT first)."""
        data = await self._get("/biometric-service/heartRateZones/v2")
        return data if isinstance(data, list) else []

    async def async_get_food_logs(self, day: date) -> dict[str, Any] | None:
        """Nutrition logged in Garmin Connect for one day."""
        return await self._get(f"/nutrition-service/food/logs/{_iso(day)}")

    async def async_get_weight_goal(self, start: date, end: date) -> dict[str, Any] | None:
        """Effective weight goals (values in grams) for a date range."""
        return await self._get(
            f"/goal-service/goal/user/effective/weightgoal/{_iso(start)}/{_iso(end)}"
        )

    async def async_get_menstrual_day(self, day: date) -> dict[str, Any] | None:
        """Menstrual-cycle day view (cycle day, phase, predictions) for ``day``."""
        return await self._get(
            f"/periodichealth-service/menstrualcycle/dayview/{_iso(day)}",
            {"todayDate": _iso(day)},
        )
