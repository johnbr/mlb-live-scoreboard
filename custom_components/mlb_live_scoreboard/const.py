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
