from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.script import Script
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ALLSTAR_SCHEDULE_STALE_FALLBACK_SECONDS,
    ALLSTAR_SCHEDULE_TTL_SECONDS,
    ALLSTAR_TEAM_IDS,
    ALLSTAR_TEAM_SLUG,
    BATTER_SEASON_STATS_TTL_SECONDS,
    BATTING_ORDER_SIZE,
    CONF_NAME,
    CONF_TEAM,
    DEFAULT_SCAN_INTERVAL_SECONDS,
    DOMAIN,
    DUE_UP_LIMIT,
    EVENT_GAME_ENDED,
    EVENT_GAME_LOST,
    EVENT_GAME_STARTED,
    EVENT_GAME_WON,
    EVENT_OPPONENT_SCORED,
    EVENT_OPTION_KEYS,
    EVENT_TEAM_SCORED,
    GROUPS_STALE_FALLBACK_SECONDS,
    GROUPS_TTL_SECONDS,
    LEADER_LIMIT,
    LIVE_STATES,
    MAX_LINESCORES,
    MAX_PLAUSIBLE_SCORE_DELTA,
    MLB_TEAM_MAP,
    NEAR_GAME_LAG_SECONDS,
    NEAR_GAME_LEAD_SECONDS,
    PLAYER_CARD_STALE_FALLBACK_SECONDS,
    PLAYER_CARD_TTL_SECONDS,
    SCAN_INTERVAL_IDLE_SECONDS,
    SCAN_INTERVAL_LIVE_SECONDS,
    SCAN_INTERVAL_NEAR_GAME_SECONDS,
    SCHEDULE_STALE_FALLBACK_SECONDS,
    SCHEDULE_TTL_SECONDS,
    SHOW_NEXT_AFTER_PREV_SECONDS,
    STANDINGS_STALE_FALLBACK_SECONDS,
    STANDINGS_TTL_SECONDS,
    STATUS_NAME_DELAYED,
    STATUS_NAME_IN_PROGRESS,
    TEAM_METADATA_TTL_SECONDS,
    TEAM_SEASON_STATS_STALE_FALLBACK_SECONDS,
    TEAM_SEASON_STATS_TTL_SECONDS,
    THIRD_OUT_HOLD_SECONDS,
    USER_AGENT,
)
from .types import (
    BatterStats,
    Competition,
    CurrentBatter,
    CurrentPitch,
    CurrentPitcher,
    DueUpEntry,
    InningContext,
    Leaders,
    Lineups,
    OnDeck,
    PitcherDecisions,
    PitcherStats,
    PlayerCard,
    ProbablePitchers,
    RecentPlay,
    ScoringPlay,
    Situation,
    Standings,
    TeamMetadata,
    WinProbability,
)

_LOGGER = logging.getLogger(__name__)


# Play-text keywords that signal the end of an at-bat. Used by
# `_normalize_current_pitches` to know when to stop scanning back through plays.
_AT_BAT_END_KEYWORDS: tuple[str, ...] = (
    "singled",
    "doubled",
    "tripled",
    "homered",
    "walked",
    "struck out",
    "flied out",
    "grounded out",
    "lined out",
    "popped out",
    "reached on",
    "hit by pitch",
    "fouled out",
    "sacrifice",
    "sacrificed",
    "intentionally walked",
    "out at",
    "reached first",
    "fielder's choice",
)

# Play ``type.text`` values that count as renderable play-by-play rows (as
# opposed to per-pitch entries). Shared by ``_normalize_recent_plays`` (what to
# show for a half) and ``_played_half_innings`` (which halves the inning pager
# can page to).
_PLAY_RESULT_PLAY_TYPES: frozenset[str] = frozenset(
    {
        "play result",
        "play-result",
        "end batter/pitcher",
        "end batter pitcher",
        "pitching change",
        "lineup change",
    }
)

# The two at-bat boundary markers, in the spelling variants ESPN has used.
# A half-inning whose last at-bat has a start marker but no end marker was cut
# short by a third out on the bases — see ``_last_batter_of_half``.
_START_BATTER_PLAY_TYPES: frozenset[str] = frozenset(
    {"start batter/pitcher", "start batter pitcher", "start-batterpitcher"}
)
_END_BATTER_PLAY_TYPES: frozenset[str] = frozenset(
    {"end batter/pitcher", "end batter pitcher", "end-batterpitcher"}
)

# Ordered list of (play-text keyword, abbreviation) used when classifying a
# completed at-bat for the current batter's game outcomes.
_BATTER_OUTCOME_PATTERNS: tuple[tuple[str, str], ...] = (
    ("homered", "HR"),
    ("home run", "HR"),
    ("tripled", "3B"),
    ("doubled", "2B"),
    ("singled", "1B"),
    ("walked", "BB"),
    ("intentionally walked", "IBB"),
    ("hit by pitch", "HBP"),
    ("struck out", "K"),
    ("grounded out", "GO"),
    ("flied out", "FO"),
    ("lined out", "LO"),
    ("popped out", "PO"),
    ("fouled out", "FO"),
    ("grounded into", "GIDP"),
    ("reached on error", "E"),
    ("reached on fielder's choice", "FC"),
    ("fielder's choice", "FC"),
    ("sacrifice fly", "SF"),
    ("sacrificed", "SAC"),
    ("sacrifice bunt", "SAC"),
)

# Outcomes excluded from the compact batter-outcome display string.
# Singles (1B) are omitted because they're already implicit in the H-AB hits
# count (a single == "had a hit but not an XBH/HR"); the outcome string is
# meant to surface the *notable* results (XBH, HR, BB, K, etc.).
_BATTER_OUTCOME_EXCLUDED: frozenset[str] = frozenset({"1B", "GO", "FO", "LO", "PO", "GIDP", "FC", "HBP"})

# Display ordering for the compact batter-outcome string.
_BATTER_OUTCOME_ORDER: tuple[str, ...] = ("HR", "3B", "2B", "1B", "BB", "IBB", "SF", "SAC", "K", "E")

# Generational suffixes that should stay attached to the last name
# (e.g. "Guerrero Jr.", "Ripken III") so the on-base indicators don't
# display just the surname.
_NAME_SUFFIX_RE = re.compile(r"^(?:[JS]r\.?|I{1,3}|IV|VI{0,3})$", re.IGNORECASE)


