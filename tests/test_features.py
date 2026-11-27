"""Tests for the newer data: live HR and zones, fitness, sleep need, weekly
totals, the activities calendar, the new-activity event and FIT download."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
from pathlib import Path
import zipfile

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.garmin_personal.const import DOMAIN, RECENT_ACTIVITIES_LIMIT
from custom_components.garmin_personal.parsing import heart_rate_zone, latest_vo2_max

from .conftest import ACTIVITIES, BASE, DAILY_SLEEP, VO2_MAX, mock_garmin

PREFIX = "sensor.garmin_test_runner"
# Sunday 22 Nov 2026, 09:00 UTC (01:00 Pacific, HA's test time zone).
pytestmark = pytest.mark.freeze_time("2026-11-22 09:00:00+00:00")


def _activity(aid: int, name: str, start: datetime, **extra: object) -> dict:
    return {
        "activityId": aid,
        "activityName": name,
        "activityType": {"typeKey": extra.pop("type_key", "running")},
        "beginTimestamp": int(start.timestamp() * 1000),
        "elapsedDuration": 1800.0,
        "duration": 1700.0,
        "distance": 5000.0,
        "calories": 400.0,
        **extra,
    }


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, *, enable_all: bool = True) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if not enable_all:
        return
    registry = er.async_get(hass)
    changed = False
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.disabled_by:
            registry.async_update_entity(ent.entity_id, disabled_by=None)
            changed = True
    if changed:
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()


def _state(hass: HomeAssistant, name: str) -> str:
    st = hass.states.get(f"{PREFIX}_{name}")
    assert st is not None, name
    return st.state


async def test_new_sensor_values(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Live HR, zones, fitness, sleep need, Pulse Ox, nutrition, goal, cycle."""
    sleep = {
        **DAILY_SLEEP,
        "nextSleepNeed": {"baseline": 480, "actual": 510, "trainingFeedback": "INCREASED"},
    }
    mock_garmin(aioclient_mock, sleep=sleep)
    await _setup(hass, config_entry)

    assert _state(hass, "heart_rate") == "128"  # newest non-null reading
    assert hass.states.get(f"{PREFIX}_heart_rate").attributes["measured_at"].startswith("2026")
    assert _state(hass, "heart_rate_zone") == "2"  # 128 bpm: zone 2 floor 117, zone 3 137
    assert _state(hass, "resting_heart_rate_7_day_average") == "53"
    assert _state(hass, "max_heart_rate_setting") == "195"
    assert hass.states.get(f"{PREFIX}_max_heart_rate_setting").attributes["zone_3_floor"] == 137
    assert _state(hass, "vo2_max") == "48.0"
    assert _state(hass, "vo2_max_cycling") == "44.6"
    assert _state(hass, "fitness_age") == "28.4"
    assert hass.states.get(f"{PREFIX}_fitness_age").attributes["chronological_age"] == 31
    assert _state(hass, "sleep_need_tonight") == "8.5"
    need = hass.states.get(f"{PREFIX}_sleep_need_tonight").attributes
    assert need["training_adjustment"] == "increased"
    assert _state(hass, "hrv_5_minute_high") == "unknown"  # not in this fixture
    assert _state(hass, "pulse_ox_7_day_average") == "97.3"
    assert _state(hass, "lowest_pulse_ox") == "86"
    assert _state(hass, "calories_eaten") == "1840"
    assert hass.states.get(f"{PREFIX}_calories_eaten").attributes["foods_logged"] == 1
    assert _state(hass, "protein_eaten") == "96.0"
    assert _state(hass, "weight_goal") == "78.0"  # grams in, kg out
    assert _state(hass, "cycle_day") == "12"

    events = hass.states.get(f"{PREFIX}_body_battery").attributes["events"]
    assert [e["type"] for e in events] == ["sleep", "activity"]
    assert events[1]["impact"] == -11


