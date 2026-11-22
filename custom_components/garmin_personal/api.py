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

from .const import KEY_EXPIRY_DETECTION, REQUEST_TIMEOUT_SECONDS, TOKEN_PATH, TOKEN_REFRESH_MARGIN

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
            if (
                not force
                and self._token is not None
                and not self._token.expires_within(TOKEN_REFRESH_MARGIN)
            ):
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

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a path and return decoded JSON (None for an empty body)."""
        for attempt in (1, 2):
            token = await self.async_get_token(force=attempt == 2)
            try:
                async with self._session.get(
                    f"{self._base_url}{path}",
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {token.value}",
                    },
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
            if status == 204 or status == 404 or not body:
                # No data for that day (e.g. HRV before the first night's sleep).
                return None
            if status >= 400:
                raise GarminConnectionError(f"{path} returned HTTP {status}")
            try:
                return json.loads(body)
            except ValueError as err:
                raise GarminConnectionError(f"{path} returned invalid JSON") from err
        raise GarminForbiddenError(f"Garmin refused {path}")  # pragma: no cover

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
        data = await self._get(
            "/activitylist-service/activities/search/activities",
            {"start": 0, "limit": 1},
        )
        if isinstance(data, list) and data:
            return data[0]
        return None
