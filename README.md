# MLB Live Scoreboard

A Home Assistant custom integration and Lovelace card for displaying live MLB game data from ESPN.

![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)
![Version](https://img.shields.io/badge/version-1.0.0-blue.svg)

## Features

- **Live game tracking** - Real-time scores, innings, count, and base runners
- **Pitcher/Batter matchup** - Current at-bat with player headshots and stats
- **Play-by-play** - Recent plays and pitch-by-pitch updates
- **Pre-game info** - Scheduled game times and probable pitchers
- **Post-game results** - Final scores and game leaders
- **Configurable display** - Toggle various UI elements on/off

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots in the top right and select "Custom repositories"
3. Add this repository URL and select "Integration" as the category
4. Click "Install"
5. Restart Home Assistant

### Manual Installation

1. Copy the `custom_components/mlb_live_scoreboard` folder to your Home Assistant `config/custom_components/` directory
2. Copy `dist/mlb-live-game-card.js` to your `config/www/` folder
3. Restart Home Assistant

## Configuration

### Integration Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "MLB Live Scoreboard"
3. Select your team (e.g., LAD for Los Angeles Dodgers)
4. Enter a display name (optional)

This creates a sensor entity like `sensor.mlb_live_scoreboard_lad`.

### Lovelace Card Setup

Add the card resource (if not automatically added):

```yaml
resources:
  - url: /local/mlb-live-game-card.js
    type: module
```

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

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please open an issue or pull request.