async def test_weekly_totals(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Only activities since Monday (local) count towards this week."""
    # Local (US/Pacific) now: Sun 22 Nov 01:00, so the week started Mon 16 Nov.
    activities = [
        _activity(3, "Sunday run", datetime(2026, 11, 22, 8, 0, tzinfo=UTC)),
        _activity(2, "Monday ride", datetime(2026, 11, 16, 18, 0, tzinfo=UTC), distance=20000.0),
        _activity(1, "Last week", datetime(2026, 11, 15, 18, 0, tzinfo=UTC)),
    ]
    mock_garmin(aioclient_mock, activities=activities)
    await _setup(hass, config_entry)

    assert _state(hass, "workouts_this_week") == "2"
    assert _state(hass, "activity_time_this_week") == "60.0"
    assert float(_state(hass, "distance_this_week")) == pytest.approx(25.0)
    assert _state(hass, "activity_calories_this_week") == "800.0"
    names = [
        a["name"] for a in hass.states.get(f"{PREFIX}_workouts_this_week").attributes["activities"]
    ]
    assert names == ["Sunday run", "Monday ride"]
    assert _state(hass, "last_activity_duration") == "30.0"


async def test_week_follows_garmin_first_day(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """With a Sunday week start, Sunday's activity starts a new week."""
    # Local (US/Pacific) now: Sun 22 Nov 01:00 -> week started Sun 22 Nov 00:00.
    activities = [
        _activity(3, "Sunday run", datetime(2026, 11, 22, 8, 30, tzinfo=UTC)),
        _activity(2, "Saturday ride", datetime(2026, 11, 21, 18, 0, tzinfo=UTC)),
    ]
    # Registered first so it wins over mock_garmin's default profile.
    aioclient_mock.get(
        f"{BASE}/userprofile-service/userprofile/settings",
        json={"displayName": "x", "firstDayOfWeek": {"dayId": 2, "dayName": "sunday"}},
    )
    mock_garmin(aioclient_mock, activities=activities)
    await _setup(hass, config_entry, enable_all=False)
    assert _state(hass, "workouts_this_week") == "1"
    attrs = hass.states.get(f"{PREFIX}_workouts_this_week").attributes
    assert attrs["week_start"] == "2026-11-22"


async def test_diagnostics_omit_intimate_data(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """Cycle and food logs never appear in diagnostics."""
    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )

    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry, enable_all=False)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)
    assert diag["data"]["menstrual"] == "**OMITTED**"
    assert diag["data"]["food"] == "**OMITTED**"
    assert "Oats" not in str(diag)


async def test_activities_calendar(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
) -> None:
    """The calendar lists activities in a date range with details."""
    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry)
    entity_id = "calendar.garmin_test_runner_activities"
    assert hass.states.get(entity_id) is not None

    client = await hass_client()
    resp = await client.get(
        f"/api/calendars/{entity_id}?start=2026-11-20T00:00:00Z&end=2026-11-23T00:00:00Z"
    )
    assert resp.status == 200
    events = await resp.json()
    assert len(events) == 1
    event = events[0]
    assert event["summary"] == "Morning Run"
    assert "Distance: 5.01 km" in event["description"]
    assert "Average HR: 148 bpm" in event["description"]
    assert event["uid"] == "12345"

    # The date range is passed to Garmin.
    search = [c for c in aioclient_mock.mock_calls if "search/activities" in str(c[1])][-1]
    assert search[1].query["startDate"] == "2026-11-18"


async def test_new_activity_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Existing activities don't fire; one that syncs later does, once."""
    mock_garmin(aioclient_mock)
    # Don't enable extra entities here: HA reloads the entry 30 s after that,
    # and the time jumps below would race with the reload.
    await _setup(hass, config_entry, enable_all=False)
    entity_id = "event.garmin_test_runner_new_activity"
    assert hass.states.get(entity_id).state == "unknown"

    newer = _activity(
        99, "Evening boxing", datetime(2026, 11, 22, 8, 30, tzinfo=UTC), type_key="boxing"
    )
    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock, activities=[newer, *ACTIVITIES])
    freezer.tick(timedelta(minutes=65))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state != "unknown"
    assert state.attributes["event_type"] == "new_activity"
    assert state.attributes["name"] == "Evening boxing"
    assert state.attributes["activity_type"] == "boxing"
    fired_at = state.state

    # Same data again: no new event.
    freezer.tick(timedelta(minutes=65))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == fired_at


