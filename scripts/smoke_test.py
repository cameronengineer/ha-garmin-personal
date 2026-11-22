#!/usr/bin/env python3
"""Check the integration's parsing against your real Garmin account.

Garmin's spec leaves a few things open (timestamp formats, weight units, the
names inside sleepScores). This script calls every endpoint the integration
uses with your own key, saves the responses with personal identifiers
redacted, and prints what each sensor would show, so mismatches are obvious.

Usage (from the repo root, inside the dev venv):

    # put your Personal API key in a file that git ignores
    printf '%s' 'YOUR-KEY' > .garmin_api_key && chmod 600 .garmin_api_key
    python scripts/smoke_test.py            # or --region china

Nothing is sent anywhere except Garmin. Output goes to smoke_output/ (also
git-ignored). Review it before sharing; it still contains your health data.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.garmin_personal.api import GarminPersonalApi  # noqa: E402
from custom_components.garmin_personal.const import (  # noqa: E402
    BASE_URLS,
    RECENT_ACTIVITIES_LIMIT,
    VO2_LOOKBACK_DAYS,
    WEIGHT_LOOKBACK_DAYS,
)
from custom_components.garmin_personal.coordinator import GarminData  # noqa: E402
from custom_components.garmin_personal.diagnostics import TO_REDACT  # noqa: E402
from custom_components.garmin_personal.parsing import latest_weigh_in  # noqa: E402
from custom_components.garmin_personal.sensor import SENSORS  # noqa: E402


def redact(obj: Any) -> Any:
    """Recursively blank out identifying fields."""
    if isinstance(obj, dict):
        return {k: "**REDACTED**" if k in TO_REDACT else redact(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def load_key() -> str:
    """Read the key from GARMIN_PERSONAL_API_KEY or .garmin_api_key."""
    key = os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip()
    if not key:
        path = ROOT / ".garmin_api_key"
        if path.exists():
            key = path.read_text(encoding="utf-8").strip()
    if not key:
        sys.exit("No key: set GARMIN_PERSONAL_API_KEY or create .garmin_api_key")
    return key


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--region", choices=sorted(BASE_URLS), default="global")
    args = parser.parse_args()

    today = date.today()
    out_dir = ROOT / "smoke_output"
    out_dir.mkdir(exist_ok=True)

    async with aiohttp.ClientSession() as session:
        api = GarminPersonalApi(session, load_key(), BASE_URLS[args.region])
        token = await api.async_get_token()
        lifetime = token.expires_at - token.issued_at
        print(f"Token OK. Lifetime {lifetime}; claims present: {sorted(token.claims)}")
        if token.key_expires_at:
            print(f"Key expires at {token.key_expires_at.isoformat()}")

        calls = {
            "profile": lambda: api.async_get_profile_settings(),
            "summary": lambda: api.async_get_daily_summary(today),
            "heart_rate": lambda: api.async_get_daily_heart_rate(today),
            "sleep": lambda: api.async_get_daily_sleep(today),
            "hrv": lambda: api.async_get_hrv(today),
            "weight": lambda: api.async_get_weight_range(
                today - timedelta(days=WEIGHT_LOOKBACK_DAYS), today
            ),
            "activities": lambda: api.async_search_activities(limit=RECENT_ACTIVITIES_LIMIT),
            "body_battery": lambda: api.async_get_body_battery_report(today),
            "spo2": lambda: api.async_get_spo2(today),
            "food": lambda: api.async_get_food_logs(today),
            "menstrual": lambda: api.async_get_menstrual_day(today),
            "vo2_max": lambda: api.async_get_vo2_max(
                today - timedelta(days=VO2_LOOKBACK_DAYS), today
            ),
            "fitness_age": lambda: api.async_get_fitness_age(today),
            "hr_zones": lambda: api.async_get_heart_rate_zones(),
            "weight_goal": lambda: api.async_get_weight_goal(today, today),
        }
        raw: dict[str, Any] = {}
        for name, call in calls.items():
            try:
                raw[name] = await call()
                status = "ok" if raw[name] not in (None, []) else "empty"
            except Exception as err:  # noqa: BLE001 - report everything
                raw[name] = None
                status = f"ERROR {err}"
            print(f"  {name:13} {status}")
            (out_dir / f"{name}.json").write_text(
                json.dumps(redact(raw[name]), indent=2, default=str), encoding="utf-8"
            )

        latest = (raw.get("activities") or [None])[0]
        if latest:
            try:
                archive = await api.async_download_activity(latest["activityId"])
                print(
                    f"  {'fit_download':13} ok ({len(archive)} bytes, zip={archive[:2] == b'PK'})"
                )
            except Exception as err:  # noqa: BLE001
                print(f"  {'fit_download':13} ERROR {err}")

    data = GarminData(
        day=today.isoformat(),
        summary=raw["summary"],
        heart_rate=raw["heart_rate"],
        sleep=raw["sleep"],
        hrv=raw["hrv"],
        weigh_in=latest_weigh_in(raw["weight"]),
        activities=raw["activities"] or [],
        body_battery=raw["body_battery"],
        spo2=raw["spo2"],
        food=raw["food"],
        menstrual=raw["menstrual"],
        vo2_max=raw["vo2_max"] or [],
        fitness_age=raw["fitness_age"],
        hr_zones=raw["hr_zones"] or [],
        weight_goal=raw["weight_goal"],
        token_expires_at=token.expires_at,
    )
    print("\nWhat each sensor would show:")
    for desc in SENSORS:
        value = desc.value_fn(data)
        unit = desc.native_unit_of_measurement or ""
        print(f"  {desc.key:28} {value!s:>32} {unit}")
    sleep_keys = sorted((raw.get("sleep") or {}).get("sleepScores") or {})
    print(f"\nsleepScores keys from Garmin: {sleep_keys}")
    print(f"Raw (redacted) responses saved in {out_dir}/")


if __name__ == "__main__":
    asyncio.run(main())
