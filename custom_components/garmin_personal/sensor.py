"""Sensors for the Garmin Connect Personal API integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfMass,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import HEART_RATE_MAX_AGE
from .coordinator import GarminConfigEntry, GarminCoordinator, GarminData
from .entity import account_id, device_info
from .parsing import (
    activities_since,
    activity_seconds,
    activity_start,
    activity_summary,
    body_battery_events,
    default_zones,
    dig,
    first_weekday,
    heart_rate_zone,
    latest_reading,
    latest_vo2_max,
    lower_label,
    parse_timestamp,
    seconds_to_hours,
    sleep_need_attrs,
    sleep_need_hours,
    sleep_score,
    sleep_score_components,
    weight_goal_kg,
    weight_kg,
)

BPM = "bpm"

# Attribute names that change often or hold lists; kept out of history.
ACTIVITY_ATTRIBUTES = (
    "activity_id",
    "activity_type",
    "start_time",
    "duration_minutes",
    "distance_km",
    "calories",
    "average_hr",
    "max_hr",
    "elevation_gain_m",
    "steps",
    "aerobic_training_effect",
    "anaerobic_training_effect",
    "training_effect_label",
    "training_load",
    "body_battery_impact",
    "moderate_intensity_minutes",
    "vigorous_intensity_minutes",
    "personal_record",
    *(f"minutes_in_hr_zone_{zone}" for zone in range(1, 6)),
)
# Sleep score components as the live API names them.
SLEEP_SCORE_ATTRIBUTES = (
    "overall",
    "totalDuration",
    "stress",
    "awakeCount",
    "remPercentage",
    "restlessness",
    "lightPercentage",
    "deepPercentage",
)
GRAMS = UnitOfMass.GRAMS
BREATHS_PER_MIN = "br/min"
HRV_UNIT = "ms"

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GarminSensorEntityDescription(SensorEntityDescription):
    """Describes a Garmin sensor: where its value comes from."""

    value_fn: Callable[[GarminData], Any]
    attrs_fn: Callable[[GarminData], dict[str, Any] | None] | None = None
    # Totals that restart each "day" or "week": reported as TOTAL with a
    # last_reset, so a value that dips (a deleted food, a revised step count)
    # isn't mistaken for a reset and double-counted in statistics.
    reset: str | None = None


def _summary(*path: str | int) -> Callable[[GarminData], Any]:
    return lambda data: dig(data.summary, *path)


def _sleep(*path: str | int) -> Callable[[GarminData], Any]:
    return lambda data: dig(data.sleep, *path)


def _sleep_hours(key: str) -> Callable[[GarminData], Any]:
    return lambda data: seconds_to_hours(dig(data.sleep, key))


def _hrv(*path: str | int) -> Callable[[GarminData], Any]:
    return lambda data: dig(data.hrv, "hrvSummary", *path)


def _weigh_in(key: str) -> Callable[[GarminData], Any]:
    return lambda data: dig(data.weigh_in, key)


def _activity(*path: str | int) -> Callable[[GarminData], Any]:
    return lambda data: dig(data.activity, *path)


def _non_negative(fn: Callable[[GarminData], Any]) -> Callable[[GarminData], Any]:
    """Garmin uses negative numbers (e.g. -1, -2) to mean 'no data'."""

    def wrapped(data: GarminData) -> Any:
        value = fn(data)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
            return None
        return value

    return wrapped


def _activity_attrs(data: GarminData) -> dict[str, Any] | None:
    # Deliberately no GPS coordinates: they'd put home/work locations in HA history.
    attrs = activity_summary(data.activity)
    attrs.pop("name", None)
    return attrs or None


def _week_start(data: GarminData) -> Any:
    """Start of this week in local time, using Garmin's first day of the week."""
    today = dt_util.start_of_local_day()
    days_back = (today.weekday() - first_weekday(data.profile)) % 7
    return today - timedelta(days=days_back)


def _this_week(data: GarminData) -> list[dict[str, Any]]:
    return activities_since(data.activities, dt_util.as_utc(_week_start(data)))


def _week_sum(key: str, scale: float = 1.0) -> Callable[[GarminData], Any]:
    def fn(data: GarminData) -> Any:
        total = 0.0
        for act in _this_week(data):
            value = act.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
                total += value
        return round(total * scale, 2)

    return fn


