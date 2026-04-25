from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_NAME, CONF_TEAM, DEFAULT_SCAN_INTERVAL_SECONDS, DOMAIN, MLB_TEAM_MAP

_LOGGER = logging.getLogger(__name__)


@dataclass
class MlbLiveScoreboardData:
    team_abbr: str
    team_id: int
    team_name: str
    display_event_id: str
    live_event_id: str
    previous_event_id: str
    next_event_id: str
    selected_competition: dict[str, Any] | None
    inning_context: dict[str, Any]
    recent_plays: list[dict[str, Any]]
    current_pitches: list[str]
    away_team: dict[str, Any]
    home_team: dict[str, Any]
    current_batter: dict[str, Any]
    current_pitcher: dict[str, Any]
    batter_stats: dict[str, Any]
    pitcher_stats: dict[str, Any]
    situation: dict[str, Any]
    probable_pitchers: dict[str, dict[str, Any]]
    due_up: list[dict[str, Any]]
    third_out_play: dict[str, Any]
    on_deck: dict[str, Any]
    leaders: dict[str, list[dict[str, Any]]]
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
        import time

        now_ts = time.time()
        prev: dict[str, Any] | None = None
        next_ev: dict[str, Any] | None = None
        live: dict[str, Any] | None = None

        for ev in events:
            date_raw = ev.get("date")
            ts = None
            if date_raw:
                try:
                    from datetime import datetime
                    ts = datetime.fromisoformat(date_raw.replace("Z", "+00:00")).timestamp()
                except Exception:
                    ts = None

            comp = ((ev.get("competitions") or [{}])[0]) if ev.get("competitions") else {}
            status = ((comp.get("status") or {}).get("type") or (ev.get("status") or {}).get("type") or {})
            state = str(status.get("state", "")).lower()
            name = str(status.get("name", "")).upper()

            if not live and (state in {"in", "live"} or name == "STATUS_IN_PROGRESS"):
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

            prev_start = prev.get("date")
            show_next = False
            if prev_start:
                try:
                    from datetime import datetime
                    prev_ts = datetime.fromisoformat(prev_start.replace("Z", "+00:00")).timestamp()
                    show_next_after = prev_ts + (16 * 60 * 60)
                    show_next = prev_complete and now_ts >= show_next_after
                except Exception:
                    show_next = False
            if show_next:
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
            for line in (competitor.get("linescores") or [])[:12]:
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

        end_keywords = (
            "singled", "doubled", "tripled", "homered", "walked", "struck out", "flied out",
            "grounded out", "lined out", "popped out", "reached on", "hit by pitch",
            "fouled out", "sacrifice", "sacrificed", "intentionally walked", "out at",
            "reached first", "fielder's choice"
        )

        current: list[str] = []
        saw_pitch = False

        for play in reversed(relevant):
            play_type = str(((play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or "")).lower()
            txt = str(play.get("text") or "").strip()
            low = txt.lower()

            if play_type in {"end batter/pitcher", "end batter pitcher"}:
                if saw_pitch:
                    break
                return []

            if play_type in {"play result", "play-result"} and any(key in low for key in end_keywords):
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
            play_type = str(((play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or "")).lower()
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
            wallclock_raw = str(play.get("wallclock") or "").strip()
            try:
                from datetime import datetime
                wallclock_ts = datetime.fromisoformat(wallclock_raw.replace("Z", "+00:00")).timestamp() if wallclock_raw else None
            except Exception:
                wallclock_ts = None
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
                "alternative_type": str(((play.get("alternativeType") or {}).get("type") or (play.get("alternativeType") or {}).get("text") or "")).lower(),
            })
        return results

    @staticmethod
    def _normalize_third_out_play(summary: dict[str, Any], inning_context: dict[str, Any]) -> dict[str, Any]:
        plays = MlbLiveScoreboardCoordinator._normalize_recent_plays(summary, inning_context)
        for play in reversed(plays):
            outs = play.get("outs")
            if outs == 3:
                return play
        return {}

    @staticmethod
    def _normalize_probable_pitchers(display_comp: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        probables: dict[str, dict[str, Any]] = {"away": {}, "home": {}}
        for competitor in (display_comp or {}).get("competitors") or []:
            side = str(competitor.get("homeAway") or "")
            if side not in {"away", "home"}:
                continue
            prob = ((competitor.get("probables") or [{}])[0]) if competitor.get("probables") else {}
            athlete = prob.get("athlete") or {}
            stats = prob.get("statistics") or []
            era = ""
            if isinstance(stats, list):
                for item in stats:
                    name = str(item.get("name") or item.get("abbreviation") or "").lower()
                    if name in {"era", "earned run average"}:
                        era = str(item.get("displayValue") or item.get("value") or "")
                        break
            probables[side] = {
                "name": athlete.get("displayName") or athlete.get("shortName") or "",
                "short_name": athlete.get("shortName") or athlete.get("displayName") or "",
                "era": era,
            }
        return probables

    @staticmethod
    def _normalize_leaders(summary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
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
                if len(compact) >= 3:
                    break
            result[side] = compact
        return result

    @staticmethod
    def _normalize_team_payload(team_payload: dict[str, Any]) -> dict[str, Any]:
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
        
        # Keywords that indicate at-bat end results
        outcome_patterns = [
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
        ]
        
        for play in plays:
            play_type = str(((play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or "")).lower()
            
            # Only look at play results / end batter events
            if play_type not in {"play result", "play-result", "end batter/pitcher", "end batter pitcher"}:
                continue
            
            txt = str(play.get("text") or "").strip()
            txt_lower = txt.lower()
            
            # Check if this play involves our batter (name appears at start of play text)
            name_match = False
            if txt_lower.startswith(last_name):
                name_match = True
            elif display_name and txt_lower.startswith(display_name):
                name_match = True
            elif short_name and txt_lower.startswith(short_name):
                name_match = True
            
            if not name_match:
                continue
            
            # Determine the outcome
            for pattern, abbrev in outcome_patterns:
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
        excluded = {"GO", "FO", "LO", "PO", "GIDP", "FC", "HBP"}
        filtered_outcomes = [o for o in outcomes if o.upper() not in excluded]
        
        if not filtered_outcomes:
            return ""
        
        # Count occurrences
        counts: dict[str, int] = {}
        for outcome in filtered_outcomes:
            counts[outcome] = counts.get(outcome, 0) + 1
        
        # Order of display priority (excluding filtered items)
        order = ["HR", "3B", "2B", "1B", "BB", "IBB", "SF", "SAC", "K", "E"]
        
        parts: list[str] = []
        for key in order:
            if key in counts:
                count = counts[key]
                if count > 1:
                    parts.append(f"{count}{key}")
                else:
                    parts.append(key)
        
        # Add any we missed
        for key, count in counts.items():
            if key not in order:
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
        situation = summary.get("situation") or {}
        due_up = situation.get("dueUp") or []
        result: list[dict[str, Any]] = []
        for item in due_up[:3]:
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
        if not athlete_id:
            return {}
        url = f"https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/{athlete_id}/stats?region=us&lang=en&contentorigin=espn"
        try:
            return await self._get_json(url)
        except Exception:
            return {}

    @staticmethod
    def _extract_current_season_batter_stats(stats_payload: dict[str, Any]) -> dict[str, Any]:
        categories = stats_payload.get("categories") or []
        from datetime import datetime
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
            def get_idx(idx: int) -> str:
                if idx >= 0 and idx < len(stats) and stats[idx] not in (None, ""):
                    return str(stats[idx])
                return ""
            return {
                "hr": get_idx(hr_idx),
                "rbi": get_idx(rbi_idx),
                "avg": get_idx(avg_idx),
            }
        return {}

    @classmethod
    def _normalize_situation(cls, summary: dict[str, Any]) -> dict[str, Any]:
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
        
        # Determine which team is batting: Top = away, Bottom = home
        period_prefix = str(inning_context.get("period_prefix") or "").lower()
        is_top = period_prefix.startswith("top")
        batting_team_key = "away" if is_top else "home"
        
        # Find current batter's batOrder in the boxscore
        boxscore = summary.get("boxscore") or {}
        current_bat_order = 0
        batting_team_block = None
        
        for team_block in boxscore.get("players") or []:
            team = team_block.get("team") or {}
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
        next_bat_order = (current_bat_order % 9) + 1
        
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

    async def _async_update_data(self) -> MlbLiveScoreboardData:
        try:
            schedule = await self._get_json(
                f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{self.team_abbr.lower()}/schedule"
            )
        except Exception as err:
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

        summary_header = summary.get("header") or {}
        summary_comp = ((summary_header.get("competitions") or [{}])[0]) if summary_header.get("competitions") else None
        display_comp = summary_comp if str(summary.get("id") or summary_header.get("id") or "") == display_id else None
        if display_comp is None:
            display_comp = ((display_event.get("competitions") or [{}])[0]) if display_event and display_event.get("competitions") else None

        away_id = ""
        home_id = ""
        for competitor in (display_comp or {}).get("competitors") or []:
            side = competitor.get("homeAway")
            if side == "away":
                away_id = str((competitor.get("team") or {}).get("id", ""))
            elif side == "home":
                home_id = str((competitor.get("team") or {}).get("id", ""))

        away_team_payload: dict[str, Any] = {}
        home_team_payload: dict[str, Any] = {}
        if away_id:
            try:
                away_team_payload = await self._get_json(
                    f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{away_id}"
                )
            except Exception as err:
                _LOGGER.debug("Unable to fetch away team metadata: %s", err)
        if home_id:
            try:
                home_team_payload = await self._get_json(
                    f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams/{home_id}"
                )
            except Exception as err:
                _LOGGER.debug("Unable to fetch home team metadata: %s", err)

        batter_id = ""
        pitcher_id = ""
        situation = summary.get("situation") or {}
        batter = situation.get("batter") or {}
        pitcher = situation.get("pitcher") or {}
        batter_id = str(batter.get("playerId") or batter.get("id") or (batter.get("athlete") or {}).get("id") or "")
        pitcher_id = str(pitcher.get("playerId") or pitcher.get("id") or (pitcher.get("athlete") or {}).get("id") or "")

        if not batter_id or not pitcher_id:
            plays = summary.get("plays") or []
            for play in reversed(plays):
                play_type = str(((play.get("type") or {}).get("text") or (play.get("type") or {}).get("type") or "")).lower()
                if play_type in {"start batter/pitcher", "start batter pitcher", "start-batterpitcher"}:
                    for participant in play.get("participants") or []:
                        part_type = str(participant.get("type", "")).lower()
                        if not batter_id and part_type == "batter":
                            batter_id = str((participant.get("athlete") or {}).get("id", ""))
                        elif not pitcher_id and part_type == "pitcher":
                            pitcher_id = str((participant.get("athlete") or {}).get("id", ""))
                    if batter_id and pitcher_id:
                        break

        status = ((display_comp or {}).get("status") or {})
        status_type = status.get("type") or {}
        state = str(status_type.get("state", "")).lower()
        status_name = str(status_type.get("name", "")).upper()
        status_detail = str(
            status_type.get("detail")
            or status_type.get("shortDetail")
            or status_type.get("statusPrimary")
            or status_type.get("description")
            or ""
        ).strip()
        is_delayed = status_name == "STATUS_DELAYED" or "delayed" in status_detail.lower()
        is_live = state in {"in", "live"} or status_name == "STATUS_IN_PROGRESS" or is_delayed

        mode = "live" if live_id and display_id == live_id else ("previous" if display_id == prev_id else "next")

        batter_season_stats = await self._get_public_batter_stats(batter_id) if batter_id else {}
        batter_season_stats = self._extract_current_season_batter_stats(batter_season_stats) if batter_season_stats else {}

        team_name = self.team_abbr
        if schedule.get("team") and isinstance(schedule["team"], dict):
            team_name = str(schedule["team"].get("displayName") or schedule["team"].get("name") or self.team_abbr)

        return MlbLiveScoreboardData(
            team_abbr=self.team_abbr,
            team_id=self.team_id,
            team_name=team_name,
            display_event_id=display_id,
            live_event_id=live_id,
            previous_event_id=prev_id,
            next_event_id=next_id,
            selected_competition=self._compact_competition(display_comp),
            inning_context=self._normalize_inning_context(summary, display_comp),
            recent_plays=self._normalize_recent_plays(summary, self._normalize_inning_context(summary, display_comp)),
            current_pitches=self._normalize_current_pitches(summary, self._normalize_inning_context(summary, display_comp)),
            away_team=self._normalize_team_payload(away_team_payload),
            home_team=self._normalize_team_payload(home_team_payload),
            current_batter=self._normalize_current_batter(summary, batter_id),
            current_pitcher=self._normalize_current_pitcher(summary, pitcher_id),
            batter_stats=self._normalize_batter_stats(summary, batter_id, batter_season_stats, is_live=is_live),
            pitcher_stats=self._normalize_pitcher_stats(summary, pitcher_id),
            situation=self._normalize_situation(summary),
            probable_pitchers=self._normalize_probable_pitchers(display_comp),
            due_up=self._normalize_due_up(summary),
            third_out_play=self._normalize_third_out_play(summary, self._normalize_inning_context(summary, display_comp)),
            on_deck=self._normalize_on_deck(summary, self._normalize_inning_context(summary, display_comp), batter_id),
            leaders=self._normalize_leaders(summary),
            mode=mode,
            status_text=status_detail,
            is_live=is_live,
            is_delayed=is_delayed,
        )