async def _next_hourly_update(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(minutes=65))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_new_activity_event_waits_for_first_successful_fetch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """If the first activities call fails, the first one that works is the baseline."""
    mock_garmin(aioclient_mock, activities_status=500)
    await _setup(hass, config_entry, enable_all=False)
    entity_id = "event.garmin_test_runner_new_activity"
    assert hass.states.get(entity_id).state == "unknown"

    # Garmin answers again: these activities were there all along, so no event.
    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock)
    await _next_hourly_update(hass, freezer)
    assert hass.states.get(entity_id).state == "unknown"

    # One that syncs after that does fire.
    newer = _activity(99, "Evening boxing", datetime(2026, 11, 22, 8, 30, tzinfo=UTC))
    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock, activities=[newer, *ACTIVITIES])
    await _next_hourly_update(hass, freezer)
    assert hass.states.get(entity_id).attributes["name"] == "Evening boxing"


async def test_new_activity_event_first_ever_activity(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """An account with no activities yet still fires for its first one."""
    mock_garmin(aioclient_mock, activities=[])
    await _setup(hass, config_entry, enable_all=False)
    entity_id = "event.garmin_test_runner_new_activity"
    assert hass.states.get(entity_id).state == "unknown"

    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock)
    await _next_hourly_update(hass, freezer)
    assert hass.states.get(entity_id).attributes["name"] == "Morning Run"


