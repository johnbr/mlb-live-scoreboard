import json
from pathlib import Path

DOMAIN = "mlb_live_scoreboard"
PLATFORMS = ["sensor"]

# Single source of truth for the shipped version: manifest.json. Used both for
# Lovelace cache busting and for the outbound User-Agent below.
_MANIFEST_PATH = Path(__file__).parent / "manifest.json"
try:
    with open(_MANIFEST_PATH) as _f:
        INTEGRATION_VERSION = json.load(_f).get("version", "0.0.0")
except Exception:  # pragma: no cover - manifest is always shipped alongside
    INTEGRATION_VERSION = "0.0.0"

# ESPN sits behind Akamai, which rejects (HTTP 403 "Access Denied") requests
# whose User-Agent it doesn't recognize as a well-formed, self-identifying
# client. A bare product token is not enough — the previous "Home Assistant"
# value, and even "mlb-live-scoreboard/<version>" on its own, are both denied.
# Naming the project and pairing it with a contact URL is both honest about who
# we are and accepted by the edge; the "(+url)" comment is the part that matters,
# so don't "simplify" this back down to a bare token.
USER_AGENT = (
    f"mlb-live-scoreboard/{INTEGRATION_VERSION} "
    "(+https://github.com/johnbr/mlb-live-scoreboard)"
)

CONF_TEAM = "team"
CONF_NAME = "name"

DEFAULT_NAME = "MLB Live Scoreboard"
DEFAULT_SCAN_INTERVAL_SECONDS = 5

# Adaptive polling. The 5 s cadence is only needed while a game is actually
# in progress; the rest of the day the displayed game is a final or a future
# matchup whose data barely moves. Around first pitch (and through the
# post-final window, where records / decisions / highlights settle) a 30 s
# cadence keeps game-start detection prompt without hammering ESPN.
SCAN_INTERVAL_LIVE_SECONDS = DEFAULT_SCAN_INTERVAL_SECONDS
SCAN_INTERVAL_NEAR_GAME_SECONDS = 30
SCAN_INTERVAL_IDLE_SECONDS = 300

# Window around an event's scheduled start treated as "near game": from this
# long before the scheduled first pitch (covers lineup posts and early status
# flips) until this long after it (covers a full game plus the post-final
# window in which ESPN finishes attaching decisions and updating records).
NEAR_GAME_LEAD_SECONDS = 30 * 60
NEAR_GAME_LAG_SECONDS = 5 * 60 * 60

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

# How long (in seconds, anchored to the play's wallclock timestamp) the third-out
# play result should remain on screen before yielding to the Due Up panel.
# Computed server-side so every card transitions in lockstep regardless of when
# the dashboard was first rendered.
THIRD_OUT_HOLD_SECONDS = 30

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

# How long to cache a player's full career card (bio + career stats table).
# Career stats change at most once per day per player, and the popup is opened
# interactively rather than polled, so a long TTL keeps repeat opens instant
# without staleness that matters at career granularity.
PLAYER_CARD_TTL_SECONDS = 6 * 60 * 60

# Maximum age of a cached player card still acceptable as a fallback when an
# ESPN athlete endpoint fails, so a transient outage doesn't blank the popup.
PLAYER_CARD_STALE_FALLBACK_SECONDS = 24 * 60 * 60

# How long to cache a single athlete's parsed current-season line (the
# lineup popup's Season view). Season totals move at most once per game per
# player and the popup is opened interactively rather than polled, so the
# same long-TTL semantics as the player card apply.
TEAM_SEASON_STATS_TTL_SECONDS = 6 * 60 * 60

# Maximum age of a cached season line still acceptable as a fallback when the
# ESPN stats endpoint fails, so a transient outage doesn't blank the popup.
TEAM_SEASON_STATS_STALE_FALLBACK_SECONDS = 24 * 60 * 60

# How long to cache the team's schedule payload. The schedule is only used to
# enumerate this team's events (previous / live / next) and to read the team's
# display name; none of the in-game state (score, count, plays) comes from it.
# Refreshing every 30 minutes is more than sufficient to pick up start-time
# changes, postponements, or newly added games while eliminating the per-poll
# hit (which dominates per-game bandwidth at the 5 s coordinator interval).
SCHEDULE_TTL_SECONDS = 30 * 60

# Maximum age of a cached schedule payload that is still acceptable as a
# fallback when ESPN's schedule endpoint fails. Beyond this we let the
# coordinator raise UpdateFailed so the sensor goes unavailable.
SCHEDULE_STALE_FALLBACK_SECONDS = 5 * 60

