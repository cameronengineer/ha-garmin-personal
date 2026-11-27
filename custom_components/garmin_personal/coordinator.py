"""Polling coordinator for the Garmin Connect Personal API."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GarminApiError, GarminAuthError, GarminPersonalApi, GarminRateLimitError
from .const import (
    CONF_SCAN_INTERVAL,
    DAILY_DATA_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    RECENT_ACTIVITIES_LIMIT,
    SLOW_DATA_INTERVAL,
    VO2_LOOKBACK_DAYS,
    WEIGHT_LOOKBACK_DAYS,
)
from .parsing import dig, latest_weigh_in

_LOGGER = logging.getLogger(__name__)

type GarminConfigEntry = ConfigEntry[GarminCoordinator]


@dataclass
class GarminData:
    """Everything the entities read from, refreshed by the coordinator."""

    day: str | None = None
    # Every update
    summary: dict[str, Any] | None = None
    heart_rate: dict[str, Any] | None = None
    # Hourly
    sleep: dict[str, Any] | None = None
    hrv: dict[str, Any] | None = None
    weigh_in: dict[str, Any] | None = None
    activities: list[dict[str, Any]] = field(default_factory=list)
    # True once an activities call has succeeded, so an empty list can be told
    # apart from "not fetched yet".
    activities_loaded: bool = False
    body_battery: dict[str, Any] | None = None
    spo2: dict[str, Any] | None = None
    food: dict[str, Any] | None = None
    menstrual: dict[str, Any] | None = None
    # Every few hours
    vo2_max: list[dict[str, Any]] = field(default_factory=list)
    fitness_age: dict[str, Any] | None = None
    hr_zones: list[dict[str, Any]] = field(default_factory=list)
    weight_goal: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None

    token_expires_at: datetime | None = None
    slow_updated_at: datetime | None = None
    daily_updated_at: datetime | None = None
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def activity(self) -> dict[str, Any] | None:
        """The most recent activity."""
        return self.activities[0] if self.activities else None


# Fields that belong to one calendar day and must not survive midnight.
# Sleep and HRV aren't here: they describe "last night", which stays true
# until the next night is synced (the API returns an empty record or 204
# for today until then), so they're replaced only by real new data.
_DAY_FIELDS = ("heart_rate", "body_battery", "spo2", "food", "menstrual")


class GarminCoordinator(DataUpdateCoordinator[GarminData]):
    """Fetch today's summary often, and slower-moving data less often."""

    config_entry: GarminConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: GarminConfigEntry, api: GarminPersonalApi
    ) -> None:
        """Initialise the coordinator."""
        minutes = entry.options.get(CONF_SCAN_INTERVAL)
        interval = timedelta(minutes=minutes) if minutes else DEFAULT_SCAN_INTERVAL
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=interval,
        )
        self.api = api

    async def _async_update_data(self) -> GarminData:
        previous = self.data or GarminData()
        now = dt_util.now()
        today = now.date()
        new_day = previous.day != today.isoformat()

        # Start from the previous data so values not due for a refresh are kept.
        data = GarminData(**{k: getattr(previous, k) for k in previous.__dataclass_fields__})
        data.day = today.isoformat()
        data.errors = {}
        if new_day:
            for name in _DAY_FIELDS:
                setattr(data, name, None)
            data.slow_updated_at = None

        try:
            # Today's summary is the one call whose failure fails the update.
            data.summary = await self.api.async_get_daily_summary(today)
            await self._async_fetch(
                data, {"heart_rate": lambda: self.api.async_get_daily_heart_rate(today)}
            )

            if _due(data.slow_updated_at, now, SLOW_DATA_INTERVAL):
                await self._async_fetch(data, self._slow_calls(today))
                data.slow_updated_at = now
            if _due(data.daily_updated_at, now, DAILY_DATA_INTERVAL):
                await self._async_fetch(data, self._daily_calls(today))
                data.daily_updated_at = now
        except GarminAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GarminRateLimitError as err:
            raise UpdateFailed(f"Garmin rate limit reached: {err}") from err
        except GarminApiError as err:
            raise UpdateFailed(str(err)) from err

        token = self.api.token
        data.token_expires_at = token.expires_at if token else None
        self._check_key_expiry(token.key_expires_at if token else None)
        return data

    def _slow_calls(self, today: date) -> dict[str, Callable[[], Awaitable[Any]]]:
        api = self.api
        return {
            "sleep": lambda: api.async_get_daily_sleep(today),
            "hrv": lambda: api.async_get_hrv(today),
            "weigh_in": lambda: api.async_get_weight_range(
                today - timedelta(days=WEIGHT_LOOKBACK_DAYS), today
            ),
            "activities": lambda: api.async_search_activities(limit=RECENT_ACTIVITIES_LIMIT),
            "body_battery": lambda: api.async_get_body_battery_report(today),
            "spo2": lambda: api.async_get_spo2(today),
            "food": lambda: api.async_get_food_logs(today),
            "menstrual": lambda: api.async_get_menstrual_day(today),
        }

    def _daily_calls(self, today: date) -> dict[str, Callable[[], Awaitable[Any]]]:
        api = self.api
        return {
            "vo2_max": lambda: api.async_get_vo2_max(
                today - timedelta(days=VO2_LOOKBACK_DAYS), today
            ),
            "fitness_age": lambda: api.async_get_fitness_age(today),
            "hr_zones": api.async_get_heart_rate_zones,
            "weight_goal": lambda: api.async_get_weight_goal(today, today),
            "profile": api.async_get_profile_settings,
        }

    async def _async_fetch(
        self, data: GarminData, calls: dict[str, Callable[[], Awaitable[Any]]]
    ) -> None:
        """Run each call; one failing keeps its previous value and doesn't stop the rest."""
        for name, call in calls.items():
            try:
                result = await call()
            except (GarminAuthError, GarminRateLimitError):
                # Auth: needs the user. Rate limit: stop and back off until the
                # next update rather than firing the rest of the batch.
                raise
            except GarminApiError as err:
                _LOGGER.debug("Garmin %s update failed: %s", name, err)
                data.errors[name] = str(err)
                continue
            if name == "sleep" and not dig(result, "sleepTimeSeconds"):
                # Empty skeleton (not synced yet): keep the last real night.
                result = data.sleep
            elif name == "hrv" and not dig(result, "hrvSummary"):
                result = data.hrv
            elif name == "weigh_in":
                # An empty window shouldn't erase a weigh-in we already know about.
                result = latest_weigh_in(result) or data.weigh_in
            elif name == "activities":
                data.activities_loaded = True
                result = result or data.activities
            setattr(data, name, result)

    def _check_key_expiry(self, key_expires_at: datetime | None) -> None:
        """Raise a repair issue once Garmin tells us the API key is about to end.

        This only works inside the key's final 24 hours (before that, tokens
        look normal). Garmin also emails about a week before expiry.
        """
        issue_id = f"key_expiring_{self.config_entry.entry_id}"
        if key_expires_at is None:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="key_expiring",
            translation_placeholders={
                "title": self.config_entry.title,
                "expires": dt_util.as_local(key_expires_at).strftime("%Y-%m-%d %H:%M"),
            },
        )


def _due(last: datetime | None, now: datetime, interval: timedelta) -> bool:
    return last is None or now - last >= interval
