"""Tests for setup, the coordinator, sensors, repairs and diagnostics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.garmin_personal.const import DOMAIN, TOKEN_PATH
from custom_components.garmin_personal.sensor import SENSORS

from .conftest import API_KEY, BASE, DAILY_SUMMARY, EMPTY_SLEEP, make_jwt, mock_garmin

PREFIX = "sensor.garmin_test_runner"

pytestmark = pytest.mark.freeze_time("2026-11-22 09:00:00+00:00")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, *, ok: bool = True) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is ok
    await hass.async_block_till_done()


async def _enable_all(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Enable the disabled-by-default sensors and reload."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.disabled_by:
            registry.async_update_entity(ent.entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_sensor_values(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Every sensor is created and the main ones read the right fields."""
    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len(entities) == len(SENSORS)

    await _enable_all(hass, config_entry)

    def state(name: str) -> str:
        st = hass.states.get(f"{PREFIX}_{name}")
        assert st is not None, name
        return st.state

    assert state("steps") == "6543"
    assert hass.states.get(f"{PREFIX}_steps").attributes["goal"] == 8000
    assert state("distance") == "4.8215"
    assert state("resting_heart_rate") == "52"
    assert state("calories_burned") == "2070"
    assert state("average_stress") == "31"
    assert state("stress_level") == "balanced"
    assert state("body_battery") == "64"
    assert hass.states.get(f"{PREFIX}_body_battery").attributes["feedback"] == (
        "RECOVERING_AND_READY"
    )
    assert state("pulse_ox") == "unknown"  # Garmin's -1 means no reading
    assert state("sleep_duration") == "7.25"
    assert state("sleep_score") == "82"
    assert hass.states.get(f"{PREFIX}_sleep_score").attributes["deepPercentage"] == 21
    assert state("deep_sleep") == "1.5"
    assert state("fell_asleep") == datetime.fromtimestamp(1_795_381_200, UTC).isoformat()
    assert state("hrv_last_night") == "61"
    assert state("hrv_status") == "balanced"
    assert state("weight") == "80.1"
    assert state("body_fat") == "19.2"
    assert state("last_activity") == "Morning Run"
    attrs = hass.states.get(f"{PREFIX}_last_activity").attributes
    assert attrs["activity_type"] == "running"
    assert attrs["distance_km"] == 5.01
    assert "startLatitude" not in attrs and "start_latitude" not in attrs

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_bad_key_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """An expired key at startup asks the user for a new one."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", status=401)
    await _setup(hass, config_entry, ok=False)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == SOURCE_REAUTH for f in flows)


async def test_setup_garmin_down_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Garmin being unreachable at startup means 'try again later'."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", status=503)
    await _setup(hass, config_entry, ok=False)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_before_sync_and_unknown_labels(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Before the morning sync: empty sleep skeleton and 'unknown' stress label."""
    summary = {
        "data": [{**DAILY_SUMMARY["data"][0], "stress": {"avgLevel": 22, "qualifier": "unknown"}}]
    }
    mock_garmin(aioclient_mock, summary=summary, sleep=EMPTY_SLEEP, hrv_status=204)
    await _setup(hass, config_entry)
    await _enable_all(hass, config_entry)
    for name in ("sleep_duration", "sleep_score", "fell_asleep", "deep_sleep", "hrv_last_night"):
        assert hass.states.get(f"{PREFIX}_{name}").state == "unknown", name
    assert hass.states.get(f"{PREFIX}_stress_level").state == "unknown"
    assert hass.states.get(f"{PREFIX}_average_stress").state == "22"


async def test_slow_data_is_polled_hourly(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The summary refreshes every interval; sleep/HRV/weight only hourly."""
    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry)

    def count(path: str) -> int:
        return sum(1 for c in aioclient_mock.mock_calls if path in str(c[1]))

    assert count("dailysummaries") == 1
    assert count("dailySleep") == 1

    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert count("dailysummaries") == 2
    assert count("dailySleep") == 1

    freezer.tick(timedelta(minutes=50))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert count("dailysummaries") == 3
    assert count("dailySleep") == 2


async def test_partial_failure_keeps_other_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A failing sleep endpoint doesn't take the summary sensors down."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=make_jwt(user_profile_pk=777))
    aioclient_mock.get(f"{BASE}/sleep-service/sleep/dailySleep", status=500, text="x")
    mock_garmin(aioclient_mock)  # registered after, so the 500 above wins for sleep
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(f"{PREFIX}_steps").state == "6543"
    assert hass.states.get(f"{PREFIX}_sleep_duration").state == "unknown"
    assert "sleep" in config_entry.runtime_data.data.errors


async def test_forbidden_endpoint_does_not_start_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Garmin refusing one endpoint must not ask the user for a new key."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=make_jwt(user_profile_pk=777))
    aioclient_mock.get(f"{BASE}/sleep-service/sleep/dailySleep", status=403, text="")
    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    flows = hass.config_entries.flow.async_progress()
    assert not any(f["context"]["source"] == SOURCE_REAUTH for f in flows)
    assert hass.states.get(f"{PREFIX}_steps").state == "6543"
    assert "sleep" in config_entry.runtime_data.data.errors


async def test_forbidden_summary_is_update_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """If even the summary is refused, retry later rather than reauth."""
    aioclient_mock.post(f"{BASE}{TOKEN_PATH}", text=make_jwt(user_profile_pk=777))
    aioclient_mock.get(f"{BASE}/usersummary-service/v2/dailysummaries", status=403, text="")
    await _setup(hass, config_entry, ok=False)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    flows = hass.config_entries.flow.async_progress()
    assert not any(f["context"]["source"] == SOURCE_REAUTH for f in flows)


async def test_expiring_key_raises_repair(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A short-lived token means the key is about to expire: tell the user."""
    mock_garmin(aioclient_mock, token=make_jwt(lifetime_s=6 * 3600, user_profile_pk=777))
    await _setup(hass, config_entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"key_expiring_{config_entry.entry_id}")
    assert issue is not None
    assert issue.translation_key == "key_expiring"


async def test_normal_key_has_no_repair(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A full-length token raises no repair issue."""
    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, f"key_expiring_{config_entry.entry_id}")
    assert issue is None


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Diagnostics never include the key or personal identifiers."""
    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    text = str(diag)
    assert API_KEY not in text
    assert "test-runner" not in text
    assert diag["entry"]["data"]["api_key"] == "**REDACTED**"
    assert diag["data"]["activity"]["startLatitude"] == "**REDACTED**"
    assert diag["data"]["summary"]["movement"]["steps"]["value"] == 6543