# The All-Star Game is auto-displayed by every entry on the local calendar day
# it's played (no club plays that day), regardless of which team the entry
# follows. ``teams/al/schedule`` returns just the single mid-July All-Star
# event; either league's slug works, so we pick one. The schedule id/date barely
# changes, so a day-long TTL keeps this to ~one extra request/day/team year-round
# (the date gate that decides whether to override is recomputed every poll, so a
# long cache doesn't delay activation); the stale fallback rides out ESPN blips.
ALLSTAR_TEAM_SLUG = "al"
ALLSTAR_SCHEDULE_TTL_SECONDS = 24 * 60 * 60
ALLSTAR_SCHEDULE_STALE_FALLBACK_SECONDS = 7 * 24 * 60 * 60

# How long to cache the division-standings payload. Standings change at most
# a few times per day, so a 10-minute TTL eliminates per-poll calls without
# making the displayed standings feel stale.
STANDINGS_TTL_SECONDS = 600

# Maximum age of a cached standings payload that is still acceptable as a
# fallback when ESPN's standings endpoint fails. Beyond this we drop the
# cache and the card simply renders empty standings.
STANDINGS_STALE_FALLBACK_SECONDS = 60 * 60

# How long to cache the league/divisions structure (the ``groups`` endpoint).
# Divisions don't change mid-season, so a 24-hour TTL is appropriate; the
# stale fallback keeps things working through extended ESPN outages.
GROUPS_TTL_SECONDS = 24 * 60 * 60
GROUPS_STALE_FALLBACK_SECONDS = 7 * 24 * 60 * 60

# Largest run increase we'll treat as a single real scoring event. A single
# play scores at most 4 runs (grand slam), and at the live poll cadence real
# scoring arrives as successive small deltas — so a larger one-poll jump is a
# stale-baseline correction (ESPN transiently under-reporting a score, a
# post-restart re-baseline, or missed polls), not a play. Such jumps are
# suppressed rather than announced as an impossible "N run play".
MAX_PLAUSIBLE_SCORE_DELTA = 4

# Game-event names fired on the Home Assistant event bus. Each is prefixed
# with the integration domain to keep them namespaced from other integrations.
EVENT_TEAM_SCORED = f"{DOMAIN}_team_scored"
EVENT_OPPONENT_SCORED = f"{DOMAIN}_opponent_scored"
EVENT_GAME_STARTED = f"{DOMAIN}_game_started"
EVENT_GAME_ENDED = f"{DOMAIN}_game_ended"
EVENT_GAME_WON = f"{DOMAIN}_game_won"
EVENT_GAME_LOST = f"{DOMAIN}_game_lost"

# Options keys for the per-event action sequences a user can configure
# through the integration's Options flow. Stored under entry.options.
OPT_ON_TEAM_SCORED = "on_team_scored"
OPT_ON_OPPONENT_SCORED = "on_opponent_scored"
OPT_ON_GAME_STARTED = "on_game_started"
OPT_ON_GAME_ENDED = "on_game_ended"
OPT_ON_GAME_WON = "on_game_won"
OPT_ON_GAME_LOST = "on_game_lost"

# Mapping from event name -> option key. Used by the coordinator to look up
# and run the configured action sequence when an event fires.
EVENT_OPTION_KEYS: dict[str, str] = {
    EVENT_TEAM_SCORED: OPT_ON_TEAM_SCORED,
    EVENT_OPPONENT_SCORED: OPT_ON_OPPONENT_SCORED,
    EVENT_GAME_STARTED: OPT_ON_GAME_STARTED,
    EVENT_GAME_ENDED: OPT_ON_GAME_ENDED,
    EVENT_GAME_WON: OPT_ON_GAME_WON,
    EVENT_GAME_LOST: OPT_ON_GAME_LOST,
}

MLB_TEAM_MAP = {
  # All-Star pseudo-teams. ESPN models the mid-July All-Star Game as a matchup
  # between two league "teams" with their own team IDs and schedule endpoints
  # (``teams/al/schedule`` -> 31, ``teams/nl/schedule`` -> 32), so picking one
  # of these makes the existing schedule -> summary -> card pipeline follow the
  # All-Star Game with no special-casing. Their schedule carries only the single
  # All-Star event, so the card is live around the game and idle otherwise.
  "AL": 31,  # American League All-Stars
  "NL": 32,  # National League All-Stars
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

# ESPN team IDs of the two All-Star squads, as strings (the summary payload
# carries competitor IDs as strings). Derived from MLB_TEAM_MAP so the two
# cannot drift apart.
#
# This is the *only* reliable in-payload signal that a summary is the All-Star
# Game: ``header.season.type`` is 2 for the All-Star Game exactly as it is for a
# regular-season game (verified live against both), so it cannot be used to tell
# them apart. The distinction matters because ESPN reports player AVG/ERA
# differently in the two — see ``_is_allstar_summary``.
ALLSTAR_TEAM_IDS = frozenset({str(MLB_TEAM_MAP["AL"]), str(MLB_TEAM_MAP["NL"])})
