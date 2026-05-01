# Architecture

This document describes how `mlb_live_scoreboard` is organized: where data
comes from, how it flows through the integration, and what surfaces it on
the dashboard.

## High-level data flow

```
                ┌──────────────────────────────────────┐
                │ ESPN public web/site/sports APIs     │
                │  (schedule, summary, team, athlete)  │
                └──────────────────┬───────────────────┘
                                   │ HTTPS (aiohttp)
                                   ▼
        ┌─────────────────────────────────────────────────┐
        │ MlbLiveScoreboardCoordinator                    │
        │   coordinator.py                                │
        │   - DataUpdateCoordinator, 5 s interval         │
        │   - in-memory TTL caches (team, batter, sched)  │
        │   - normalizers → MlbLiveScoreboardData         │
        └────────────────┬────────────────────────────────┘
                         │
                         ▼
        ┌─────────────────────────────────────────────────┐
        │ MlbLiveScoreboardSensor                         │
        │   sensor.py                                     │
        │   - native_value: display_event_id | "idle"     │
        │   - extra_state_attributes: see "Sensor attrs"  │
        └────────────────┬────────────────────────────────┘
                         │ HA state machine
                         ▼
        ┌─────────────────────────────────────────────────┐
        │ mlb-live-game-card.js (Lovelace custom card)    │
        │   - reads hass.states[entity].attributes        │
        │   - renders scoreboard / matchup / linescore    │
        │   - render fingerprint short-circuit            │
        └─────────────────────────────────────────────────┘
```

## Components

### `__init__.py`
- Registers a static path (`/mlb_live_scoreboard/...`) so HA serves the JS card
  bundled with the integration, and registers it as a Lovelace resource.
- Declares `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)` (this is
  required by hassfest because `async_setup` is defined).
- Sets up the `DataUpdateCoordinator` per config entry and forwards to the
  `sensor` platform.

### `const.py`
All static configuration: domain, scan interval, ESPN status names, normalize
limits, cache TTLs, and the `MLB_TEAM_MAP` (abbreviation → ESPN team id).

### `coordinator.py`
The bulk of the integration. Responsibilities:

1. **Schedule fetch** — pulls the team's schedule, picks the most relevant
   game (`_select_event`) using priority order: in-progress → delayed →
   recent final (within `SHOW_NEXT_AFTER_PREV_SECONDS`) → next scheduled.
   Tolerates transient ESPN failures via stale-fallback up to
   `SCHEDULE_STALE_FALLBACK_SECONDS`.
2. **Summary fetch** — for the selected event, pulls live competition data
   (situation, plays, on-deck, etc.).
3. **Team metadata** — separate per-team payload cached for
   `TEAM_METADATA_TTL_SECONDS` (logo, record).
4. **Batter season stats** — per-athlete payload cached for
   `BATTER_SEASON_STATS_TTL_SECONDS` (refreshed often enough to feel live
   without hammering ESPN during long at-bats).
5. **Normalization** — many `_normalize_*` helpers shape ESPN payloads into
   the `MlbLiveScoreboardData` dataclass consumed by the sensor.

Key patterns:
- `_async_update_data` is an orchestrator. It calls helpers
  (`_resolve_display_comp`, `_resolve_competitor_ids`, `_fetch_team_payload`,
  `_resolve_batter_pitcher_ids`, `_resolve_status_info`) and produces the
  final dataclass.
- `_normalize_inning_context` is computed once per refresh and passed to all
  callers that need it.
- All HTTP calls use the shared aiohttp `ClientSession` (`async_get_clientsession`).

### `sensor.py`
A single `MlbLiveScoreboardSensor` per config entry.

- `unique_id`: `<entry_id>_scoreboard`
- `suggested_object_id`: `mlb_live_scoreboard_<team>` →
  `sensor.mlb_live_scoreboard_<team>`
- `native_value`: `display_event_id` (or `"idle"` when no game is in scope)
- `extra_state_attributes`: see table below

`recent_plays` is projected down to the fields the JS card actually reads,
to keep the HA state object small.

### `mlb-live-game-card.js`
A plain ES module custom element (not Lit). It:

- Subscribes to `hass.states[config.entity]` and reads
  `extra_state_attributes`.
- Computes a scalar **render fingerprint** (`_computeRenderFingerprint`) and
  short-circuits if nothing visible changed.
