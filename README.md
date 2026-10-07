# MLB Live Scoreboard / GameTracker

A Home Assistant custom integration and Lovelace card for displaying live MLB game data from ESPN.

![HACS](https://img.shields.io/badge/HACS-Default-blue.svg)
![Version](https://img.shields.io/github/v/release/johnbr/mlb-live-scoreboard?label=version&color=blue)

## Features

- **Live game tracking** - Real-time scores, innings, count, base runners, and a live win-probability bar
- **Collapsible live card** - A live game starts as just the two score rows + inning marker; click to expand to the full live view (`live_default_view`)
- **Pitcher/Batter matchup** - Current at-bat with player headshots and live in-game stats (ERA/AVG update as the game is played)
- **Play-by-play** - Recent plays and pitch-by-pitch updates (pitch type, velocity, result)
- **Half-inning pager** - Page the live play-by-play back to earlier half-innings ("what happened in the 4th?"); snaps back to the live half after ~20s (`show_inning_nav`)
- **Pitch-zone graphic** - Optional strike-zone plot with one numbered, color-coded dot per pitch in the current at-bat (`show_pitch_zone`, off by default)
- **ABS challenges remaining** - Yellow dots beside each team's score on the live card show its ball-strike challenges left (filled = remaining, hollow = lost); a dot pulses while that team's challenge is under review (`show_challenges`)
- **Due-up panel** - Between half-innings, the matchup row shows the next three batters with portraits and stats
- **Pre-game info** - Scheduled game times and probable pitchers
- **Post-game results** - Final scores and game leaders
- **Compact-card expand panel** - Click an upcoming or completed game card to expand it. Upcoming games show probable starters + current division standings; completed games show the pitching decisions (W/L/SV with records and game lines), every scoring play of the game, and game leaders (top hitters / pitchers per side) above the same standings block. An optional "Watch highlights on ESPN" link appears once clips are published (`show_highlights`)
- **Schedule navigation** - `‹ ›` arrows on the non-live card page back through previous results and forward through upcoming games (`show_schedule_nav`)
- **Postseason support** - Playoff games are included in the schedule, a series banner (e.g. `NLDS · Dodgers lead 2-0`) sits above the score (`show_series`), postseason batting lines use playoff stats, and the expand panel shows the day's playoff games in place of division standings
- **All-Star Game** - On All-Star Game day every card automatically shows the All-Star Game, whichever team it follows; `AL` / `NL` can also be configured as teams for a dedicated All-Star card
- **MLB Stats API live feed** - The live batter, pitcher, count, plays and box score come from MLB's official Stats API by default (faster than ESPN), with ESPN as the fallback; MLB's play text is rewritten in ESPN's terse style. You can switch the preference to ESPN, in which case MLB fills in for games ESPN publishes without play-by-play. See [Live data source](#live-data-source) below
- **Delays and postponements** - Rain/weather delays and suspensions show ESPN's specific delay reason; postponed games show `PPD`
- **Player career popup** - Click any (yellow) player name to open an in-card popup with their bio and season-by-season career stats; configurable to open ESPN's player page instead (`player_link_target`)
- **Team lineup popup** - Click a team's side of the pitcher/batter matchup (anywhere but the player name) to open an in-card popup with that team's full lineup and every player who appeared in the game, toggleable between **Game** (this game's box score) and **Season** stats for hitters and pitchers
- **Configurable game-event actions** - Fire Home Assistant events (or invoke services directly from the integration options) on team scored, opponent scored, game won, game lost, and game started, so you can drive lights, TTS, notifications, or any other automation. See [Game Event Actions](#game-event-actions) below.
- **Configurable display** - Toggle various UI elements on/off
- **Auto-registered card** - The Lovelace card is automatically registered on install

## Screenshots

<!-- Absolute raw.githubusercontent.com URLs (not relative paths) so the
     images render on the HACS frontend as well as on GitHub — HACS doesn't
     resolve relative paths or always preserve raw <img> HTML tags. Each
     WebP is pre-sized to its intended display width so no width attribute
     is needed. -->

**Live game** — scoreline, live win probability, count + outs, pitcher/batter matchup with portraits, base runners, pitch sequence, and recent plays.

![Live game card showing Dodgers vs Padres with full live UI](https://raw.githubusercontent.com/johnbr/mlb-live-scoreboard/main/docs/screenshots/01-live-game.webp)

**Final game with scoring summary** — click a completed game's card to expand it and see every scoring play of the game plus the current division standings.

![Final-game expand panel listing scoring plays and division standings](https://raw.githubusercontent.com/johnbr/mlb-live-scoreboard/main/docs/screenshots/02-final-summary.webp)

**Player career popup** — click any (yellow) player name to open an in-card popup with that player's bio and season-by-season career stats.

![Player career popup showing Vladimir Guerrero Jr.'s career batting table](https://raw.githubusercontent.com/johnbr/mlb-live-scoreboard/main/docs/screenshots/03-career-popup.webp)

**Between-innings due-up panel** — after the third out, the matchup row swaps to show the next three batters' portraits and stats until the half-inning ends.

![Due-up panel showing three on-deck batter portraits between innings](https://raw.githubusercontent.com/johnbr/mlb-live-scoreboard/main/docs/screenshots/04-due-up.webp)

**Track multiple teams** — one card per team, each independently configurable, sharing the dashboard with the rest of your Home Assistant tiles.

![Four MLB cards tiled on a tablet dashboard showing different game states](https://raw.githubusercontent.com/johnbr/mlb-live-scoreboard/main/docs/screenshots/05-multiple-teams.webp)

## Installation

### HACS (Recommended)

This integration is available in the [HACS](https://hacs.xyz/) default store — no custom repository needed.

1. Open HACS in Home Assistant
2. Search for "MLB Live Scoreboard"
3. Click "Install"
4. Restart Home Assistant

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

Add the card to your dashboard — either way:

- **Visual (no YAML):** _Edit dashboard → Add card → search "MLB Live Game Card"_. The picker shows a live preview; the card lands pre-configured against the first MLB Live Scoreboard sensor it finds. Every option below is exposed as a form field — click **Edit** on the card and use the **Visual editor** tab.
- **YAML** (equivalent):

  ```yaml
  type: custom:mlb-live-game-card
  entity: sensor.mlb_live_scoreboard_lad
  title: Dodgers
  ```

## Card Configuration Options

| Option                 | Type    | Default      | Description                                                                                                                                                                                                                                                                                              |
| ---------------------- | ------- | ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `entity`               | string  | **required** | The MLB scoreboard sensor entity                                                                                                                                                                                                                                                                         |
| `title`                | string  | Team name    | Card title                                                                                                                                                                                                                                                                                               |
| `refresh_rate`         | number  | `0`          | Auto-refresh interval in seconds (0 = disabled)                                                                                                                                                                                                                                                          |
| `show_series`          | boolean | `true`       | Postseason only: show the series standing above the score rows (e.g. `NLDS · Dodgers lead 2-0`, `NLDS · Game 1` before the series starts). Renders nothing in the regular season                                                                                                                         |
| `show_challenges`      | boolean | `true`       | Show ball-strike (ABS) challenges remaining as dots left of each team's score on the live card: filled = remaining, hollow = lost (a won challenge is kept). A dot pulses while that team's challenge is under review. Data comes from MLB's Stats API; renders nothing when MLB reports no ABS challenges for the game |
| `show_batter`          | boolean | `true`       | Show pitcher/batter matchup panel                                                                                                                                                                                                                                                                        |
| `show_records`         | boolean | `true`       | Show team win/loss records (regular-season games only; hidden for postseason games)                                                                                                                                                                                                                      |
| `show_linescore`       | boolean | `false`      | Show detailed inning-by-inning linescore                                                                                                                                                                                                                                                                 |
| `show_pitches`         | boolean | `true`       | Show pitch-by-pitch display                                                                                                                                                                                                                                                                              |
| `show_play_results`    | boolean | `true`       | Show play-by-play results                                                                                                                                                                                                                                                                                |
| `show_on_deck`         | boolean | `true`       | Show on-deck batter                                                                                                                                                                                                                                                                                      |
| `show_base_occupancy`  | boolean | `true`       | Show base runner names                                                                                                                                                                                                                                                                                   |
| `show_diamond`         | boolean | `true`       | Show base diamond graphic                                                                                                                                                                                                                                                                                |
| `show_pitch_zone`      | boolean | `false`      | Show a strike-zone graphic under the base diamond with one numbered, color-coded dot per pitch in the current at-bat. Auto-hides between at-bats; off by default                                                                                                                                         |
| `show_count`           | boolean | `true`       | Show balls/strikes/outs dots                                                                                                                                                                                                                                                                             |
| `show_win_probability` | boolean | `true`       | Show live win-probability bar between the score rows and the balls/strikes/outs row (hidden pre-game when ESPN doesn't yet publish a probability series)                                                                                                                                                 |
| `show_highlights`      | boolean | `false`      | Show a "Watch highlights on ESPN" link in the final-game expand panel. Only renders once ESPN publishes clips (typically 30-90 min after the final pitch); off by default                                                                                                                                |
| `player_link_target`   | string  | `popup`      | What clicking a (yellow) player name does: `popup` opens an in-card career-stats popup; `espn` opens ESPN's player page directly. The popup always includes a "View on ESPN" link, so ESPN stays reachable either way                                                                                    |
| `show_lineup_popup`    | boolean | `true`       | Allow clicking a team's side of the matchup to open the team-lineup popup. Set `false` to make the matchup sides inert (player-name links still work)                                                                                                                                                    |
| `show_schedule_nav`    | boolean | `true`       | Show `‹ ›` arrows beside the date/status on the non-live card to page back through previous results and forward through upcoming games (as many taps as needed). Hidden while a game is live; set `false` to hide entirely                                                                                |
| `show_inning_nav`      | boolean | `true`       | Show the half-inning pager on the live card, which swaps the play-by-play to earlier half-innings one at a time (the inning marker shows which half you're viewing). Snaps back to the live half after ~20s without a tap                                                                                |
| `lineup_default_view`  | string  | `auto`       | Which view the lineup popup opens to: `auto` (Game while the game is live, Season otherwise), or force `game` / `season`                                                                                                                                                                                 |
| `live_default_view`    | string  | `collapsed`  | How much of the live card shows by default: `collapsed` (just the two score rows + inning marker) or `expanded` (the full live view). Either way, clicking the score rows — or the `⌄` strip under them — toggles between the two; the choice is per-browser and re-baselines when a new game starts     |
| `headshot_size`        | string  | `auto`       | Size of inline headshots (matchup, due-up, probable pitchers). `auto` scales them with the card's actual width via a CSS container query — responsive to HA's per-column dashboards. Fixed presets: `small` (40px), `medium` (56px), `large` (72px), `xlarge` (88px). Modal-popup avatars are unaffected |

### Example with all options

```yaml
type: custom:mlb-live-game-card
entity: sensor.mlb_live_scoreboard_lad
title: Dodgers
refresh_rate: 10
show_series: true
show_challenges: true
show_batter: true
show_records: true
show_linescore: false
show_pitches: true
show_play_results: true
show_on_deck: true
show_base_occupancy: true
show_diamond: true
show_pitch_zone: false
show_count: true
show_win_probability: true
show_highlights: false
player_link_target: popup
show_lineup_popup: true
show_schedule_nav: true
show_inning_nav: true
lineup_default_view: auto
live_default_view: collapsed
headshot_size: auto
```

## Game Event Actions

The integration fires Home Assistant events on the bus whenever notable
in-game things happen for the team you've configured. You can react to
these in two ways:

1. **Built-in options flow** — quick & visual: Settings → Devices & Services →
   _MLB Live Scoreboard_ → **Configure**. Each event has a field that accepts
   any sequence of Home Assistant actions (call services, run scripts, fire
   notifications, activate scenes, etc.).
2. **Automations against the event bus** — more flexible: write your own
   automations triggered on the events listed below. Use this when you need
   conditions, multi-step logic, or want different behavior in different
   automations.

Both mechanisms work simultaneously. Configured options run in addition to,
not instead of, any automations you have listening for the same events.

### Events fired

| Event type                            | When it fires                                   |
| ------------------------------------- | ----------------------------------------------- |
| `mlb_live_scoreboard_team_scored`     | Your team's score increased since the last poll |
| `mlb_live_scoreboard_opponent_scored` | The opposing team's score increased             |
| `mlb_live_scoreboard_game_started`    | The game transitioned from scheduled to live    |
| `mlb_live_scoreboard_game_ended`      | The game transitioned to final (any result)     |
| `mlb_live_scoreboard_game_won`        | Game ended and your team won                    |
| `mlb_live_scoreboard_game_lost`       | Game ended and your team lost                   |

A tie/suspension fires `game_ended` but neither `game_won` nor `game_lost`.

### Event payload

Every event includes the same base payload, with two extra fields on
score-change events:

| Field               | Type   | Description                                             |
| ------------------- | ------ | ------------------------------------------------------- |
| `team_abbr`         | string | Your configured team's abbreviation, e.g. `"LAD"`       |
| `team_name`         | string | Your configured team's display name                     |
| `team_score`        | int    | Your team's score _after_ this event                    |
| `opponent_abbr`     | string | Opposing team's abbreviation                            |
| `opponent_name`     | string | Opposing team's display name                            |
| `opponent_score`    | int    | Opponent's score _after_ this event                     |
| `is_home`           | bool   | True if your team is the home side                      |
| `inning`            | int    | Current inning number (0 if not started)                |
| `inning_half`       | string | `"top"`, `"bottom"`, or `""`                            |
| `event_id`          | string | ESPN event ID for the game                              |
| `status_detail`     | string | Human-readable status text, e.g. `"Bot 7th"`            |
| `score_delta`       | int    | (`*_scored` only) How many runs scored on this play     |
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
- `game_ended` / `game_won` / `game_lost` only fire on the _transition_
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

## Live data source

By default, the live batter, pitcher, count, plays, and box score come from
[MLB's Stats API](https://statsapi.mlb.com) (`feed/live`), which updates
noticeably faster than ESPN. If MLB's feed is unavailable, the card falls back
to ESPN. The score, hits, errors and inning-by-inning line come from the same
feed as the plays, so a run shows in the score the moment its play appears.
Player names stay linked to ESPN's player pages and career popup whichever
feed is in use.

With ESPN preferred instead, MLB still fills in for games ESPN covers with the
score only (no play-by-play), switching back as soon as ESPN's plays arrive.

You can choose which feed is preferred under Settings → Devices & Services →
_MLB Live Scoreboard_ → **Configure** → **Preferred live data source**:

| Option            | Behavior                                                                                         |
| ----------------- | ------------------------------------------------------------------------------------------------ |
| `MLB Stats API` (default) | MLB for the live batter, pitcher, count, plays, and box score; ESPN whenever MLB's feed is unavailable |
| `ESPN`            | ESPN for everything; MLB fills in when ESPN publishes no play-by-play for a live game             |

While the fallback (non-preferred) feed is in use, the card shows a small
**via ESPN** or **via MLB** tag at the bottom right of the expanded live view.
The sensor's `data_source` / `data_source_fallback`
attributes (below) report the same. Neither feed requires an API key.

MLB's play descriptions are rewritten into ESPN's terse style, so the
play-by-play reads the same whichever feed it comes from: last names only (an
initial when two players share one), plain field positions, runners folded
into one sentence. For example, MLB's "Kyle Tucker singles on a line drive to
left fielder Mauricio Dubón." reads "Tucker singled to left.", and "Kyle
Tucker steals (2) 2nd base." reads "Tucker stole second."

ABS challenge counts (`show_challenges`) always come from MLB, since ESPN
doesn't publish them. During live games the integration polls a field-filtered
copy of MLB's feed (a few hundred bytes) alongside ESPN, or reads them from the
full MLB feed when MLB is already the live source.

## Sensor state attributes

The sensor's state is the ESPN event ID (or `idle`); all game data rides in
state attributes. A few are handy for dashboards and automations:

| Attribute     | Type   | Description                                                                                                       |
| ------------- | ------ | --------------------------------------------------------------------------------------------------------------- |
| `game_active` | bool   | `true` only while the card is displaying the live game (`mode == "live"`). Use it to gate cards/automations on "a game is on screen right now." |
| `mode`        | string | Which event the card shows: `live`, `previous`, or `next`.                                                       |
| `is_live`     | bool   | The displayed competition's status is in-progress (or delayed/suspended).                                        |
| `status_text` | string | Human-readable status detail, e.g. `Top 3rd`, `Final`, `Rain Delay`.                                             |
| `data_source` | string | Which feed supplied the live game detail: `espn` or `mlb_statsapi`.                                              |
| `data_source_fallback` | bool | `true` while a live game is being served by the non-preferred feed (see [Live data source](#live-data-source)). |
| `abs_challenges` | object | Ball-strike challenges per team while the game is live: `{has_challenges, away: {remaining, used_successful, used_failed, in_progress}, home: {…}}`, from MLB's Stats API; `{}` otherwise. |
| `series`      | object | Postseason series standing (e.g. summary `NLDS · Dodgers lead 2-0`); empty in the regular season.                |

Example — only show the scoreboard card while a game is live:

```yaml
type: conditional
conditions:
  - condition: state
    entity: sensor.mlb_live_scoreboard_lad
    attribute: game_active
    state: true
card:
  type: custom:mlb-live-game-card
  entity: sensor.mlb_live_scoreboard_lad
```

## Supported Teams

| Abbreviation | Team                  |
| ------------ | --------------------- |
| ARI          | Arizona Diamondbacks  |
| ATH          | Athletics             |
| ATL          | Atlanta Braves        |
| BAL          | Baltimore Orioles     |
| BOS          | Boston Red Sox        |
| CHC          | Chicago Cubs          |
| CIN          | Cincinnati Reds       |
| CLE          | Cleveland Guardians   |
| COL          | Colorado Rockies      |
| CWS          | Chicago White Sox     |
| DET          | Detroit Tigers        |
| HOU          | Houston Astros        |
| KC           | Kansas City Royals    |
| LAA          | Los Angeles Angels    |
| LAD          | Los Angeles Dodgers   |
| MIA          | Miami Marlins         |
| MIL          | Milwaukee Brewers     |
| MIN          | Minnesota Twins       |
| NYM          | New York Mets         |
| NYY          | New York Yankees      |
| OAK          | Oakland Athletics     |
| PHI          | Philadelphia Phillies |
| PIT          | Pittsburgh Pirates    |
| SD           | San Diego Padres      |
| SEA          | Seattle Mariners      |
| SF           | San Francisco Giants  |
| STL          | St. Louis Cardinals   |
| TB           | Tampa Bay Rays        |
| TEX          | Texas Rangers         |
| TOR          | Toronto Blue Jays     |
| WSH          | Washington Nationals  |
| AL           | American League All-Stars |
| NL           | National League All-Stars |

You don't need the `AL` / `NL` entries to see the All-Star Game: on All-Star
Game day, every card automatically shows it in place of the team's own
schedule (no club plays during the break). A club's card that switches over
this way fires no game-event actions for the exhibition.

## Data Source

This integration uses ESPN's public API for MLB game data, with MLB's Stats
API as a live-game fallback (see [Live data source](#live-data-source)). No
API keys are needed. Polling adapts to the game state: every 5 seconds while a
game is live, every 30 seconds around first pitch and after the final, and
every 5 minutes otherwise.

For details on data flow, sensor attributes, ESPN endpoints, and the card's
internal architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or pull request.
