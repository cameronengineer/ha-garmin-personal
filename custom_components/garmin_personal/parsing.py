"""Turn Garmin responses into plain values.

Garmin's spec is not always consistent about formats (some "date-time" fields
are described as epoch milliseconds, ISO timestamps carry no "Z", weight is
documented in kilograms but the older Garmin APIs used grams), so every
conversion lives here, in one tested place, and fails soft by returning None.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def dig(data: Any, *path: str | int) -> Any:
    """Safely walk nested dicts/lists, returning None on any miss."""
    cur = data
    for key in path:
        if isinstance(cur, dict):
            cur = cur.get(key)
        elif isinstance(cur, list) and isinstance(key, int):
            cur = cur[key] if -len(cur) <= key < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


def parse_timestamp(value: Any) -> datetime | None:
    """Parse a Garmin GMT timestamp into an aware UTC datetime.

    Accepts epoch milliseconds (int/float or numeric string), epoch seconds,
    and ISO 8601 strings with or without a trailing "Z" or offset. Garmin's
    "GMT" strings have no offset, so naive values are treated as UTC.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
    else:
        return None
    # Heuristic: anything past year ~2286 in seconds is really milliseconds.
    if number > 1e11:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def seconds_to_hours(value: Any) -> float | None:
    """Convert a duration in seconds to hours (rounded to 0.01 h)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return round(value / 3600, 2)


def weight_kg(value: Any) -> float | None:
    """Return a body weight in kilograms.

    The spec says kilograms, but the live API returns grams (confirmed against
    a real account, 2026-11). Accept both: a body weight above 1000 can only
    be grams.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    kg = value / 1000 if value > 1000 else float(value)
    return round(kg, 2)


def sleep_score(sleep: dict[str, Any] | None) -> int | None:
    """The overall sleep score (0-100), if Garmin computed one."""
    scores = dig(sleep, "sleepScores")
    if not isinstance(scores, dict):
        return None
    overall = scores.get("overall")
    if isinstance(overall, dict) and isinstance(overall.get("value"), (int, float)):
        return int(overall["value"])
    return None


def sleep_score_components(sleep: dict[str, Any] | None) -> dict[str, Any]:
    """All sleep-score components as ``{name: value or qualifier}`` attributes."""
    scores = dig(sleep, "sleepScores")
    if not isinstance(scores, dict):
        return {}
    out: dict[str, Any] = {}
    for name, item in scores.items():
        if not isinstance(item, dict):
            continue
        if item.get("value") is not None:
            out[name] = item["value"]
        elif item.get("qualifierKey"):
            out[name] = item["qualifierKey"]
    return out


