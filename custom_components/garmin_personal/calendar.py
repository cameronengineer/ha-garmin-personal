"""Garmin activities as a Home Assistant calendar."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import GarminApiError
from .const import CALENDAR_MAX_PAGES, CALENDAR_PAGE_SIZE
from .coordinator import GarminConfigEntry, GarminCoordinator
from .entity import account_id, device_info
from .parsing import activity_seconds, activity_start, activity_summary

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# Labels for the event description, in display order.
_DESCRIPTION_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("activity_type", "Type", ""),
    ("duration_minutes", "Duration", " min"),
    ("distance_km", "Distance", " km"),
    ("calories", "Calories", " kcal"),
    ("average_hr", "Average HR", " bpm"),
    ("max_hr", "Max HR", " bpm"),
    ("elevation_gain_m", "Elevation gain", " m"),
    ("aerobic_training_effect", "Aerobic training effect", ""),
    ("anaerobic_training_effect", "Anaerobic training effect", ""),
    ("training_load", "Training load", ""),
    ("body_battery_impact", "Body Battery impact", ""),
)


def activity_to_event(activity: dict[str, Any]) -> CalendarEvent | None:
    """Turn one Garmin activity into a calendar event."""
    start = activity_start(activity)
    if start is None:
        return None
    seconds = activity_seconds(activity) or 60
    summary = activity_summary(activity)
    lines = []
    for key, label, unit in _DESCRIPTION_FIELDS:
        value = summary.get(key)
        if value is None:
            continue
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        lines.append(f"{label}: {str(value).replace('_', ' ')}{unit}")
    return CalendarEvent(
        start=start,
        end=start + timedelta(seconds=seconds),
        summary=activity.get("activityName") or summary.get("activity_type") or "Activity",
        description="\n".join(lines) or None,
        uid=str(activity.get("activityId")) if activity.get("activityId") else None,
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GarminConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the activities calendar."""
    async_add_entities([GarminActivitiesCalendar(entry.runtime_data)])


class GarminActivitiesCalendar(CoordinatorEntity[GarminCoordinator], CalendarEntity):
    """Every recorded activity, browsable in the Calendar panel."""

    _attr_has_entity_name = True
    _attr_translation_key = "activities"

    def __init__(self, coordinator: GarminCoordinator) -> None:
        """Initialise the calendar."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{account_id(entry)}_activities"
        self._attr_device_info = device_info(entry)

    @property
    def event(self) -> CalendarEvent | None:
        """The most recent activity (the calendar is 'on' while it's happening)."""
        latest = self.coordinator.data.activity if self.coordinator.data else None
        return activity_to_event(latest) if latest else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Activities overlapping a date range, fetched from Garmin on demand."""
        # Garmin filters by local calendar date; widen by a day either side and
        # trim precisely below, so time-zone edges don't drop activities.
        first = dt_util.as_local(start_date).date() - timedelta(days=1)
        last = dt_util.as_local(end_date).date() + timedelta(days=1)
        activities: list[dict[str, Any]] = []
        try:
            for page in range(CALENDAR_MAX_PAGES):
                batch = await self.coordinator.api.async_search_activities(
                    start=page * CALENDAR_PAGE_SIZE,
                    limit=CALENDAR_PAGE_SIZE,
                    start_date=first,
                    end_date=last,
                )
                activities.extend(batch)
                if len(batch) < CALENDAR_PAGE_SIZE:
                    break
            else:
                _LOGGER.warning(
                    "Showing only the newest %s Garmin activities between %s and %s; "
                    "narrow the calendar range to see older ones",
                    len(activities),
                    first,
                    last,
                )
        except GarminApiError as err:
            raise HomeAssistantError(f"Couldn't load Garmin activities: {err}") from err

        events = []
        for activity in activities:
            event = activity_to_event(activity)
            if event and event.end > start_date and event.start < end_date:
                events.append(event)
        events.sort(key=lambda e: e.start)
        return events
