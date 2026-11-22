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
