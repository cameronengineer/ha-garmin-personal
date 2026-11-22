"""Tests for the response parsing helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.garmin_personal.parsing import (
    dig,
    latest_weigh_in,
    lower_label,
    parse_timestamp,
    seconds_to_hours,
    sleep_score,
    sleep_score_components,
    weight_kg,
)

EXPECTED = datetime(2026, 11, 22, 6, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        int(EXPECTED.timestamp() * 1000),  # epoch ms (int)
        EXPECTED.timestamp() * 1000,  # epoch ms (float)
        str(int(EXPECTED.timestamp() * 1000)),  # epoch ms as a string
        int(EXPECTED.timestamp()),  # epoch seconds
        "2026-11-22T06:30:00",  # Garmin "GMT" ISO, no offset
        "2026-11-22T06:30:00.0",
        "2026-11-22 06:30:00",  # activity startTimeGMT style
        "2026-11-22T06:30:00Z",
        "2026-11-22T16:30:00+10:00",
    ],
)
def test_parse_timestamp_formats(value: object) -> None:
    """Every format Garmin might use lands on the same UTC instant."""
    assert parse_timestamp(value) == EXPECTED


@pytest.mark.parametrize("value", [None, "", "not a date", True, {}, []])
def test_parse_timestamp_garbage(value: object) -> None:
    """Unparseable input is None, not an exception."""
    assert parse_timestamp(value) is None


def test_weight_kg() -> None:
    """Kilograms pass through; grams are converted; junk is None."""
    assert weight_kg(80.4) == 80.4
    assert weight_kg(80400) == 80.4
    assert weight_kg(0) is None
    assert weight_kg(-5) is None
    assert weight_kg(None) is None
    assert weight_kg("80") is None


def test_seconds_to_hours() -> None:
    """Durations are reported in hours."""
    assert seconds_to_hours(27_000) == 7.5
    assert seconds_to_hours(None) is None
    assert seconds_to_hours(True) is None


def test_dig() -> None:
    """dig walks dicts and lists and stops quietly on a miss."""
    data = {"a": {"b": [{"c": 1}]}}
    assert dig(data, "a", "b", 0, "c") == 1
    assert dig(data, "a", "x", "c") is None
    assert dig(data, "a", "b", 5) is None
    assert dig(None, "a") is None
    assert dig({"a": 1}, "a", "b") is None


def test_sleep_score() -> None:
    """Overall score is read when present; components become attributes."""
    sleep = {
        "sleepScores": {
            "overall": {"value": 82, "qualifierKey": "GOOD"},
            "remPercentage": {"qualifierKey": "FAIR"},
            "junk": 4,
        }
    }
    assert sleep_score(sleep) == 82
    assert sleep_score_components(sleep) == {"overall": 82, "remPercentage": "FAIR"}
    assert sleep_score({"sleepScores": {"other": {"value": 1}}}) is None
    assert sleep_score(None) is None
    assert sleep_score_components({}) == {}


def test_latest_weigh_in() -> None:
    """The most recent weigh-in wins, across days and within a day."""
    weights = {
        "dailyWeightSummaries": [
            {"allWeightMetrics": [{"timestampGMT": 2000, "weight": 81}]},
            {
                "allWeightMetrics": [
                    {"timestampGMT": 3000, "weight": 80},
                    {"timestampGMT": 1000, "weight": 82},
                    {"timestampGMT": 9000, "weight": None},
                ]
            },
        ]
    }
    assert latest_weigh_in(weights)["weight"] == 80
    assert latest_weigh_in({"dailyWeightSummaries": []}) is None
    assert latest_weigh_in(None) is None


def test_lower_label() -> None:
    """Enum-ish labels are normalised."""
    assert lower_label("BALANCED") == "balanced"
    assert lower_label("  ") is None
    assert lower_label("unknown") is None  # Garmin's live "no qualifier" value
    assert lower_label(None) is None