- Holds a small client-side state machine for the "third-out hold" UX
  (encapsulated in `this._thirdOutHold`).
- Renders one of three layouts based on `stateInfo.pillClass`:
  `live`, `final`, or `next` (compact pre-game layout).

## Sensor attributes (HA state)

Top-level `extra_state_attributes` exposed on the sensor:

| Attribute | Type | Notes |
|---|---|---|
| `team_abbr` | str | e.g. `"LAD"` |
| `team_id` | int | ESPN team id |
| `team_name` | str | full team name |
| `mode` | str | `"live"` / `"next"` / `"final"` / `"idle"` |
| `is_live` | bool | game state is in-progress |
| `is_delayed` | bool | ESPN status `STATUS_DELAYED` |
| `status_text` | str | human-readable status |
| `display_event_id` | str | event id the card should render |
| `live_event_id` | str \| None | active game id, if any |
| `previous_event_id` | str \| None | last completed game id |
| `next_event_id` | str \| None | upcoming game id |
| `competition` | dict | ESPN competition object (subset, see below) |
| `inning_context` | dict | period prefix, half, between-halves flag |
| `recent_plays` | list[dict] | trimmed to: `id`, `text`, `outs`, `away_score`, `home_score`, `wallclock_ts` |
| `current_pitches` | list[dict] | per-pitch results for current AB |
| `away_team` / `home_team` | dict | normalized team metadata (logo, record, name) |
| `current_batter` / `current_pitcher` | dict | `display_name`, `short_name`, `headshot` |
| `batter_stats` | dict | `avg`, `hits_ab`, `hr`, `rbi`, `game_outcomes_display`, … |
| `pitcher_stats` | dict | `era`, `ip`, `pitches_strikes`, `strikeouts`, … |
| `situation` | dict | `balls`, `strikes`, `outs`, `onFirst/Second/Third` |
| `due_up` | list[dict] | up to `DUE_UP_LIMIT` next batters |
| `third_out_play` | dict \| None | the play that produced the 3rd out (when ESPN flags it) |
| `on_deck` | dict | next batter info |

`competition` contains the fields the card reads:
`competitors[]` (each with `team`, `score`, `homeAway`, `linescores`, totals),
`status.type.{state,name,displayClock}`, `status.period`, `date`, `id`.

## Card configuration

All card options have safe defaults. Minimum required is `entity`.

| Option | Default | Purpose |
|---|---|---|
| `entity` | (required) | the `sensor.mlb_live_scoreboard_*` entity |
| `title` | `""` | optional display title |
| `show_batter` | `true` | show the batter / pitcher matchup panel |
| `show_records` | `true` | show team `(W-L)` next to names |
| `show_linescore` | `false` | show inning-by-inning grid below the score |
| `show_pitches` | `true` | show per-pitch sequence in the play feed |
| `show_play_results` | `true` | show recent play results |
| `show_on_deck` | `true` | show on-deck batter line |
| `show_base_occupancy` | `true` | show occupied-bases summary row |
| `show_diamond` | `true` | show the bases diamond graphic |
| `show_count` | `true` | show ball/strike/out count dots |
| `refresh_rate` | `0` | seconds; `0` disables (rely on HA state updates) |

## ESPN endpoints used

| Endpoint | Purpose | Cache |
|---|---|---|
| `site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/<abbr>/schedule` | team schedule | per refresh + 5 min stale fallback |
| `site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event=<id>` | live game state | per refresh |
| `site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/<id>` | team logo / record | 1 hour (`TEAM_METADATA_TTL_SECONDS`) |
| `site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/<id>/stats` | batter season stats | 60 s (`BATTER_SEASON_STATS_TTL_SECONDS`) |

These are unauthenticated public endpoints. Calls share a single aiohttp
session and are awaited concurrently where independent.

## Testing & validation

- `ruff check .` — lint (configured in `pyproject.toml`)
- `python3 -m py_compile custom_components/mlb_live_scoreboard/*.py`
- `node --check custom_components/mlb_live_scoreboard/mlb-live-game-card.js`
- `pre-commit run --all-files` runs ruff, prettier, and standard hooks.
- GitHub Actions: `validate.yml` (HACS) and `hassfest.yml` (HA core).
