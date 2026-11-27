# Garmin Connect Personal API for Home Assistant

<!-- ha-marketplace:exclude-start -->
[![Validate](https://github.com/cameronengineer/ha-garmin-personal/actions/workflows/validate.yml/badge.svg)](https://github.com/cameronengineer/ha-garmin-personal/actions/workflows/validate.yml)
[![Tests](https://github.com/cameronengineer/ha-garmin-personal/actions/workflows/tests.yml/badge.svg)](https://github.com/cameronengineer/ha-garmin-personal/actions/workflows/tests.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
<!-- ha-marketplace:exclude-end -->

Bring your Garmin health and fitness data into Home Assistant — 69 sensors
(steps, live heart rate and zone, stress, Body Battery, sleep and sleep need,
HRV, Pulse Ox, VO2 max, fitness age, weight, nutrition, weekly training totals),
an **activities calendar**, a **new-activity event** for automations, and an
action to **download the original FIT file** of any workout — using **Garmin's official [Connect Personal API](https://developer.garmin.com/gc-developer-program/personal/)**
(launched October 2026).

No Garmin password, no scraping, no unofficial login. You create a Personal
API key in Garmin Connect, paste it into Home Assistant, and that's it.

> **Not affiliated with Garmin.** "Garmin", "Garmin Connect" and "Body Battery"
> are trademarks of Garmin Ltd. or its subsidiaries. This is an independent,
> community-built integration that uses Garmin's public Personal API under
> Garmin's terms.

> **Status: early (v0.3).** Built from Garmin's published OpenAPI spec and
> [verified against the live API](#verified-against-the-live-api). If a sensor
> looks wrong for you, run the [smoke test](#checking-against-your-own-account)
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

### Home Assistant 2026.11 and newer (Marketplace)

[![Open in the Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cameronengineer&repository=ha-garmin-personal&category=integration)

1. **Settings → Marketplace**, search for **Garmin Connect Personal API**, and
   **Install**.
2. Restart Home Assistant.

### Older Home Assistant (HACS)

1. In HACS, search for **Garmin Connect Personal API** and download it.
2. Restart Home Assistant.

### If it doesn't show up in search

Add the repository by hand, then install it as above:

- **Marketplace:** ⋮ **Add from link**, paste
  `https://github.com/cameronengineer/ha-garmin-personal`, type **Integration**.
- **HACS:** ⋮ **Custom repositories**, add the same URL, category **Integration**.

### Manual

Copy `custom_components/garmin_personal/` into your Home Assistant
`config/custom_components/` folder and restart.

## Setup

1. In a browser, sign in to Garmin Connect and open
   **[Settings → Personal API Key](https://connect.garmin.com/app/settings/personalApiToken)**.
2. **Create Key**, accept the terms, and choose an expiry (30 days, 90 days,
   180 days or 1 year). Copy the key — Garmin only shows it once.
3. In Home Assistant: **Settings → Devices & services → Add integration →
   Garmin Connect Personal API** ([open it directly](https://my.home-assistant.io/redirect/config_flow_start/?domain=garmin_personal)), paste the key, pick your server (Global, or
   China for `connect.garmin.cn` accounts).

You'll get one device, named after your Garmin display name, with the sensors,
calendar and event below.

### When the key expires

Personal API keys expire on the schedule you chose. Garmin emails you about a
week beforehand. This integration also raises a **repair notice** in Home
Assistant during the key's last day, and once the key stops working Home
Assistant shows a **re-authentication prompt**: create a new key in Garmin
Connect and paste it there. Your entities, history and automations are kept.

## Sensors

30 sensors are on by default. The ones marked "—" are created disabled to keep
dashboards and history tidy (Home Assistant's own integrations do the same):
open the device page, pick the sensor, ⚙️ → **Enabled**.

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
| Heart rate | bpm | yes |
| Heart rate zone |  | yes |
| Resting heart rate | bpm | yes |
| Resting heart rate 7-day average | bpm | — |
| Minimum heart rate | bpm | — |
| Maximum heart rate | bpm | — |
| Max heart rate setting | bpm | — |
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
| Pulse Ox 7-day average | % | — |
| Lowest Pulse Ox | % | — |
| Sleeping Pulse Ox | % | — |
| **Sleep (last night)** | | |
| Sleep duration | h | yes |
| Sleep score |  | yes |
| Fell asleep | timestamp | yes |
| Woke up | timestamp | yes |
| Sleep need tonight | h | yes |
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
| HRV 5-minute high | ms | — |
| HRV status |  | yes |
| **Fitness** | | |
| VO2 max | mL/kg/min | yes |
| VO2 max cycling | mL/kg/min | — |
| Fitness age | y | yes |
| **Weight (latest weigh-in, last 12 months)** | | |
| Weight | kg | yes |
| BMI |  | — |
| Body fat | % | — |
| Last weigh-in | timestamp | — |
| Weight goal | kg | — |
| **Nutrition (if you log food in Garmin Connect)** | | |
| Calories eaten | kcal | — |
| Carbs eaten | g | — |
| Protein eaten | g | — |
| Fat eaten | g | — |
| **Activities** | | |
| Last activity |  | yes |
| Last activity start | timestamp | yes |
| Last activity duration | min | yes |
| Workouts this week | workouts | yes |
| Activity time this week | min | yes |
| Distance this week | km | yes |
| Activity calories this week | kcal | yes |
| **Cycle tracking (if enabled in Garmin Connect)** | | |
| Cycle day |  | — |
| **Diagnostic** | | |
| Access token expires | timestamp | — |

Notes:

- **Daily** values (steps, calories, stress, Body Battery…) are for today in
  Home Assistant's time zone, and reset at midnight.
- **Sleep** and **HRV** show your most recent night. They keep last night's
  values until the next night has synced, rather than going blank at midnight.
- **Heart rate** and **Heart rate zone** show *unknown* once the newest reading
  is over an hour old (the watch only uploads when it syncs), so a stale value
  is never presented as current.
- **Running totals** (steps, calories, intensity minutes, food, and the weekly
  totals) report Home Assistant's `total` state class with a `last_reset` at
  local midnight or the start of your Garmin week, so long-term statistics
  stay correct even if Garmin revises a number down.
- Lists and per-update details (Body Battery events, this week's workouts,
  activity details, sleep-score components) are on the state but kept out of
  Home Assistant's history database.
- Garmin uses `-1`/`-2` to mean "no reading"; those show as *unknown* rather than
  a misleading number.
- **Weekly** totals start on the first day of the week set in your Garmin
  profile (e.g. Sunday), matching Garmin Connect's own weekly view.
- Extra detail is on attributes: the step goal, Body Battery feedback, sleep
  score components, HRV baseline ranges, and the latest activity's type,
  duration, distance, calories, heart rate and training effect.
- The latest activity deliberately has **no GPS coordinates**, so your
  start/finish locations don't end up in Home Assistant's history.

## Activities calendar

`calendar.garmin_<name>_activities` shows every recorded activity in Home
Assistant's **Calendar** panel — scroll back as far as you like. Each event has
the activity name, start and end time, and a description with type, duration,
distance, calories, heart rate, elevation, training effect, training load and
Body Battery impact. It's fetched from Garmin when you open a date range, so it
costs nothing while you're not looking at it.

## New-activity event

`event.garmin_<name>_new_activity` fires `new_activity` once for each workout
that syncs after Home Assistant starts (activities already in Garmin at
startup don't fire). The event carries the same details as the calendar:
`activity_id`, `name`, `activity_type`, `start_time`, `duration_minutes`,
`distance_km`, `calories`, `average_hr`, `max_hr`, training effect, minutes in
each heart-rate zone, and more.

```yaml
automation:
  - alias: Post-workout shower heat
    triggers:
      - trigger: state
        entity_id: event.garmin_YOURNAME_new_activity
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.attributes.duration_minutes | float(0) > 20 }}"
    actions:
      - action: switch.turn_on
        target:
          entity_id: switch.bathroom_heater
```

## Downloading FIT files

The **Download activity file** action (`garmin_personal.download_activity`)
saves an activity's original FIT file into `garmin_activities/` in your Home
Assistant config folder and returns the path. Leave `activity_id` empty for
your latest activity; IDs are on the *Last activity* sensor and in calendar
events.

```yaml
action: garmin_personal.download_activity
data:
  activity_id: 21234567890   # optional
response_variable: download  # download.files = ["/config/garmin_activities/…fit"]
```

## How often it polls

Data only changes when your watch syncs to Garmin Connect, so there's no point
hammering the API, and Garmin doesn't publish rate limits.

- **Today's summary and heart rate:** every 15 minutes by default. Change it
  under the integration's **Configure** (5–240 minutes).
- **Sleep, HRV, weight, recent activities, Body Battery events, Pulse Ox, food
  and cycle data:** at most once an hour.
- **VO2 max, fitness age, heart-rate zones and weight goal:** every 6 hours.
- **The activities calendar** only calls Garmin when you open it.
- **At startup** only today's summary is fetched before the integration
  loads; everything else follows in the background, so those sensors read
  *unknown* for a moment after a restart.
- **Access tokens** last 24 hours and are renewed automatically, an hour early.

That's roughly 400 requests a day at the default setting.

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
  name, device IDs, GPS coordinates, location names, sleep notes and activity
  descriptions/comments, and leave out cycle-tracking and food logs entirely.
- To revoke access at any time, delete the key in Garmin Connect.

## Verified against the live API

Checked against a real account (November 2026). Where the live API differs
from Garmin's spec, the integration follows the live behaviour:

| Detail | Spec says | Live API |
|---|---|---|
| Access token | JWT, 24 h | Plain-text JWT, exactly 24 h; includes a stable `garmin_guid` claim (used as the account ID) |
| Wrong or revoked key | — | HTTP 401 from the token endpoint → re-authentication prompt |
| Sleep timestamps | `date-time`, "in ms" | Epoch milliseconds |
| Sleep score keys | not listed | `overall`, `totalDuration`, `stress`, `awakeCount`, `remPercentage`, `restlessness`, `lightPercentage`, `deepPercentage` |
| Weight | kilograms | **grams** (converted to kg) |
| Day with no sleep yet | — | HTTP 200 with an empty skeleton → sleep sensors show *unknown* |
| Day with no HRV | 204 | 204 |
| Stress qualifier with no value | — | the string `"unknown"` → shown as *unknown* |
| Rate-limit headers | — | none sent |
| FIT download | ZIP | ZIP (`application/x-zip-compressed`); **HTTP 406** if the request only accepts `application/octet-stream` |
| Heart rate and Body Battery readings | arrays | `[epoch_ms, value]` pairs; the newest heart-rate entry can be `null` |
| VO2 max | per sport | empty unless you've done qualifying runs/rides |

Still unconfirmed: actual rate limits (none published, none hit at ~200
requests a day), and the China (`.cn`) server.

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
| `coordinator.py` | Three-tier polling, partial-failure handling, key-expiry repair issue |
| `sensor.py` | Sensor descriptions (`value_fn` per sensor) |
| `calendar.py` | Activities calendar (fetches a date range on demand) |
| `event.py` | New-activity event entity |
| `services.py` | `download_activity` action |
| `config_flow.py` | Setup, reauth and options flows |
| `diagnostics.py` | Redacted diagnostics |
| `translations/en.json` | All UI text (entity names, config flow, action, errors) |
| `brand/` | The integration's own icon (shown in Settings and the Marketplace) |

## Acknowledgements

Endpoint paths and the token flow follow Garmin's published
[Personal API docs and OpenAPI spec](https://developer.garmin.com/gc-developer-program/personal/swagger-docs/)
and Garmin's official Apache-2.0 Python client,
[garmin/connect-personal-api-python](https://github.com/garmin/connect-personal-api-python).
No code is copied from it; this integration uses Home Assistant's async HTTP
session instead of `requests`.

## License

MIT — see [LICENSE](LICENSE).

## Author

Built by [Cameron Mitchell](https://cameronengineer.com/), an engineer in
Melbourne, Australia. There's a short write-up of why and how it was built on
the [project page](https://cameronengineer.com/projects/garmin-home-assistant/).