def latest_weigh_in(weights: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the most recent individual weigh-in from a weight-range response."""
    best: dict[str, Any] | None = None
    best_ts: float = float("-inf")
    for summary in dig(weights, "dailyWeightSummaries") or []:
        if not isinstance(summary, dict):
            continue
        for metric in summary.get("allWeightMetrics") or []:
            if not isinstance(metric, dict) or metric.get("weight") is None:
                continue
            when = parse_timestamp(metric.get("timestampGMT") or metric.get("date"))
            ts = when.timestamp() if when else float("-inf")
            if best is None or ts >= best_ts:
                best, best_ts = metric, ts
    return best


# Labels Garmin uses to mean "no value" (seen live: stress qualifier "unknown").
_EMPTY_LABELS = {"unknown", "none", "invalid", "null", "n/a"}


def lower_label(value: Any) -> str | None:
    """Normalise a Garmin enum-ish label ("BALANCED") for an enum sensor."""
    if not isinstance(value, str) or not value.strip():
        return None
    label = value.strip().lower()
    return None if label in _EMPTY_LABELS else label


def latest_reading(values: Any) -> tuple[datetime, float] | None:
    """Newest non-null ``[timestamp_ms, value]`` pair from a Garmin readings array."""
    if not isinstance(values, list):
        return None
    for row in reversed(values):
        if isinstance(row, list) and len(row) >= 2 and isinstance(row[1], (int, float)):
            when = parse_timestamp(row[0])
            if when is not None and not isinstance(row[1], bool) and row[1] >= 0:
                return when, row[1]
    return None


def default_zones(zones: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """The DEFAULT-sport heart-rate zone set (or the first one listed)."""
    if not zones:
        return None
    for zone_set in zones:
        if isinstance(zone_set, dict) and zone_set.get("sport") == "DEFAULT":
            return zone_set
    first = zones[0]
    return first if isinstance(first, dict) else None


def heart_rate_zone(bpm: float | None, zones: dict[str, Any] | None) -> int | None:
    """Which zone (0-5) a heart rate falls in; 0 is below zone 1."""
    if bpm is None or not zones:
        return None
    floors = [zones.get(f"zone{i}Floor") for i in range(1, 6)]
    if not all(isinstance(f, (int, float)) for f in floors):
        return None
    zone = 0
    for i, floor in enumerate(floors, start=1):
        if bpm >= floor:
            zone = i
    return zone


def latest_vo2_max(entries: list[dict[str, Any]] | None, sport: str) -> float | None:
    """Most recent VO2 max for ``sport`` ("generic" = running/walking, "cycling")."""
    best: tuple[str, float] | None = None
    for entry in entries or []:
        item = dig(entry, sport)
        if not isinstance(item, dict):
            continue
        value = item.get("vo2MaxPreciseValue") or item.get("vo2MaxValue")
        when = item.get("calendarDate") or ""
        if isinstance(value, (int, float)) and value > 0 and (best is None or when >= best[0]):
            best = (when, float(value))
    return round(best[1], 1) if best else None


def weight_goal_kg(goal: dict[str, Any] | None) -> float | None:
    """Current target weight in kg (the API returns grams)."""
    goals = dig(goal, "userGoals")
    if not isinstance(goals, list):
        return None
    for item in reversed(goals):
        if isinstance(item, dict):
            kg = weight_kg(item.get("goalValue"))
            if kg is not None:
                return kg
    return None


def activity_start(activity: dict[str, Any] | None) -> datetime | None:
    """When an activity started (UTC)."""
    if not activity:
        return None
    return parse_timestamp(activity.get("beginTimestamp")) or parse_timestamp(
        activity.get("startTimeGMT")
    )


def activity_seconds(activity: dict[str, Any] | None) -> float | None:
    """How long an activity lasted, in seconds (elapsed, falling back to timer)."""
    if not activity:
        return None
    for key in ("elapsedDuration", "duration", "movingDuration"):
        value = activity.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def activity_type(activity: dict[str, Any] | None) -> str | None:
    """The activity's type key, e.g. "running" or "strength_training"."""
    return dig(activity, "activityType", "typeKey") or dig(activity, "activityTypeDTO", "typeKey")


def activity_summary(activity: dict[str, Any] | None) -> dict[str, Any]:
    """Human-useful fields of an activity, without GPS or social data."""
    if not activity:
        return {}
    start = activity_start(activity)
    seconds = activity_seconds(activity)
    distance = activity.get("distance")
    out = {
        "activity_id": activity.get("activityId"),
        "name": activity.get("activityName"),
        "activity_type": activity_type(activity),
        "start_time": start.isoformat() if start else None,
        "duration_minutes": round(seconds / 60, 1) if seconds else None,
        "distance_km": round(distance / 1000, 2)
        if isinstance(distance, (int, float)) and distance > 0
        else None,
        "calories": activity.get("calories"),
        "average_hr": activity.get("averageHR"),
        "max_hr": activity.get("maxHR"),
        "elevation_gain_m": activity.get("elevationGain"),
        "steps": activity.get("steps"),
        "aerobic_training_effect": activity.get("aerobicTrainingEffect"),
        "anaerobic_training_effect": activity.get("anaerobicTrainingEffect"),
        "training_effect_label": lower_label(activity.get("trainingEffectLabel")),
        "training_load": activity.get("activityTrainingLoad"),
        "body_battery_impact": activity.get("differenceBodyBattery"),
        "moderate_intensity_minutes": activity.get("moderateIntensityMinutes"),
        "vigorous_intensity_minutes": activity.get("vigorousIntensityMinutes"),
        "personal_record": activity.get("isPR") or activity.get("pr") or None,
    }
    for zone in range(1, 6):
        value = activity.get(f"hrTimeInZone_{zone}")
        if isinstance(value, (int, float)) and value > 0:
            out[f"minutes_in_hr_zone_{zone}"] = round(value / 60, 1)
    return {k: v for k, v in out.items() if v is not None}


def activities_since(
    activities: list[dict[str, Any]] | None, since: datetime
) -> list[dict[str, Any]]:
    """Activities that started at or after ``since``."""
    out = []
    for activity in activities or []:
        start = activity_start(activity)
        if start is not None and start >= since:
            out.append(activity)
    return out


def body_battery_events(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """What charged or drained Body Battery today (sleep, activities, stress...)."""
    events = []
    for event in dig(report, "bodyBatteryActivityEvent") or []:
        if not isinstance(event, dict):
            continue
        start = parse_timestamp(event.get("eventStartTimeGmt"))
        millis = event.get("durationInMilliseconds")
        events.append(
            {
                "type": lower_label(event.get("eventType")),
                "start": start.isoformat() if start else None,
                "duration_minutes": round(millis / 60000)
                if isinstance(millis, (int, float))
                else None,
                "impact": event.get("bodyBatteryImpact"),
                "feedback": lower_label(event.get("shortFeedback")),
            }
        )
    return events


def sleep_need_hours(need: dict[str, Any] | None) -> float | None:
    """Garmin's recommended sleep (minutes in the API) as hours."""
    minutes = dig(need, "actual")
    if not isinstance(minutes, (int, float)) or isinstance(minutes, bool) or minutes <= 0:
        return None
    return round(minutes / 60, 2)


def sleep_need_attrs(need: dict[str, Any] | None) -> dict[str, Any] | None:
    """Why Garmin adjusted tonight's sleep need."""
    if not isinstance(need, dict):
        return None
    baseline = need.get("baseline")
    out = {
        "baseline_hours": round(baseline / 60, 2) if isinstance(baseline, (int, float)) else None,
        "feedback": lower_label(need.get("feedback")),
        "training_adjustment": lower_label(need.get("trainingFeedback")),
        "sleep_history_adjustment": lower_label(need.get("sleepHistoryAdjustment")),
        "hrv_adjustment": lower_label(need.get("hrvAdjustment")),
        "nap_adjustment": lower_label(need.get("napAdjustment")),
    }
    return {k: v for k, v in out.items() if v is not None} or None
