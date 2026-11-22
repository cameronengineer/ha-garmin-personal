"""Config flow for the Garmin Connect Personal API integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import (
    GarminApiError,
    GarminAuthError,
    GarminConnectionError,
    GarminForbiddenError,
    GarminPersonalApi,
)
from .const import (
    BASE_URLS,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    KEY_SETTINGS_URL,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
    REGION_CHINA,
    REGION_GLOBAL,
)
from .coordinator import GarminConfigEntry

_LOGGER = logging.getLogger(__name__)

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

API_KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
REGION_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=[REGION_GLOBAL, REGION_CHINA],
        mode=SelectSelectorMode.LIST,
        translation_key="region",
    )
)

# Claims that might identify the Garmin account inside the access token. Not
# "sub": it could identify the key rather than the account, which would make
# every key renewal look like a different account and fail reauth.
_ACCOUNT_CLAIMS = ("user_profile_pk", "userProfilePk", "garmin_guid", "garminGuid")


async def _validate(flow: ConfigFlow, api_key: str, region: str) -> tuple[str, str]:
    """Check the key works. Return (unique_id, title); raise on failure."""
    api = GarminPersonalApi(async_get_clientsession(flow.hass), api_key, BASE_URLS[region])
    token = await api.async_get_token()
    try:
        profile = await api.async_get_profile_settings() or {}
    except GarminForbiddenError:
        profile = {}
    display_name = profile.get("displayName")

    # Prefer a stable account identifier over the (user-editable) display name:
    # a claim in the token, then the documented userProfilePk on today's summary.
    unique_id = next((str(token.claims[c]) for c in _ACCOUNT_CLAIMS if token.claims.get(c)), None)
    if unique_id is None:
        try:
            summary = await api.async_get_daily_summary(dt_util.now().date()) or {}
        except GarminForbiddenError:
            summary = {}
        if summary.get("userProfilePk"):
            unique_id = str(summary["userProfilePk"])
    if unique_id is None:
        unique_id = str(display_name or "account")
    # Garmin gives accounts without a chosen display name a UUID; that makes a
    # poor device name and entity IDs like sensor.garmin_b3f19d41_..._steps.
    friendly = display_name and not _UUID_RE.fullmatch(str(display_name))
    title = f"Garmin {display_name}" if friendly else "Garmin Connect"
    return f"{region}:{unique_id}", title


class GarminPersonalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Garmin Connect Personal API."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the Personal API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            region = user_input.get(CONF_REGION, REGION_GLOBAL)
            try:
                unique_id, title = await _validate(self, user_input[CONF_API_KEY], region)
            except GarminAuthError:
                errors["base"] = "invalid_auth"
            except GarminConnectionError:
                errors["base"] = "cannot_connect"
            except GarminApiError:
                _LOGGER.exception("Unexpected response from Garmin")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=title,
                    data={CONF_API_KEY: user_input[CONF_API_KEY].strip(), CONF_REGION: region},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): API_KEY_SELECTOR,
                    vol.Required(CONF_REGION, default=REGION_GLOBAL): REGION_SELECTOR,
                }
            ),
            errors=errors,
            description_placeholders={"key_url": KEY_SETTINGS_URL},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The key expired or was revoked."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new key and swap it in."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            region = entry.data.get(CONF_REGION, REGION_GLOBAL)
            try:
                unique_id, _ = await _validate(self, user_input[CONF_API_KEY], region)
            except GarminAuthError:
                errors["base"] = "invalid_auth"
            except GarminConnectionError:
                errors["base"] = "cannot_connect"
            except GarminApiError:
                _LOGGER.exception("Unexpected response from Garmin")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_API_KEY: user_input[CONF_API_KEY].strip()},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): API_KEY_SELECTOR}),
            errors=errors,
            description_placeholders={"key_url": KEY_SETTINGS_URL, "title": entry.title},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: GarminConfigEntry) -> GarminOptionsFlow:
        """Return the options flow."""
        return GarminOptionsFlow()


class GarminOptionsFlow(OptionsFlowWithReload):
    """Let the user change the polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL, int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=MAX_SCAN_INTERVAL_MINUTES,
                            step=1,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
