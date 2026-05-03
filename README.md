# MLB Live Scoreboard

A Home Assistant custom integration and Lovelace card for displaying live MLB game data from ESPN.

![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)
![Version](https://img.shields.io/badge/version-1.8.7-blue.svg)

## Features

- **Live game tracking** - Real-time scores, innings, count, and base runners
- **Pitcher/Batter matchup** - Current at-bat with player headshots and stats
- **Play-by-play** - Recent plays and pitch-by-pitch updates
- **Pre-game info** - Scheduled game times and probable pitchers
- **Post-game results** - Final scores and game leaders
- **Division standings popup** - Click an upcoming or completed game card to expand probable starters (upcoming only) and current division standings
- **Configurable game-event actions** - Fire Home Assistant events (or invoke services directly from the integration options) on team scored, opponent scored, game won, game lost, and game started, so you can drive lights, TTS, notifications, or any other automation. See [Game Event Actions](#game-event-actions) below.
- **Configurable display** - Toggle various UI elements on/off
- **Auto-registered card** - The Lovelace card is automatically registered on install

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right and select "Custom repositories"
3. Add this repository URL and select "Integration" as the category
4. Click "Install"
5. Restart Home Assistant

The JavaScript card is automatically served by the integration - no manual file copying needed!

### Manual Installation

1. Copy the `custom_components/mlb_live_scoreboard` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

### Integration Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "MLB Live Scoreboard"
3. Select your team (e.g., LAD for Los Angeles Dodgers)
4. Enter a display name (optional)

This creates a sensor entity like `sensor.mlb_live_scoreboard_lad`.

### Lovelace Card Setup

The card resource is automatically registered at `/mlb_live_scoreboard/mlb-live-game-card.js`.

If the auto-registration doesn't work, manually add the resource:
1. Go to **Settings → Dashboards → ⋮ → Resources**
2. Add URL: `/mlb_live_scoreboard/mlb-live-game-card.js`
3. Type: **JavaScript Module**

Add the card to your dashboard:

```yaml
type: custom:mlb-live-game-card
entity: sensor.mlb_live_scoreboard_lad
title: Dodgers
```

## Card Configuration Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `entity` | string | **required** | The MLB scoreboard sensor entity |
| `title` | string | Team name | Card title |
| `refresh_rate` | number | `0` | Auto-refresh interval in seconds (0 = disabled) |
| `show_batter` | boolean | `true` | Show pitcher/batter matchup panel |
| `show_records` | boolean | `true` | Show team win/loss records |
| `show_linescore` | boolean | `false` | Show detailed inning-by-inning linescore |
| `show_pitches` | boolean | `true` | Show pitch-by-pitch display |
| `show_play_results` | boolean | `true` | Show play-by-play results |
| `show_on_deck` | boolean | `true` | Show on-deck batter |
| `show_base_occupancy` | boolean | `true` | Show base runner names |
| `show_diamond` | boolean | `true` | Show base diamond graphic |
| `show_count` | boolean | `true` | Show balls/strikes/outs dots |

### Example with all options

```yaml
type: custom:mlb-live-game-card
entity: sensor.mlb_live_scoreboard_lad
title: Dodgers
refresh_rate: 10
show_batter: true
show_records: true
show_linescore: false
show_pitches: true
show_play_results: true
show_on_deck: true
show_base_occupancy: true
show_diamond: true
show_count: true
```

## Game Event Actions

The integration fires Home Assistant events on the bus whenever notable
in-game things happen for the team you've configured. You can react to
these in two ways:

1. **Built-in options flow** — quick & visual: Settings → Devices & Services →
   *MLB Live Scoreboard* → **Configure**. Each event has a field that accepts
   any sequence of Home Assistant actions (call services, run scripts, fire
   notifications, activate scenes, etc.).
2. **Automations against the event bus** — more flexible: write your own
   automations triggered on the events listed below. Use this when you need
   conditions, multi-step logic, or want different behavior in different
   automations.

Both mechanisms work simultaneously. Configured options run in addition to,
not instead of, any automations you have listening for the same events.

### Events fired

| Event type | When it fires |
|---|---|
| `mlb_live_scoreboard_team_scored` | Your team's score increased since the last poll |
| `mlb_live_scoreboard_opponent_scored` | The opposing team's score increased |
| `mlb_live_scoreboard_game_started` | The game transitioned from scheduled to live |
| `mlb_live_scoreboard_game_ended` | The game transitioned to final (any result) |
| `mlb_live_scoreboard_game_won` | Game ended and your team won |
| `mlb_live_scoreboard_game_lost` | Game ended and your team lost |

A tie/suspension fires `game_ended` but neither `game_won` nor `game_lost`.

### Event payload

Every event includes the same base payload, with two extra fields on
score-change events:

| Field | Type | Description |
|---|---|---|
| `team_abbr` | string | Your configured team's abbreviation, e.g. `"LAD"` |
| `team_name` | string | Your configured team's display name |
| `team_score` | int | Your team's score *after* this event |
| `opponent_abbr` | string | Opposing team's abbreviation |
| `opponent_name` | string | Opposing team's display name |
| `opponent_score` | int | Opponent's score *after* this event |
| `is_home` | bool | True if your team is the home side |
| `inning` | int | Current inning number (0 if not started) |
| `inning_half` | string | `"top"`, `"bottom"`, or `""` |
| `event_id` | string | ESPN event ID for the game |
| `status_detail` | string | Human-readable status text, e.g. `"Bot 7th"` |
| `score_delta` | int | (`*_scored` only) How many runs scored on this play |
| `scoring_play_text` | string | (`*_scored` only) ESPN play description, when available |

### Detection rules

- The first refresh after Home Assistant starts only **establishes a
  baseline** — it does not fire any events. Score and state changes are
  detected on subsequent refreshes.
- When a new game appears (different `event_id`), no events are fired for
  that polling cycle to avoid spurious score events across game boundaries.
  The next refresh becomes the new baseline.
- `team_scored` / `opponent_scored` only fire on positive score deltas, and
  are suppressed while the game is delayed (since ESPN occasionally
  corrects scores during a delay).
- `game_ended` / `game_won` / `game_lost` only fire on the *transition*
  into the final state — they will not re-fire on subsequent refreshes
  while the game remains final.

### Example: automation triggered by a bus event

```yaml
automation:
  - alias: "Flash lights and notify when Dodgers score"
    trigger:
      platform: event
      event_type: mlb_live_scoreboard_team_scored
      event_data:
        team_abbr: LAD
    action:
      - service: light.turn_on
        target:
          entity_id: light.living_room
        data:
          flash: short
          color_name: blue
      - service: notify.mobile_app_phone
        data:
          title: >-
            Dodgers scored! ({{ trigger.event.data.team_score }}-{{
            trigger.event.data.opponent_score }})
          message: "{{ trigger.event.data.scoring_play_text }}"
```

### Example: configured action via the options flow

In the options flow's **When my team wins** field:

```yaml
- service: notify.persistent_notification
  data:
    title: "{{ team_name }} won!"
    message: "Final: {{ team_score }}-{{ opponent_score }} vs {{ opponent_name }}"
- service: scene.turn_on
  target:
    entity_id: scene.victory_celebration
```

Inside an option-flow action sequence, payload fields are available as
top-level template variables (e.g. `{{ team_score }}`), whereas in
automations they're nested under `trigger.event.data` (e.g.
`{{ trigger.event.data.team_score }}`).

## Supported Teams

| Abbreviation | Team |
|--------------|------|
| ARI | Arizona Diamondbacks |
| ATH | Athletics |
| ATL | Atlanta Braves |
| BAL | Baltimore Orioles |
| BOS | Boston Red Sox |
| CHC | Chicago Cubs |
| CIN | Cincinnati Reds |
| CLE | Cleveland Guardians |
| COL | Colorado Rockies |
| CWS | Chicago White Sox |
| DET | Detroit Tigers |
| HOU | Houston Astros |
| KC | Kansas City Royals |
| LAA | Los Angeles Angels |
| LAD | Los Angeles Dodgers |
| MIA | Miami Marlins |
| MIL | Milwaukee Brewers |
| MIN | Minnesota Twins |
| NYM | New York Mets |
| NYY | New York Yankees |
| OAK | Oakland Athletics |
| PHI | Philadelphia Phillies |
| PIT | Pittsburgh Pirates |
| SD | San Diego Padres |
| SEA | Seattle Mariners |
| SF | San Francisco Giants |
| STL | St. Louis Cardinals |
| TB | Tampa Bay Rays |
| TEX | Texas Rangers |
| TOR | Toronto Blue Jays |
| WSH | Washington Nationals |

## Data Source

This integration uses ESPN's public API for MLB game data. Data is refreshed every 5 seconds during live games.

For details on data flow, sensor attributes, ESPN endpoints, and the card's
internal architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or pull request.
