"""A 'new activity' event entity: fires once per newly synced workout."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import EVENT_TYPE_NEW_ACTIVITY, RECENT_ACTIVITIES_LIMIT
from .coordinator import GarminConfigEntry, GarminCoordinator
from .entity import account_id, device_info
from .parsing import activity_start, activity_summary

PARALLEL_UPDATES = 0
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GarminConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the new-activity event entity."""
    async_add_entities([GarminNewActivityEvent(entry.runtime_data)])


class GarminNewActivityEvent(CoordinatorEntity[GarminCoordinator], EventEntity):
    """Fires `new_activity` when a workout appears in Garmin Connect.

    Activities already in Garmin when Home Assistant starts don't fire; only
    ones that show up afterwards do.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "new_activity"
    _attr_event_types = [EVENT_TYPE_NEW_ACTIVITY]

    def __init__(self, coordinator: GarminCoordinator) -> None:
        """Initialise the event entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{account_id(entry)}_new_activity"
        self._attr_device_info = device_info(entry)
        self._seen: set[int] | None = None
        # Start of the oldest activity in the last full window we were given.
        self._window_start: datetime | None = None
        self._remember(fire=False)

    def _remember(self, *, fire: bool) -> None:
        data = self.coordinator.data
        if data is None or not data.activities_loaded:
            # No successful activities call yet (it may have failed while the
            # rest of the update worked): wait, or the first list we get would
            # all look new.
            return
        current = {a["activityId"]: a for a in data.activities if a.get("activityId")}
        if self._seen is None:
            # First look: everything already there counts as seen.
            new = []
            self._seen = set(current)
        else:
            new = [a for aid, a in current.items() if aid not in self._seen]
            self._seen.update(current)
        # An unseen activity older than the previous full window isn't new: it
        # slid into view because a more recent one was deleted.
        window_start = self._window_start
        if window_start is not None:
            new = [a for a in new if (activity_start(a) or window_start) >= window_start]
        starts = [start for a in current.values() if (start := activity_start(a))]
        full = len(data.activities) >= RECENT_ACTIVITIES_LIMIT
        self._window_start = min(starts) if full and starts else None
        if not fire:
            return
        # Oldest first, so automations see them in the order they happened.
        # Write state after each one so every activity is its own event.
        for activity in sorted(new, key=lambda a: activity_start(a) or _EPOCH):
            self._trigger_event(EVENT_TYPE_NEW_ACTIVITY, activity_summary(activity))
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._remember(fire=True)
        super()._handle_coordinator_update()
