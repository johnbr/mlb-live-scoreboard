DOMAIN = "mlb_live_scoreboard"
PLATFORMS = ["sensor"]

CONF_TEAM = "team"
CONF_NAME = "name"

DEFAULT_NAME = "MLB Live Scoreboard"
DEFAULT_SCAN_INTERVAL_SECONDS = 5

# ESPN status state values that indicate a live game.
LIVE_STATES = frozenset({"in", "live"})
STATUS_NAME_IN_PROGRESS = "STATUS_IN_PROGRESS"
STATUS_NAME_DELAYED = "STATUS_DELAYED"
STATUS_NAME_FINAL = "STATUS_FINAL"
STATUS_NAME_SCHEDULED = "STATUS_SCHEDULED"

# Limits used when normalizing ESPN payloads.
MAX_LINESCORES = 12
BATTING_ORDER_SIZE = 9
DUE_UP_LIMIT = 3
LEADER_LIMIT = 3

# Threshold for switching the displayed event from a completed prior game to
# the next scheduled game (in seconds).
SHOW_NEXT_AFTER_PREV_SECONDS = 16 * 60 * 60

# How long to cache ESPN team metadata (logo / record summary). Team metadata
# changes only on roster moves and standings updates, so refetching every 5 s
# is wasteful — re-use the previous payload until this many seconds have passed.
TEAM_METADATA_TTL_SECONDS = 3600

# How long to cache an athlete's season stats. Season stats only change when
# the player completes a plate appearance, so a short cache eliminates the
# repeat ESPN calls that happen during a long at-bat without making in-game
# stat updates feel stale.
BATTER_SEASON_STATS_TTL_SECONDS = 60

# Maximum age of a cached schedule payload that is still acceptable as a
# fallback when ESPN's schedule endpoint fails. Beyond this we let the
# coordinator raise UpdateFailed so the sensor goes unavailable.
SCHEDULE_STALE_FALLBACK_SECONDS = 5 * 60

MLB_TEAM_MAP = {
  "ARI": 29,
  "ATH": 11,
  "ATL": 15,
  "BAL": 1,
  "BOS": 2,
  "CHC": 16,
  "CIN": 17,
  "CLE": 5,
  "COL": 27,
  "CWS": 4,
  "DET": 6,
  "HOU": 18,
  "KC": 7,
  "LAA": 3,
  "LAD": 19,
  "MIA": 28,
  "MIL": 8,
  "MIN": 9,
  "NYM": 21,
  "NYY": 10,
  "OAK": 11,
  "PHI": 22,
  "PIT": 23,
  "SD": 25,
  "SEA": 12,
  "SF": 26,
  "STL": 24,
  "TB": 30,
  "TEX": 13,
  "TOR": 14,
  "WSH": 20,
}
