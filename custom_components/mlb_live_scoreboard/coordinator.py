from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.script import Script
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
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
    MLB_TEAM_MAP,
    SCHEDULE_STALE_FALLBACK_SECONDS,
    SHOW_NEXT_AFTER_PREV_SECONDS,
    STANDINGS_STALE_FALLBACK_SECONDS,
    STANDINGS_TTL_SECONDS,
    STATUS_NAME_DELAYED,
    STATUS_NAME_IN_PROGRESS,
    TEAM_METADATA_TTL_SECONDS,
)
from .types import (
    BatterStats,
    Competition,
    CurrentBatter,
    CurrentPitcher,
    DueUpEntry,
    InningContext,
    Leaders,
    OnDeck,
    PitcherStats,
    ProbablePitchers,
    RecentPlay,
    Situation,
    Standings,
    TeamMetadata,
)

_LOGGER = logging.getLogger(__name__)


# Play-text keywords that signal the end of an at-bat. Used by
# `_normalize_current_pitches` to know when to stop scanning back through plays.
_AT_BAT_END_KEYWORDS: tuple[str, ...] = (
    "singled", "doubled", "tripled", "homered", "walked", "struck out", "flied out",
    "grounded out", "lined out", "popped out", "reached on", "hit by pitch",
    "fouled out", "sacrifice", "sacrificed", "intentionally walked", "out at",
    "reached first", "fielder's choice",
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
_BATTER_OUTCOME_EXCLUDED: frozenset[str] = frozenset({"GO", "FO", "LO", "PO", "GIDP", "FC", "HBP"})

# Display ordering for the compact batter-outcome string.
_BATTER_OUTCOME_ORDER: tuple[str, ...] = ("HR", "3B", "2B", "1B", "BB", "IBB", "SF", "SAC", "K", "E")


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


def _resolve_my_side(
    comp: dict[str, Any], team_id: int
) -> tuple[str | None, str | None]:
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


def _scores_for_sides(
    comp: dict[str, Any], my_side: str, opp_side: str
) -> tuple[int, int]:
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
    current_pitches: list[str]
    away_team: TeamMetadata
    home_team: TeamMetadata
    current_batter: CurrentBatter
    current_pitcher: CurrentPitcher
    batter_stats: BatterStats
    pitcher_stats: PitcherStats
    situation: Situation
    probable_pitchers: ProbablePitchers
    due_up: list[DueUpEntry]
    third_out_play: RecentPlay
    on_deck: OnDeck
    leaders: Leaders
    division_standings: Standings
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
        # (fetched_at_ts, payload) for the team schedule endpoint. Used as a
        # short-lived fallback when ESPN's schedule endpoint has a transient
        # failure, so a one-poll hiccup doesn't blank the card.
        self._schedule_cache: tuple[float, dict[str, Any]] | None = None
        # (fetched_at_ts, payload) for the league standings endpoint.
        # Standings change a few times per day at most, so we re-fetch lazily
        # once TTL expires and reuse the prior payload as a stale fallback if
        # ESPN's standings endpoint fails.
        self._standings_cache: tuple[float, dict[str, Any]] | None = None
        # (fetched_at_ts, payload) for the league/divisions ``groups``
        # endpoint. Divisions don't change mid-season, so this is cached
        # for a long time and rarely re-fetched.
        self._groups_cache: tuple[float, dict[str, Any]] | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.team_abbr}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL_SECONDS),
        )

    async def _get_json(self, url: str) -> dict[str, Any]:
        headers = {
            "User-Agent": "Home Assistant",
            "Accept": "application/json",
        }
        async with self._session.get(url, headers=headers, timeout=20) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise UpdateFailed(f"HTTP {resp.status} for {url}: {text[:200]}")
            return await resp.json()

    def _select_event(self, events: list[dict[str, Any]]) -> tuple[str, str, str, str, dict[str, Any] | None]:
        now_ts = time.time()
        prev: dict[str, Any] | None = None
        next_ev: dict[str, Any] | None = None
        live: dict[str, Any] | None = None

        for ev in events:
            ts = _parse_iso_ts(ev.get("date"))

            comp = ((ev.get("competitions") or [{}])[0]) if ev.get("competitions") else {}
            status = ((comp.get("status") or {}).get("type") or (ev.get("status") or {}).get("type") or {})
            state = str(status.get("state", "")).lower()
            name = str(status.get("name", "")).upper()

            if not live and (state in LIVE_STATES or name == STATUS_NAME_IN_PROGRESS):
                live = ev

            if ts is not None:
                if ts <= now_ts:
                    prev = ev
                elif next_ev is None:
                    next_ev = ev

        previous_event_id = str((prev or {}).get("id", ""))
        next_event_id = str((next_ev or {}).get("id", ""))
        live_event_id = str((live or {}).get("id", ""))

        if live is not None:
            return previous_event_id, next_event_id, live_event_id, str(live.get("id", "")), live

        display_event = prev or next_ev
        if prev is not None and next_ev is not None:
            comp = ((prev.get("competitions") or [{}])[0]) if prev.get("competitions") else {}
            prev_status = ((comp.get("status") or {}).get("type") or (prev.get("status") or {}).get("type") or {})
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
    def _compact_competition(display_comp: dict[str, Any] | None) -> dict[str, Any] | None:
        if not display_comp:
            return None
        status = display_comp.get("status") or {}
        status_type = status.get("type") or {}
        compact_competitors: list[dict[str, Any]] = []
        for competitor in display_comp.get("competitors") or []:
            team = competitor.get("team") or {}
            logos = team.get("logos") or []
            compact_lines = []
            for line in (competitor.get("linescores") or [])[:MAX_LINESCORES]:
                compact_lines.append({
                    "value": line.get("value"),
                    "displayValue": line.get("displayValue"),
                    "hits": line.get("hits"),
                    "errors": line.get("errors"),
                })
            compact_competitors.append({
                "homeAway": competitor.get("homeAway"),
                "score": competitor.get("score"),
                "hits": competitor.get("hits"),
                "errors": competitor.get("errors"),
                "recordSummary": competitor.get("recordSummary"),
                "records": competitor.get("records") or [],
                "probables": competitor.get("probables") or [],
                "linescores": compact_lines,
                "team": {
                    "id": team.get("id"),
                    "abbreviation": team.get("abbreviation"),
                    "name": team.get("name") or team.get("displayName"),
                    "displayName": team.get("displayName") or team.get("name"),
                    "shortDisplayName": team.get("shortDisplayName") or team.get("abbreviation"),
                    "logo": team.get("logo") or (logos[0].get("href") if logos and isinstance(logos[0], dict) else ""),
                },
            })
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
        competition status block. Used to filter recent plays/pitches by inning."""
        status = (display_comp or {}).get("status") or {}
        prefix = str(status.get("periodPrefix") or ((status.get("type") or {}).get("detail") or ""))
        period = int(status.get("period") or ((status.get("type") or {}).get("period") or 0) or 0)
        due_up = (summary.get("situation") or {}).get("dueUp") or []
        return {
            "period": period,
            "period_prefix": prefix,
            "display_period": str(status.get("displayPeriod") or ""),
            "is_between_halves": prefix.lower().startswith(("mid", "end")),
            "has_due_up": bool(due_up),
        }


    @staticmethod
    def _normalize_current_pitches(summary: dict[str, Any], inning_context: dict[str, Any]) -> list[str]:
        """Return the pitch-text list for the at-bat in progress, in chronological order.

        Walks plays backwards from newest, collecting ``Pitch N: ...`` entries until
        an at-bat boundary (start/end batter, terminating play result) is reached.
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

        current: list[str] = []
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
                current.insert(0, txt)
                saw_pitch = True
                continue

            if play_type in {"start batter/pitcher", "start batter pitcher", "start-batterpitcher"}:
                break

            # keep scanning past steals/advances/other non-terminal updates for same batter

        return current

    @staticmethod
    def _normalize_recent_plays(summary: dict[str, Any], inning_context: dict[str, Any]) -> list[dict[str, Any]]:
        """Return play-result entries for the current half-inning in chronological order."""
        plays = summary.get("plays") or []
        if not isinstance(plays, list) or not plays:
            return []
        target_half = ""
        target_inning = int(inning_context.get("period") or 0)
        prefix = str(inning_context.get("period_prefix") or "").lower()
        if prefix.startswith("top"):
            target_half = "top"
        elif prefix.startswith("bottom") or prefix.startswith("bot"):
            target_half = "bottom"
        elif inning_context.get("is_between_halves") and target_inning > 0:
            target_half = "top" if prefix.startswith("mid") else "bottom"
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
            if play_type not in {"play result", "play-result", "end batter/pitcher", "end batter pitcher", "pitching change", "lineup change"}:
                continue
            outs = play.get("outs") or ((play.get("result") or {}).get("outs"))
            away_score = play.get("awayScore")
            home_score = play.get("homeScore")
            wallclock_ts = _parse_iso_ts(play.get("wallclock"))
            results.append({
                "id": str(play.get("id") or ""),
                "text": txt,
                "outs": int(outs) if outs not in (None, "") else None,
                "away_score": away_score,
                "home_score": home_score,
                "wallclock_ts": wallclock_ts,
                "scoring_play": play.get("scoringPlay") is True,
                "score_value": int(play.get("scoreValue") or 0),
                "play_type": play_type,
                "alternative_type": str((play.get("alternativeType") or {}).get("type") or (play.get("alternativeType") or {}).get("text") or "").lower(),
            })
        return results

    @staticmethod
    def _normalize_third_out_play(summary: dict[str, Any], inning_context: dict[str, Any]) -> dict[str, Any]:
        """Return the most recent play that produced the third out, or ``{}``."""
        plays = MlbLiveScoreboardCoordinator._normalize_recent_plays(summary, inning_context)
        for play in reversed(plays):
            outs = play.get("outs")
            if outs == 3:
                return play
        return {}

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
                division_name = str(
                    division.get("name") or division.get("abbreviation") or ""
                )
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
            e for e in league_entries
            if division_index.get(str((e.get("team") or {}).get("id") or "")) == my_division
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
            games_back = _stat_value(
                entry, {"divisiongamesbehind"}, {"dgb"}
            ) or _stat_value(entry, {"gamesbehind"}, {"gb"})
            normalized.append({
                "team_id": str(team.get("id") or ""),
                "team_name": str(team.get("displayName") or team.get("name") or ""),
                "team_short_name": str(
                    team.get("shortDisplayName")
                    or team.get("name")
                    or team.get("abbreviation")
                    or ""
                ),
                "wins": wins,
                "losses": losses,
                "games_back": games_back,
            })
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
                compact.append({
                    "category": str(category.get("displayName") or category.get("name") or ""),
                    "value": str(leader.get("displayValue") or leader.get("value") or ""),
                    "name": athlete.get("shortName") or athlete.get("displayName") or "",
                })
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
        fallback_entry: dict[str, Any] = {}
        fallback_keys: list[str] = []
        boxscore = summary.get("boxscore") or {}
        for team_block in boxscore.get("players") or []:
            for stat_block in team_block.get("statistics") or []:
                keys = [str(k or "") for k in (stat_block.get("keys") or [])]
                keys_lower = [k.lower() for k in keys]
                for athlete_entry in stat_block.get("athletes") or []:
                    athlete = athlete_entry.get("athlete") or {}
                    if str(athlete.get("id") or "") != athlete_id:
                        continue
                    if not fallback_entry:
                        fallback_entry, fallback_keys = athlete_entry, keys
                    if not preferred or all(pref in keys_lower for pref in preferred):
                        return athlete_entry, keys
        return fallback_entry, fallback_keys

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
    def _normalize_batter_stats(cls, summary: dict[str, Any], batter_id: str, season_stats: dict[str, Any] | None = None, is_live: bool = False) -> dict[str, Any]:
        entry, keys = cls._find_boxscore_athlete(summary, batter_id, preferred_keys=["avg", "atBats"])
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
            "avg": avg or season_stats.get("avg") or "",
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

    @classmethod
    def _normalize_pitcher_stats(cls, summary: dict[str, Any], pitcher_id: str) -> dict[str, Any]:
        """Extract IP / ERA / SO / pitch count for the pitcher of record."""
        entry, keys = cls._find_boxscore_athlete(summary, pitcher_id, preferred_keys=["era", "pitches"])
        pitches = cls._stat_from_entry(entry, keys, "pitches")
        strikes = cls._stat_from_entry(entry, keys, "strikes")
        innings_pitched = cls._stat_from_entry(entry, keys, "ip", "inningsPitched", "IP")
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
                        innings_pitched = innings_pitched or cls._stat_from_entry(athlete_entry, block_keys, "ip", "inningsPitched", "IP")
                        era = era or cls._stat_from_entry(athlete_entry, block_keys, "era", "earnedRunAverage", "ERA")
                        strikeouts = strikeouts or cls._stat_from_entry(athlete_entry, block_keys, "so", "strikeouts", "SO")
                        pitches = pitches or cls._stat_from_entry(athlete_entry, block_keys, "pitches")
                        strikes = strikes or cls._stat_from_entry(athlete_entry, block_keys, "strikes")

        return {
            "era": era,
            "innings_pitched": innings_pitched,
            "ip": innings_pitched,
            "pitches_strikes": f"{pitches}-{strikes}" if pitches and strikes else (pitches or ""),
            "strikeouts": strikeouts,
        }


    @classmethod
    def _normalize_due_up(cls, summary: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the next ``DUE_UP_LIMIT`` batters scheduled to bat next half-inning."""
        situation = summary.get("situation") or {}
        due_up = situation.get("dueUp") or []
        result: list[dict[str, Any]] = []
        for item in due_up[:DUE_UP_LIMIT]:
            player_id = str(item.get("playerId") or item.get("id") or "")
            entry, keys = cls._find_boxscore_athlete(summary, player_id)
            athlete = entry.get("athlete") or cls._find_roster_athlete(summary, player_id) or {}
            avg = cls._stat_from_entry(entry, keys, "avg", "battingAverage")
            ab = cls._stat_from_entry(entry, keys, "ab", "atBats")
            h = cls._stat_from_entry(entry, keys, "h", "hits")
            result.append({
                "id": player_id,
                "display_name": item.get("displayName") or athlete.get("displayName") or athlete.get("shortName") or "",
                "short_name": item.get("shortName") or athlete.get("shortName") or athlete.get("displayName") or "",
                "headshot": ((athlete.get("headshot") or {}).get("href") or ""),
                "avg": avg,
                "hits_ab": f"{h}-{ab}" if h and ab else "",
            })
        return result

    async def _get_public_batter_stats(self, athlete_id: str) -> dict[str, Any]:
        """Fetch an athlete's season stats payload, served from a TTL cache.

        Stats only change when the player completes an at-bat, so a short TTL
        eliminates the repeat ESPN calls that occur every 5 s while the same
        batter is at the plate. Falls back to a stale cache entry on fetch
        failure rather than blanking the season HR/RBI display.
        """
        if not athlete_id:
            return {}
        cached = self._batter_stats_cache.get(athlete_id)
        now_ts = time.time()
        if cached is not None and (now_ts - cached[0]) < BATTER_SEASON_STATS_TTL_SECONDS:
            return cached[1]
        url = f"https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/{athlete_id}/stats?region=us&lang=en&contentorigin=espn"
        try:
            payload = await self._get_json(url)
        except Exception as err:
            _LOGGER.debug("Unable to fetch batter season stats for %s: %s", athlete_id, err)
            return cached[1] if cached is not None else {}
        self._batter_stats_cache[athlete_id] = (now_ts, payload)
        return payload

    @staticmethod
    def _extract_current_season_batter_stats(stats_payload: dict[str, Any]) -> dict[str, Any]:
        categories = stats_payload.get("categories") or []
        current_year = datetime.now().year
        for category in categories:
            names = [str(n or "") for n in (category.get("names") or [])]
            if "homeRuns" not in names or "RBIs" not in names:
                continue
            hr_idx = names.index("homeRuns")
            rbi_idx = names.index("RBIs")
            avg_idx = names.index("avg") if "avg" in names else -1
            season_rows = category.get("statistics") or []
            row = next((r for r in season_rows if int((r.get("season") or {}).get("year") or 0) == current_year), None)
            if row is None and season_rows:
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
    def _normalize_situation(cls, summary: dict[str, Any]) -> dict[str, Any]:
        """Return balls/strikes/outs and base-runner occupancy + last names."""
        situation = summary.get("situation") or {}

        def _runner_ref(*candidates: Any) -> Any:
            for candidate in candidates:
                if candidate:
                    return candidate
            return None

        def _runner_last_name(ref: Any) -> str:
            if not ref:
                return ""
            athlete_id = ""
            if isinstance(ref, dict):
                athlete_id = str(
                    ref.get("playerId")
                    or ref.get("id")
                    or ((ref.get("athlete") or {}).get("id"))
                    or ""
                )
            athlete = cls._find_any_athlete(summary, athlete_id) if athlete_id else {}
            last_name = str(athlete.get("lastName") or "").strip()
            if last_name:
                return last_name
            display_name = str(
                athlete.get("displayName")
                or athlete.get("shortName")
                or (ref.get("displayName") if isinstance(ref, dict) else "")
                or (ref.get("shortName") if isinstance(ref, dict) else "")
                or ""
            ).strip()
            if display_name:
                parts = display_name.split()
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

        # Find current batter's batOrder in the boxscore
        boxscore = summary.get("boxscore") or {}
        current_bat_order = 0
        batting_team_block = None

        for team_block in boxscore.get("players") or []:
            # Match by checking competitors in header or by position
            for stat_block in team_block.get("statistics") or []:
                if stat_block.get("type") != "batting":
                    continue
                for athlete_entry in stat_block.get("athletes") or []:
                    athlete = athlete_entry.get("athlete") or {}
                    if str(athlete.get("id") or "") == batter_id:
                        current_bat_order = int(athlete_entry.get("batOrder") or 0)
                        batting_team_block = team_block
                        break
                if batting_team_block:
                    break
            if batting_team_block:
                break

        if not current_bat_order or not batting_team_block:
            return {}

        # Calculate next batter in order (wrap 9 -> 1)
        next_bat_order = (current_bat_order % BATTING_ORDER_SIZE) + 1

        # Find the next batter
        for stat_block in batting_team_block.get("statistics") or []:
            if stat_block.get("type") != "batting":
                continue
            keys = [str(k or "") for k in (stat_block.get("keys") or [])]
            for athlete_entry in stat_block.get("athletes") or []:
                if int(athlete_entry.get("batOrder") or 0) == next_bat_order:
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
        return {}

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
            payload = await self._get_json(
                "https://site.api.espn.com/apis/v2/sports/baseball/mlb/standings"
            )
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
            payload = await self._get_json(
                "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/groups"
            )
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
            if play_type not in {"start batter/pitcher", "start batter pitcher", "start-batterpitcher"}:
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
        is_delayed = status_name == STATUS_NAME_DELAYED or "delayed" in status_detail.lower()
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
        # will establish the new baseline.
        if (
            prev.display_event_id
            and curr.display_event_id
            and prev.display_event_id != curr.display_event_id
        ):
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
        if not curr.is_delayed:
            if my_score_curr > my_score_prev:
                payload = {
                    **base_payload,
                    "score_delta": my_score_curr - my_score_prev,
                    "scoring_play_text": _latest_scoring_play_text(curr),
                }
                events.append((EVENT_TEAM_SCORED, payload))
            if opp_score_curr > opp_score_prev:
                payload = {
                    **base_payload,
                    "score_delta": opp_score_curr - opp_score_prev,
                    "scoring_play_text": _latest_scoring_play_text(curr),
                }
                events.append((EVENT_OPPONENT_SCORED, payload))

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

    def _dispatch_game_events(
        self, events: list[tuple[str, dict[str, Any]]]
    ) -> None:
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
                self.hass.async_create_task(
                    self._run_event_action(name, sequence, payload)
                )

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
            _LOGGER.warning(
                "Configured action for %s failed: %s", event_name, err
            )

    async def _async_update_data(self) -> MlbLiveScoreboardData:
        schedule_url = (
            f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{self.team_abbr.lower()}/schedule"
        )
        try:
            schedule = await self._get_json(schedule_url)
            self._schedule_cache = (time.time(), schedule)
        except Exception as err:
            cached = self._schedule_cache
            now_ts = time.time()
            if cached is not None and (now_ts - cached[0]) < SCHEDULE_STALE_FALLBACK_SECONDS:
                _LOGGER.warning(
                    "Schedule fetch failed (%s); reusing cache from %.0fs ago",
                    err,
                    now_ts - cached[0],
                )
                schedule = cached[1]
            else:
                raise UpdateFailed(f"Unable to fetch schedule: {err}") from err

        events = schedule.get("events") or []
        prev_id, next_id, live_id, display_id, display_event = self._select_event(events)

        summary: dict[str, Any] = {}
        if display_id:
            try:
                summary = await self._get_json(
                    f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event={display_id}"
                )
            except Exception as err:
                _LOGGER.warning("Unable to fetch summary for %s: %s", display_id, err)

        display_comp = self._resolve_display_comp(summary, display_id, display_event)
        away_id, home_id = self._resolve_competitor_ids(display_comp)
        away_team_payload = await self._fetch_team_payload(away_id, "away")
        home_team_payload = await self._fetch_team_payload(home_id, "home")

        batter_id, pitcher_id = self._resolve_batter_pitcher_ids(summary)
        status_detail, is_live, is_delayed = self._resolve_status_info(display_comp)

        mode = "live" if live_id and display_id == live_id else ("previous" if display_id == prev_id else "next")

        batter_season_payload = await self._get_public_batter_stats(batter_id) if batter_id else {}
        batter_season_stats = self._extract_current_season_batter_stats(batter_season_payload) if batter_season_payload else {}

        team_name = self.team_abbr
        if schedule.get("team") and isinstance(schedule["team"], dict):
            team_name = str(schedule["team"].get("displayName") or schedule["team"].get("name") or self.team_abbr)

        # Compute once and reuse — `_normalize_inning_context` is a pure function of
        # (summary, display_comp), so previously calling it 5x per refresh was wasteful.
        inning_context = self._normalize_inning_context(summary, display_comp)

        standings_payload = await self._get_standings()
        groups_payload = await self._get_groups()
        division_index = self._team_id_division_index(groups_payload)
        division_standings = self._normalize_standings(
            standings_payload, division_index, self.team_id
        )

        new_data = MlbLiveScoreboardData(
            team_abbr=self.team_abbr,
            team_id=self.team_id,
            team_name=team_name,
            display_event_id=display_id,
            live_event_id=live_id,
            previous_event_id=prev_id,
            next_event_id=next_id,
            selected_competition=self._compact_competition(display_comp),
            inning_context=inning_context,
            recent_plays=self._normalize_recent_plays(summary, inning_context),
            current_pitches=self._normalize_current_pitches(summary, inning_context),
            away_team=self._normalize_team_payload(away_team_payload),
            home_team=self._normalize_team_payload(home_team_payload),
            current_batter=self._normalize_current_batter(summary, batter_id),
            current_pitcher=self._normalize_current_pitcher(summary, pitcher_id),
            batter_stats=self._normalize_batter_stats(summary, batter_id, batter_season_stats, is_live=is_live),
            pitcher_stats=self._normalize_pitcher_stats(summary, pitcher_id),
            situation=self._normalize_situation(summary),
            probable_pitchers=self._normalize_probable_pitchers(display_comp),
            due_up=self._normalize_due_up(summary),
            third_out_play=self._normalize_third_out_play(summary, inning_context),
            on_deck=self._normalize_on_deck(summary, inning_context, batter_id),
            leaders=self._normalize_leaders(summary),
            division_standings=division_standings,
            mode=mode,
            status_text=status_detail,
            is_live=is_live,
            is_delayed=is_delayed,
        )

        # Detect and fire game events by comparing against the previously
        # cached coordinator data. ``self.data`` is the last successful
        # snapshot — None on first refresh, in which case the detector
        # returns [] and we just establish the baseline.
        try:
            game_events = self._detect_game_events(self.data, new_data, self.team_id)
            if game_events:
                self._dispatch_game_events(game_events)
        except Exception as err:
            # Never let event dispatch break a refresh.
            _LOGGER.warning("Game-event dispatch failed: %s", err)

        return new_data