def _week_minutes(data: GarminData) -> float:
    return round(sum(activity_seconds(a) or 0 for a in _this_week(data)) / 60, 1)


def _week_attrs(data: GarminData) -> dict[str, Any] | None:
    acts = _this_week(data)
    if not acts:
        return {"week_start": _week_start(data).date().isoformat(), "activities": []}
    return {
        "week_start": _week_start(data).date().isoformat(),
        "activities": [
            {
                "name": a.get("activityName"),
                "type": dig(a, "activityType", "typeKey"),
                "start": (activity_start(a) or _week_start(data)).isoformat(),
            }
            for a in acts
        ],
    }


def _latest_hr(data: GarminData) -> float | None:
    """Latest heart rate, or None once it's too old to call 'current'.

    The watch only uploads when it syncs, so the newest reading can be hours
    old; showing it as the live value would be misleading.
    """
    reading = latest_reading(dig(data.heart_rate, "heartRateValues"))
    if not reading or dt_util.utcnow() - reading[0] > HEART_RATE_MAX_AGE:
        return None
    return reading[1]


def _latest_hr_attrs(data: GarminData) -> dict[str, Any] | None:
    reading = latest_reading(dig(data.heart_rate, "heartRateValues"))
    return {"measured_at": reading[0].isoformat()} if reading else None


def _zones_attrs(data: GarminData) -> dict[str, Any] | None:
    zones = default_zones(data.hr_zones)
    if not zones:
        return None
    out: dict[str, Any] = {f"zone_{i}_floor": zones.get(f"zone{i}Floor") for i in range(1, 6)}
    out["lactate_threshold_hr"] = zones.get("lactateThresholdHeartRateUsed")
    out["resting_hr_used"] = zones.get("restingHeartRateUsed")
    out["method"] = lower_label(zones.get("trainingMethod"))
    return {k: v for k, v in out.items() if v is not None}


def _fitness_age_attrs(data: GarminData) -> dict[str, Any] | None:
    fa = data.fitness_age
    if not isinstance(fa, dict):
        return None
    out = {
        "chronological_age": fa.get("chronologicalAge"),
        "achievable_fitness_age": round(fa["achievableFitnessAge"], 1)
        if isinstance(fa.get("achievableFitnessAge"), (int, float))
        else None,
        "previous_fitness_age": round(fa["previousFitnessAge"], 1)
        if isinstance(fa.get("previousFitnessAge"), (int, float))
        else None,
    }
    return {k: v for k, v in out.items() if v is not None} or None


def _round1(value: Any) -> Any:
    return (
        round(value, 1) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    )


def _cycle_attrs(data: GarminData) -> dict[str, Any] | None:
    summary = dig(data.menstrual, "daySummary")
    if not isinstance(summary, dict):
        return None
    out = {
        "phase": summary.get("currentPhase"),
        "days_until_next_phase": summary.get("daysUntilNextPhase"),
        "predicted_cycle_length": summary.get("predictedCycleLength"),
        "period_length": summary.get("periodLength"),
        "predicted_ovulation_date": dig(summary, "predictedOvulationDate", "localDate"),
        "cycle_start": dig(summary, "startDate", "localDate"),
    }
    return {k: v for k, v in out.items() if v is not None} or None


def _steps_attrs(data: GarminData) -> dict[str, Any] | None:
    goal = dig(data.summary, "movement", "steps", "goal")
    return {"goal": goal} if goal is not None else None


def _body_battery_attrs(data: GarminData) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    feedback = dig(data.summary, "bodyBattery", "dynamicFeedbackEvent", "feedbackShortType")
    if feedback:
        out["feedback"] = feedback
    events = body_battery_events(data.body_battery)
    if events:
        out["events"] = events
    return out or None


def _hrv_attrs(data: GarminData) -> dict[str, Any] | None:
    baseline = dig(data.hrv, "hrvSummary", "baseline")
    if not isinstance(baseline, dict):
        return None
    return {
        "baseline_low_upper": baseline.get("lowUpper"),
        "baseline_balanced_low": baseline.get("balancedLow"),
        "baseline_balanced_upper": baseline.get("balancedUpper"),
        "feedback": lower_label(dig(data.hrv, "hrvSummary", "feedbackPhrase")),
    }


