"""Sensors for the Garmin Connect Personal API integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GarminConfigEntry, GarminCoordinator, GarminData
from .parsing import (
    dig,
    lower_label,
    parse_timestamp,
    seconds_to_hours,
    sleep_score,
    sleep_score_components,
    weight_kg,
)

BPM = "bpm"
BREATHS_PER_MIN = "br/min"
HRV_UNIT = "ms"

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GarminSensorEntityDescription(SensorEntityDescription):
    """Describes a Garmin sensor: where its value comes from."""

    value_fn: Callable[[GarminData], Any]
    attrs_fn: Callable[[GarminData], dict[str, Any] | None] | None = None


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
    act = data.activity
    if not act:
        return None
    start = parse_timestamp(act.get("beginTimestamp")) or parse_timestamp(act.get("startTimeGMT"))
    # Deliberately no GPS coordinates: they'd put home/work locations in HA history.
    attrs = {
        "activity_id": act.get("activityId"),
        "activity_type": dig(act, "activityType", "typeKey"),
        "start_time": start.isoformat() if start else None,
        "duration_minutes": round(act["duration"] / 60, 1)
        if isinstance(act.get("duration"), (int, float))
        else None,
        "distance_km": round(act["distance"] / 1000, 2)
        if isinstance(act.get("distance"), (int, float))
        else None,
        "calories": act.get("calories"),
        "average_hr": act.get("averageHR"),
        "max_hr": act.get("maxHR"),
        "aerobic_training_effect": act.get("aerobicTrainingEffect"),
        "anaerobic_training_effect": act.get("anaerobicTrainingEffect"),
        "training_load": act.get("activityTrainingLoad"),
    }
    return {k: v for k, v in attrs.items() if v is not None}


def _steps_attrs(data: GarminData) -> dict[str, Any] | None:
    goal = dig(data.summary, "movement", "steps", "goal")
    return {"goal": goal} if goal is not None else None


def _body_battery_attrs(data: GarminData) -> dict[str, Any] | None:
    feedback = dig(data.summary, "bodyBattery", "dynamicFeedbackEvent", "feedbackShortType")
    return {"feedback": feedback} if feedback else None


def _hrv_attrs(data: GarminData) -> dict[str, Any] | None:
    baseline = dig(data.hrv, "hrvSummary", "baseline")
    if not isinstance(baseline, dict):
        return None
    return {
        "baseline_low_upper": baseline.get("lowUpper"),
        "baseline_balanced_low": baseline.get("balancedLow"),
        "baseline_balanced_upper": baseline.get("balancedUpper"),
    }


SENSORS: tuple[GarminSensorEntityDescription, ...] = (
    # ---------------------------------------------------------------- activity
    GarminSensorEntityDescription(
        key="steps",
        translation_key="steps",
        native_unit_of_measurement="steps",
        state_class=SensorStateClass.TOTAL_INCREASING,
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
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_non_negative(_summary("movement", "steps", "distanceInMeters")),
    ),
    GarminSensorEntityDescription(
        key="floors_ascended",
        translation_key="floors_ascended",
        native_unit_of_measurement="floors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("movement", "floorsAscended", "value")),
    ),
    GarminSensorEntityDescription(
        key="calories_total",
        translation_key="calories_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_non_negative(_summary("calories", "burnedTotal")),
    ),
    GarminSensorEntityDescription(
        key="calories_active",
        translation_key="calories_active",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_non_negative(_summary("calories", "burnedActive")),
    ),
    GarminSensorEntityDescription(
        key="calories_resting",
        translation_key="calories_resting",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_registry_enabled_default=False,
        value_fn=_non_negative(_summary("calories", "burnedResting")),
    ),
    GarminSensorEntityDescription(
        key="intensity_minutes_moderate",
        translation_key="intensity_minutes_moderate",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_non_negative(_summary("intensityMinutes", "moderate")),
    ),
    GarminSensorEntityDescription(
        key="intensity_minutes_vigorous",
        translation_key="intensity_minutes_vigorous",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_non_negative(_summary("intensityMinutes", "vigorous")),
    ),
    GarminSensorEntityDescription(
        key="hydration",
        translation_key="hydration",
        device_class=SensorDeviceClass.VOLUME,
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
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
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=entry.title,
            manufacturer="Garmin",
            model="Garmin Connect account",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://connect.garmin.com/",
        )

    @property
    def native_value(self) -> Any:
        """The sensor's current value."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Extra detail, where it helps."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)