def _parse_iso_ts(date_raw: Any) -> float | None:
    """Parse an ESPN-style ISO datetime string into a POSIX timestamp.

    Returns None for missing or unparseable values. ESPN consistently uses a
    trailing ``Z`` for UTC which `datetime.fromisoformat` does not accept on
    older Python versions, so we normalize it to ``+00:00``.
    """
    if not date_raw:
        return None
    try:
        return datetime.fromisoformat(str(date_raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int:
    """Coerce ESPN score values (often strings like ``"3"``) to int. Returns 0
    for missing or unparseable inputs so score-delta comparisons are stable.
    """
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0


def _competitor_for_side(comp: dict[str, Any], side: str) -> dict[str, Any]:
    """Return the competitor block (``home`` or ``away``) from a compact
    competition dict, or ``{}`` if not found.
    """
    for competitor in comp.get("competitors") or []:
        if competitor.get("homeAway") == side:
            return competitor
    return {}


def _resolve_my_side(comp: dict[str, Any], team_id: int) -> tuple[str | None, str | None]:
    """Identify which side (``home``/``away``) the configured team is on by
    matching team_id. Returns ``(my_side, opponent_side)`` or ``(None, None)``
    when the configured team is not in this competition.
    """
    target = str(team_id)
    for competitor in comp.get("competitors") or []:
        if str((competitor.get("team") or {}).get("id", "")) == target:
            my_side = competitor.get("homeAway")
            if my_side == "home":
                return "home", "away"
            if my_side == "away":
                return "away", "home"
    return None, None


def _scores_for_sides(comp: dict[str, Any], my_side: str, opp_side: str) -> tuple[int, int]:
    """Return ``(my_score, opp_score)`` for the named sides, parsed as ints."""
    return (
        _safe_int(_competitor_for_side(comp, my_side).get("score")),
        _safe_int(_competitor_for_side(comp, opp_side).get("score")),
    )


def _is_final(comp: dict[str, Any] | None) -> bool:
    """Return True if the competition is in the post-game final state."""
    status_type = ((comp or {}).get("status") or {}).get("type") or {}
    state = str(status_type.get("state", "")).lower()
    return state == "post" or status_type.get("completed") is True


def _inning_half(inning_context: dict[str, Any]) -> str:
    """Map the inning prefix to a stable half label (``top``/``bottom``/``""``)."""
    prefix = str(inning_context.get("period_prefix") or "").lower()
    if prefix.startswith("top"):
        return "top"
    if prefix.startswith(("bottom", "bot")):
        return "bottom"
    return ""


def _latest_scoring_play_text(curr: MlbLiveScoreboardData) -> str:
    """Return the text of the most recent scoring play in ``recent_plays``,
    or ``""`` when none is available. Useful for templating in automations.
    """
    for play in reversed(curr.recent_plays or []):
        if play.get("scoring_play"):
            return str(play.get("text") or "")
    return ""


@dataclass
class MlbLiveScoreboardData:
    team_abbr: str
    team_id: int
    team_name: str
    display_event_id: str
    live_event_id: str
    previous_event_id: str
    next_event_id: str
    selected_competition: Competition | None
    inning_context: InningContext
    recent_plays: list[RecentPlay]
    scoring_plays: list[ScoringPlay]
    current_pitches: list[CurrentPitch]
    away_team: TeamMetadata
    home_team: TeamMetadata
    current_batter: CurrentBatter
    current_pitcher: CurrentPitcher
    batter_stats: BatterStats
    pitcher_stats: PitcherStats
    situation: Situation
    probable_pitchers: ProbablePitchers
    win_probability: WinProbability
    due_up: list[DueUpEntry]
    third_out_play: RecentPlay
    third_out_hold_until: float | None
    on_deck: OnDeck
    lineups: Lineups
    leaders: Leaders
    decisions: PitcherDecisions
    division_standings: Standings
    # Post-game ESPN-hosted highlights gallery URL (e.g.
    # ``https://www.espn.com/mlb/video?gameId=…``). Empty until ESPN publishes
    # at least one clip — typically 30-90 minutes after the final pitch.
    highlights_url: str
    mode: str
    status_text: str
    is_live: bool
    is_delayed: bool


class MlbLiveScoreboardCoordinator(DataUpdateCoordinator[MlbLiveScoreboardData]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.team_abbr = str(entry.data[CONF_TEAM]).upper()
        self.team_id = MLB_TEAM_MAP[self.team_abbr]
        self.display_name = str(entry.data.get(CONF_NAME) or entry.title or self.team_abbr)
        self._session = async_get_clientsession(hass)
        # team_id -> (fetched_at_ts, payload). Refreshed lazily once TTL expires;
        # entries are also reused as a fallback when a refresh attempt fails.
        self._team_payload_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # athlete_id -> (fetched_at_ts, payload). Avoids repeat fetches for the
        # same batter during a single at-bat.
        self._batter_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # athlete_id -> (fetched_at_ts, payload) for the current pitcher's season
        # line (used for the displayed ERA). Same short-TTL semantics as the
        # batter cache — the pitcher rarely changes within the window.
        self._pitcher_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # athlete_id -> (fetched_at_ts, parsed PlayerCard). Backs the player
        # career-stats popup; opened interactively, so a long TTL keeps repeat
        # opens instant and a stale entry is reused if ESPN is briefly down.
        self._player_card_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # athlete_id -> (fetched_at_ts, parsed season line). Backs the lineup
        # popup's Season view; opened interactively, so a long TTL keeps
        # repeat opens instant and a stale entry is reused if ESPN is briefly
        # down (same semantics as _player_card_cache).
        self._team_season_stats_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        # (fetched_at_ts, payload) for the team schedule endpoint. Used as a
        # short-lived fallback when ESPN's schedule endpoint has a transient
        # failure, so a one-poll hiccup doesn't blank the card.
        self._schedule_cache: tuple[float, dict[str, Any]] | None = None
        # (fetched_at_ts, payload) for the All-Star Game schedule endpoint.
        # Fetched year-round behind a day-long TTL so that on the one local
        # calendar day the game is played, every entry can display it in place
        # of its own (mid-break, gameless) schedule. See ``_allstar_override``.
        self._allstar_schedule_cache: tuple[float, dict[str, Any]] | None = None
        # (fetched_at_ts, payload) for the league standings endpoint.
        # Standings change a few times per day at most, so we re-fetch lazily
        # once TTL expires and reuse the prior payload as a stale fallback if
        # ESPN's standings endpoint fails.
        self._standings_cache: tuple[float, dict[str, Any]] | None = None
        # (fetched_at_ts, payload) for the league/divisions ``groups``
        # endpoint. Divisions don't change mid-season, so this is cached
        # for a long time and rarely re-fetched.
        self._groups_cache: tuple[float, dict[str, Any]] | None = None
        # Wall-clock timestamp at which ``is_between_halves`` most recently
        # flipped from False to True. Used as a fallback anchor for the
        # third-out hold deadline when ESPN reports the inning transition
        # before the third-out play appears in ``plays[]``.
        self._between_halves_entered_at: float | None = None
        # Batter id captured at the moment the half ended. Lets us detect
        # the brief window after the next half begins but ESPN's
        # ``situation.batter`` still points at the just-ended at-bat.
        self._third_out_batter_id: str | None = None
        # (event_id, summary, inning_context) snapshot from the most recent live
        # refresh. Lets the inning pager (``half_inning_at_offset`` WS command)
        # slice an arbitrary already-played half-inning from the cached
        # ``summary.plays[]`` without a fresh ESPN fetch on every arrow tap.
        self._live_summary_cache: tuple[str, dict[str, Any], dict[str, Any]] | None = None
        # Once-per-game transition events already fired for the currently
        # displayed game. ESPN intermittently serves a stale "pre-game"
        # status between live reads at first pitch, which flickers
        # ``is_live`` True->False->True and would otherwise re-fire
        # GAME_STARTED (and likewise ENDED/WON/LOST on a final-status
        # flicker). Keyed by ``display_event_id`` so a new game resets it.
        self._fired_once_event_id: str | None = None
        self._fired_once_events: set[str] = set()

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.team_abbr}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )

    async def _get_json(self, url: str) -> dict[str, Any]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        async with self._session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise UpdateFailed(f"HTTP {resp.status} for {url}: {text[:200]}")
            return await resp.json()

    def _select_event(self, events: list[dict[str, Any]]) -> tuple[str, str, str, str, dict[str, Any] | None]:
        now_ts = time.time()
        prev: dict[str, Any] | None = None
        next_ev: dict[str, Any] | None = None
        # Postponed/canceled past events are tracked separately. They carry
        # state="post" with completed=false and score 0-0, so they must not be
        # treated as a real "prev" (would shadow the actual most-recent final).
        # But we still want the card to surface "Postponed" rather than skip
        # straight to the next matchup, so we may promote one to `next_ev`
        # below.
        postponed: dict[str, Any] | None = None
        live: dict[str, Any] | None = None

        for ev in events:
            ts = _parse_iso_ts(ev.get("date"))

            comp = ((ev.get("competitions") or [{}])[0]) if ev.get("competitions") else {}
            status = (comp.get("status") or {}).get("type") or (ev.get("status") or {}).get("type") or {}
            state = str(status.get("state", "")).lower()
            name = str(status.get("name", "")).upper()

            if not live and (state in LIVE_STATES or name == STATUS_NAME_IN_PROGRESS):
                live = ev

            if ts is None:
                continue

            is_unplayed_post = state == "post" and not status.get("completed")

            if is_unplayed_post:
                if ts <= now_ts:
                    postponed = ev
            elif ts <= now_ts:
                prev = ev
            elif next_ev is None:
                next_ev = ev

        # Promote a recent postponement to fill the gap between `prev` and the
        # actual next scheduled game, so the card shows "Postponed" once `prev`
        # ages out of the SHOW_NEXT_AFTER_PREV_SECONDS window.
        if postponed is not None:
            post_ts = _parse_iso_ts(postponed.get("date"))
            prev_ts = _parse_iso_ts(prev.get("date")) if prev else None
            next_ts = _parse_iso_ts((next_ev or {}).get("date")) if next_ev else None
            if post_ts is not None:
                after_prev = prev_ts is None or post_ts > prev_ts
                before_next = next_ts is None or post_ts < next_ts
                if after_prev and before_next:
                    next_ev = postponed

        previous_event_id = str((prev or {}).get("id", ""))
        next_event_id = str((next_ev or {}).get("id", ""))
        live_event_id = str((live or {}).get("id", ""))

        if live is not None:
            return previous_event_id, next_event_id, live_event_id, str(live.get("id", "")), live

        display_event = prev or next_ev
        if prev is not None and next_ev is not None:
            comp = ((prev.get("competitions") or [{}])[0]) if prev.get("competitions") else {}
            prev_status = (comp.get("status") or {}).get("type") or (prev.get("status") or {}).get("type") or {}
            prev_state = str(prev_status.get("state", "")).lower()
            prev_complete = prev_state == "post" or prev_status.get("completed") is True

            prev_ts = _parse_iso_ts(prev.get("date"))
            if prev_ts is not None and prev_complete and now_ts >= prev_ts + SHOW_NEXT_AFTER_PREV_SECONDS:
                display_event = next_ev

        return (
            previous_event_id,
            next_event_id,
            live_event_id,
            str((display_event or {}).get("id", "")),
            display_event,
        )

    @staticmethod
    def _event_at_offset(
        events: list[dict[str, Any]],
        anchor_event_id: str,
        offset: int,
    ) -> tuple[str | None, int, bool, bool]:
        """Resolve the schedule event ``offset`` steps from the anchor.

        ``anchor_event_id`` is the id the coordinator currently displays
        (navigation offset 0). Events are ordered chronologically; the offset is
        applied and clamped to the schedule bounds. Returns
        ``(target_event_id, clamped_offset, has_prev, has_next)`` where the
        booleans say whether an earlier/later game exists relative to the
        target, so the card can disable the arrows at the ends of the schedule.
        Returns ``(None, 0, False, False)`` when the schedule is empty or the
        anchor is absent.
        """
        ordered = sorted(
            (e for e in events if e.get("id") is not None),
            key=lambda e: (_parse_iso_ts(e.get("date")) or 0.0),
        )
        ids = [str(e.get("id", "")) for e in ordered]
        if not ids:
            return None, 0, False, False
        try:
            anchor_idx = ids.index(str(anchor_event_id))
        except ValueError:
            return None, 0, False, False
        target_idx = max(0, min(anchor_idx + int(offset), len(ids) - 1))
        return (
            ids[target_idx],
            target_idx - anchor_idx,
            target_idx > 0,
            target_idx < len(ids) - 1,
        )

    @staticmethod
    def _team_display_name(team: dict[str, Any]) -> str:
        """Return the team's display name, disambiguating the two All-Star squads.

        ESPN names *both* the AL and NL All-Star teams "All-Stars" (they differ
        only by logo, abbreviation, and ``displayName``), so the card's matchup —
        which prefers ``team.name`` — would show "All-Stars" on both sides.
        Prefix the league abbreviation ("AL All-Stars" / "NL All-Stars") so the
        two are distinguishable. Regular teams pass through unchanged.
        """
        name = str(team.get("name") or team.get("displayName") or "")
        abbr = str(team.get("abbreviation") or "")
        if name == "All-Stars" and abbr in ("AL", "NL"):
            return f"{abbr} All-Stars"
        return name

    @staticmethod
    def _compact_competition(
        display_comp: dict[str, Any] | None,
        records_map: dict[str, str] | None = None,
    ) -> dict[str, Any] | None:
        if not display_comp:
            return None
        status = display_comp.get("status") or {}
        status_type = status.get("type") or {}
        compact_competitors: list[dict[str, Any]] = []
        records_map = records_map or {}
        for competitor in display_comp.get("competitors") or []:
            team = competitor.get("team") or {}
            logos = team.get("logos") or []
            compact_lines = []
            for line in (competitor.get("linescores") or [])[:MAX_LINESCORES]:
                compact_lines.append(
                    {
                        "value": line.get("value"),
                        "displayValue": line.get("displayValue"),
                        "hits": line.get("hits"),
                        "errors": line.get("errors"),
                    }
                )
            # Prefer the live standings record over the (often-stale) per-game
            # ``recordSummary`` baked into the summary payload. Falls back to
            # the summary value when this team isn't in the standings map
            # (shouldn't happen for MLB teams, but be defensive).
            standings_record = records_map.get(str(team.get("id") or ""))
            record_summary = standings_record or competitor.get("recordSummary")
            compact_competitors.append(
                {
                    "homeAway": competitor.get("homeAway"),
                    "score": competitor.get("score"),
                    "hits": competitor.get("hits"),
                    "errors": competitor.get("errors"),
                    "recordSummary": record_summary,
                    "linescores": compact_lines,
                    "team": {
                        "id": team.get("id"),
                        "abbreviation": team.get("abbreviation"),
                        "name": MlbLiveScoreboardCoordinator._team_display_name(team) or team.get("displayName"),
                        "displayName": team.get("displayName") or team.get("name"),
                        "shortDisplayName": team.get("shortDisplayName") or team.get("abbreviation"),
                        "logo": team.get("logo")
                        or (logos[0].get("href") if logos and isinstance(logos[0], dict) else ""),
                    },
                }
            )
        return {
            "id": display_comp.get("id"),
            "date": display_comp.get("date"),
            "status": {
                "displayPeriod": status.get("displayPeriod"),
                "period": status.get("period"),
                "periodPrefix": status.get("periodPrefix"),
                "type": {
                    "state": status_type.get("state"),
                    "name": status_type.get("name"),
                    "detail": status_type.get("detail"),
                    "shortDetail": status_type.get("shortDetail"),
                    "statusPrimary": status_type.get("statusPrimary"),
                    "description": status_type.get("description"),
                    "completed": status_type.get("completed"),
                    "period": status_type.get("period"),
                    "periodPrefix": status_type.get("periodPrefix"),
                },
            },
            "competitors": compact_competitors,
        }

    @staticmethod
    def _normalize_inning_context(summary: dict[str, Any], display_comp: dict[str, Any] | None) -> dict[str, Any]:
        """Derive inning number, half (top/bot/mid/end) and display strings from the
        competition status block. Used to filter recent plays/pitches by inning.

        Falls back to the freshest play's period when ESPN's status block
        lags behind ``summary.plays`` — a real failure mode observed in
        production where the CDN serves a stale status while plays update
        in-band, pinning the card to a half-inning the game has already
        left. Cache-busting the summary fetch mitigates most cases; this
        fallback covers the residual.
        """
        status = (display_comp or {}).get("status") or {}
        prefix = str(status.get("periodPrefix") or ((status.get("type") or {}).get("detail") or ""))
        period = int(status.get("period") or ((status.get("type") or {}).get("period") or 0) or 0)
        plays = summary.get("plays") or []
        if isinstance(plays, list) and plays:
            # Plays are chronological; the freshest is at the tail. Scan the
            # tail backwards for the newest play carrying an in-progress half
            # (top/bottom), skipping malformed entries and between-inning
            # markers (mid/end) that don't name a live half.
            play_period = 0
            play_half = ""  # original casing, used for the display prefix
            play_half_low = ""
            for play in reversed(plays):
                if not isinstance(play, dict):
                    continue
                pp = play.get("period") or {}
                n = int(pp.get("number") or 0)
                half_raw = str(pp.get("type") or "").strip()
                if n > 0 and half_raw.lower() in ("top", "bottom"):
                    play_period = n
                    play_half = half_raw
                    play_half_low = half_raw.lower()
                    break
            # Promote to the plays' half when ESPN's status block lags behind.
            # Compare (inning, half) as an ordered pair — top precedes bottom —
            # so a same-inning top->bottom flip is caught too, not just an
            # inning-number jump. Without this, a stale "Top N" status keeps the
            # card filtering plays to the just-ended half while the bottom-half
            # leadoff batter is already up, leaving the previous half's
            # play-by-play on screen under the new batter. Only override an
            # in-progress status half (top/bottom); leave between-halves markers
            # (mid/end) alone so the third-out hold that depends on them stands.
            if play_period and play_half_low:
                rank = {"top": 0, "bottom": 1}
                status_low = str(prefix).lower()
                status_half = (
                    "top"
                    if status_low.startswith("top")
                    else "bottom"
                    if status_low.startswith(("bottom", "bot"))
                    else ""
                )
                play_pos = (play_period, rank[play_half_low])
                if status_half:
                    if play_pos > (period, rank[status_half]):
                        period = play_period
                        prefix = f"{play_half} {play_period}"
                elif play_period > period:
                    # Status is a between-halves/unknown marker — fall back to
                    # the conservative inning-number-only advance.
                    period = play_period
                    prefix = f"{play_half} {play_period}"
        due_up = (summary.get("situation") or {}).get("dueUp") or []
        return {
            "period": period,
            "period_prefix": prefix,
            "display_period": str(status.get("displayPeriod") or ""),
            "is_between_halves": prefix.lower().startswith(("mid", "end")),
            "has_due_up": bool(due_up),
        }

    @staticmethod
    def _normalize_current_pitches(
        summary: dict[str, Any], inning_context: dict[str, Any]
    ) -> list[CurrentPitch]:
        """Return the pitches for the at-bat in progress, in chronological order.

        Walks plays backwards from newest, collecting ``Pitch N: ...`` entries until
        an at-bat boundary (start/end batter, terminating play result) is reached.
        Each entry carries ESPN's raw text plus structured fields pulled from the
        same play (``pitchType``, ``pitchVelocity``, ``type.text``, ``resultCount``)
        so the card can render richer per-pitch lines without re-fetching.
        """
        plays = summary.get("plays") or []
        if not isinstance(plays, list) or not plays:
            return []
        target_inning = int(inning_context.get("period") or 0)
        prefix = str(inning_context.get("period_prefix") or "").lower()
        target_half = "top" if prefix.startswith("top") else ("bottom" if prefix.startswith(("bottom", "bot")) else "")
        relevant: list[dict[str, Any]] = []
        for play in plays:
            period = play.get("period") or {}
            if target_inning and int(period.get("number") or 0) != target_inning:
                continue
            if target_half and str(period.get("type") or "").lower() != target_half:
                continue
            txt = str(play.get("text") or "").strip()
            if not txt:
                continue
            relevant.append(play)

        if not relevant:
            return []

        current: list[CurrentPitch] = []
        saw_pitch = False

        for play in reversed(relevant):
            play_type = str((play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or "").lower()
            txt = str(play.get("text") or "").strip()
            low = txt.lower()

            if play_type in {"end batter/pitcher", "end batter pitcher"}:
                if saw_pitch:
                    break
                return []

            if play_type in {"play result", "play-result"} and any(key in low for key in _AT_BAT_END_KEYWORDS):
                if saw_pitch:
                    break
                return []

            if txt.lower().startswith("pitch "):
                entry: CurrentPitch = {"text": txt}
                pt = play.get("pitchType") or {}
                pt_text = str(pt.get("text") or "").strip()
                pt_abbr = str(pt.get("abbreviation") or "").strip()
                if pt_text:
                    entry["pitch_type"] = pt_text
                if pt_abbr:
                    entry["pitch_type_abbr"] = pt_abbr
                vel = play.get("pitchVelocity")
                if isinstance(vel, (int, float)) and vel > 0:
                    entry["velocity"] = int(vel)
                result_text = str((play.get("type") or {}).get("text") or "").strip()
                if result_text:
                    entry["result"] = result_text
                rc = play.get("resultCount") or {}
                if isinstance(rc.get("balls"), int):
                    entry["balls"] = rc["balls"]
                if isinstance(rc.get("strikes"), int):
                    entry["strikes"] = rc["strikes"]
                pc = play.get("pitchCoordinate") or {}
                px, py = pc.get("x"), pc.get("y")
                if isinstance(px, (int, float)) and isinstance(py, (int, float)):
                    entry["pitch_coordinate"] = {"x": int(px), "y": int(py)}
                current.insert(0, entry)
                saw_pitch = True
                continue

            if play_type in _START_BATTER_PLAY_TYPES:
                break

            # keep scanning past steals/advances/other non-terminal updates for same batter

        return current

    @staticmethod
    def _resolve_target_half(inning_context: dict[str, Any]) -> tuple[int, str]:
        """Return ``(inning, half)`` for the half-inning whose plays the card is
        showing, mapping ESPN's prefix to a ``top``/``bottom`` half and resolving
        the between-halves case to the just-ended half (``mid`` -> top, ``end`` ->
        bottom). ``half`` is ``""`` when it can't be determined. Shared by
        ``_normalize_recent_plays`` and the inning pager so they agree on what
        "the current half" is.
        """
        target_inning = int(inning_context.get("period") or 0)
        prefix = str(inning_context.get("period_prefix") or "").lower()
        if prefix.startswith("top"):
            return target_inning, "top"
        if prefix.startswith(("bottom", "bot")):
            return target_inning, "bottom"
        if inning_context.get("is_between_halves") and target_inning > 0:
            return target_inning, ("top" if prefix.startswith("mid") else "bottom")
        return target_inning, ""

    @staticmethod
    def _played_half_innings(summary: dict[str, Any]) -> list[tuple[int, str]]:
        """Return the distinct ``(inning, half)`` pairs that contain at least one
        renderable play-by-play row, in chronological order. These are exactly the
        half-innings the inning pager can page back to (halves that exist only as
        start/end markers are skipped so the pager never lands on an empty view).
        """
        plays = summary.get("plays") or []
        if not isinstance(plays, list):
            return []
        seen: set[tuple[int, str]] = set()
        ordered: list[tuple[int, str]] = []
        for play in plays:
            if not isinstance(play, dict):
                continue
            period = play.get("period") or {}
            inning = int(period.get("number") or 0)
            half = str(period.get("type") or "").strip().lower()
            if inning <= 0 or half not in ("top", "bottom"):
                continue
            if not str(play.get("text") or "").strip():
                continue
            play_type = str(
                (play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or ""
            ).lower()
            if play_type not in _PLAY_RESULT_PLAY_TYPES:
                continue
            key = (inning, half)
            if key not in seen:
                seen.add(key)
                ordered.append(key)
        return ordered

    @staticmethod
    def _ordinal(n: int) -> str:
        """Return ``n`` with its English ordinal suffix (1 -> '1st', 4 -> '4th')."""
        if 10 <= (n % 100) <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        return f"{n}{suffix}"

    @staticmethod
    def _normalize_recent_plays(summary: dict[str, Any], inning_context: dict[str, Any]) -> list[dict[str, Any]]:
        """Return play-result entries for the current half-inning in chronological order."""
        plays = summary.get("plays") or []
        if not isinstance(plays, list) or not plays:
            return []
        target_inning, target_half = MlbLiveScoreboardCoordinator._resolve_target_half(inning_context)
        results = []
        for play in plays:
            period = play.get("period") or {}
            play_half = str(period.get("type") or "").lower()
            play_inning = int(period.get("number") or 0)
            play_type = str((play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or "").lower()
            txt = str(play.get("text") or "").strip()
            if not txt:
                continue
            if target_inning and play_inning != target_inning:
                continue
            if target_half and play_half != target_half:
                continue
            if play_type not in _PLAY_RESULT_PLAY_TYPES:
                continue
            outs = play.get("outs") or ((play.get("result") or {}).get("outs"))
            away_score = play.get("awayScore")
            home_score = play.get("homeScore")
            wallclock_ts = _parse_iso_ts(play.get("wallclock"))
            results.append(
                {
                    "id": str(play.get("id") or ""),
                    "text": txt,
                    "outs": int(outs) if outs not in (None, "") else None,
                    "away_score": away_score,
                    "home_score": home_score,
                    "wallclock_ts": wallclock_ts,
                    "scoring_play": play.get("scoringPlay") is True,
                    "score_value": int(play.get("scoreValue") or 0),
                    "play_type": play_type,
                    "alternative_type": str(
                        (play.get("alternativeType") or {}).get("type")
                        or (play.get("alternativeType") or {}).get("text")
                        or ""
                    ).lower(),
                }
            )
        return results

    @staticmethod
    def _normalize_scoring_plays(summary: dict[str, Any]) -> list[dict[str, Any]]:
        """Return every scoring play of the game in chronological order.

        Distinct from :meth:`_normalize_recent_plays`, which is bounded to the
        *current* half-inning. ESPN exposes ``summary.scoringPlays`` as a
        purpose-built top-level array; we fall back to scanning
        ``summary.plays`` for ``scoringPlay is True`` when ESPN omits it
        (occasionally happens early in a game).

        Deduplicates within the same half-inning by play id (preferred) and
        by ``(half, inning, text)`` signature (fallback). ESPN has been
        observed emitting the same scoring play twice in ``scoringPlays`` —
        e.g. a multi-runner wild-pitch entry showing up as two identical
        rows in the same inning — and the fallback path that scans
        ``summary.plays`` can pick up the same play via both branches when
        ``scoringPlay`` is set on multiple sub-events.
        """
        scoring = summary.get("scoringPlays")
        if not isinstance(scoring, list) or not scoring:
            scoring = [p for p in (summary.get("plays") or []) if isinstance(p, dict) and p.get("scoringPlay") is True]
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_signatures: set[tuple[str, int, str]] = set()
        for play in scoring:
            if not isinstance(play, dict):
                continue
            txt = str(play.get("text") or "").strip()
            if not txt:
                continue
            period = play.get("period") or {}
            team = play.get("team") or {}
            play_id = str(play.get("id") or "")
            period_type = str(period.get("type") or "").strip()
            period_number = int(period.get("number") or 0)
            if play_id and play_id in seen_ids:
                continue
            signature = (period_type.lower(), period_number, txt)
            if signature in seen_signatures:
                continue
            if play_id:
                seen_ids.add(play_id)
            seen_signatures.add(signature)
            results.append(
                {
                    "id": play_id,
                    "text": txt,
                    "period_type": period_type,
                    "period_number": period_number,
                    "away_score": play.get("awayScore"),
                    "home_score": play.get("homeScore"),
                    "score_value": int(play.get("scoreValue") or 0),
                    "team_id": str(team.get("id") or ""),
                }
            )
        return results

    @staticmethod
    def _third_out_from_plays(plays: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the most recent play in ``plays`` that produced the third out, or ``{}``."""
        for play in reversed(plays):
            if play.get("outs") == 3:
                return play
        return {}

    @staticmethod
    def _normalize_third_out_play(summary: dict[str, Any], inning_context: dict[str, Any]) -> dict[str, Any]:
        """Return the most recent play that produced the third out, or ``{}``.

        Convenience wrapper over :meth:`_third_out_from_plays`; the refresh
        path normalizes the plays once and calls the plays-based helper
        directly instead.
        """
        return MlbLiveScoreboardCoordinator._third_out_from_plays(
            MlbLiveScoreboardCoordinator._normalize_recent_plays(summary, inning_context)
        )

    @staticmethod
    def _hold_until_for_play(play: dict[str, Any]) -> float | None:
        """Return the wallclock-anchored deadline for the third-out hold UI.

        Anchoring the deadline to the play's wallclock (rather than first
        observation by each browser) ensures every card transitions from the
        third-out result to the Due Up panel at the same moment, regardless of
        when the dashboard was rendered.
        """
        if not play:
            return None
        wallclock_ts = play.get("wallclock_ts")
        if not isinstance(wallclock_ts, int | float):
            return None
        return float(wallclock_ts) + float(THIRD_OUT_HOLD_SECONDS)

    @staticmethod
    def _normalize_probable_pitchers(display_comp: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        """Extract probable starting pitcher (name, ERA, W-L, headshot) for both sides, used pre-game.

        ESPN exposes probable-pitcher statistics in two shapes depending on
        which endpoint produced ``display_comp``:

        * **Schedule shape**: ``probables[0].statistics`` is a flat list of
          ``{name, abbreviation, displayValue}`` dicts.
        * **Summary header shape**: ``probables[0].statistics`` is an object
          ``{splits: {categories: [{name, abbreviation, displayValue}, ...]}}``.

        Both shapes are flattened to a single list before extraction.
        """
        probables: dict[str, dict[str, Any]] = {"away": {}, "home": {}}
        for competitor in (display_comp or {}).get("competitors") or []:
            side = str(competitor.get("homeAway") or "")
            if side not in {"away", "home"}:
                continue
            prob = ((competitor.get("probables") or [{}])[0]) if competitor.get("probables") else {}
            athlete = prob.get("athlete") or {}
            stats_raw = prob.get("statistics") or []
            if isinstance(stats_raw, dict):
                # Summary-header shape: {"splits": {"categories": [...]}}
                splits = stats_raw.get("splits") or {}
                stats_list = splits.get("categories") if isinstance(splits, dict) else []
                if not isinstance(stats_list, list):
                    stats_list = []
            elif isinstance(stats_raw, list):
                stats_list = stats_raw
            else:
                stats_list = []
            era = ""
            wins = ""
            losses = ""
            for item in stats_list:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").lower()
                abbr = str(item.get("abbreviation") or "").lower()
                value = str(item.get("displayValue") or item.get("value") or "")
                if not era and (name in {"era", "earned run average"} or abbr == "era"):
                    era = value
                elif not wins and (name == "wins" or abbr == "w"):
                    wins = value
                elif not losses and (name == "losses" or abbr == "l"):
                    losses = value
            record = f"{wins}-{losses}" if wins and losses else ""
            headshot = ""
            head = athlete.get("headshot")
            if isinstance(head, dict):
                headshot = str(head.get("href") or "")
            elif isinstance(head, str):
                headshot = head
            probables[side] = {
                "id": str(athlete.get("id") or ""),
                "name": athlete.get("displayName") or athlete.get("shortName") or "",
                "short_name": athlete.get("shortName") or athlete.get("displayName") or "",
                "era": era,
                "wins": wins,
                "losses": losses,
                "record": record,
                "headshot": headshot,
            }
        return probables

    @staticmethod
    def _normalize_win_probability(summary: dict[str, Any] | None) -> dict[str, float]:
        """Return the most recent win-probability snapshot as ``{"home", "away"}``
        percentages (0..100, one decimal).

        ESPN publishes the per-play series under ``summary.winprobability`` as
        ``[{playId, homeWinPercentage, tiePercentage}, ...]`` in chronological
        order; the final entry reflects the current game state. Pre-game the
        series is typically absent or empty — in that case we return ``{}`` so
        the card can hide the bar.
        """
        if not isinstance(summary, dict):
            return {}
        series = summary.get("winprobability")
        if not isinstance(series, list) or not series:
            return {}
        latest: dict[str, Any] | None = None
        for entry in series:
            if isinstance(entry, dict) and entry.get("homeWinPercentage") is not None:
                latest = entry
        if not latest:
            return {}
        try:
            home_frac = float(latest.get("homeWinPercentage") or 0)
        except (TypeError, ValueError):
            return {}
        try:
            tie_frac = float(latest.get("tiePercentage") or 0)
        except (TypeError, ValueError):
            tie_frac = 0.0
        away_frac = max(0.0, 1.0 - home_frac - tie_frac)
        return {
            "home": round(home_frac * 100.0, 1),
            "away": round(away_frac * 100.0, 1),
        }

    @staticmethod
    def _team_id_division_index(
        groups_payload: dict[str, Any] | None,
    ) -> dict[str, str]:
        """Build a ``{team_id: division_name}`` mapping from the ``groups`` payload.

        ESPN's ``/groups`` endpoint nests teams under
        ``groups[].children[].teams[]``, where each ``children[]`` entry is a
        division (e.g. ``"American League East"``).
        """
        index: dict[str, str] = {}
        if not isinstance(groups_payload, dict):
            return index
        leagues = groups_payload.get("groups")
        if not isinstance(leagues, list):
            return index
        for league in leagues:
            if not isinstance(league, dict):
                continue
            divisions = league.get("children")
            if not isinstance(divisions, list):
                continue
            for division in divisions:
                if not isinstance(division, dict):
                    continue
                division_name = str(division.get("name") or division.get("abbreviation") or "")
                if not division_name:
                    continue
                teams = division.get("teams")
                if not isinstance(teams, list):
                    continue
                for team in teams:
                    if not isinstance(team, dict):
                        continue
                    tid = str(team.get("id") or "")
                    if tid:
                        index[tid] = division_name
        return index

    @staticmethod
    def _records_from_standings(standings_payload: dict[str, Any] | None) -> dict[str, str]:
        """Return ``{team_id: "W-L"}`` for every team in the standings payload.

        ESPN's per-game ``summary`` endpoint freezes ``competitor.recordSummary``
        at the pre-game value and only refreshes it hours later. The league
        ``/standings`` endpoint, in contrast, updates within minutes of a final.
        We use this map to override the stale per-game records so the on-card
        display matches the popup standings.
        """
        records: dict[str, str] = {}
        if not standings_payload:
            return records
        children = standings_payload.get("children")
        if not isinstance(children, list):
            return records
        for league in children:
            if not isinstance(league, dict):
                continue
            standings = league.get("standings") or {}
            entries = standings.get("entries") if isinstance(standings, dict) else None
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                team = entry.get("team") or {}
                tid = str(team.get("id") or "")
                if not tid:
                    continue
                wins = ""
                losses = ""
                for stat in entry.get("stats") or []:
                    if not isinstance(stat, dict):
                        continue
                    name = str(stat.get("name") or "").lower()
                    abbr = str(stat.get("abbreviation") or "").lower()
                    val = str(stat.get("displayValue") or stat.get("value") or "")
                    if name == "wins" or abbr == "w":
                        wins = val
                    elif name == "losses" or abbr == "l":
                        losses = val
                if wins and losses:
                    records[tid] = f"{wins}-{losses}"
        return records

    @staticmethod
    def _extract_highlights_url(summary: dict[str, Any]) -> str:
        """Pull the ESPN-hosted highlights gallery URL from ``header.links[]``.

        ESPN tags the gallery entry with ``rel: ['videos', ...]``. Returns ``""``
        when the entry is absent (pre-game and during the game), so the card
        renders nothing until ESPN actually publishes clips.
        """
        header = summary.get("header") or {}
        links = header.get("links") or []
        if not isinstance(links, list):
            return ""
        for link in links:
            if not isinstance(link, dict):
                continue
            rel = link.get("rel") or []
            if not isinstance(rel, list):
                continue
            if "videos" in (str(r).lower() for r in rel):
                href = str(link.get("href") or "").strip()
                if href:
                    return href
        return ""

    @staticmethod
    def _normalize_standings(
        standings_payload: dict[str, Any] | None,
        division_index: dict[str, str],
        team_id: int,
    ) -> dict[str, Any]:
        """Filter the league standings to the configured team's division.

        ESPN's ``/standings`` endpoint groups entries under ``children[]`` per
        league (AL, NL), each with a flat ``standings.entries[]`` of every
        team in the league. There's no per-division grouping in the payload,
        so we use the ``team_id -> division_name`` index built from the
        ``/groups`` endpoint to filter each league's entries down to the
        configured team's division. Sorting is by wins desc, then losses asc.
        """
        empty: dict[str, Any] = {"division_name": "", "entries": []}
        if not standings_payload or not division_index:
            return empty
        team_id_str = str(team_id)
        my_division = division_index.get(team_id_str, "")
        if not my_division:
            return empty
        children = standings_payload.get("children")
        if not isinstance(children, list):
            return empty

        # Collect this team's league entries.
        league_entries: list[dict[str, Any]] = []
        for league in children:
            if not isinstance(league, dict):
                continue
            standings = league.get("standings") or {}
            entries = standings.get("entries") if isinstance(standings, dict) else None
            if not isinstance(entries, list):
                continue
            in_league = any(
                isinstance(e, dict)
                and isinstance(e.get("team"), dict)
                and str(e["team"].get("id") or "") == team_id_str
                for e in entries
            )
            if in_league:
                league_entries = [e for e in entries if isinstance(e, dict)]
                break
        if not league_entries:
            return empty

        # Filter to division peers using the index.
        division_entries = [
            e for e in league_entries if division_index.get(str((e.get("team") or {}).get("id") or "")) == my_division
        ]

        def _stat_value(entry: dict[str, Any], names: set[str], abbrs: set[str]) -> str:
            stats = entry.get("stats") if isinstance(entry.get("stats"), list) else []
            for stat in stats:
                if not isinstance(stat, dict):
                    continue
                name = str(stat.get("name") or "").lower()
                abbr = str(stat.get("abbreviation") or "").lower()
                if name in names or abbr in abbrs:
                    return str(stat.get("displayValue") or stat.get("value") or "")
            return ""

        def _wins_int(entry: dict[str, Any]) -> int:
            try:
                return int(_stat_value(entry, {"wins"}, {"w"}) or 0)
            except (ValueError, TypeError):
                return 0

        def _losses_int(entry: dict[str, Any]) -> int:
            try:
                return int(_stat_value(entry, {"losses"}, {"l"}) or 0)
            except (ValueError, TypeError):
                return 0

        division_entries.sort(key=lambda e: (-_wins_int(e), _losses_int(e)))

        normalized: list[dict[str, Any]] = []
        for entry in division_entries:
            team = entry.get("team") or {}
            wins = _stat_value(entry, {"wins"}, {"w"})
            losses = _stat_value(entry, {"losses"}, {"l"})
            # Prefer divisionGamesBehind (DGB) since we're filtered to a
            # single division; fall back to gamesBehind only if DGB is absent.
            games_back = _stat_value(entry, {"divisiongamesbehind"}, {"dgb"}) or _stat_value(
                entry, {"gamesbehind"}, {"gb"}
            )
            normalized.append(
                {
                    "team_id": str(team.get("id") or ""),
                    "team_name": str(team.get("displayName") or team.get("name") or ""),
                    "team_short_name": str(
                        team.get("shortDisplayName") or team.get("name") or team.get("abbreviation") or ""
                    ),
                    "wins": wins,
                    "losses": losses,
                    "games_back": games_back,
                }
            )
        return {"division_name": my_division, "entries": normalized}

    @staticmethod
    def _normalize_leaders(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """Extract the top statistical leader per category for each team."""
        result: dict[str, list[dict[str, Any]]] = {"away": [], "home": []}
        for team_block in summary.get("leaders") or []:
            side = str(team_block.get("homeAway") or "")
            if side not in {"away", "home"}:
                continue
            compact: list[dict[str, Any]] = []
            for category in team_block.get("leaders") or []:
                leaders = category.get("leaders") or []
                if not leaders:
                    continue
                leader = leaders[0] or {}
                athlete = leader.get("athlete") or {}
                compact.append(
                    {
                        "category": str(category.get("displayName") or category.get("name") or ""),
                        "value": str(leader.get("displayValue") or leader.get("value") or ""),
                        "name": athlete.get("shortName") or athlete.get("displayName") or "",
                        "id": str(athlete.get("id") or ""),
                    }
                )
                if len(compact) >= LEADER_LIMIT:
                    break
            result[side] = compact
        return result

    @staticmethod
    def _normalize_team_payload(team_payload: dict[str, Any]) -> dict[str, Any]:
        """Flatten the ESPN team-metadata response into the fields the card consumes."""
        team = team_payload.get("team") or {}
        record_items = ((team.get("record") or {}).get("items") or []) if isinstance(team, dict) else []
        overall = {}
        if isinstance(record_items, list) and record_items:
            overall = next(
                (item for item in record_items if str(item.get("description", "")).lower() == "overall record"),
                record_items[0],
            )
        logos = team.get("logos") or []
        return {
            "id": str(team.get("id", "")),
            "abbreviation": team.get("abbreviation") or "",
            "name": team.get("displayName") or team.get("name") or "",
            "short_name": team.get("shortDisplayName") or team.get("abbreviation") or "",
            "logo": team.get("logo") or (logos[0].get("href") if logos and isinstance(logos[0], dict) else ""),
            "record_summary": overall.get("summary") or "",
        }

    @staticmethod
    def _find_roster_athlete(summary: dict[str, Any], athlete_id: str) -> dict[str, Any]:
        if not athlete_id:
            return {}
        for team_block in summary.get("rosters") or []:
            for roster_entry in team_block.get("roster") or []:
                athlete = roster_entry.get("athlete") or {}
                if str(athlete.get("id") or "") == athlete_id:
                    return athlete
        return {}

    @classmethod
    def _find_any_athlete(cls, summary: dict[str, Any], athlete_id: str) -> dict[str, Any]:
        entry, _keys = cls._find_boxscore_athlete(summary, athlete_id)
        athlete = entry.get("athlete") or {}
        if athlete:
            return athlete
        return cls._find_roster_athlete(summary, athlete_id)

    @classmethod
    def _normalize_current_batter(cls, summary: dict[str, Any], batter_id: str) -> dict[str, Any]:
        situation = summary.get("situation") or {}
        batter = situation.get("batter") or {}
        athlete = batter.get("athlete") or cls._find_any_athlete(summary, batter_id)
        display_name = (
            batter.get("displayName")
            or batter.get("shortName")
            or athlete.get("displayName")
            or athlete.get("shortName")
            or ""
        )
        return {
            "id": batter_id,
            "display_name": display_name,
            "short_name": batter.get("shortName") or athlete.get("shortName") or display_name,
            "headshot": ((athlete.get("headshot") or {}).get("href") or ""),
        }

    @classmethod
    def _normalize_current_pitcher(cls, summary: dict[str, Any], pitcher_id: str) -> dict[str, Any]:
        situation = summary.get("situation") or {}
        pitcher = situation.get("pitcher") or {}
        athlete = pitcher.get("athlete") or cls._find_any_athlete(summary, pitcher_id)
        display_name = (
            pitcher.get("displayName")
            or pitcher.get("shortName")
            or athlete.get("displayName")
            or athlete.get("shortName")
            or ""
        )
        return {
            "id": pitcher_id,
            "display_name": display_name,
            "short_name": pitcher.get("shortName") or athlete.get("shortName") or display_name,
            "headshot": ((athlete.get("headshot") or {}).get("href") or ""),
        }

    @staticmethod
    def _find_boxscore_athlete(
        summary: dict[str, Any], athlete_id: str, preferred_keys: list[str] | None = None
    ) -> tuple[dict[str, Any], list[str]]:
        if not athlete_id:
            return {}, []
        preferred = [str(k or "").lower() for k in (preferred_keys or []) if str(k or "").strip()]
        best_entry: dict[str, Any] = {}
        best_keys: list[str] = []
        best_score = 0
        boxscore = summary.get("boxscore") or {}
        for team_block in boxscore.get("players") or []:
            for stat_block in team_block.get("statistics") or []:
                keys = [str(k or "") for k in (stat_block.get("keys") or [])]
                keys_lower = [k.lower() for k in keys]
                for athlete_entry in stat_block.get("athletes") or []:
                    athlete = athlete_entry.get("athlete") or {}
                    if str(athlete.get("id") or "") != athlete_id:
                        continue
                    if not preferred or all(pref in keys_lower for pref in preferred):
                        return athlete_entry, keys
                    # Track the best partial match. A two-way player (Ohtani)
                    # is listed in several stat blocks; ranking by how many
                    # preferred keys a block carries keeps us in the right
                    # category rather than grabbing whichever block came first.
                    score = sum(pref in keys_lower for pref in preferred)
                    if score > best_score:
                        best_score = score
                        best_entry, best_keys = athlete_entry, keys
        # Only fall back to a block that shares at least one preferred key with
        # the request. A block matching none of them is the wrong stat category
        # — e.g. a two-way player who is pitching but has come up to bat before
        # ESPN adds his batting line: his only entry is the pitching block, and
        # reading it would surface pitching "hits allowed" as batter hits
        # ("H 7" instead of a hitless 0-for). Return nothing so the caller
        # shows blanks until the correct-category line appears.
        if best_score >= 1:
            return best_entry, best_keys
        return {}, []

    @staticmethod
    def _stat_from_entry(entry: dict[str, Any], keys: list[str], *names: str) -> str:
        if not entry or not keys:
            return ""
        lowered = [str(k).lower() for k in keys]
        for name in names:
            try:
                idx = lowered.index(str(name).lower())
            except ValueError:
                continue
            stats = entry.get("stats") or []
            if idx < len(stats):
                val = stats[idx]
                if val not in (None, ""):
                    return str(val)
        return ""

    @classmethod
    def _extract_batter_game_outcomes(cls, summary: dict[str, Any], batter_id: str) -> list[str]:
        """Extract at-bat outcomes for the current batter from game plays."""
        if not batter_id:
            return []

        plays = summary.get("plays") or []
        if not isinstance(plays, list) or not plays:
            return []

        # Find the batter's name for matching in play text
        athlete = cls._find_any_athlete(summary, batter_id)
        last_name = str(athlete.get("lastName") or "").strip().lower()
        display_name = str(athlete.get("displayName") or athlete.get("shortName") or "").strip().lower()
        short_name = str(athlete.get("shortName") or "").strip().lower()

        if not last_name and display_name:
            parts = display_name.split()
            last_name = parts[-1] if parts else ""

        if not last_name:
            return []

        outcomes: list[str] = []

        for play in plays:
            play_type = str((play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or "").lower()

            # Only look at play results / end batter events
            if play_type not in {"play result", "play-result", "end batter/pitcher", "end batter pitcher"}:
                continue

            txt = str(play.get("text") or "").strip()
            txt_lower = txt.lower()

            # Check if this play involves our batter (name appears at start of play text)
            name_match = (
                txt_lower.startswith(last_name)
                or (bool(display_name) and txt_lower.startswith(display_name))
                or (bool(short_name) and txt_lower.startswith(short_name))
            )
            if not name_match:
                continue

            # Determine the outcome
            for pattern, abbrev in _BATTER_OUTCOME_PATTERNS:
                if pattern in txt_lower:
                    outcomes.append(abbrev)
                    break

        return outcomes

    @classmethod
    def _format_batter_outcomes(cls, outcomes: list[str]) -> str:
        """Format outcomes list into compact display string like '2HR, 2B, BB, K'.

        Excludes routine outs: GO, FO, LO, PO, GIDP, FC, HBP.
        """
        if not outcomes:
            return ""

        # Filter out routine outs that we don't want to display
        filtered_outcomes = [o for o in outcomes if o.upper() not in _BATTER_OUTCOME_EXCLUDED]

        if not filtered_outcomes:
            return ""

        # Count occurrences
        counts: dict[str, int] = {}
        for outcome in filtered_outcomes:
            counts[outcome] = counts.get(outcome, 0) + 1

        parts: list[str] = []
        for key in _BATTER_OUTCOME_ORDER:
            if key in counts:
                count = counts[key]
                if count > 1:
                    parts.append(f"{count}{key}")
                else:
                    parts.append(key)

        # Add any we missed
        for key, count in counts.items():
            if key not in _BATTER_OUTCOME_ORDER:
                if count > 1:
                    parts.append(f"{count}{key}")
                else:
                    parts.append(key)

        return ", ".join(parts)

    @classmethod
    def _is_allstar_summary(cls, summary: dict[str, Any]) -> bool:
        """Return True when this summary is the All-Star Game.

        ESPN reports a player's displayed AVG / ERA differently in the two kinds
        of game, and the difference is not cosmetic (see ``_normalize_batter_stats``
        and ``_normalize_pitcher_stats``), so the two paths must be told apart.

        Detection is by competitor team ID against the AL/NL pseudo-teams.
        ``header.season.type`` is **not** usable: it is ``2`` for the All-Star
        Game exactly as for a regular-season game (verified live against both the
        2026 ASG and a regular-season game on the same day). Neither is the
        innings-pitched key — ESPN now emits ``fullInnings.partInnings`` in
        regular-game boxscores too, so that old tell is gone.
        """
        competitions = (summary.get("header") or {}).get("competitions") or []
        for competition in competitions:
            for competitor in competition.get("competitors") or []:
                if str((competitor.get("team") or {}).get("id") or "") in ALLSTAR_TEAM_IDS:
                    return True
        return False

    @classmethod
    def _normalize_batter_stats(
        cls, summary: dict[str, Any], batter_id: str, season_stats: dict[str, Any] | None = None, is_live: bool = False
    ) -> dict[str, Any]:
        entry, keys = cls._find_boxscore_athlete(summary, batter_id, preferred_keys=["avg", "atBats"])
        is_allstar = cls._is_allstar_summary(summary)
        avg = cls._stat_from_entry(entry, keys, "avg", "battingAverage")
        ab = cls._stat_from_entry(entry, keys, "ab", "atBats")
        h = cls._stat_from_entry(entry, keys, "h", "hits")
        game_hr = cls._stat_from_entry(entry, keys, "hr", "homeRuns")
        game_rbi = cls._stat_from_entry(entry, keys, "rbi", "RBIs")
        season_stats = season_stats or {}

        # Extract at-bat outcomes
        outcomes = cls._extract_batter_game_outcomes(summary, batter_id)
        outcomes_display = cls._format_batter_outcomes(outcomes)

        def _to_int(value: Any) -> int | None:
            if value in (None, ""):
                return None
            try:
                return int(str(value))
            except (TypeError, ValueError):
                return None

        season_hr = season_stats.get("hr") or ""
        season_rbi = season_stats.get("rbi") or ""
        display_hr = season_hr or game_hr
        display_rbi = season_rbi or game_rbi

        if is_live:
            season_hr_i = _to_int(season_hr)
            season_rbi_i = _to_int(season_rbi)
            game_hr_i = _to_int(game_hr)
            game_rbi_i = _to_int(game_rbi)
            if season_hr_i is not None and game_hr_i is not None:
                display_hr = str(season_hr_i + game_hr_i)
            elif game_hr:
                display_hr = game_hr
            if season_rbi_i is not None and game_rbi_i is not None:
                display_rbi = str(season_rbi_i + game_rbi_i)
            elif game_rbi:
                display_rbi = game_rbi

        return {
            # AVG source differs by game kind, and the two must not be unified:
            #
            # Regular game — the boxscore ``avg`` is the season average
            # RECOMPUTED LIVE to include this game's at-bats, while the athlete
            # stats endpoint still serves the PRE-GAME season line. Measured
            # mid-game: Betts boxscore .231 vs endpoint .229, Edman .283 vs
            # .278. The boxscore is the number ESPN itself shows in the lineup,
            # so it is what we display; the endpoint is the fallback.
            #
            # All-Star Game — the boxscore ``avg`` is the *game* average
            # (".000" / "1.000" off a 0-1 line), so the season value wins there.
            # That inversion is the whole point of ``_is_allstar_summary``.
            #
            # Either way we fall back to the other source when the preferred one
            # is absent (rookies, two-way edge cases).
            "avg": (season_stats.get("avg") or avg or "") if is_allstar else (avg or season_stats.get("avg") or ""),
            "ab": ab,
            "h": h,
            "hr": display_hr,
            "rbi": display_rbi,
            "game_hr": game_hr,
            "game_rbi": game_rbi,
            "season_hr": season_hr,
            "season_rbi": season_rbi,
            "hits_ab": f"{h}-{ab}" if h and ab else "",
            "game_outcomes": outcomes,
            "game_outcomes_display": outcomes_display,
        }

    # Boxscore key aliases for a pitcher's innings pitched. The All-Star Game
    # boxscore uses ``fullInnings.partInnings`` (e.g. "0.1") where regular-game
    # boxscores use ``ip`` / ``IP``, so include it or IP renders blank there.
    _IP_KEYS: tuple[str, ...] = ("ip", "inningsPitched", "IP", "fullInnings.partInnings")

    @classmethod
    def _normalize_pitcher_stats(
        cls, summary: dict[str, Any], pitcher_id: str, season_era: str = ""
    ) -> dict[str, Any]:
        """Extract IP / ERA / SO / pitch count for the pitcher of record.

        The displayed ERA source differs by game kind, and the two must not be
        unified:

        Regular game — the boxscore ``ERA`` is the season ERA RECOMPUTED LIVE to
        include the in-progress outing, while ``season_era`` (the athlete stats
        endpoint) still serves the PRE-GAME season line. Measured mid-game:
        Snell had thrown 3.0 scoreless innings on top of a 3.0 IP / 4 ER season,
        so the boxscore read 6.00 while the endpoint still read 12.00. The
        boxscore is the number ESPN itself shows in the lineup, so it is what we
        display; ``season_era`` is the fallback.

        All-Star Game — the boxscore ``ERA`` is the *game* ERA ("0.00" off a
        clean inning), so ``season_era`` wins there. That inversion is the whole
        point of ``_is_allstar_summary``.
        """
        entry, keys = cls._find_boxscore_athlete(summary, pitcher_id, preferred_keys=["era", "pitches"])
        is_allstar = cls._is_allstar_summary(summary)
        pitches = cls._stat_from_entry(entry, keys, "pitches")
        strikes = cls._stat_from_entry(entry, keys, "strikes")
        innings_pitched = cls._stat_from_entry(entry, keys, *cls._IP_KEYS)
        era = cls._stat_from_entry(entry, keys, "era", "earnedRunAverage", "ERA")
        strikeouts = cls._stat_from_entry(entry, keys, "so", "strikeouts", "SO")

        if pitcher_id and (not innings_pitched or not era or not strikeouts or not pitches):
            for team_block in summary.get("boxscore", {}).get("players", []) or []:
                for stat_block in team_block.get("statistics", []) or []:
                    block_keys = stat_block.get("keys") or []
                    for athlete_entry in stat_block.get("athletes") or []:
                        athlete = athlete_entry.get("athlete") or {}
                        if str(athlete.get("id") or "") != pitcher_id:
                            continue
                        innings_pitched = innings_pitched or cls._stat_from_entry(
                            athlete_entry, block_keys, *cls._IP_KEYS
                        )
                        era = era or cls._stat_from_entry(athlete_entry, block_keys, "era", "earnedRunAverage", "ERA")
                        strikeouts = strikeouts or cls._stat_from_entry(
                            athlete_entry, block_keys, "so", "strikeouts", "SO"
                        )
                        pitches = pitches or cls._stat_from_entry(athlete_entry, block_keys, "pitches")
                        strikes = strikes or cls._stat_from_entry(athlete_entry, block_keys, "strikes")

        return {
            "era": (season_era or era) if is_allstar else (era or season_era),
            "innings_pitched": innings_pitched,
            "ip": innings_pitched,
            "pitches_strikes": f"{pitches}-{strikes}" if pitches and strikes else (pitches or ""),
            "strikeouts": strikeouts,
        }

    @staticmethod
    def _bat_order_and_team_block(summary: dict[str, Any], athlete_id: str) -> tuple[int, dict[str, Any] | None]:
        """Return ``(batOrder, team_block)`` for ``athlete_id`` from the box score.

        The team block is the caller's handle on the rest of that side's
        batting order, so both are returned from the one scan.
        """
        if not athlete_id:
            return 0, None
        for team_block in (summary.get("boxscore") or {}).get("players") or []:
            for stat_block in team_block.get("statistics") or []:
                if stat_block.get("type") != "batting":
                    continue
                for athlete_entry in stat_block.get("athletes") or []:
                    athlete = athlete_entry.get("athlete") or {}
                    if str(athlete.get("id") or "") == athlete_id:
                        return _safe_int(athlete_entry.get("batOrder")), team_block
        return 0, None

    @staticmethod
    def _batting_slot_entry(team_block: dict[str, Any], bat_order: int) -> tuple[dict[str, Any], list[str]]:
        """Return ``(athlete_entry, keys)`` for the player currently filling ``bat_order``.

        When a slot has been double-filled by a substitution, ESPN keeps both
        the starter and the sub at the same batOrder (starter listed first), so
        the first match is the subbed-out player. Prefer the entry currently in
        the game (active=True); fall back to the last candidate.
        """
        for stat_block in team_block.get("statistics") or []:
            if stat_block.get("type") != "batting":
                continue
            keys = [str(k or "") for k in (stat_block.get("keys") or [])]
            candidates = [
                entry
                for entry in (stat_block.get("athletes") or [])
                if _safe_int(entry.get("batOrder")) == bat_order
            ]
            if not candidates:
                continue
            return next((e for e in candidates if e.get("active")), candidates[-1]), keys
        return {}, []

    @staticmethod
    def _last_batter_of_half(summary: dict[str, Any], half: str, max_inning: int) -> tuple[str, bool]:
        """Return ``(athlete_id, at_bat_completed)`` for the last plate appearance in ``half``.

        ``half`` is ESPN's ``period.type`` casing-insensitively — "top" or
        "bottom". Scans newest-first, so at a between-halves break this is the
        last batter of that side's previous turn at the plate.

        ``at_bat_completed`` is False when the half ended on the bases with
        that batter still at the plate — a caught stealing or pickoff for the
        third out. **That batter then leads off the next half with a fresh
        count**, so the anchor is his own slot rather than the next one. Real
        case in the same game: the top of the 3rd ended with Chourio caught
        stealing on Sánchez's 2-0 count, and Sánchez opened the 4th. ESPN marks
        such an at-bat by emitting its ``Start Batter/Pitcher`` with no
        matching ``End Batter/Pitcher``; a payload carrying neither marker
        reads as completed, which is the ordinary case.

        ``max_inning`` bounds the scan to halves that have actually been
        played. ESPN logs the break's roster moves into the *upcoming* half
        (e.g. "Hall relieved Senzatela" lands in Bottom 7 while the top of the
        7th is still the live half), so without the bound an announcement that
        happened to name a batter would advance the anchor a slot too far.
        Substitution plays carry no ``batter`` participant in the payloads
        we've seen, but the bound makes that an observation we don't depend on.
        """
        plays = summary.get("plays") or []
        if not isinstance(plays, list):
            return "", True
        target = half.strip().lower()
        athlete_id = ""
        completed = True
        boundary_seen = False
        for play in reversed(plays):
            if not isinstance(play, dict):
                continue
            period = play.get("period") or {}
            if str(period.get("type") or "").strip().lower() != target:
                continue
            inning = _safe_int(period.get("number"))
            if not 0 < inning <= max_inning:
                continue
            if not boundary_seen:
                play_type = str((play.get("type") or {}).get("text") or "").strip().lower()
                if play_type in _END_BATTER_PLAY_TYPES:
                    boundary_seen = True
                elif play_type in _START_BATTER_PLAY_TYPES:
                    completed, boundary_seen = False, True
            if not athlete_id:
                for participant in play.get("participants") or []:
                    if str(participant.get("type") or "").strip().lower() != "batter":
                        continue
                    athlete_id = str(((participant.get("athlete") or {}).get("id")) or "")
                    if athlete_id:
                        break
            if athlete_id and boundary_seen:
                break
        return athlete_id, completed

    @classmethod
    def _due_up_in_batting_order(
        cls, summary: dict[str, Any], raw: list[dict[str, Any]], inning_context: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        """Re-anchor ESPN's ``situation.dueUp`` to the slot that actually leads off next.

        ESPN's list is **not reliably anchored at the true next batter**: when
        the order wraps past the 9-hole it restarts at the top of the lineup
        instead. Observed live 2026-08-13 (MIL @ LAD, event 401816515): the
        bottom of the 6th ended on slot 7, so the bottom of the 7th was due to
        open 8-9-1 (E. Hernández, Rortvedt, Ohtani) and did — but the panel
        rendered 1-2-3 (Ohtani, Pages, Edman), i.e. two of the three names were
        simply wrong. Breaks sampled at slots 5 and 7 that same game (no wrap)
        were correct, so the failure tracks the wrap, not the game.

        Everything needed to fix it is already in the payload we fetch each
        tick, so recompute the anchor from the batting order and reorder
        around it. Falls back to ESPN's list verbatim whenever the anchor
        cannot be established, so a payload shape we don't recognise degrades
        to the old behaviour rather than to an empty panel.
        """
        if not raw:
            # Only ever *repair* a list ESPN sent. ESPN drops `dueUp` outside a
            # break, and an empty list is load-bearing downstream: the card
            # gates the panel on it, and the coordinator's stale-situation
            # bridge reuses the prior snapshot when it comes back empty.
            # Synthesizing one from the box score would put a Due Up panel on
            # screen where ESPN publishes none.
            return []

        prefix = str((inning_context or {}).get("period_prefix") or "").strip().lower()
        inning = _safe_int((inning_context or {}).get("period"))
        if not inning:
            return list(raw)
        if prefix.startswith("mid"):
            # Top half just ended -> the home side (bottom half) bats next, and
            # last batted no later than the *previous* inning.
            last_half, max_inning = "bottom", inning - 1
        elif prefix.startswith("end"):
            # Bottom half just ended -> the away side bats next in the top of
            # the following inning; it last batted in the top of this one.
            last_half, max_inning = "top", inning
        else:
            # Not a between-halves break; there is no "next half" to anchor to.
            return list(raw)

        reference_id, at_bat_completed = cls._last_batter_of_half(summary, last_half, max_inning)
        if not reference_id:
            # That side hasn't batted yet (first time through the order), so
            # the top of the lineup — what ESPN already sent — is correct.
            return list(raw)
        last_slot, team_block = cls._bat_order_and_team_block(summary, reference_id)
        if not last_slot or team_block is None:
            return list(raw)

        # A third out made on the bases leaves that batter's at-bat unfinished;
        # he leads off the next half himself instead of yielding to the next slot.
        anchor = (last_slot % BATTING_ORDER_SIZE) + 1 if at_bat_completed else last_slot

        by_slot: dict[int, dict[str, Any]] = {}
        for item in raw:
            by_slot.setdefault(_safe_int(item.get("batOrder")), item)

        ordered: list[dict[str, Any]] = []
        for offset in range(DUE_UP_LIMIT):
            slot = ((anchor - 1 + offset) % BATTING_ORDER_SIZE) + 1
            item = by_slot.get(slot)
            if item is None:
                # ESPN didn't send this slot — resolve it from the box score.
                entry, _keys = cls._batting_slot_entry(team_block, slot)
                athlete_id = str(((entry.get("athlete") or {}).get("id")) or "")
                if not athlete_id:
                    return list(raw)
                item = {"playerId": athlete_id, "batOrder": slot}
            ordered.append(item)
        return ordered

    @classmethod
    def _up_bat_order(
        cls,
        summary: dict[str, Any],
        inning_context: dict[str, Any] | None,
        side: str,
        batter_id: str,
        is_batting: bool,
    ) -> int:
        """Return the batting-order slot (1-9) that is "up" for ``side``.

        "Up" in the baseball sense, which resolves differently per side and
        lets the lineup popup mark both teams with one field:

        * **Batting side** — the batter *at the plate right now*.
        * **Fielding side** — nobody is up, so the slot that leads off its
          next half-inning.

        Both fall out of one rule: an at-bat still in progress points at that
        batter, a completed one points at the slot after him. That is exactly
        the ``at_bat_completed`` flag :meth:`_last_batter_of_half` already
        returns, so this reuses it (and :meth:`_bat_order_and_team_block`)
        rather than growing a second implementation of the batting order.
        The reuse also inherits the caught-stealing/pickoff case those solved:
        a half that ended on the bases leaves that batter's at-bat unfinished
        and he leads off the next half himself rather than yielding.

        ``situation.batter`` is authoritative for who is in the box, so the
        batting side short-circuits to it and only falls back to the play
        scan when ESPN drops the field for a tick — at which point the
        general rule above lands on the right answer anyway.

        The away side bats the top of every inning and the home side the
        bottom, so "its own most recent half" is a pure function of the
        status prefix: the top of inning N is under way or done in every
        state we can be called in, while the bottom of N has only begun once
        the prefix reads "Bottom"/"End".

        Returns ``0`` when the anchor cannot be established (unrecognised
        status, or a box score missing the reference batter), which the card
        reads as "draw no marker".
        """
        inning = _safe_int((inning_context or {}).get("period"))
        if not inning or side not in ("away", "home"):
            return 0

        if is_batting and batter_id:
            current_slot, _team_block = cls._bat_order_and_team_block(summary, batter_id)
            return current_slot or 0

        prefix = str((inning_context or {}).get("period_prefix") or "").strip().lower()
        if side == "away":
            side_half, max_inning = "top", inning
        else:
            side_half = "bottom"
            max_inning = inning if prefix.startswith(("bot", "end")) else inning - 1
        if max_inning < 1:
            # That side has not come to bat yet; the lineup leads off.
            return 1

        reference_id, at_bat_completed = cls._last_batter_of_half(summary, side_half, max_inning)
        if not reference_id:
            return 1
        last_slot, team_block = cls._bat_order_and_team_block(summary, reference_id)
        if not last_slot or team_block is None:
            return 0
        return (last_slot % BATTING_ORDER_SIZE) + 1 if at_bat_completed else last_slot

    @classmethod
    def _normalize_due_up(
        cls, summary: dict[str, Any], inning_context: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Return the next ``DUE_UP_LIMIT`` batters scheduled to bat next half-inning."""
        situation = summary.get("situation") or {}
        raw = situation.get("dueUp") or []
        due_up = cls._due_up_in_batting_order(summary, raw, inning_context)
        if _LOGGER.isEnabledFor(logging.DEBUG):
            before = [_safe_int(i.get("batOrder")) for i in raw[:DUE_UP_LIMIT]]
            after = [_safe_int(i.get("batOrder")) for i in due_up[:DUE_UP_LIMIT]]
            if before != after:
                _LOGGER.debug("Re-anchored ESPN dueUp batting order %s -> %s", before, after)
        result: list[dict[str, Any]] = []
        for item in due_up[:DUE_UP_LIMIT]:
            player_id = str(item.get("playerId") or item.get("id") or "")
            entry, keys = cls._find_boxscore_athlete(summary, player_id)
            athlete = entry.get("athlete") or cls._find_roster_athlete(summary, player_id) or {}
            avg = cls._stat_from_entry(entry, keys, "avg", "battingAverage")
            ab = cls._stat_from_entry(entry, keys, "ab", "atBats")
            h = cls._stat_from_entry(entry, keys, "h", "hits")
            result.append(
                {
                    "id": player_id,
                    "display_name": item.get("displayName")
                    or athlete.get("displayName")
                    or athlete.get("shortName")
                    or "",
                    "short_name": item.get("shortName") or athlete.get("shortName") or athlete.get("displayName") or "",
                    "headshot": ((athlete.get("headshot") or {}).get("href") or ""),
                    "avg": avg,
                    "hits_ab": f"{h}-{ab}" if h and ab else "",
                }
            )
        return result

    async def _get_public_batter_stats(self, athlete_id: str) -> dict[str, Any]:
        """Fetch an athlete's season *batting* stats payload, served from a TTL cache.

        Stats only change when the player completes an at-bat, so a short TTL
        eliminates the repeat ESPN calls that occur every 5 s while the same
        batter is at the plate. Falls back to a stale cache entry on fetch
        failure rather than blanking the season HR/RBI display.

        The ``category=batting`` query parameter forces ESPN to return
        ``career-batting`` (and friends) even for players whose *listed
        position* is a pitcher — without it, a two-way player like Ohtani
        (listed SP) yields only pitching categories plus ``opponent-batting``
        (the hitting line batters have produced **against** him), which the
        in-game display has no use for. Pure hitters and pure pitchers also
        return ``career-batting`` under this query, so the same URL works
        for every athlete the at-bat caller will ever pass in.
        """
        if not athlete_id:
            return {}
        cached = self._batter_stats_cache.get(athlete_id)
        now_ts = time.time()
        if cached is not None and (now_ts - cached[0]) < BATTER_SEASON_STATS_TTL_SECONDS:
            return cached[1]
        url = (
            "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/"
            f"athletes/{athlete_id}/stats?region=us&lang=en&contentorigin=espn&category=batting"
        )
        try:
            payload = await self._get_json(url)
        except Exception as err:
            _LOGGER.debug("Unable to fetch batter season stats for %s: %s", athlete_id, err)
            return cached[1] if cached is not None else {}
        self._batter_stats_cache[athlete_id] = (now_ts, payload)
        return payload

    async def _get_public_pitcher_stats(self, athlete_id: str) -> dict[str, Any]:
        """Fetch an athlete's season *pitching* stats payload, TTL-cached.

        Mirror of :meth:`_get_public_batter_stats`; the ``category=pitching``
        query forces the pitching line (so a two-way player yields their own
        pitching stats rather than the batting line). Used to source the
        displayed season ERA, which the All-Star boxscore reports as a game
        value. Falls back to a stale cache entry on fetch failure.
        """
        if not athlete_id:
            return {}
        cached = self._pitcher_stats_cache.get(athlete_id)
        now_ts = time.time()
        if cached is not None and (now_ts - cached[0]) < BATTER_SEASON_STATS_TTL_SECONDS:
            return cached[1]
        url = (
            "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/"
            f"athletes/{athlete_id}/stats?region=us&lang=en&contentorigin=espn&category=pitching"
        )
        try:
            payload = await self._get_json(url)
        except Exception as err:
            _LOGGER.debug("Unable to fetch pitcher season stats for %s: %s", athlete_id, err)
            return cached[1] if cached is not None else {}
        self._pitcher_stats_cache[athlete_id] = (now_ts, payload)
        return payload

    # Categories that actually represent the *player's own* hitting line.
    # Excludes ``opponent-batting`` (a pitcher-allowed line that confusingly
    # also carries ``homeRuns``/``RBIs`` keys) and any postseason / expanded
    # / advanced variant. Order = preference; first non-empty match wins.
    _BATTING_LINE_CATEGORIES: tuple[str, ...] = ("career-batting", "batting")

    @classmethod
    def _extract_current_season_batter_stats(cls, stats_payload: dict[str, Any]) -> dict[str, Any]:
        """Pick the current-season hitting line (HR / RBI / AVG) from an ESPN
        ``/athletes/{id}/stats?category=batting`` payload.

        Only the player's own batting categories are considered — never
        ``opponent-batting``, even though its key set overlaps. ESPN's
        ``?category=batting`` response sometimes lists ``career-batting``
        twice, so we walk in preference order and take the first occurrence
        whose row count is non-empty.
        """
        categories = stats_payload.get("categories") or []
        current_year = datetime.now().year
        for preferred in cls._BATTING_LINE_CATEGORIES:
            for category in categories:
                if category.get("name") != preferred:
                    continue
                season_rows = category.get("statistics") or []
                if not season_rows:
                    continue
                names = [str(n or "") for n in (category.get("names") or [])]
                if "homeRuns" not in names or "RBIs" not in names:
                    continue
                hr_idx = names.index("homeRuns")
                rbi_idx = names.index("RBIs")
                avg_idx = names.index("avg") if "avg" in names else -1
                row = next(
                    (r for r in season_rows if int((r.get("season") or {}).get("year") or 0) == current_year),
                    None,
                )
                if row is None:
                    row = season_rows[-1]
                if not row:
                    continue
                stats = row.get("stats") or []

                def get_idx(idx: int, _stats: list = stats) -> str:
                    if 0 <= idx < len(_stats) and _stats[idx] not in (None, ""):
                        return str(_stats[idx])
                    return ""

                return {
                    "hr": get_idx(hr_idx),
                    "rbi": get_idx(rbi_idx),
                    "avg": get_idx(avg_idx),
                }
        return {}

    @classmethod
    def _extract_season_line(cls, stats_payload: dict[str, Any]) -> dict[str, Any]:
        """Pull the current season's hitting *or* pitching line from an ESPN
        ``/athletes/{id}/stats`` payload.

        Pure (no I/O) — unit-tested directly against fixtures. Returns
        ``{"hitting": {ab,h,hr,rbi,sb,avg}}`` for a hitter or
        ``{"pitching": {w,l,era,ip,k,whip}}`` for a pitcher, or ``{}`` when
        no usable category/row is present. ESPN's ``/stats`` exposes
        categories by the player's *listed* position only, so at most one
        side is available per call (documented Option B limitation —
        see ``tests/fixtures/README.md``). Picks the current-year row,
        falling back to the most recent season row when ESPN has not opened
        the current season for that player yet.
        """
        categories = {
            str(c.get("name") or ""): c for c in (stats_payload.get("categories") or []) if isinstance(c, dict)
        }
        primary = next((n for n in cls._PRIMARY_STAT_CATEGORIES if n in categories), "")
        if not primary:
            return {}
        cat = categories[primary]
        names = [str(n or "") for n in (cat.get("names") or [])]
        rows = cat.get("statistics") or []
        current_year = datetime.now().year
        row = next(
            (r for r in rows if int((r.get("season") or {}).get("year") or 0) == current_year),
            None,
        )
        if row is None and rows:
            row = rows[-1]
        if not isinstance(row, dict):
            return {}
        stats = row.get("stats") or []

        def by_name(*candidates: str) -> str:
            for nm in candidates:
                if nm in names:
                    idx = names.index(nm)
                    if 0 <= idx < len(stats) and stats[idx] not in (None, ""):
                        return str(stats[idx])
            return ""

        if "pitch" in primary:
            return {
                "pitching": {
                    "w": by_name("wins"),
                    "l": by_name("losses"),
                    "era": by_name("ERA"),
                    "ip": by_name("innings", "inningsPitched"),
                    "k": by_name("strikeouts"),
                    "whip": by_name("WHIP"),
                }
            }
        return {
            "hitting": {
                "ab": by_name("atBats"),
                "h": by_name("hits"),
                "hr": by_name("homeRuns"),
                "rbi": by_name("RBIs"),
                "sb": by_name("stolenBases"),
                "avg": by_name("avg", "battingAverage"),
            }
        }

    # Primary stats category preference, by exact ESPN category name. A hitter
    # exposes ``career-batting``; a pitcher (incl. a two-way player ESPN lists
    # as a pitcher) exposes ``pitching``. Postseason / expanded / advanced and
    # ``opponent-batting`` (opponents' line, not the player's) are skipped.
    _PRIMARY_STAT_CATEGORIES: tuple[str, ...] = ("career-batting", "batting", "pitching")

    async def async_get_player_card(self, athlete_id: str) -> PlayerCard:
        """Public entrypoint for the player-card WebSocket command.

        Thin boundary over :meth:`_get_player_card` so cross-module callers
        (the integration's websocket handler) don't reach into a private
        method; all fetch/cache/parse logic lives in the private impl.
        """
        return await self._get_player_card(athlete_id)

    async def _get_player_card(self, athlete_id: str) -> PlayerCard:
        """Fetch a player's full career card (bio + career stats), TTL-cached.

        Bio and career stats live behind two separate ESPN athlete endpoints
        (the stats payload carries no bio block), so both are fetched
        concurrently. The popup is opened interactively rather than polled, so
        a long TTL makes repeat opens instant; a still-recent cached card is
        reused as a fallback when ESPN is briefly unreachable rather than
        blanking the popup.
        """
        if not athlete_id:
            return {}
        now_ts = time.time()
        cached = self._player_card_cache.get(athlete_id)
        if cached is not None and (now_ts - cached[0]) < PLAYER_CARD_TTL_SECONDS:
            return cached[1]
        base = f"https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/{athlete_id}"
        suffix = "?region=us&lang=en&contentorigin=espn"
        bio_res, stats_res = await asyncio.gather(
            self._get_json(f"{base}{suffix}"),
            self._get_json(f"{base}/stats{suffix}"),
            return_exceptions=True,
        )
        bio_payload = bio_res if isinstance(bio_res, dict) else {}
        stats_payload = stats_res if isinstance(stats_res, dict) else {}
        if not bio_payload and not stats_payload:
            # Both endpoints failed — serve a still-acceptable stale card
            # rather than an empty popup.
            if cached is not None and (now_ts - cached[0]) < PLAYER_CARD_STALE_FALLBACK_SECONDS:
                return cached[1]
            _LOGGER.debug(
                "Unable to fetch player card for %s: bio=%s stats=%s",
                athlete_id,
                bio_res,
                stats_res,
            )
            return {}
        card = self._parse_player_card(athlete_id, bio_payload, stats_payload)
        self._player_card_cache[athlete_id] = (now_ts, card)
        return card

    async def async_get_team_season_stats(self, athlete_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Public entrypoint for the team-season-stats WebSocket command.

        Thin boundary over :meth:`_get_team_season_stats` so the
        integration's websocket handler doesn't reach into a private method;
        all fetch/cache/parse logic lives in the private impl.
        """
        return await self._get_team_season_stats(athlete_ids)

    async def _get_team_season_stats(self, athlete_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch current-season lines for many athletes at once.

        Backs the lineup popup's Season view: opened interactively, so a
        long per-athlete TTL keeps re-opens instant and a still-recent
        cached line is reused when ESPN is briefly unreachable. Ids are
        de-duplicated (preserving order) since a popup may list the same
        player across roster + box score, then fetched concurrently.
        Returns ``{athlete_id: {"hitting"|"pitching": {...}}}``, omitting
        ids with no usable line.
        """
        ids = [s for s in dict.fromkeys(str(a or "").strip() for a in (athlete_ids or [])) if s]
        if not ids:
            return {}
        results = await asyncio.gather(
            *(self._get_one_season_line(aid) for aid in ids),
            return_exceptions=True,
        )
        out: dict[str, dict[str, Any]] = {}
        for aid, res in zip(ids, results, strict=True):
            if isinstance(res, dict) and res:
                out[aid] = res
        return out

    async def _get_one_season_line(self, athlete_id: str) -> dict[str, Any]:
        """One athlete's parsed current-season line, TTL-cached with a
        stale fallback (mirrors :meth:`_get_player_card`'s resilience)."""
        if not athlete_id:
            return {}
        now_ts = time.time()
        cached = self._team_season_stats_cache.get(athlete_id)
        if cached is not None and (now_ts - cached[0]) < TEAM_SEASON_STATS_TTL_SECONDS:
            return cached[1]
        url = (
            "https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/"
            f"athletes/{athlete_id}/stats?region=us&lang=en&contentorigin=espn"
        )
        try:
            payload = await self._get_json(url)
        except Exception as err:
            _LOGGER.debug("Unable to fetch season stats for %s: %s", athlete_id, err)
            if cached is not None and (now_ts - cached[0]) < TEAM_SEASON_STATS_STALE_FALLBACK_SECONDS:
                return cached[1]
            return {}
        line = self._extract_season_line(payload)
        if line:
            self._team_season_stats_cache[athlete_id] = (now_ts, line)
        return line

    @staticmethod
    def _team_abbr_map(stats_payload: dict[str, Any]) -> dict[str, str]:
        """Build ``teamId -> abbreviation`` from the stats payload ``teams``.

        ESPN keys ``teams`` by both numeric id and slug; iterating values and
        re-keying by the entry's own numeric ``id`` collapses the duplicates
        so a season row's ``teamId`` resolves to a short label.
        """
        teams = stats_payload.get("teams")
        result: dict[str, str] = {}
        if isinstance(teams, dict):
            for value in teams.values():
                if not isinstance(value, dict):
                    continue
                tid = str(value.get("id") or "")
                abbr = str(value.get("abbreviation") or "")
                if tid and abbr:
                    result[tid] = abbr
        return result

    @classmethod
    def _parse_player_card(
        cls,
        athlete_id: str,
        bio_payload: dict[str, Any],
        stats_payload: dict[str, Any],
    ) -> PlayerCard:
        """Pure transform of the two ESPN athlete payloads into a PlayerCard.

        No I/O — unit-tested directly against captured fixtures. Tolerates
        either payload being empty (one endpoint failed): a missing bio
        yields an empty bio block, missing stats an empty career table.
        """
        athlete = bio_payload.get("athlete") or {}
        position = athlete.get("position") or {}
        team = athlete.get("team") or {}
        headshot = athlete.get("headshot")
        if isinstance(headshot, dict):
            headshot_url = str(headshot.get("href") or "")
        elif isinstance(headshot, str):
            headshot_url = headshot
        else:
            headshot_url = ""
        bio: dict[str, Any] = {
            "name": athlete.get("displayName") or athlete.get("fullName") or "",
            "team": team.get("displayName") or team.get("name") or team.get("abbreviation") or "",
            "position": position.get("abbreviation") or position.get("displayName") or "",
            "bats_throws": athlete.get("displayBatsThrows") or "",
            "height": athlete.get("displayHeight") or "",
            "weight": athlete.get("displayWeight") or "",
            "age": str(athlete.get("age") or ""),
            "jersey": str(athlete.get("jersey") or athlete.get("displayJersey") or ""),
            "headshot": headshot_url,
            "draft": athlete.get("displayDraft") or "",
            "debut_year": str(athlete.get("debutYear") or ""),
        }

        categories = {
            str(c.get("name") or ""): c for c in (stats_payload.get("categories") or []) if isinstance(c, dict)
        }
        primary_name = next((n for n in cls._PRIMARY_STAT_CATEGORIES if n in categories), "")
        career: dict[str, Any] = {}
        if primary_name:
            cat = categories[primary_name]
            team_abbr = cls._team_abbr_map(stats_payload)

            def _cell(value: Any) -> str:
                return "" if value is None else str(value)

            seasons: list[dict[str, Any]] = []
            for row in cat.get("statistics") or []:
                if not isinstance(row, dict):
                    continue
                season = row.get("season") or {}
                tid = str(row.get("teamId") or "")
                seasons.append(
                    {
                        "year": str(season.get("year") or season.get("displayName") or ""),
                        "team": team_abbr.get(tid, "") or str(row.get("teamSlug") or ""),
                        "stats": [_cell(s) for s in (row.get("stats") or [])],
                    }
                )
            career = {
                "kind": "pitching" if "pitch" in primary_name else "batting",
                "columns": [str(x or "") for x in (cat.get("labels") or [])],
                "keys": [str(x or "") for x in (cat.get("names") or [])],
                "seasons": seasons,
                "totals": [_cell(s) for s in (cat.get("totals") or [])],
            }

        glossary = {
            str(g.get("abbreviation") or ""): str(g.get("displayName") or "")
            for g in (stats_payload.get("glossary") or [])
            if isinstance(g, dict) and g.get("abbreviation")
        }

        return {
            "id": str(athlete_id or athlete.get("id") or ""),
            "bio": bio,
            "career": career,
            "glossary": glossary,
        }

    @classmethod
    def _normalize_situation(cls, summary: dict[str, Any]) -> dict[str, Any]:
        """Return balls/strikes/outs and base-runner occupancy + last names."""
        situation = summary.get("situation") or {}

        def _runner_ref(*candidates: Any) -> Any:
            for candidate in candidates:
                if candidate:
                    return candidate
            return None

        def _suffix_from_display_name(display_name: str) -> str:
            parts = str(display_name or "").strip().split()
            if len(parts) >= 3 and _NAME_SUFFIX_RE.match(parts[-1]):
                return parts[-1]
            return ""

        def _runner_last_name(ref: Any) -> str:
            if not ref:
                return ""
            athlete_id = ""
            if isinstance(ref, dict):
                athlete_id = str(ref.get("playerId") or ref.get("id") or ((ref.get("athlete") or {}).get("id")) or "")
            athlete = cls._find_any_athlete(summary, athlete_id) if athlete_id else {}
            display_name = str(
                athlete.get("displayName")
                or athlete.get("shortName")
                or (ref.get("displayName") if isinstance(ref, dict) else "")
                or (ref.get("shortName") if isinstance(ref, dict) else "")
                or ""
            ).strip()
            suffix = str(athlete.get("suffix") or "").strip() or _suffix_from_display_name(display_name)
            last_name = str(athlete.get("lastName") or "").strip()
            if last_name:
                return f"{last_name} {suffix}".strip() if suffix else last_name
            if display_name:
                parts = display_name.split()
                if suffix and len(parts) >= 2:
                    return f"{parts[-2]} {suffix}"
                return parts[-1]
            return ""

        first_ref = _runner_ref(
            situation.get("onFirst"),
            situation.get("first"),
            (situation.get("runnersOn") or {}).get("first"),
            (situation.get("runners") or {}).get("first"),
        )
        second_ref = _runner_ref(
            situation.get("onSecond"),
            situation.get("second"),
            (situation.get("runnersOn") or {}).get("second"),
            (situation.get("runners") or {}).get("second"),
        )
        third_ref = _runner_ref(
            situation.get("onThird"),
            situation.get("third"),
            (situation.get("runnersOn") or {}).get("third"),
            (situation.get("runners") or {}).get("third"),
        )

        return {
            "balls": int(situation.get("balls") or 0),
            "strikes": int(situation.get("strikes") or 0),
            "outs": int(situation.get("outs") or 0),
            "on_first": bool(first_ref),
            "on_second": bool(second_ref),
            "on_third": bool(third_ref),
            "first_last_name": _runner_last_name(first_ref),
            "second_last_name": _runner_last_name(second_ref),
            "third_last_name": _runner_last_name(third_ref),
        }

    @classmethod
    def _normalize_on_deck(
        cls, summary: dict[str, Any], inning_context: dict[str, Any], batter_id: str
    ) -> dict[str, Any]:
        """Calculate the on-deck batter from the batting order."""
        if not batter_id:
            return {}

        current_bat_order, batting_team_block = cls._bat_order_and_team_block(summary, batter_id)
        if not current_bat_order or batting_team_block is None:
            return {}

        # Calculate next batter in order (wrap 9 -> 1)
        next_bat_order = (current_bat_order % BATTING_ORDER_SIZE) + 1
        athlete_entry, keys = cls._batting_slot_entry(batting_team_block, next_bat_order)
        if not athlete_entry:
            return {}

        athlete = athlete_entry.get("athlete") or {}
        # Get stats for on-deck batter
        h = cls._stat_from_entry(athlete_entry, keys, "h", "hits")
        ab = cls._stat_from_entry(athlete_entry, keys, "ab", "atBats")
        avg = cls._stat_from_entry(athlete_entry, keys, "avg", "battingAverage")
        return {
            "id": str(athlete.get("id") or ""),
            "display_name": athlete.get("displayName") or athlete.get("shortName") or "",
            "short_name": athlete.get("shortName") or athlete.get("displayName") or "",
            "headshot": ((athlete.get("headshot") or {}).get("href") or ""),
            "bat_order": next_bat_order,
            "avg": avg,
            "hits_ab": f"{h}-{ab}" if h and ab else "",
        }

    @classmethod
    def _normalize_lineups(
        cls,
        summary: dict[str, Any],
        batter_id: str,
        inning_context: dict[str, Any] | None = None,
        is_live: bool = False,
    ) -> Lineups:
        """Flatten ``summary["boxscore"]`` into per-side Game-stat lineups.

        Pure transform of the box score the coordinator already fetches every
        tick — **zero extra ESPN calls**. Returns ``{}`` when the box score
        has no usable players (typically pre-game; the card then shows
        "Lineup not posted yet"). Season stats are *not* sourced here — the
        card fetches those lazily over WebSocket (see handoff §3).
        """
        boxscore = summary.get("boxscore") or {}
        team_blocks = boxscore.get("players") or []
        if not team_blocks:
            return {}

        # Resolve away/home by joining each players block's team id to the
        # boxscore.teams homeAway map. ESPN usually orders players[] as
        # [away, home], but that is not guaranteed, so only fall back to
        # positional assignment when the map can't resolve a block.
        side_by_team_id: dict[str, str] = {}
        for team_entry in boxscore.get("teams") or []:
            tid = str((team_entry.get("team") or {}).get("id") or "")
            side = str(team_entry.get("homeAway") or "").lower()
            if tid and side in ("away", "home"):
                side_by_team_id[tid] = side

        # Which side is batting: the team block whose batting list contains
        # the current batter (same approach as _normalize_on_deck). Empty
        # for a pre-game or completed game (no current batter).
        batting_team_id = ""
        if batter_id:
            for team_block in team_blocks:
                for stat_block in team_block.get("statistics") or []:
                    if stat_block.get("type") != "batting":
                        continue
                    for entry in stat_block.get("athletes") or []:
                        if str((entry.get("athlete") or {}).get("id") or "") == batter_id:
                            batting_team_id = str((team_block.get("team") or {}).get("id") or "")
                            break
                    if batting_team_id:
                        break
                if batting_team_id:
                    break

        result: Lineups = {}
        for index, team_block in enumerate(team_blocks):
            team = team_block.get("team") or {}
            team_id = str(team.get("id") or "")
            side = side_by_team_id.get(team_id, "")
            if side not in ("away", "home"):
                # Last-resort positional fallback (ESPN convention: away first).
                side = "away" if (index == 0 and "away" not in result) else "home"
            if side in result:
                continue

            hitters: list[dict[str, Any]] = []
            pitchers: list[dict[str, Any]] = []
            for stat_block in team_block.get("statistics") or []:
                block_type = stat_block.get("type")
                keys = [str(k or "") for k in (stat_block.get("keys") or [])]
                athletes = stat_block.get("athletes") or []
                if block_type == "batting":
                    for entry in athletes:
                        hitters.append(cls._lineup_hitter_row(entry, keys))
                elif block_type == "pitching":
                    for entry in athletes:
                        pitchers.append(cls._lineup_pitcher_row(entry, keys))

            # Stable sort by batting order (0 — i.e. pitchers who batted /
            # missing — sinks to the end). Python's sort is stable, so a
            # substitute keeps its position behind the starter it replaced
            # (ESPN already lists starter-before-sub within a shared slot).
            hitters.sort(key=lambda h: h.get("bat_order") or 99)

            is_batting = bool(batting_team_id) and team_id == batting_team_id
            result[side] = {  # type: ignore[literal-required]
                "team_id": team_id,
                "abbreviation": str(team.get("abbreviation") or ""),
                "name": str(team.get("displayName") or ""),
                "short_name": str(team.get("name") or team.get("shortDisplayName") or ""),
                "logo": str(team.get("logo") or ""),
                "is_batting": is_batting,
                # Only meaningful while a game is under way — pre-game there is
                # no half to anchor to, and post-final "up" has no referent.
                "up_bat_order": (
                    cls._up_bat_order(summary, inning_context, side, batter_id, is_batting) if is_live else 0
                ),
                "hitters": hitters,
                "pitchers": pitchers,
            }

        return result

    @classmethod
    def _normalize_decisions(cls, summary: dict[str, Any]) -> PitcherDecisions:
        """Pull the W/L/SV pitcher trio out of the box score's pitching notes.

        ESPN tags decision pitchers with a ``"pitchingDecision"`` note whose
        text is ``"W, 3-1"`` / ``"L, 2-5"`` / ``"S, 12"`` (the trailing token
        is a season W-L for W/L and a save count for S). The save code is
        observed as ``"S"`` in ESPN's MLB feeds; we also accept ``"SV"``
        defensively. ESPN occasionally concatenates a second decision onto a
        loss (``"L, 3-2, B, 4"`` for a loss + blown save) — we keep only
        the first comma-segment after the code as the record. Pure
        transform of the already-fetched box score; no extra ESPN calls.
        Returns ``{}`` until ESPN attaches the notes (post-final). HLD and
        other decisions are ignored.
        """
        boxscore = summary.get("boxscore") or {}
        side_by_team_id: dict[str, str] = {}
        for team_entry in boxscore.get("teams") or []:
            tid = str((team_entry.get("team") or {}).get("id") or "")
            side = str(team_entry.get("homeAway") or "").lower()
            if tid and side in ("away", "home"):
                side_by_team_id[tid] = side

        role_by_code = {"W": "win", "L": "loss", "S": "save", "SV": "save"}
        result: PitcherDecisions = {}
        for team_block in boxscore.get("players") or []:
            team = team_block.get("team") or {}
            team_id = str(team.get("id") or "")
            team_abbr = str(team.get("abbreviation") or "")
            team_side = side_by_team_id.get(team_id, "")
            for stat_block in team_block.get("statistics") or []:
                if stat_block.get("type") != "pitching":
                    continue
                for entry in stat_block.get("athletes") or []:
                    text = ""
                    for note in entry.get("notes") or []:
                        if str((note or {}).get("type") or "") == "pitchingDecision":
                            text = str((note or {}).get("text") or "")
                            if text:
                                break
                    if not text:
                        continue
                    segments = [s.strip() for s in text.split(",")]
                    code = segments[0].upper() if segments else ""
                    role = role_by_code.get(code)
                    if not role:
                        continue
                    record = segments[1] if len(segments) > 1 else ""
                    athlete = entry.get("athlete") or {}
                    headshot_obj = athlete.get("headshot")
                    if isinstance(headshot_obj, dict):
                        headshot = str(headshot_obj.get("href") or "")
                    else:
                        headshot = str(headshot_obj or "")
                    result[role] = {  # type: ignore[literal-required]
                        "id": str(athlete.get("id") or ""),
                        "name": str(athlete.get("displayName") or athlete.get("shortName") or ""),
                        "short_name": str(athlete.get("shortName") or athlete.get("displayName") or ""),
                        "headshot": headshot,
                        "record": record,
                        "decision": code,
                        "team_side": team_side,
                        "team_abbr": team_abbr,
                    }
        return result

    @classmethod
    def _lineup_hitter_row(cls, entry: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        """One hitter row for :meth:`_normalize_lineups` (Game stats).

        ``avg`` is the *season* average ESPN carries in the box score, not a
        game value. ``position`` is the in-game fielding position (entry
        level), falling back to the player's listed position.
        """
        athlete = entry.get("athlete") or {}
        entry_pos = (entry.get("position") or {}).get("abbreviation")
        listed_pos = (athlete.get("position") or {}).get("abbreviation")
        return {
            "id": str(athlete.get("id") or ""),
            "name": str(athlete.get("displayName") or athlete.get("shortName") or ""),
            "short_name": str(athlete.get("shortName") or athlete.get("displayName") or ""),
            "position": str(entry_pos or listed_pos or ""),
            "bat_order": int(entry.get("batOrder") or 0),
            "starter": bool(entry.get("starter")),
            "active": bool(entry.get("active")),
            "ab": cls._stat_from_entry(entry, keys, "atBats", "ab"),
            "r": cls._stat_from_entry(entry, keys, "runs", "r"),
            "h": cls._stat_from_entry(entry, keys, "hits", "h"),
            "hr": cls._stat_from_entry(entry, keys, "homeRuns", "hr"),
            "rbi": cls._stat_from_entry(entry, keys, "RBIs", "rbi"),
            "bb": cls._stat_from_entry(entry, keys, "walks", "bb"),
            "k": cls._stat_from_entry(entry, keys, "strikeouts", "so", "k"),
            "avg": cls._stat_from_entry(entry, keys, "avg", "battingAverage"),
        }

    @classmethod
    def _lineup_pitcher_row(cls, entry: dict[str, Any], keys: list[str]) -> dict[str, Any]:
        """One pitcher row for :meth:`_normalize_lineups` (Game stats).

        ``era`` is the *season* ERA from the box score. ``pc`` is the total
        pitch count (``pitches``; the ``pitches-strikes`` key is the
        ``"87-58"`` form). ``decision`` is the W/L/SV/HLD note text, empty
        for a no-decision.
        """
        athlete = entry.get("athlete") or {}
        decision = ""
        for note in entry.get("notes") or []:
            if str((note or {}).get("type") or "") == "pitchingDecision":
                decision = str((note or {}).get("text") or "")
                if decision:
                    break
        return {
            "id": str(athlete.get("id") or ""),
            "name": str(athlete.get("displayName") or athlete.get("shortName") or ""),
            "short_name": str(athlete.get("shortName") or athlete.get("displayName") or ""),
            "starter": bool(entry.get("starter")),
            "active": bool(entry.get("active")),
            "decision": decision,
            "ip": cls._stat_from_entry(entry, keys, "fullInnings.partInnings", "ip", "inningsPitched", "IP"),
            "h": cls._stat_from_entry(entry, keys, "hits", "h"),
            "r": cls._stat_from_entry(entry, keys, "runs", "r"),
            "er": cls._stat_from_entry(entry, keys, "earnedRuns", "er"),
            "bb": cls._stat_from_entry(entry, keys, "walks", "bb"),
            "k": cls._stat_from_entry(entry, keys, "strikeouts", "so", "k"),
            "pc": cls._stat_from_entry(entry, keys, "pitches", "pitchCount"),
            "era": cls._stat_from_entry(entry, keys, "ERA", "era", "earnedRunAverage"),
        }

    @staticmethod
    def _resolve_display_comp(
        summary: dict[str, Any], display_id: str, display_event: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Pick the competition dict to render from, preferring the live summary payload.

        ESPN's summary endpoint returns a richer competition object than the schedule
        feed; only fall back to the schedule's copy when the summary id doesn't match
        the event we're displaying (e.g. summary fetch failed or returned a different
        game).
        """
        summary_header = summary.get("header") or {}
        summary_competitions = summary_header.get("competitions") or []
        summary_comp = summary_competitions[0] if summary_competitions else None
        summary_id = str(summary.get("id") or summary_header.get("id") or "")
        if summary_comp is not None and summary_id == display_id:
            return summary_comp
        if display_event and display_event.get("competitions"):
            return (display_event["competitions"] or [{}])[0]
        return None

    @staticmethod
    def _resolve_competitor_ids(display_comp: dict[str, Any] | None) -> tuple[str, str]:
        """Return (away_team_id, home_team_id) from a competition dict."""
        away_id = ""
        home_id = ""
        for competitor in (display_comp or {}).get("competitors") or []:
            side = competitor.get("homeAway")
            team_id = str((competitor.get("team") or {}).get("id", ""))
            if side == "away":
                away_id = team_id
            elif side == "home":
                home_id = team_id
        return away_id, home_id

    async def _fetch_team_payload(self, team_id: str, side: str) -> dict[str, Any]:
        """Fetch team metadata, served from a TTL cache to avoid repeat ESPN calls.

        Logs failures at debug level. On failure, falls back to the last-known
        cached payload (even if expired) before returning ``{}``.
        """
        if not team_id:
            return {}
        cached = self._team_payload_cache.get(team_id)
        now_ts = time.time()
        if cached is not None and (now_ts - cached[0]) < TEAM_METADATA_TTL_SECONDS:
            return cached[1]
        try:
            payload = await self._get_json(
                f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{team_id}"
            )
        except Exception as err:
            _LOGGER.debug("Unable to fetch %s team metadata: %s", side, err)
            # Re-use stale cache rather than blanking the UI.
            return cached[1] if cached is not None else {}
        self._team_payload_cache[team_id] = (now_ts, payload)
        return payload

    async def _get_standings(self) -> dict[str, Any]:
        """Fetch league standings, served from a TTL cache.

        Standings change at most a few times per day, so a long TTL is
        appropriate. On failure, returns the last-known payload (even if
        beyond the stale-fallback window we'd let it go entirely empty).
        """
        cached = self._standings_cache
        now_ts = time.time()
        if cached is not None and (now_ts - cached[0]) < STANDINGS_TTL_SECONDS:
            return cached[1]
        try:
            payload = await self._get_json("https://site.api.espn.com/apis/v2/sports/baseball/mlb/standings")
        except Exception as err:
            _LOGGER.debug("Unable to fetch standings: %s", err)
            if cached is not None and (now_ts - cached[0]) < STANDINGS_STALE_FALLBACK_SECONDS:
                return cached[1]
            return {}
        self._standings_cache = (now_ts, payload)
        return payload

    async def _get_groups(self) -> dict[str, Any]:
        """Fetch the league/divisions ``groups`` payload, served from a long TTL cache.

        Divisions are stable across the regular season, so this is fetched
        infrequently and reused. The stale-fallback window is intentionally
        long so a temporary ESPN outage doesn't blank the standings panel.
        """
        cached = self._groups_cache
        now_ts = time.time()
        if cached is not None and (now_ts - cached[0]) < GROUPS_TTL_SECONDS:
            return cached[1]
        try:
            payload = await self._get_json("https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/groups")
        except Exception as err:
            _LOGGER.debug("Unable to fetch groups: %s", err)
            if cached is not None and (now_ts - cached[0]) < GROUPS_STALE_FALLBACK_SECONDS:
                return cached[1]
            return {}
        self._groups_cache = (now_ts, payload)
        return payload

    @staticmethod
    def _resolve_batter_pitcher_ids(summary: dict[str, Any]) -> tuple[str, str]:
        """Return (batter_id, pitcher_id), falling back to the most recent
        ``start batter/pitcher`` play when the situation block is empty.
        """
        situation = summary.get("situation") or {}
        batter = situation.get("batter") or {}
        pitcher = situation.get("pitcher") or {}
        batter_id = str(batter.get("playerId") or batter.get("id") or (batter.get("athlete") or {}).get("id") or "")
        pitcher_id = str(pitcher.get("playerId") or pitcher.get("id") or (pitcher.get("athlete") or {}).get("id") or "")

        if batter_id and pitcher_id:
            return batter_id, pitcher_id

        plays = summary.get("plays") or []
        for play in reversed(plays):
            play_type = str((play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or "").lower()
            if play_type not in _START_BATTER_PLAY_TYPES:
                continue
            for participant in play.get("participants") or []:
                part_type = str(participant.get("type", "")).lower()
                if not batter_id and part_type == "batter":
                    batter_id = str((participant.get("athlete") or {}).get("id", ""))
                elif not pitcher_id and part_type == "pitcher":
                    pitcher_id = str((participant.get("athlete") or {}).get("id", ""))
            if batter_id and pitcher_id:
                break
        return batter_id, pitcher_id

    @staticmethod
    def _resolve_status_info(display_comp: dict[str, Any] | None) -> tuple[str, bool, bool]:
        """Return (status_detail_text, is_live, is_delayed) for a competition."""
        status_type = ((display_comp or {}).get("status") or {}).get("type") or {}
        state = str(status_type.get("state", "")).lower()
        status_name = str(status_type.get("name", "")).upper()
        status_detail = str(
            status_type.get("detail")
            or status_type.get("shortDetail")
            or status_type.get("statusPrimary")
            or status_type.get("description")
            or ""
        ).strip()
        # ESPN uses several status flavors for an interrupted live game:
        # STATUS_DELAYED, STATUS_RAIN_DELAY, STATUS_SUSPENDED, etc.; the
        # detail text likewise varies ("Delayed", "Rain Delay", "Weather
        # Delay", "Delay: Rain", "Suspended"). Match on the shorter "delay"
        # stem (the previous "delayed" check missed "Rain Delay") and on
        # "suspend" so suspended games also flip the live matchup view off.
        detail_lower = status_detail.lower()
        is_delayed = (
            status_name == STATUS_NAME_DELAYED
            or "DELAY" in status_name
            or "SUSPEND" in status_name
            or "delay" in detail_lower
            or "suspend" in detail_lower
        )
        is_live = state in LIVE_STATES or status_name == STATUS_NAME_IN_PROGRESS or is_delayed
        return status_detail, is_live, is_delayed

    @staticmethod
    def _detect_game_events(
        prev: MlbLiveScoreboardData | None,
        curr: MlbLiveScoreboardData,
        team_id: int,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Compare the previous and current coordinator data and return a list of
        ``(event_name, payload)`` pairs that should be fired on the HA bus.

        Pure function (no I/O, no ``self``) so it is straightforward to unit-test
        offline. Returns ``[]`` when there is no previous data (first refresh)
        or when the displayed event has changed (different game), to avoid
        firing spurious events at startup or across game boundaries.
        """
        if prev is None:
            return []

        # Across a game boundary, scores from the previous game don't compare
        # meaningfully to the new game. Skip dispatch entirely; the next poll
        # will establish the new baseline. An empty-vs-set transition (e.g. a
        # cold-start refresh that couldn't resolve a display event) is also
        # treated as a boundary so we don't compare a synthetic 0-0 baseline
        # against a real final score and emit spurious score deltas.
        if prev.display_event_id != curr.display_event_id:
            return []
        # A prev with no usable competition has no real scores to compare
        # against; treat this refresh as the baseline rather than firing
        # deltas relative to (0, 0).
        if not prev.selected_competition:
            return []

        comp = curr.selected_competition or {}
        my_side, opp_side = _resolve_my_side(comp, team_id)
        if my_side is None or opp_side is None:
            return []

        my_score_curr, opp_score_curr = _scores_for_sides(comp, my_side, opp_side)
        prev_comp = prev.selected_competition or {}
        my_score_prev, opp_score_prev = _scores_for_sides(prev_comp, my_side, opp_side)

        opp_team_block = _competitor_for_side(comp, opp_side)
        opp_team = (opp_team_block.get("team") or {}) if opp_team_block else {}

        base_payload: dict[str, Any] = {
            "team_abbr": curr.team_abbr,
            "team_name": curr.team_name,
            "team_score": my_score_curr,
            "opponent_abbr": opp_team.get("abbreviation") or "",
            "opponent_name": opp_team.get("displayName") or opp_team.get("name") or "",
            "opponent_score": opp_score_curr,
            "is_home": my_side == "home",
            "inning": curr.inning_context.get("period") or 0,
            "inning_half": _inning_half(curr.inning_context),
            "event_id": curr.display_event_id,
            "status_detail": curr.status_text,
        }

        events: list[tuple[str, dict[str, Any]]] = []

        # Score deltas: only fire when scores increase. Skip while delayed
        # because ESPN occasionally flips scores during delay corrections.
        # An increase larger than a grand slam (MAX_PLAUSIBLE_SCORE_DELTA) in a
        # single comparison can't be one play; it's a stale-baseline correction
        # (ESPN under-reporting a score, a post-restart re-baseline, or missed
        # polls). Suppress it instead of announcing an impossible "N run play";
        # ``self.data`` updates every refresh, so the next poll re-baselines and
        # genuine later scoring still fires a normal small delta.
        if not curr.is_delayed:
            my_delta = my_score_curr - my_score_prev
            opp_delta = opp_score_curr - opp_score_prev
            if 0 < my_delta <= MAX_PLAUSIBLE_SCORE_DELTA:
                payload = {
                    **base_payload,
                    "score_delta": my_delta,
                    "scoring_play_text": _latest_scoring_play_text(curr),
                }
                events.append((EVENT_TEAM_SCORED, payload))
            elif my_delta > MAX_PLAUSIBLE_SCORE_DELTA:
                _LOGGER.warning(
                    "Suppressing implausible %s score jump %s->%s (delta %s) for %s; "
                    "treating as a stale-baseline correction, not a scoring play",
                    curr.team_abbr,
                    my_score_prev,
                    my_score_curr,
                    my_delta,
                    curr.display_event_id,
                )
            if 0 < opp_delta <= MAX_PLAUSIBLE_SCORE_DELTA:
                payload = {
                    **base_payload,
                    "score_delta": opp_delta,
                    "scoring_play_text": _latest_scoring_play_text(curr),
                }
                events.append((EVENT_OPPONENT_SCORED, payload))
            elif opp_delta > MAX_PLAUSIBLE_SCORE_DELTA:
                _LOGGER.warning(
                    "Suppressing implausible opponent score jump %s->%s (delta %s) "
                    "for %s; treating as a stale-baseline correction, not a scoring play",
                    opp_score_prev,
                    opp_score_curr,
                    opp_delta,
                    curr.display_event_id,
                )

        # State transitions
        if not prev.is_live and curr.is_live:
            events.append((EVENT_GAME_STARTED, dict(base_payload)))

        prev_final = _is_final(prev.selected_competition)
        curr_final = _is_final(curr.selected_competition)
        if not prev_final and curr_final:
            events.append((EVENT_GAME_ENDED, dict(base_payload)))
            if my_score_curr > opp_score_curr:
                events.append((EVENT_GAME_WON, dict(base_payload)))
            elif opp_score_curr > my_score_curr:
                events.append((EVENT_GAME_LOST, dict(base_payload)))

        return events

    # Game-lifecycle transitions that are meaningful exactly once per game.
    # Score deltas are intentionally excluded: they legitimately recur and
    # are already guarded by the score-increase comparison.
    _ONCE_PER_GAME_EVENTS = frozenset(
        {EVENT_GAME_STARTED, EVENT_GAME_ENDED, EVENT_GAME_WON, EVENT_GAME_LOST}
    )

    def _suppress_repeat_once_events(
        self,
        events: list[tuple[str, dict[str, Any]]],
        event_id: str | None,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Drop once-per-game transition events that already fired for this game.

        ESPN can flicker ``is_live`` (and final status) at game boundaries by
        briefly serving a stale status read, which would otherwise re-fire
        GAME_STARTED/ENDED/WON/LOST. We remember which of these already fired
        for the current ``display_event_id`` and suppress repeats; a new game
        id resets the memory so the next game fires normally.
        """
        if event_id != self._fired_once_event_id:
            self._fired_once_event_id = event_id
            self._fired_once_events = set()

        kept: list[tuple[str, dict[str, Any]]] = []
        for name, payload in events:
            if name in self._ONCE_PER_GAME_EVENTS:
                if name in self._fired_once_events:
                    _LOGGER.debug(
                        "Suppressing repeat %s for game %s (already fired)",
                        name,
                        event_id,
                    )
                    continue
                self._fired_once_events.add(name)
            kept.append((name, payload))
        return kept

    def _dispatch_game_events(self, events: list[tuple[str, dict[str, Any]]]) -> None:
        """Fire detector-produced events on the Home Assistant bus and run any
        user-configured action sequences attached to them.
        """
        options = self.entry.options or {}
        for name, payload in events:
            _LOGGER.info(
                "Firing %s for %s vs %s (%s-%s)",
                name,
                payload.get("team_abbr"),
                payload.get("opponent_abbr"),
                payload.get("team_score"),
                payload.get("opponent_score"),
            )
            self.hass.bus.async_fire(name, payload)

            # Run inline action sequence if the user configured one for this
            # event in the options flow. Each invocation is fire-and-forget
            # so a slow/failing user action cannot block the next refresh.
            opt_key = EVENT_OPTION_KEYS.get(name)
            sequence = options.get(opt_key) if opt_key else None
            if sequence:
                self.hass.async_create_task(self._run_event_action(name, sequence, payload))

    async def _run_event_action(
        self,
        event_name: str,
        sequence: Any,
        payload: dict[str, Any],
    ) -> None:
        """Execute a configured action sequence with event payload as variables."""
        try:
            script = Script(
                self.hass,
                sequence,
                f"{DOMAIN} {event_name}",
                DOMAIN,
            )
            await script.async_run(payload, Context())
        except Exception as err:
            _LOGGER.warning("Configured action for %s failed: %s", event_name, err)

    async def _fetch_schedule(self) -> dict[str, Any]:
        """Fetch this team's schedule, served from a 30-min TTL cache.

        The endpoint is ~55 KB gzip and dominates per-game bandwidth, but it is
        only used to enumerate this team's events and pull the display name —
        none of the live in-game state comes from it — so a long TTL has no
        user-visible impact. Falls back to a slightly-stale cache when a fetch
        fails, and only raises once even that window has elapsed.
        """
        schedule_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{self.team_abbr.lower()}/schedule"
        )
        now_ts = time.time()
        cached_schedule = self._schedule_cache
        if cached_schedule is not None and (now_ts - cached_schedule[0]) < SCHEDULE_TTL_SECONDS:
            return cached_schedule[1]
        try:
            schedule = await self._get_json(schedule_url)
            self._schedule_cache = (now_ts, schedule)
            return schedule
        except Exception as err:
            if cached_schedule is not None and (now_ts - cached_schedule[0]) < SCHEDULE_STALE_FALLBACK_SECONDS:
                _LOGGER.warning(
                    "Schedule fetch failed (%s); reusing cache from %.0fs ago",
                    err,
                    now_ts - cached_schedule[0],
                )
                return cached_schedule[1]
            raise UpdateFailed(f"Unable to fetch schedule: {err}") from err

    def _local_now(self) -> datetime:
        """Return a timezone-aware 'now' in the Home Assistant local time zone.

        Falls back to the system local zone when HA's configured zone is
        missing or unrecognized, so the All-Star day comparison always has an
        aware reference to work from.
        """
        tzname = getattr(getattr(self.hass, "config", None), "time_zone", None)
        if tzname:
            try:
                return datetime.now(ZoneInfo(tzname))
            except Exception:
                # Unknown/invalid tz name; fall back to the system local zone.
                pass
        return datetime.now().astimezone()

    @staticmethod
    def _event_on_local_day(event_date_raw: Any, now_local: datetime) -> bool:
        """Return True if the ESPN event's start falls on the same local calendar
        day as ``now_local`` (a timezone-aware datetime). ESPN dates are UTC; we
        convert into ``now_local``'s zone before comparing so an ~8 PM ET first
        pitch (stored as the next day's 00:00 UTC) still counts as "today".
        """
        ts = _parse_iso_ts(event_date_raw)
        if ts is None:
            return False
        tz = now_local.tzinfo
        return datetime.fromtimestamp(ts, tz).date() == now_local.date()

    async def _fetch_allstar_schedule(self) -> dict[str, Any] | None:
        """Fetch the All-Star Game schedule (a single event), day-TTL cached.

        Best-effort: on a fetch failure with no usable cache it returns None so
        a hiccup here never disrupts the normal team refresh (the caller simply
        falls through to the configured team's schedule).
        """
        now_ts = time.time()
        cached = self._allstar_schedule_cache
        if cached is not None and (now_ts - cached[0]) < ALLSTAR_SCHEDULE_TTL_SECONDS:
            return cached[1]
        url = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{ALLSTAR_TEAM_SLUG}/schedule"
        try:
            payload = await self._get_json(url)
        except Exception as err:
            # Best-effort: a failed fetch must never break the normal refresh.
            _LOGGER.debug("All-Star schedule fetch failed: %s", err)
            if cached is not None and (now_ts - cached[0]) < ALLSTAR_SCHEDULE_STALE_FALLBACK_SECONDS:
                return cached[1]
            return None
        self._allstar_schedule_cache = (now_ts, payload)
        return payload

    async def _allstar_override(self) -> tuple[dict[str, Any], list[dict[str, Any]]] | None:
        """Return ``(schedule, events)`` for the All-Star Game when it is played
        on today's local calendar day, else None.

        This is what lets every entry surface the All-Star Game on that one day
        regardless of which club it follows — no team plays during the break, so
        the club's own schedule has nothing current to show. The configured
        ``team_id`` is left untouched, so ``_detect_game_events`` (which bails
        when the team isn't a competitor) fires no bus events for the exhibition.
        """
        schedule = await self._fetch_allstar_schedule()
        if not schedule:
            return None
        events = schedule.get("events") or []
        if not events:
            return None
        now_local = self._local_now()
        if not any(self._event_on_local_day(e.get("date"), now_local) for e in events):
            return None
        return schedule, events

    async def _resolve_schedule(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Return the ``(schedule, events)`` the coordinator should display now:
        the All-Star Game on the day it's played, otherwise the configured team's
        schedule. Shared by the live refresh and schedule navigation so both
        agree on which schedule is in effect (and the prev/next arrows correctly
        find nothing to page to on the single-event All-Star schedule).
        """
        override = await self._allstar_override()
        if override is not None:
            return override
        schedule = await self._fetch_schedule()
        return schedule, (schedule.get("events") or [])

    async def async_get_game_at_offset(self, offset: int) -> dict[str, Any] | None:
        """Return a neighboring game's full card payload for schedule navigation.

        ``offset`` is signed steps from the game the card currently displays
        (0 = the auto-selected game). Used by the
        ``mlb_live_scoreboard/game_at_offset`` WebSocket command so the card can
        page back through previous results / forward through upcoming games
        without disturbing the shared live sensor. Returns ``None`` when there
        is no game at the (clamped) offset.
        """
        schedule, events = await self._resolve_schedule()
        prev_id, next_id, live_id, display_id, _ = self._select_event(events)
        target_id, clamped_offset, has_prev, has_next = self._event_at_offset(events, display_id, offset)
        if not target_id:
            return None
        data = await self._assemble_game_data(
            events, target_id, prev_id, next_id, live_id, schedule, live_bridge=False
        )
        # Lazy import avoids a module-load cycle (sensor imports the package).
        from .sensor import build_state_attributes

        return {
            "game_data": build_state_attributes(data),
            "offset": clamped_offset,
            "has_prev": has_prev,
            "has_next": has_next,
            "event_id": target_id,
        }

    async def async_half_inning_at_offset(self, offset: int) -> dict[str, Any] | None:
        """Return the play-by-play for an already-played half-inning of the live
        game, ``offset`` signed steps back from the current/live half (0 = live,
        -1 = the previous half, ...). Backs the inning pager so the card can page
        through past half-innings without disturbing the live sensor or refetching
        ESPN — it slices the summary cached on the last live refresh.

        Returns ``{plays, inning, half, label, offset (clamped), has_prev,
        has_next}`` or ``None`` when there's no cached live game or no half to
        show. ``offset`` is clamped so the pager can never page into a future
        (unplayed) half or before the first played half.
        """
        cache = self._live_summary_cache
        if not cache:
            return None
        _event_id, summary, inning_context = cache
        halves = self._played_half_innings(summary)
        if not halves:
            return None

        # Anchor (offset 0) = the current/live half. When it has plays it's in
        # the list; when it's brand new (no plays yet) anchor just past the end,
        # so a single step back lands on the most recent completed half.
        anchor_inning, anchor_half = self._resolve_target_half(inning_context)
        try:
            anchor_idx = halves.index((anchor_inning, anchor_half))
        except ValueError:
            anchor_idx = len(halves)

        # Clamp to a real played half no later than the anchor.
        upper = min(len(halves) - 1, anchor_idx)
        target_idx = max(0, min(anchor_idx + int(offset), upper))
        inning, half = halves[target_idx]
        half_cap = "Top" if half == "top" else "Bottom"
        synthetic_context = {
            "period": inning,
            "period_prefix": f"{half_cap} {inning}",
            "is_between_halves": False,
        }
        return {
            "plays": self._normalize_recent_plays(summary, synthetic_context),
            "inning": inning,
            "half": half,
            "label": f"{half_cap} {self._ordinal(inning)}",
            "offset": target_idx - anchor_idx,
            "has_prev": target_idx > 0,
            "has_next": target_idx < anchor_idx,
        }

    async def _assemble_game_data(
        self,
        events: list[dict[str, Any]],
        display_id: str,
        prev_id: str,
        next_id: str,
        live_id: str,
        schedule: dict[str, Any],
        *,
        live_bridge: bool,
    ) -> MlbLiveScoreboardData:
        """Fetch the summary for ``display_id`` and normalize it into card data.

        Shared by the live refresh (``live_bridge=True``) and schedule
        navigation (``live_bridge=False``). The live-only inning-rollover
        bridging mutates coordinator state and compares against ``self.data``,
        so it runs only for the live refresh; a navigated final/scheduled game
        is never between-halves, so skipping it is both correct and
        side-effect-free.
        """
        now_ts = time.time()
        display_event = next((e for e in events if str(e.get("id", "")) == str(display_id)), None)

        summary: dict[str, Any] = {}
        if display_id:
            # `_=<ts>` busts ESPN's CDN cache so we don't serve a stale status
            # block that pins our inning filter to a half-inning the game has
            # already left (observed in the wild as a multi-minute card
            # freeze). That failure mode only exists while the game is live,
            # and the buster forces every request through to ESPN's origin —
            # so only apply it when this (or the previous) refresh looks live,
            # and let the CDN absorb the idle-day polling.
            looks_live = bool(live_id and display_id == live_id) or bool(
                live_bridge and self.data is not None and self.data.is_live
            )
            summary_url = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event={display_id}"
            if looks_live:
                summary_url += f"&_={int(now_ts)}"
            try:
                summary = await self._get_json(summary_url)
            except Exception as err:
                _LOGGER.warning("Unable to fetch summary for %s: %s", display_id, err)

        display_comp = self._resolve_display_comp(summary, display_id, display_event)
        away_id, home_id = self._resolve_competitor_ids(display_comp)
        away_team_payload, home_team_payload = await asyncio.gather(
            self._fetch_team_payload(away_id, "away"),
            self._fetch_team_payload(home_id, "home"),
        )

        batter_id, pitcher_id = self._resolve_batter_pitcher_ids(summary)
        status_detail, is_live, is_delayed = self._resolve_status_info(display_comp)

        mode = "live" if live_id and display_id == live_id else ("previous" if display_id == prev_id else "next")

        # Season lines for the current batter (AVG/HR/RBI) and pitcher (ERA).
        # ESPN's boxscore carries these as season figures in regular games but
        # as *game* figures in the All-Star Game, so we source them from the
        # athlete stats endpoint (fetched in parallel, per-athlete TTL cached).
        batter_season_payload, pitcher_season_payload = await asyncio.gather(
            self._get_public_batter_stats(batter_id),
            self._get_public_pitcher_stats(pitcher_id),
        )
        batter_season_stats = (
            self._extract_current_season_batter_stats(batter_season_payload) if batter_season_payload else {}
        )
        pitcher_season_era = (
            (self._extract_season_line(pitcher_season_payload).get("pitching") or {}).get("era", "")
            if pitcher_season_payload
            else ""
        )

        team_name = self.team_abbr
        if schedule.get("team") and isinstance(schedule["team"], dict):
            team_name = str(schedule["team"].get("displayName") or schedule["team"].get("name") or self.team_abbr)

        # Compute once and reuse — `_normalize_inning_context` is a pure function of
        # (summary, display_comp), so previously calling it 5x per refresh was wasteful.
        inning_context = self._normalize_inning_context(summary, display_comp)

        effective_between = False
        if live_bridge:
            # Bridge the inning rollover so the card never flashes between Due Up
            # and the matchup view while ESPN's status / situation fields update
            # out of order. See the two bug modes documented around
            # ``_between_halves_entered_at`` in ``__init__``.
            now_ts = time.time()
            raw_between = bool(inning_context.get("is_between_halves"))
            prev_inning_context = getattr(self.data, "inning_context", None) or {}
            prev_between = bool(prev_inning_context.get("is_between_halves"))

            if raw_between:
                if not prev_between:
                    self._between_halves_entered_at = now_ts
                if batter_id and not self._third_out_batter_id:
                    # ``situation.batter`` at this moment is still the at-bat that
                    # produced the third out — capture so we can detect a stale
                    # situation block right after the next half starts.
                    self._third_out_batter_id = batter_id

            # Treat the very brief window where ESPN has advanced ``periodPrefix``
            # to the next half but ``situation.batter`` still points at the
            # just-ended at-bat as a continuation of between-halves, so the Due Up
            # panel stays on screen instead of rendering the previous matchup.
            stale_situation = (
                not raw_between
                and prev_between
                and bool(self._third_out_batter_id)
                and bool(batter_id)
                and batter_id == self._third_out_batter_id
            )

            effective_between = raw_between or stale_situation
            if effective_between:
                inning_context["is_between_halves"] = True
            else:
                self._between_halves_entered_at = None
                self._third_out_batter_id = None

            due_up = self._normalize_due_up(summary, inning_context)
            if stale_situation and not due_up and self.data and self.data.due_up:
                # ESPN occasionally clears ``situation.dueUp`` before
                # ``situation.batter`` updates — reuse the prior snapshot so the
                # Due Up panel keeps rendering through the stale window.
                due_up = list(self.data.due_up)
        else:
            # Navigated games are finals/scheduled — no inning-rollover bridge.
            due_up = self._normalize_due_up(summary, inning_context)

        # One pass over the summary plays serves the play-by-play list, the
        # third-out play, and the hold deadline. Previously each was normalized
        # independently (three full scans of the game's plays per refresh) —
        # same reasoning as the ``inning_context`` memo above.
        recent_plays = self._normalize_recent_plays(summary, inning_context)
        third_out_play = self._third_out_from_plays(recent_plays)
        third_out_hold_until = self._hold_until_for_play(third_out_play)
        if effective_between and self._between_halves_entered_at is not None:
            # Anchor the hold to whichever is later: the third-out play's
            # wallclock (preferred, when it has arrived) or the moment we
            # observed the inning end. The fallback covers the case where
            # ESPN flips the inning prefix before the third-out play lands.
            fallback_until = self._between_halves_entered_at + float(THIRD_OUT_HOLD_SECONDS)
            third_out_hold_until = (
                max(third_out_hold_until, fallback_until) if third_out_hold_until is not None else fallback_until
            )

        standings_payload, groups_payload = await asyncio.gather(self._get_standings(), self._get_groups())
        division_index = self._team_id_division_index(groups_payload)
        division_standings = self._normalize_standings(standings_payload, division_index, self.team_id)
        # ``records_map`` overrides the per-game ``recordSummary`` (which ESPN
        # leaves stale for hours after a final) with live standings W-L.
        records_map = self._records_from_standings(standings_payload)

        away_team_norm = self._normalize_team_payload(away_team_payload)
        home_team_norm = self._normalize_team_payload(home_team_payload)
        # Same override for the team-metadata-derived ``record_summary`` field.
        away_standings_record = records_map.get(str(away_team_norm.get("id") or ""))
        home_standings_record = records_map.get(str(home_team_norm.get("id") or ""))
        if away_standings_record:
            away_team_norm["record_summary"] = away_standings_record
        if home_standings_record:
            home_team_norm["record_summary"] = home_standings_record

        if live_bridge:
            # Snapshot the live summary + final inning context so the inning
            # pager can slice past half-innings without a fresh ESPN fetch. Only
            # the live refresh caches this; navigated (final/scheduled) games
            # don't drive the pager.
            self._live_summary_cache = (str(display_id), summary, dict(inning_context))

        return MlbLiveScoreboardData(
            team_abbr=self.team_abbr,
            team_id=self.team_id,
            team_name=team_name,
            display_event_id=display_id,
            live_event_id=live_id,
            previous_event_id=prev_id,
            next_event_id=next_id,
            selected_competition=self._compact_competition(display_comp, records_map),
            inning_context=inning_context,
            recent_plays=recent_plays,
            scoring_plays=self._normalize_scoring_plays(summary),
            current_pitches=self._normalize_current_pitches(summary, inning_context),
            away_team=away_team_norm,
            home_team=home_team_norm,
            current_batter=self._normalize_current_batter(summary, batter_id),
            current_pitcher=self._normalize_current_pitcher(summary, pitcher_id),
            batter_stats=self._normalize_batter_stats(summary, batter_id, batter_season_stats, is_live=is_live),
            pitcher_stats=self._normalize_pitcher_stats(summary, pitcher_id, season_era=pitcher_season_era),
            situation=self._normalize_situation(summary),
            probable_pitchers=self._normalize_probable_pitchers(display_comp),
            win_probability=self._normalize_win_probability(summary),
            due_up=due_up,
            third_out_play=third_out_play,
            third_out_hold_until=third_out_hold_until,
            on_deck=self._normalize_on_deck(summary, inning_context, batter_id),
            lineups=self._normalize_lineups(summary, batter_id, inning_context, is_live),
            leaders=self._normalize_leaders(summary),
            decisions=self._normalize_decisions(summary),
            division_standings=division_standings,
            highlights_url=self._extract_highlights_url(summary),
            mode=mode,
            status_text=status_detail,
            is_live=is_live,
            is_delayed=is_delayed,
        )

    def _compute_update_interval(self, data: MlbLiveScoreboardData, events: list[dict[str, Any]]) -> timedelta:
        """Pick the poll interval for the next refresh from the game state.

        Live (or delayed) games keep the fast cadence. Otherwise, when any
        scheduled event is inside the near-game window — shortly before its
        first pitch through the post-final settling period — poll at the
        near-game cadence so the live transition (and the bus events that
        hang off it) is detected promptly. The rest of the day the card is
        showing a final or a future matchup whose data barely moves, so the
        idle cadence is plenty and spares ESPN ~17k requests/day per team.
        """
        if data.is_live:
            return timedelta(seconds=SCAN_INTERVAL_LIVE_SECONDS)
        now_ts = time.time()
        for event in events:
            start_ts = _parse_iso_ts(event.get("date"))
            if start_ts is None:
                continue
            if start_ts - NEAR_GAME_LEAD_SECONDS <= now_ts <= start_ts + NEAR_GAME_LAG_SECONDS:
                return timedelta(seconds=SCAN_INTERVAL_NEAR_GAME_SECONDS)
        return timedelta(seconds=SCAN_INTERVAL_IDLE_SECONDS)

    async def _async_update_data(self) -> MlbLiveScoreboardData:
        schedule, events = await self._resolve_schedule()
        prev_id, next_id, live_id, display_id, _ = self._select_event(events)

        new_data = await self._assemble_game_data(
            events, display_id, prev_id, next_id, live_id, schedule, live_bridge=True
        )

        # Adapt the poll cadence to the game state; the coordinator reads
        # ``update_interval`` when it schedules the refresh after this one.
        self.update_interval = self._compute_update_interval(new_data, events)

        # Detect and fire game events by comparing against the previously
        # cached coordinator data. ``self.data`` is the last successful
        # snapshot — None on first refresh, in which case the detector
        # returns [] and we just establish the baseline.
        try:
            game_events = self._detect_game_events(self.data, new_data, self.team_id)
            game_events = self._suppress_repeat_once_events(
                game_events, new_data.display_event_id
            )
            if game_events:
                self._dispatch_game_events(game_events)
                # When a game ends, invalidate the standings cache so the very
                # next poll (≤ scan interval) re-fetches W-L. Without this the
                # on-card record could lag by up to STANDINGS_TTL_SECONDS.
                if any(name == EVENT_GAME_ENDED for name, _ in game_events):
                    self._standings_cache = None
        except Exception as err:
            # Never let event dispatch break a refresh.
            _LOGGER.warning("Game-event dispatch failed: %s", err)

        return new_data
