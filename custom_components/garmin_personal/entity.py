"""Shared entity helpers for the Garmin Connect Personal API integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN


def account_id(entry: ConfigEntry) -> str:
    """Stable identifier for the Garmin account behind a config entry."""
    return entry.unique_id or entry.entry_id


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """One service device per Garmin account."""
    return DeviceInfo(
        identifiers={(DOMAIN, account_id(entry))},
        name=entry.title,
        manufacturer="Garmin",
        model="Garmin Connect account",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://connect.garmin.com/",
    )
