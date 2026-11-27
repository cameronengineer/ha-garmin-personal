"""The Garmin Connect Personal API integration."""

from __future__ import annotations

from homeassistant.const import CONF_API_KEY, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import GarminPersonalApi
from .const import BASE_URLS, CONF_REGION, DOMAIN, REGION_GLOBAL
from .coordinator import GarminConfigEntry, GarminCoordinator
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.EVENT, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions once, independent of config entries."""
    async_setup_services(hass)
    return True


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
    # The first refresh only fetched today's summary. Sleep, activities and the
    # other slower data (up to 14 more requests) load here, without holding up
    # the entry or Home Assistant's startup.
    entry.async_create_background_task(
        hass, coordinator.async_load_remaining(), f"{DOMAIN} load remaining data"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GarminConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GarminConfigEntry) -> None:
    """Clear this account's repair notice when the integration is removed."""
    ir.async_delete_issue(hass, DOMAIN, f"key_expiring_{entry.entry_id}")
