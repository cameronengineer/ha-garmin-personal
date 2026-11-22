"""Diagnostics support for the Garmin Connect Personal API integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant

from .coordinator import GarminConfigEntry

# Secrets, plus anything that identifies the person or where they are.
TO_REDACT = {
    CONF_API_KEY,
    "userProfilePk",
    "userProfilePK",
    "userProfileId",
    "ownerId",
    "ownerDisplayName",
    "ownerFullName",
    "ownerProfileImageUrlSmall",
    "ownerProfileImageUrlMedium",
    "ownerProfileImageUrlLarge",
    "displayName",
    "uuid",
    "activityUUID",
    "deviceId",
    "startLatitude",
    "startLongitude",
    "endLatitude",
    "endLongitude",
    "locationName",
    "userNote",
    "kidSleepNote",
    "unique_id",
    "title",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GarminConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = asdict(coordinator.data) if coordinator.data else None
    return {
        "entry": async_redact_data(
            {"data": dict(entry.data), "options": dict(entry.options), "title": entry.title},
            TO_REDACT,
        ),
        "last_update_success": coordinator.last_update_success,
        "update_interval_seconds": coordinator.update_interval.total_seconds()
        if coordinator.update_interval
        else None,
        "data": async_redact_data(data, TO_REDACT) if data else None,
    }