SENSORS: tuple[GarminSensorEntityDescription, ...] = (
    # ---------------------------------------------------------------- activity
    GarminSensorEntityDescription(
        key="steps",
        translation_key="steps",
        native_unit_of_measurement="steps",
        state_class=SensorStateClass.TOTAL,
        reset="day",
        value_fn=_non_negative(_summary("movement", "steps", "value")),
        attrs_fn=_steps_attrs,
    ),
    GarminSensorEntityDescription(
        key="distance",
        translation_key="distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        value_fn=_non_negative(_summary("movement", "steps", "distanceInMeters")),
    ),
    GarminSensorEntityDescription(
        key="floors_ascended",
        translation_key="floors_ascended",
        native_unit_of_measurement="floors",
        state_class=SensorStateClass.TOTAL,
        reset="day",
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("movement", "floorsAscended", "value")),
    ),
    GarminSensorEntityDescription(
        key="calories_total",
        translation_key="calories_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        value_fn=_non_negative(_summary("calories", "burnedTotal")),
    ),
    GarminSensorEntityDescription(
        key="calories_active",
        translation_key="calories_active",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        value_fn=_non_negative(_summary("calories", "burnedActive")),
    ),
    GarminSensorEntityDescription(
        key="calories_resting",
        translation_key="calories_resting",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("calories", "burnedResting")),
    ),
    GarminSensorEntityDescription(
        key="intensity_minutes_moderate",
        translation_key="intensity_minutes_moderate",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        value_fn=_non_negative(_summary("intensityMinutes", "moderate")),
    ),
    GarminSensorEntityDescription(
        key="intensity_minutes_vigorous",
        translation_key="intensity_minutes_vigorous",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        value_fn=_non_negative(_summary("intensityMinutes", "vigorous")),
    ),
    GarminSensorEntityDescription(
        key="hydration",
        translation_key="hydration",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("hydration", "consumedInFractionalMl")),
    ),
    # -------------------------------------------------------------- heart rate
    GarminSensorEntityDescription(
        key="resting_heart_rate",
        translation_key="resting_heart_rate",
        native_unit_of_measurement=BPM,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_non_negative(_summary("heartRate", "restingValue")),
    ),
    GarminSensorEntityDescription(
        key="min_heart_rate",
        translation_key="min_heart_rate",
        native_unit_of_measurement=BPM,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("heartRate", "minValue")),
    ),
    GarminSensorEntityDescription(
        key="max_heart_rate",
        translation_key="max_heart_rate",
        native_unit_of_measurement=BPM,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("heartRate", "maxValue")),
    ),
    # ------------------------------------------------------ stress / battery
    GarminSensorEntityDescription(
        key="stress_average",
        translation_key="stress_average",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_non_negative(_summary("stress", "avgLevel")),
    ),
    GarminSensorEntityDescription(
        key="stress_max",
        translation_key="stress_max",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("stress", "maxLevel")),
    ),
    GarminSensorEntityDescription(
        key="stress_qualifier",
        translation_key="stress_qualifier",
        value_fn=lambda data: lower_label(dig(data.summary, "stress", "qualifier")),
    ),
    GarminSensorEntityDescription(
        key="body_battery",
        translation_key="body_battery",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_non_negative(_summary("bodyBattery", "latestValue")),
        attrs_fn=_body_battery_attrs,
    ),
    GarminSensorEntityDescription(
        key="body_battery_max",
        translation_key="body_battery_max",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("bodyBattery", "maxValue")),
    ),
    GarminSensorEntityDescription(
        key="body_battery_min",
        translation_key="body_battery_min",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("bodyBattery", "minValue")),
    ),
    GarminSensorEntityDescription(
        key="body_battery_charged",
        translation_key="body_battery_charged",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("bodyBattery", "chargedValue")),
    ),
    GarminSensorEntityDescription(
        key="body_battery_drained",
        translation_key="body_battery_drained",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("bodyBattery", "drainedValue")),
    ),
    # ----------------------------------------------------- respiration / SpO2
    GarminSensorEntityDescription(
        key="respiration_latest",
        translation_key="respiration_latest",
        native_unit_of_measurement=BREATHS_PER_MIN,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("respiration", "latestValue")),
    ),
    GarminSensorEntityDescription(
        key="respiration_average",
        translation_key="respiration_average",
        native_unit_of_measurement=BREATHS_PER_MIN,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("respiration", "avgValue")),
    ),
    GarminSensorEntityDescription(
        key="pulse_ox_latest",
        translation_key="pulse_ox_latest",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("pulseOx", "latestValue")),
    ),
    GarminSensorEntityDescription(
        key="pulse_ox_average",
        translation_key="pulse_ox_average",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("pulseOx", "avgValue")),
    ),
    # ------------------------------------------------------------------ sleep
    GarminSensorEntityDescription(
        key="sleep_duration",
        translation_key="sleep_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_sleep_hours("sleepTimeSeconds"),
    ),
    GarminSensorEntityDescription(
        key="sleep_score",
        translation_key="sleep_score",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: sleep_score(data.sleep),
        attrs_fn=lambda data: sleep_score_components(data.sleep) or None,
    ),
    GarminSensorEntityDescription(
        key="sleep_start",
        translation_key="sleep_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: parse_timestamp(dig(data.sleep, "sleepStartTimestampGMT")),
    ),
    GarminSensorEntityDescription(
        key="sleep_end",
        translation_key="sleep_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: parse_timestamp(dig(data.sleep, "sleepEndTimestampGMT")),
    ),
    GarminSensorEntityDescription(
        key="deep_sleep",
        translation_key="deep_sleep",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_sleep_hours("deepSleepSeconds"),
    ),
    GarminSensorEntityDescription(
        key="light_sleep",
        translation_key="light_sleep",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_sleep_hours("lightSleepSeconds"),
    ),
    GarminSensorEntityDescription(
        key="rem_sleep",
        translation_key="rem_sleep",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_sleep_hours("remSleepSeconds"),
    ),
    GarminSensorEntityDescription(
        key="awake_time",
        translation_key="awake_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_sleep_hours("awakeSleepSeconds"),
    ),
    GarminSensorEntityDescription(
        key="awake_count",
        translation_key="awake_count",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_sleep("awakeCount")),
    ),
    GarminSensorEntityDescription(
        key="sleep_heart_rate",
        translation_key="sleep_heart_rate",
        native_unit_of_measurement=BPM,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_sleep("avgHeartRate")),
    ),
    GarminSensorEntityDescription(
        key="sleep_stress",
        translation_key="sleep_stress",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_sleep("avgSleepStress")),
    ),
    GarminSensorEntityDescription(
        key="sleep_respiration",
        translation_key="sleep_respiration",
        native_unit_of_measurement=BREATHS_PER_MIN,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_sleep("averageRespirationValue")),
    ),
    # -------------------------------------------------------------------- HRV
    GarminSensorEntityDescription(
        key="hrv_last_night",
        translation_key="hrv_last_night",
        native_unit_of_measurement=HRV_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_non_negative(_hrv("lastNightAvg")),
        attrs_fn=_hrv_attrs,
    ),
    GarminSensorEntityDescription(
        key="hrv_weekly_average",
        translation_key="hrv_weekly_average",
        native_unit_of_measurement=HRV_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_non_negative(_hrv("weeklyAvg")),
    ),
    GarminSensorEntityDescription(
        key="hrv_status",
        translation_key="hrv_status",
        value_fn=lambda data: lower_label(dig(data.hrv, "hrvSummary", "status")),
    ),
    # ----------------------------------------------------------------- weight
    GarminSensorEntityDescription(
        key="weight",
        translation_key="weight",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: weight_kg(dig(data.weigh_in, "weight")),
    ),
    GarminSensorEntityDescription(
        key="bmi",
        translation_key="bmi",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_weigh_in("bmi")),
    ),
    GarminSensorEntityDescription(
        key="body_fat",
        translation_key="body_fat",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_weigh_in("bodyFat")),
    ),
    GarminSensorEntityDescription(
        key="last_weigh_in",
        translation_key="last_weigh_in",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=lambda data: parse_timestamp(dig(data.weigh_in, "timestampGMT")),
    ),
    # -------------------------------------------------------- latest activity
    GarminSensorEntityDescription(
        key="last_activity",
        translation_key="last_activity",
        value_fn=_activity("activityName"),
        attrs_fn=_activity_attrs,
    ),
    GarminSensorEntityDescription(
        key="last_activity_start",
        translation_key="last_activity_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: (
            parse_timestamp(dig(data.activity, "beginTimestamp"))
            or parse_timestamp(dig(data.activity, "startTimeGMT"))
        ),
    ),
    GarminSensorEntityDescription(
        key="last_activity_duration",
        translation_key="last_activity_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        value_fn=lambda data: _round1((activity_seconds(data.activity) or 0) / 60 or None),
    ),
    # -------------------------------------------------------------- this week
    GarminSensorEntityDescription(
        key="workouts_this_week",
        translation_key="workouts_this_week",
        native_unit_of_measurement="workouts",
        state_class=SensorStateClass.TOTAL,
        reset="week",
        value_fn=lambda data: len(_this_week(data)),
        attrs_fn=_week_attrs,
    ),
    GarminSensorEntityDescription(
        key="activity_time_this_week",
        translation_key="activity_time_this_week",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        suggested_display_precision=0,
        state_class=SensorStateClass.TOTAL,
        reset="week",
        value_fn=_week_minutes,
    ),
    GarminSensorEntityDescription(
        key="distance_this_week",
        translation_key="distance_this_week",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        suggested_unit_of_measurement=UnitOfLength.KILOMETERS,
        suggested_display_precision=1,
        state_class=SensorStateClass.TOTAL,
        reset="week",
        value_fn=_week_sum("distance"),
    ),
    GarminSensorEntityDescription(
        key="activity_calories_this_week",
        translation_key="activity_calories_this_week",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL,
        reset="week",
        value_fn=_week_sum("calories"),
    ),
    # ------------------------------------------------- live heart rate / zones
    GarminSensorEntityDescription(
        key="heart_rate",
        translation_key="heart_rate",
        native_unit_of_measurement=BPM,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_latest_hr,
        attrs_fn=_latest_hr_attrs,
    ),
    GarminSensorEntityDescription(
        key="heart_rate_zone",
        translation_key="heart_rate_zone",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: heart_rate_zone(_latest_hr(data), default_zones(data.hr_zones)),
    ),
    GarminSensorEntityDescription(
        key="resting_heart_rate_7_day",
        translation_key="resting_heart_rate_7_day",
        native_unit_of_measurement=BPM,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(
            lambda data: dig(data.heart_rate, "lastSevenDaysAvgRestingHeartRate")
        ),
    ),
    GarminSensorEntityDescription(
        key="max_heart_rate_setting",
        translation_key="max_heart_rate_setting",
        native_unit_of_measurement=BPM,
        entity_registry_enabled_default=False,
        value_fn=lambda data: dig(default_zones(data.hr_zones), "maxHeartRateUsed"),
        attrs_fn=_zones_attrs,
    ),
    # --------------------------------------------------------------- fitness
    GarminSensorEntityDescription(
        key="vo2_max",
        translation_key="vo2_max",
        native_unit_of_measurement="mL/kg/min",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: latest_vo2_max(data.vo2_max, "generic"),
    ),
    GarminSensorEntityDescription(
        key="vo2_max_cycling",
        translation_key="vo2_max_cycling",
        native_unit_of_measurement="mL/kg/min",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda data: latest_vo2_max(data.vo2_max, "cycling"),
    ),
    GarminSensorEntityDescription(
        key="fitness_age",
        translation_key="fitness_age",
        native_unit_of_measurement=UnitOfTime.YEARS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _round1(dig(data.fitness_age, "fitnessAge")),
        attrs_fn=_fitness_age_attrs,
    ),
    # ------------------------------------------------------------ sleep need
    GarminSensorEntityDescription(
        key="sleep_need",
        translation_key="sleep_need",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        value_fn=lambda data: sleep_need_hours(
            dig(data.sleep, "nextSleepNeed") or dig(data.sleep, "sleepNeed")
        ),
        attrs_fn=lambda data: sleep_need_attrs(
            dig(data.sleep, "nextSleepNeed") or dig(data.sleep, "sleepNeed")
        ),
    ),
    GarminSensorEntityDescription(
        key="hrv_5_min_high",
        translation_key="hrv_5_min_high",
        native_unit_of_measurement=HRV_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_hrv("lastNight5MinHigh")),
    ),
    # ------------------------------------------------------------- Pulse Ox
    GarminSensorEntityDescription(
        key="pulse_ox_7_day",
        translation_key="pulse_ox_7_day",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(lambda data: _round1(dig(data.spo2, "lastSevenDaysAvgSpO2"))),
    ),
    GarminSensorEntityDescription(
        key="pulse_ox_lowest",
        translation_key="pulse_ox_lowest",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(lambda data: dig(data.spo2, "lowestSpO2")),
    ),
    GarminSensorEntityDescription(
        key="sleep_pulse_ox",
        translation_key="sleep_pulse_ox",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(lambda data: _round1(dig(data.spo2, "avgSleepSpO2"))),
    ),
    # ------------------------------------------------------------ nutrition
    GarminSensorEntityDescription(
        key="calories_consumed",
        translation_key="calories_consumed",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        entity_registry_enabled_default=False,
        value_fn=_non_negative(
            lambda data: (
                dig(data.food, "dailyNutritionContent", "calories")
                or dig(data.summary, "calories", "consumedValue")
            )
        ),
        attrs_fn=lambda data: (
            {
                k: v
                for k, v in {
                    "goal": dig(data.food, "dailyNutritionGoals", "calories")
                    or dig(data.summary, "calories", "consumedGoal"),
                    "foods_logged": len(dig(data.food, "loggedFoodsWithServingSizes") or []),
                }.items()
                if v is not None
            }
            or None
        ),
    ),
    GarminSensorEntityDescription(
        key="carbs_consumed",
        translation_key="carbs_consumed",
        native_unit_of_measurement=GRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        entity_registry_enabled_default=False,
        value_fn=_non_negative(lambda data: dig(data.food, "dailyNutritionContent", "carbs")),
    ),
    GarminSensorEntityDescription(
        key="protein_consumed",
        translation_key="protein_consumed",
        native_unit_of_measurement=GRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        entity_registry_enabled_default=False,
        value_fn=_non_negative(lambda data: dig(data.food, "dailyNutritionContent", "protein")),
    ),
    GarminSensorEntityDescription(
        key="fat_consumed",
        translation_key="fat_consumed",
        native_unit_of_measurement=GRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.TOTAL,
        reset="day",
        entity_registry_enabled_default=False,
        value_fn=_non_negative(lambda data: dig(data.food, "dailyNutritionContent", "fat")),
    ),
    # ----------------------------------------------------- goals and cycle
    GarminSensorEntityDescription(
        key="weight_goal",
        translation_key="weight_goal",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda data: weight_goal_kg(data.weight_goal),
    ),
    GarminSensorEntityDescription(
        key="cycle_day",
        translation_key="cycle_day",
        entity_registry_enabled_default=False,
        value_fn=_non_negative(lambda data: dig(data.menstrual, "daySummary", "dayInCycle")),
        attrs_fn=_cycle_attrs,
    ),
    # ------------------------------------------------------------ diagnostics
    GarminSensorEntityDescription(
        key="token_expires",
        translation_key="token_expires",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.token_expires_at,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GarminConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Garmin sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(GarminSensor(coordinator, description) for description in SENSORS)


class GarminSensor(CoordinatorEntity[GarminCoordinator], SensorEntity):
    """A single Garmin value."""

    _attr_has_entity_name = True
    # Lists and per-update details are useful on the state but would bloat the
    # history database if written with every update.
    _unrecorded_attributes = frozenset(
        {
            "events",
            "activities",
            "measured_at",
            *ACTIVITY_ATTRIBUTES,
            *SLEEP_SCORE_ATTRIBUTES,
        }
    )
    entity_description: GarminSensorEntityDescription

    def __init__(
        self,
        coordinator: GarminCoordinator,
        description: GarminSensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{account_id(entry)}_{description.key}"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> Any:
        """The sensor's current value."""
        data = self.coordinator.data
        if self.entity_description.reset == "week" and not data.complete:
            # Activities and the first day of the week aren't in yet: a
            # passing 0 (or a week starting on the wrong day) would read as a
            # reset in long-term statistics.
            return None
        return self.entity_description.value_fn(data)

    @property
    def last_reset(self) -> datetime | None:
        """Start of the day or week a running total belongs to."""
        if self.entity_description.reset == "day":
            return dt_util.start_of_local_day()
        if self.entity_description.reset == "week":
            return _week_start(self.coordinator.data)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra detail, where it helps."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
