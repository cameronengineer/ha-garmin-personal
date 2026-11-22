"""Tests for the config, reauth and options flows."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.garmin_personal.const import (
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    DOMAIN,
    REGION_GLOBAL,
    TOKEN_PATH,
)

from .conftest import API_KEY, BASE, DAILY_SUMMARY, make_jwt, mock_garmin


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A working key creates an entry named after the account."""
    mock_garmin(aioclient_mock)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_key": f" {API_KEY} ", CONF_REGION: REGION_GLOBAL}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garmin test-runner"
    assert result["data"] == {"api_key": API_KEY, CONF_REGION: REGION_GLOBAL}
    assert result["result"].unique_id == "global:777"


async def test_unique_id_falls_back_to_display_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With no account claim in the token, the display name is used."""
    mock_garmin(aioclient_mock, token=make_jwt())
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_key": API_KEY, CONF_REGION: REGION_GLOBAL}
    )
    await hass.async_block_till_done()
    assert result["result"].unique_id == "global:test-runner"


async def test_unique_id_from_summary_profile_pk(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With no account claim, the documented userProfilePk is used; sub is ignored."""
    summary = {"data": [{**DAILY_SUMMARY["data"][0], "userProfilePk": 4242}]}
    mock_garmin(aioclient_mock, token=make_jwt(sub="key-or-client-id"), summary=summary)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_key": API_KEY, CONF_REGION: REGION_GLOBAL}
    )
    await hass.async_block_till_done()
    assert result["result"].unique_id == "global:4242"


async def test_profile_forbidden_still_sets_up(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused profile endpoint doesn't block setup."""
    aioclient_mock.get(f"{BASE}/userprofile-service/userprofile/settings", status=403, text="")
    mock_garmin(aioclient_mock)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_key": API_KEY, CONF_REGION: REGION_GLOBAL}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Garmin Connect"


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, "invalid_auth"), (503, "cannot_connect")],
)
async def test_user_flow_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int, error: str
) -> None:
    """Errors are shown on the form, and the user can retry."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", status=status)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_key": "bad", CONF_REGION: REGION_GLOBAL}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_key": API_KEY, CONF_REGION: REGION_GLOBAL}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A malformed token is an 'unknown' error."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text="not-a-jwt")
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_key": API_KEY, CONF_REGION: REGION_GLOBAL}
    )
    assert result["errors"] == {"base": "unknown"}


async def test_already_configured(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The same account can't be added twice."""
    config_entry.add_to_hass(hass)
    mock_garmin(aioclient_mock)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"api_key": API_KEY, CONF_REGION: REGION_GLOBAL}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A new key for the same account replaces the old one."""
    config_entry.add_to_hass(hass)
    mock_garmin(aioclient_mock)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "brand-new-key"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["api_key"] == "brand-new-key"


async def test_reauth_wrong_account(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A key for a different Garmin account is refused."""
    config_entry.add_to_hass(hass)
    mock_garmin(aioclient_mock, token=make_jwt(user_profile_pk=999))
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "someone-elses-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert config_entry.data["api_key"] == API_KEY


async def test_reauth_bad_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A bad key during reauth shows an error."""
    config_entry.add_to_hass(hass)
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", status=401)
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"api_key": "still-bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_options_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The polling interval can be changed."""
    mock_garmin(aioclient_mock)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options == {CONF_SCAN_INTERVAL: 30}
    coordinator = config_entry.runtime_data
    assert coordinator.update_interval.total_seconds() == 30 * 60
