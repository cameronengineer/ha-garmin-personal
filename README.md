# Garmin Connect Personal API for Home Assistant

[![Validate](https://github.com/cameronengineer/ha-garmin-personal/actions/workflows/validate.yml/badge.svg)](https://github.com/cameronengineer/ha-garmin-personal/actions/workflows/validate.yml)
[![Tests](https://github.com/cameronengineer/ha-garmin-personal/actions/workflows/tests.yml/badge.svg)](https://github.com/cameronengineer/ha-garmin-personal/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)

Bring your Garmin health and fitness data into Home Assistant as sensors —
steps, heart rate, stress, Body Battery, sleep, HRV, weight and your latest
activity — using **Garmin's official [Connect Personal API](https://developer.garmin.com/gc-developer-program/personal/)**
(launched October 2026).

No Garmin password, no scraping, no unofficial login. You create a Personal
API key in Garmin Connect, paste it into Home Assistant, and that's it.

> **Not affiliated with Garmin.** "Garmin", "Garmin Connect" and "Body Battery"
> are trademarks of Garmin Ltd. or its subsidiaries. This is an independent,
> community-built integration that uses Garmin's public Personal API under
> Garmin's terms.

> **Status: early (v0.1).** Built from Garmin's published OpenAPI spec and
> tested against synthetic data shaped after it. A few details the spec leaves
> open are handled defensively — see [Known unknowns](#known-unknowns). If a
> sensor looks wrong for you, run the [smoke test](#checking-against-your-own-account)
> and open an issue.

## Why this and not the older Garmin Connect integration?

| | This integration | [`garmin_connect`](https://github.com/cyberjunky/home-assistant-garmin_connect) (HACS) |
|---|---|---|
| Access | Official Personal API | Unofficial, logs in as you |
| Credentials | Revocable API key | Your Garmin username + password |
| Breaks when Garmin changes its website | No | Can |
| Data breadth | What the Personal API exposes | More (training readiness, etc.) |

They use different domains (`garmin_personal` vs `garmin_connect`), so you can
run both side by side.

## Installation

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/cameronengineer/ha-garmin-personal`, category **Integration**.
2. Search for **Garmin Connect Personal API** and download it.
3. Restart Home Assistant.

### Manual

Copy `custom_components/garmin_personal/` into your Home Assistant
`config/custom_components/` folder and restart.

## Setup

1. In a browser, sign in to Garmin Connect and open
   **[Settings → Personal API Key](https://connect.garmin.com/app/settings/personalApiToken)**.
2. **Create Key**, accept the terms, and choose an expiry (30 days, 90 days,
   180 days or 1 year). Copy the key — Garmin only shows it once.
3. In Home Assistant: **Settings → Devices & services → Add integration →
   Garmin Connect Personal API**, paste the key, pick your server (Global, or
   China for `connect.garmin.cn` accounts).

You'll get one device, named after your Garmin display name, with the sensors
below.

### When the key expires

Personal API keys expire on the schedule you chose. Garmin emails you about a
week beforehand. This integration also raises a **repair notice** in Home
Assistant during the key's last day, and once the key stops working Home
Assistant shows a **re-authentication prompt**: create a new key in Garmin
Connect and paste it there. Your entities, history and automations are kept.

## Sensors

Sensors marked "—" are created disabled to keep things tidy; enable any of them
from the device page.

| Sensor | Unit | On by default |
|---|---|---|
| **Daily activity** | | |
| Steps | steps | yes |
| Distance | km | yes |
| Floors climbed | floors | — |
| Calories burned | kcal | yes |
| Active calories | kcal | yes |
| Resting calories | kcal | — |
| Moderate intensity minutes | min | yes |
| Vigorous intensity minutes | min | yes |
| Water intake | mL | — |
| **Heart rate** | | |
| Resting heart rate | bpm | yes |
| Minimum heart rate | bpm | — |
| Maximum heart rate | bpm | — |
| **Stress and Body Battery** | | |
| Average stress |  | yes |
| Maximum stress |  | — |
| Stress level |  | yes |
| Body Battery |  | yes |
| Body Battery high |  | — |
| Body Battery low |  | — |
| Body Battery charged |  | — |
| Body Battery drained |  | — |
| **Breathing and Pulse Ox** | | |
| Respiration rate | br/min | — |
| Average respiration rate | br/min | — |
| Pulse Ox | % | — |
| Average Pulse Ox | % | — |
| **Sleep (last night)** | | |
| Sleep duration | h | yes |
| Sleep score |  | yes |
| Fell asleep | timestamp | yes |
| Woke up | timestamp | yes |
| Deep sleep | h | — |
| Light sleep | h | — |
| REM sleep | h | — |
| Awake time | h | — |
| Times woken |  | — |
| Sleeping heart rate | bpm | — |
| Sleep stress |  | — |
| Sleeping respiration rate | br/min | — |
| **HRV** | | |
| HRV last night | ms | yes |
| HRV 7-day average | ms | yes |
| HRV status |  | yes |
| **Weight (latest weigh-in, last 30 days)** | | |
| Weight | kg | yes |
| BMI |  | — |
| Body fat | % | — |
| Last weigh-in | timestamp | — |
| **Activities** | | |
| Last activity |  | yes |
| Last activity start | timestamp | yes |
| **Diagnostic** | | |
| Access token expires | timestamp | — |

Notes:

- **Daily** values (steps, calories, stress, Body Battery…) are for today in
  Home Assistant's time zone, and reset at midnight.
- **Sleep** and **HRV** are for the night that ended today. Before you've synced
  in the morning they show *unknown*.
- Garmin uses `-1`/`-2` to mean "no reading"; those show as *unknown* rather than
  a misleading number.
- Extra detail is on attributes: the step goal, Body Battery feedback, sleep
  score components, HRV baseline ranges, and the latest activity's type,
  duration, distance, calories, heart rate and training effect.
- The latest activity deliberately has **no GPS coordinates**, so your
  start/finish locations don't end up in Home Assistant's history.

## How often it polls

Data only changes when your watch syncs to Garmin Connect, so there's no point
hammering the API, and Garmin doesn't publish rate limits.

- **Today's summary** (steps, heart rate, stress, Body Battery, calories…):
  every 15 minutes by default. Change it under the integration's
  **Configure** (5–240 minutes).
- **Sleep, HRV, weight and latest activity:** at most once an hour.
- **Access tokens** last 24 hours and are renewed automatically, an hour early.

That's roughly 200 requests a day at the default setting (96 summary calls plus four hourly calls).

## Automation ideas

Turn the bathroom heater on when you actually wake up, instead of at a fixed time:

```yaml
automation:
  - alias: Bathroom heater on at wake-up
    triggers:
      - trigger: state
        entity_id: sensor.garmin_YOURNAME_woke_up
    conditions:
      # Only for a fresh wake-up, in the morning, on a cold day.
      - condition: template
        value_template: >
          {{ (now() - as_datetime(trigger.to_state.state)).total_seconds() < 3600 }}
      - condition: time
        after: "04:30:00"
        before: "10:00:00"
      - condition: numeric_state
        entity_id: weather.forecast_home
        attribute: temperature
        below: 18
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.bathroom_heater
```

> The "Woke up" sensor only changes after your watch syncs, and sleep data is
> fetched hourly, so expect this to fire within about an hour of waking, not to
> the minute. For a pre-heated bathroom, pair it with a schedule as a fallback.

A gentle evening reminder when Body Battery is low:

```yaml
automation:
  - alias: Low Body Battery wind-down
    triggers:
      - trigger: numeric_state
        entity_id: sensor.garmin_YOURNAME_body_battery
        below: 20
    conditions:
      - condition: time
        after: "19:00:00"
    actions:
      - action: light.turn_on
        target:
          area_id: living_room
        data:
          color_temp_kelvin: 2200
          brightness_pct: 40
```

## Privacy

- Your API key is stored in Home Assistant's config entry storage, like every
  other integration's credentials. It's never logged.
- Requests go only to Garmin (`personal.connectapi.garmin.com`, or `.cn`).
- **Diagnostics** downloads redact the key, your Garmin user IDs, display
  name, device IDs, GPS coordinates, location names and sleep notes.
- To revoke access at any time, delete the key in Garmin Connect.

## Known unknowns

Garmin's spec is thorough, but a few details can't be confirmed without real
responses. Each is handled defensively and covered by tests:

| Question | How it's handled |
|---|---|
| Sleep timestamps are typed `date-time` but described as "in ms" | Accepts epoch ms, epoch seconds, and ISO strings (with or without `Z`) |
| Weight is documented in kg; Garmin's older APIs used grams | Values above 1000 are treated as grams |
| The keys inside `sleepScores` aren't listed | Reads `overall` for the score; exposes every component as attributes |
| Which JWT claim identifies your account | Tries account-ID claims, then the documented `userProfilePk` from today's summary, then your display name |
| An endpoint refused for your account (HTTP 403) | Only that sensor group goes *unknown*; you're never asked for a new key unless the key itself is rejected |
| Rate limits | Conservative two-tier polling; HTTP 429 is reported as a temporary failure |

## Checking against your own account

`scripts/smoke_test.py` calls every endpoint with your key, saves the
responses (with personal identifiers redacted) to `smoke_output/`, and prints
what each sensor would show:

```bash
git clone https://github.com/cameronengineer/ha-garmin-personal && cd ha-garmin-personal
python3 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
printf '%s' 'YOUR-KEY' > .garmin_api_key && chmod 600 .garmin_api_key
.venv/bin/python scripts/smoke_test.py
```

`.garmin_api_key` and `smoke_output/` are git-ignored. The saved responses
still contain your health data, so check them before attaching anything to an
issue.

## Development

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt ruff
.venv/bin/pytest --cov=custom_components/garmin_personal
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

CI runs Home Assistant's `hassfest`, the HACS validator, Ruff and the test
suite on every push.

Layout:

| File | Purpose |
|---|---|
| `api.py` | Async client: key → token exchange, token caching, endpoint calls. No Home Assistant imports. |
| `parsing.py` | Every format conversion (timestamps, weight units, sleep scores) in one tested place |
| `coordinator.py` | Two-tier polling, partial-failure handling, key-expiry repair issue |
| `sensor.py` | Sensor descriptions (`value_fn` per sensor) |
| `config_flow.py` | Setup, reauth and options flows |
| `diagnostics.py` | Redacted diagnostics |

## Acknowledgements

Endpoint paths and the token flow follow Garmin's published
[Personal API docs and OpenAPI spec](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/)
and Garmin's official Apache-2.0 Python client,
[garmin/connect-personal-api-python](https://github.com/garmin/connect-personal-api-python).
No code is copied from it; this integration uses Home Assistant's async HTTP
session instead of `requests`.

## License

MIT — see [LICENSE](LICENSE).
