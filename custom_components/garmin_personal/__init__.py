"""The Garmin Connect Personal API integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import GarminPersonalApi
from .const import BASE_URLS, CONF_REGION, REGION_GLOBAL
from .coordinator import GarminConfigEntry, GarminCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: GarminConfigEntry) -> bool:
    """Set up Garmin Connect Personal API from a config entry."""
    api = GarminPersonalApi(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        BASE_URLS[entry.data.get(CONF_REGION, REGION_GLOBAL)],
    )
    coordinator = GarminCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GarminConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
