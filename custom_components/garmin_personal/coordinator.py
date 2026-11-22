"""Polling coordinator for the Garmin Connect Personal API."""

from __future__ import annotations

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
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SLOW_DATA_INTERVAL,
    WEIGHT_LOOKBACK_DAYS,
)
from .parsing import latest_weigh_in

_LOGGER = logging.getLogger(__name__)

type GarminConfigEntry = ConfigEntry[GarminCoordinator]


@dataclass
class GarminData:
    """Everything the sensors read from, refreshed by the coordinator."""

    day: str | None = None
    summary: dict[str, Any] | None = None
    sleep: dict[str, Any] | None = None
    hrv: dict[str, Any] | None = None
    weigh_in: dict[str, Any] | None = None
    activity: dict[str, Any] | None = None
    token_expires_at: datetime | None = None
    slow_updated_at: datetime | None = None
    errors: dict[str, str] = field(default_factory=dict)


class GarminCoordinator(DataUpdateCoordinator[GarminData]):
    """Fetch the daily summary often and the slower-moving data hourly."""

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

        data = GarminData(
            day=today.isoformat(),
            # Carry the slow data over unless it is due (or the day rolled over,
            # in which case yesterday's sleep/HRV no longer apply).
            sleep=None if new_day else previous.sleep,
            hrv=None if new_day else previous.hrv,
            weigh_in=previous.weigh_in,
            activity=previous.activity,
            slow_updated_at=None if new_day else previous.slow_updated_at,
        )

        try:
            # The daily summary is the one call whose failure fails the update.
            data.summary = await self.api.async_get_daily_summary(today)

            slow_due = (
                data.slow_updated_at is None or now - data.slow_updated_at >= SLOW_DATA_INTERVAL
            )
            if slow_due:
                await self._async_update_slow(data, today)
                data.slow_updated_at = now
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

    async def _async_update_slow(self, data: GarminData, today: date) -> None:
        """Refresh sleep, HRV, weight and activity; one failing doesn't stop the rest."""
        calls = {
            "sleep": lambda: self.api.async_get_daily_sleep(today),
            "hrv": lambda: self.api.async_get_hrv(today),
            "weight": lambda: self.api.async_get_weight_range(
                today - timedelta(days=WEIGHT_LOOKBACK_DAYS), today
            ),
            "activity": self.api.async_get_latest_activity,
        }
        for name, call in calls.items():
            try:
                result = await call()
            except GarminAuthError:
                raise
            except GarminApiError as err:
                # Keep the previous value; the next slow refresh will retry.
                _LOGGER.debug("Garmin %s update failed: %s", name, err)
                data.errors[name] = str(err)
                continue
            if name == "sleep":
                data.sleep = result
            elif name == "hrv":
                data.hrv = result
            elif name == "weight":
                data.weigh_in = latest_weigh_in(result) or data.weigh_in
            elif name == "activity":
                data.activity = result or data.activity

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