async def test_new_activity_event_ignores_backfill_after_delete(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Deleting a recent activity pulls an old one into the window: not new."""
    start = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)
    # Newest first, one a day, as the API returns them.
    history = [
        _activity(1000 - i, f"Run {i}", start - timedelta(days=i))
        for i in range(RECENT_ACTIVITIES_LIMIT + 1)
    ]
    mock_garmin(aioclient_mock, activities=history[:RECENT_ACTIVITIES_LIMIT])
    await _setup(hass, config_entry, enable_all=False)
    entity_id = "event.garmin_test_runner_new_activity"
    assert hass.states.get(entity_id).state == "unknown"

    # The newest is deleted in Garmin Connect; the 51st slides into the window.
    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock, activities=history[1:])
    await _next_hourly_update(hass, freezer)
    assert hass.states.get(entity_id).state == "unknown"

    # A late sync of an older workout that falls inside the window still fires.
    late = _activity(2000, "Late sync", start - timedelta(days=3, hours=5))
    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock, activities=[*history[1:4], late, *history[4:50]])
    await _next_hourly_update(hass, freezer)
    assert hass.states.get(entity_id).attributes["name"] == "Late sync"


def _zip(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, payload)
    return buf.getvalue()


async def test_download_activity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The action saves the FIT file out of Garmin's ZIP and returns its path."""
    mock_garmin(aioclient_mock)
    aioclient_mock.get(
        f"{BASE}/download-service/files/activity/12345",
        content=_zip("12345_ACTIVITY.fit", b".FIT-data"),
        headers={"Content-Type": "application/x-zip-compressed"},
    )
    await _setup(hass, config_entry)

    result = await hass.services.async_call(
        DOMAIN, "download_activity", {}, blocking=True, return_response=True
    )
    assert result["activity_id"] == 12345
    (path,) = result["files"]
    assert path.endswith("12345_Morning_Run.fit")
    assert Path(path).read_bytes() == b".FIT-data"
    _m, _url, _d, headers = aioclient_mock.mock_calls[-1]
    assert "application/zip" in headers["Accept"]  # Garmin 406s without it


async def test_download_by_id_from_text_field(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A long activity ID given as text (what the UI's text box sends) works."""
    mock_garmin(aioclient_mock)
    aioclient_mock.get(
        f"{BASE}/download-service/files/activity/21234567890",
        content=_zip("a.fit", b"x"),
    )
    await _setup(hass, config_entry, enable_all=False)
    result = await hass.services.async_call(
        DOMAIN,
        "download_activity",
        {"activity_id": "21234567890"},
        blocking=True,
        return_response=True,
    )
    assert result["activity_id"] == 21234567890
    assert result["files"][0].endswith("21234567890_activity.fit")


async def test_download_needs_an_account(hass: HomeAssistant) -> None:
    """Calling the action with nothing set up is a clear validation error."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "download_activity", {}, blocking=True)


def test_heart_rate_zone_edges() -> None:
    """Zone boundaries: below zone 1 is 0, a floor value is in that zone."""
    zones = {f"zone{i}Floor": f for i, f in enumerate([98, 117, 137, 156, 176], start=1)}
    assert heart_rate_zone(60, zones) == 0
    assert heart_rate_zone(98, zones) == 1
    assert heart_rate_zone(176, zones) == 5
    assert heart_rate_zone(None, zones) is None
    assert heart_rate_zone(120, {"zone1Floor": 98}) is None


def test_latest_vo2_max() -> None:
    """Newest entry per sport wins; missing sports are None."""
    assert latest_vo2_max(VO2_MAX, "generic") == 48.0
    assert latest_vo2_max(VO2_MAX, "rowing") is None
    assert latest_vo2_max([], "generic") is None


async def test_totals_report_last_reset(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Running totals are TOTAL with a last_reset at local midnight / week start."""
    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry)
    steps = hass.states.get(f"{PREFIX}_steps").attributes
    assert steps["state_class"] == "total"
    # Frozen at 09:00 UTC = 01:00 US/Pacific on Sun 22 Nov.
    assert steps["last_reset"] == "2026-11-22T00:00:00-08:00"
    week = hass.states.get(f"{PREFIX}_distance_this_week").attributes
    assert week["state_class"] == "total"
    assert week["last_reset"] == "2026-11-16T00:00:00-08:00"  # Monday by default
    assert hass.states.get(f"{PREFIX}_heart_rate").attributes["state_class"] == "measurement"
    assert "last_reset" not in hass.states.get(f"{PREFIX}_heart_rate").attributes


def test_heavy_attributes_not_recorded() -> None:
    """Lists and per-update details are excluded from the recorder."""
    from custom_components.garmin_personal.sensor import GarminSensor

    excluded = GarminSensor._unrecorded_attributes
    for name in ("events", "activities", "measured_at", "activity_id", "overall", "distance_km"):
        assert name in excluded


async def test_sleep_survives_midnight_until_next_sync(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Last night's sleep and HRV stay until a newer night actually syncs."""
    from .conftest import EMPTY_SLEEP

    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry, enable_all=False)
    assert _state(hass, "sleep_duration") == "7.25"
    assert _state(hass, "hrv_last_night") == "61"

    # Next day, before the watch syncs: empty sleep record, HRV 204.
    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock, sleep=EMPTY_SLEEP, hrv_status=204)
    freezer.tick(timedelta(hours=24))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "sleep_duration") == "7.25"
    assert _state(hass, "hrv_last_night") == "61"

    # The new night syncs: it replaces the old one.
    aioclient_mock.clear_requests()
    mock_garmin(aioclient_mock, sleep={**DAILY_SLEEP, "sleepTimeSeconds": 28_800})
    freezer.tick(timedelta(hours=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "sleep_duration") == "8.0"


async def test_stale_heart_rate_is_unknown(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """A heart-rate reading over an hour old isn't shown as current."""
    mock_garmin(aioclient_mock)
    await _setup(hass, config_entry, enable_all=False)
    assert _state(hass, "heart_rate") == "128"  # reading is ~38 min old

    freezer.tick(timedelta(minutes=30))  # same data, now ~68 min old
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "heart_rate") == "unknown"
    assert _state(hass, "heart_rate_zone") == "unknown"


async def test_removing_entry_clears_repair(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Deleting the integration doesn't leave a 'key expiring' notice behind."""
    from homeassistant.helpers import issue_registry as ir

    from .conftest import make_jwt

    mock_garmin(aioclient_mock, token=make_jwt(lifetime_s=6 * 3600, user_profile_pk=777))
    await _setup(hass, config_entry, enable_all=False)
    issue_id = f"key_expiring_{config_entry.entry_id}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
