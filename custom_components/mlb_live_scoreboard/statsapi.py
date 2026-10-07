"""MLB Stats API fallback for live games ESPN publishes no play-by-play for.

ESPN occasionally covers a game with score + status only: the summary's
``header.competitions[0].playByPlaySource`` reads ``"none"``, ``plays`` is
empty, there is no ``situation``, and every box-score cell is ``--``. Observed
2026-10-05 on ALDS Game 2, NYY @ TB (ESPN event 401907986), while MLB's own
feed for the same game (gamePk 849839) carried everything.

Everything here is a **pure function**. The coordinator fetches the payloads;
this module only answers "should we fall back", "which MLB game is this", and
translates MLB's ``feed/live`` INTO the ESPN summary shape the existing
``_normalize_*`` helpers already read (``plays``, ``situation``, ``boxscore``,
``rosters``), so none of them had to learn a second source. See
``docs/plan-statsapi-fallback.md``.

Identity is the hard part: every downstream field keys on ESPN athlete ids.
MLB players are matched by name to the ESPN ids the uncovered summary still
carries (both starting nines + listed pitchers); anyone else gets a synthetic
``mlb-<mlbamId>`` id with MLB's headshot. ESPN-backed features (the career
popup, season-stat fetches) must skip those — see :func:`is_mlb_id`.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

MLB_ID_PREFIX = "mlb-"

DATA_SOURCE_ESPN = "espn"
DATA_SOURCE_MLB = "mlb_statsapi"

STATSAPI_SCHEDULE_URL = (
    "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={start}&endDate={end}&teamId={team}"
)
STATSAPI_FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
# The same feed trimmed (via MLB's ``fields`` filter) to what
# :func:`abs_challenges` reads: ~270 bytes instead of ~750 KB, cheap enough to
# poll alongside ESPN for every live game.
STATSAPI_CHALLENGES_URL = STATSAPI_FEED_URL + (
    "?fields=gameData,teams,away,home,id,absChallenges,hasChallenges,usedSuccessful,usedFailed,remaining,"
    "liveData,plays,currentPlay,playEvents,reviewDetails,inProgress,reviewType,challengeTeamId"
)
# ``reviewDetails.reviewType`` of a ball-strike (ABS) challenge. Manager replay
# challenges carry other codes (MA, MV, ...) and a separate budget.
ABS_REVIEW_TYPE = "MJ"
MLB_HEADSHOT_URL = (
    "https://img.mlbstatic.com/mlb-photos/image/upload/w_213,q_auto:best/v1/people/{id}/headshot/67/current"
)

# ESPN team id -> MLB (statsapi) team id. Abbreviations can't be used: the two
# disagree (ESPN CWS/ARI vs MLB CWS/AZ, and ESPN's own CHW in scoreboards).
# The All-Star pseudo-teams (ESPN 31/32) are deliberately absent, so the
# fallback never applies to the All-Star Game.
ESPN_TO_MLB_TEAM_ID: dict[str, int] = {
    "1": 110,  # BAL
    "2": 111,  # BOS
    "3": 108,  # LAA
    "4": 145,  # CWS
    "5": 114,  # CLE
    "6": 116,  # DET
    "7": 118,  # KC
    "8": 158,  # MIL
    "9": 142,  # MIN
    "10": 147,  # NYY
    "11": 133,  # ATH
    "12": 136,  # SEA
    "13": 140,  # TEX
    "14": 141,  # TOR
    "15": 144,  # ATL
    "16": 112,  # CHC
    "17": 113,  # CIN
    "18": 117,  # HOU
    "19": 119,  # LAD
    "20": 120,  # WSH
    "21": 121,  # NYM
    "22": 143,  # PHI
    "23": 134,  # PIT
    "24": 138,  # STL
    "25": 135,  # SD
    "26": 137,  # SF
    "27": 115,  # COL
    "28": 146,  # MIA
    "29": 109,  # ARI
    "30": 139,  # TB
}
MLB_TO_ESPN_TEAM_ID: dict[int, str] = {v: k for k, v in ESPN_TO_MLB_TEAM_ID.items()}

# The box-score columns ESPN publishes for a covered game, in ESPN's order.
# `_stat_from_entry` reads cells by KEY, so these must be ESPN's key names.
BATTING_KEYS = [
    "hits-atBats",
    "atBats",
    "runs",
    "hits",
    "RBIs",
    "homeRuns",
    "walks",
    "strikeouts",
    "pitches",
    "avg",
    "onBasePct",
    "slugAvg",
]
BATTING_LABELS = ["H-AB", "AB", "R", "H", "RBI", "HR", "BB", "K", "#P", "AVG", "OBP", "SLG"]
PITCHING_KEYS = [
    "fullInnings.partInnings",
    "hits",
    "runs",
    "earnedRuns",
    "walks",
    "strikeouts",
    "homeRuns",
    "pitches-strikes",
    "ERA",
    "pitches",
]
PITCHING_LABELS = ["IP", "H", "R", "ER", "BB", "K", "HR", "PC-ST", "ERA", "PC"]

# MLB pitch-type code -> ESPN's (shorter) display text, measured off the same
# game in both feeds. Unlisted codes fall back to MLB's own description.
PITCH_TYPE_TEXT: dict[str, str] = {
    "FF": "Four-seam FB",
    "CU": "Curve",
    "FC": "Cutter",
    "ST": "Sweeper",
    "SI": "Sinker",
    "SV": "Slurve",
    "KC": "Knuckle Curve",
    "SL": "Slider",
    "CH": "Changeup",
    "FS": "Splitter",
}

# Non-pitch events with no place in the play-by-play.
_SKIPPED_ACTION_EVENT_TYPES = frozenset(
    {
        "mound_visit",
        "batter_timeout",
        "pitcher_step_off",
        "game_advisory",
        "no_pitch",
        "injury",
        "ejection",
        "umpire_substitution",
    }
)
_LINEUP_CHANGE_EVENT_TYPES = frozenset(
    {"pitching_substitution", "offensive_substitution", "defensive_substitution", "defensive_switch"}
)
# An at-bat whose third out (or out) was made on the bases leaves the batter's
# plate appearance unfinished: ESPN then emits no "End Batter/Pitcher" for it,
# which is what the due-up / up-bat-order helpers read as "he leads off next".
_RUNNER_OUT_RESULT_PREFIXES = ("caught_stealing", "pickoff", "other_out", "runner_double_play")

# MLB writes plays in the present tense ("grounds out"), ESPN in the past
# ("grounded out") — and the coordinator's at-bat / outcome detection matches
# ESPN's words. Order matters: longer phrases first.
_PAST_TENSE: tuple[tuple[str, str], ...] = (
    ("hits a grand slam", "hit a grand slam home run"),
    ("hits an inside-the-park home run", "hit an inside-the-park home run"),
    ("hits a ground-rule double", "doubled (ground-rule)"),
    ("called out on strikes", "struck out looking"),
    ("strikes out", "struck out"),
    ("flies out", "flied out"),
    ("flies into", "flied into"),
    ("grounds out", "grounded out"),
    ("grounds into", "grounded into"),
    ("lines out", "lined out"),
    ("lines into", "lined into"),
    ("pops out", "popped out"),
    ("pops into", "popped into"),
    ("fouls out", "fouled out"),
    ("reaches on", "reached on"),
    ("singles", "singled"),
    ("doubles", "doubled"),
    ("triples", "tripled"),
    ("homers", "homered"),
    ("walks", "walked"),
    ("steals", "stole"),
)
_PAST_TENSE_RE = [(re.compile(rf"\b{re.escape(a)}\b", re.IGNORECASE), b) for a, b in _PAST_TENSE]

_PITCHING_CHANGE_RE = re.compile(r"^Pitching Change:\s*(.+?)\s+replaces\s+(.+?)\.?$")
_PINCH_RE = re.compile(r"^Offensive Substitution:\s*Pinch-(hitter|runner)\s+(.+?)\s+replaces\s+(.+?)\.?$")
_DEFENSIVE_SUB_RE = re.compile(
    r"^Defensive Substitution:\s*(.+?)\s+replaces\s+.+?,\s*(?:batting \w+,\s*)?playing\s+(.+?)\.?$"
)
_REMAINS_DH_RE = re.compile(r"^(.+?)\s+remains in the game as the designated hitter\.?$")

_SUFFIX_TOKENS = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})

# ESPN's terse play wording ("Tucker singled to left.") from MLB's long form
# ("Kyle Tucker singles on a line drive to left fielder Mauricio Dubón.").
# See :func:`espn_play_text`.
_FIELDER_POSITIONS = {
    "pitcher": "pitcher",
    "catcher": "catcher",
    "first baseman": "first",
    "second baseman": "second",
    "third baseman": "third",
    "shortstop": "shortstop",
    "left fielder": "left",
    "center fielder": "center",
    "right fielder": "right",
}
_POS_RE = "|".join(sorted(_FIELDER_POSITIONS, key=len, reverse=True))
_NAME_TOKEN = r"\x00\d+\x00"
_BASE_WORDS = {"1st": "first", "2nd": "second", "3rd": "third"}
_SEASON_COUNT_RE = re.compile(r" \(\d+\)")
_BATTED_BALL_RE = re.compile(
    r" on an? (?:(?:sharp|soft|weak|hard|bunt) )*(?:ground ball|line drive|fly ball|pop up|bunt)", re.IGNORECASE
)
_ADVERB_RE = re.compile(r" (?:sharply|softly)(?= to|,|\.|$)")
_FOUL_TERRITORY_RE = re.compile(r"\b(?:flied|popped|lined) out(?P<rest>.*?) in foul territory")
_SIMPLE_OUT_CHAIN_RE = re.compile(
    rf"\b(?P<verb>grounded out|flied out|lined out|popped out|fouled out|bunted out)(?:,| to) "
    rf"(?P<pos>{_POS_RE}) {_NAME_TOKEN}(?: to (?:{_POS_RE}) {_NAME_TOKEN})*"
)
_FIELDER_RE = re.compile(rf"(?:(?<=to )|(?<=, ))(?P<pos>{_POS_RE}) {_NAME_TOKEN}")
_FIELD_DIRECTION_RE = re.compile(r"\bto (left|right|center|left center|right center) field\b")
_STEAL_RE = re.compile(r"\bstole (1st|2nd|3rd) base\b")
_BASE_RE = re.compile(r"\b(to|at|stealing) (1st|2nd|3rd)(?: base)?\b")
_BALL_GOT_AWAY_RE = re.compile(rf"^(?P<what>Wild pitch|Passed ball) by (?:pitcher|catcher) (?P<who>{_NAME_TOKEN})$")
_CHALLENGE_RE = re.compile(
    r"^(?P<who>.+?) challenged \([^)]*\), call on the field was (?P<result>\w+):\s*(?P<play>.+)$", re.DOTALL
)
_CHALLENGE_RESULTS = {"confirmed": "upheld", "upheld": "upheld", "overturned": "overturned", "stands": "stands"}
_INFIELD_SINGLE_RE = re.compile(rf"^(?P<who>{_NAME_TOKEN}) singled to (?P<pos>pitcher|catcher|first|second|third|shortstop)$")
_SCORED_RE = re.compile(rf"^(?P<who>{_NAME_TOKEN}) (?:scores|scored)$")


def is_mlb_id(athlete_id: Any) -> bool:
    """True for a synthetic id this module minted (no ESPN athlete behind it)."""
    return str(athlete_id or "").startswith(MLB_ID_PREFIX)


def _competition(summary: dict[str, Any]) -> dict[str, Any]:
    comps = (summary.get("header") or {}).get("competitions") or []
    return comps[0] if comps and isinstance(comps[0], dict) else {}


def espn_lacks_play_by_play(summary: dict[str, Any]) -> bool:
    """True when ESPN says it has no play-by-play for this game AND sent none.

    Both halves are required: an empty ``plays`` alone also happens in the
    first seconds of a covered game, and must not switch sources.
    """
    if not summary:
        return False
    if str(_competition(summary).get("playByPlaySource") or "").lower() != "none":
        return False
    return not (summary.get("plays") or [])


def should_use_statsapi(summary: dict[str, Any], already_using: bool) -> bool:
    """Whether this refresh should fill the live view from MLB.

    Starting needs ESPN's ``"none"`` flag (see :func:`espn_lacks_play_by_play`).
    Stopping needs ESPN's PLAYS, not just its flag: when coverage resumed on
    2026-10-05 ESPN flipped to ``"full"`` about a minute before its plays
    arrived, which would have blanked the card for that minute. So a game
    already on MLB stays there until ``plays`` is non-empty.
    """
    if espn_lacks_play_by_play(summary):
        return True
    return bool(already_using and summary and not (summary.get("plays") or []))


def wants_statsapi(summary: dict[str, Any], prefer_mlb: bool, already_using: bool) -> bool:
    """Whether this live refresh should try MLB first.

    MLB preferred: always (ESPN's summary, as-is, is then the fallback).
    ESPN preferred: only when ESPN has no play-by-play (:func:`should_use_statsapi`).
    """
    return True if prefer_mlb else should_use_statsapi(summary, already_using)


def schedule_window(espn_date: Any) -> tuple[str, str] | None:
    """``(startDate, endDate)`` for the MLB schedule query around an ESPN start.

    MLB files a game under its local calendar date, ESPN dates are UTC — a 7 pm
    Pacific start is the next UTC day — so ask for the UTC day and the one
    before it and let :func:`find_game_pk` pick by start time.
    """
    start_ts = _parse_ts(espn_date)
    if start_ts is None:
        return None
    day = datetime.fromtimestamp(start_ts, tz=UTC).date()
    prev = datetime.fromtimestamp(start_ts - 86400, tz=UTC).date()
    return prev.isoformat(), day.isoformat()


def find_game_pk(schedule: dict[str, Any], away_espn_id: str, home_espn_id: str, espn_date: Any) -> int | None:
    """Pick the MLB gamePk matching an ESPN competition, or None.

    Teams must match exactly (via :data:`ESPN_TO_MLB_TEAM_ID`); among those the
    start time closest to ESPN's wins, which also separates a doubleheader. A
    candidate more than 12 h off is not the same game.
    """
    away = ESPN_TO_MLB_TEAM_ID.get(str(away_espn_id))
    home = ESPN_TO_MLB_TEAM_ID.get(str(home_espn_id))
    target = _parse_ts(espn_date)
    if not away or not home or target is None:
        return None
    best: tuple[float, int] | None = None
    for day in (schedule or {}).get("dates") or []:
        for game in day.get("games") or []:
            teams = game.get("teams") or {}
            if ((teams.get("away") or {}).get("team") or {}).get("id") != away:
                continue
            if ((teams.get("home") or {}).get("team") or {}).get("id") != home:
                continue
            ts = _parse_ts(game.get("gameDate"))
            if ts is None:
                continue
            delta = abs(ts - target)
            if delta <= 12 * 3600 and (best is None or delta < best[0]):
                best = (delta, int(game.get("gamePk")))
    return best[1] if best else None


def _parse_ts(value: Any) -> float | None:
    if not value:
        return None
    raw = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return None


def normalize_name(name: Any) -> str:
    """Case-, accent- and suffix-insensitive form of a player name.

    MLB sends "Luis García Jr.", ESPN "Luis Garcia Jr." — both become
    ``luis garcia``.
    """
    text = unicodedata.normalize("NFKD", str(name or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub("[.'`\u2019]", "", text)
    tokens = [t for t in re.split(r"[\s\-]+", text) if t]
    while len(tokens) > 1 and tokens[-1] in _SUFFIX_TOKENS:
        tokens.pop()
    return " ".join(tokens)


def past_tense(text: str) -> str:
    """Rewrite MLB's present-tense play verbs into ESPN's past tense."""
    out = text
    for pattern, repl in _PAST_TENSE_RE:
        out = pattern.sub(repl, out)
    return out


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def espn_play_text(
    text: str, roster: _Roster, batter_mlb: Any = None, hit_distance: Any = None
) -> str:
    """Rewrite one MLB play description in ESPN's terse style.

    "Kyle Tucker singles on a line drive to left fielder Mauricio Dubón." ->
    "Tucker singled to left."; "Kyle Tucker steals (2) 2nd base." -> "Tucker
    stole second."; runner sentences fold into one clause list ("Vargas
    walked, Teel to second."). Anything not recognised keeps MLB's wording,
    past-tensed, with names shortened -- never worse than the input.
    """
    tokens: list[str] = []
    token_pids: list[int | None] = []

    def tokenize(match: re.Match[str]) -> str:
        tokens.append(roster.play_name_for(match.group(0)))
        token_pids.append(roster.pid_for(match.group(0)))
        return f"\x00{len(tokens) - 1}\x00"

    pattern = roster.name_pattern()
    out = text.strip()
    if pattern is not None:
        out = pattern.sub(tokenize, out)
    # "Braves challenged (pitch result), call on the field was confirmed: X
    # strikes out looking." -> ESPN leads with the play and appends the review.
    challenge = ""
    review = _CHALLENGE_RE.match(out)
    if review:
        result = _CHALLENGE_RESULTS.get(review.group("result").lower(), review.group("result").lower())
        who = review.group("who")
        # A player's (ABS) challenge is credited to his team, as ESPN does.
        token = re.fullmatch(_NAME_TOKEN, who)
        pid = token_pids[int(who[1:-1])] if token else None
        who = roster.team_name_of(pid) or roster.team_names_by_nickname.get(who, who)
        challenge = f" {who} challenged: call on the field was {result}."
        out = review.group("play")
    bunt = re.search(r"\bbunt\b", out) is not None
    out = past_tense(out)
    out = _SEASON_COUNT_RE.sub("", out)
    out = _BATTED_BALL_RE.sub("", out)
    out = _ADVERB_RE.sub("", out)
    out = _FOUL_TERRITORY_RE.sub(lambda m: f"fouled out{m.group('rest')}", out)
    out = out.replace("into a double play", "into double play").replace("into a triple play", "into triple play")
    out = _SIMPLE_OUT_CHAIN_RE.sub(lambda m: f"{m.group('verb')} to {_FIELDER_POSITIONS[m.group('pos')]}", out)
    out = _FIELDER_RE.sub(lambda m: _FIELDER_POSITIONS[m.group("pos")], out)
    out = _FIELD_DIRECTION_RE.sub(r"to \1", out)
    out = _STEAL_RE.sub(lambda m: f"stole {_BASE_WORDS[m.group(1)]}", out)
    out = _BASE_RE.sub(lambda m: f"{m.group(1)} {_BASE_WORDS[m.group(2)]}", out)

    sentences = [part.strip() for part in re.split(r"\.\s+|\.$", out) if part.strip()]
    if not sentences:
        return text
    main, runners = sentences[0], sentences[1:]
    batter_name = roster.play_name_for_id(batter_mlb) if batter_mlb else ""

    def name_of(token_text: str) -> str:
        return re.sub(_NAME_TOKEN, lambda m: tokens[int(m.group(0)[1:-1])], token_text)

    # The batter's own out at first in a double play is implied by the verb.
    if "double play" in main or "triple play" in main:
        runners = [r for r in runners if not (name_of(r) == f"{batter_name} out at first" and batter_name)]

    clauses: list[str] = []
    got_away = _BALL_GOT_AWAY_RE.match(main)
    if got_away and runners:
        # "Wild pitch by pitcher X. A to 3rd." -> "A to third on wild pitch by X."
        cause = f"on {got_away.group('what').lower()} by {got_away.group('who')}"
        clauses = [f"{r} {cause}" for r in runners]
    else:
        infield = _INFIELD_SINGLE_RE.match(main)
        if infield:
            kind = "bunt single" if bunt else "infield single"
            main = f"{infield.group('who')} reached on {kind} to {infield.group('pos')}"
        distance = _safe_int(hit_distance)
        if distance and "homered" in main:
            main = f"{main} ({distance} feet)"
        clauses = [main]
        for runner in runners:
            scored = _SCORED_RE.match(runner)
            runner = f"{scored.group('who')} scored" if scored else runner
            if scored and clauses[-1].endswith(" scored") and len(clauses) > 1:
                clauses[-1] = f"{clauses[-1]} and {runner}"
            else:
                clauses.append(runner)
    return name_of(", ".join(clauses) + "." + challenge)


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


class _Roster:
    """MLB player id -> ESPN-shaped athlete dict, plus team bookkeeping."""

    def __init__(
        self,
        espn_summary: dict[str, Any],
        feed: dict[str, Any],
        team_rosters: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        game_data = feed.get("gameData") or {}
        self.people: dict[int, dict[str, Any]] = {}
        for person in (game_data.get("players") or {}).values():
            if isinstance(person, dict) and person.get("id"):
                self.people[int(person["id"])] = person

        teams = game_data.get("teams") or {}
        self.mlb_team_id = {side: int((teams.get(side) or {}).get("id") or 0) for side in ("away", "home")}
        self.espn_team_id = {side: MLB_TO_ESPN_TEAM_ID.get(self.mlb_team_id[side], "") for side in ("away", "home")}
        self.team_names = {side: str((teams.get(side) or {}).get("name") or "") for side in ("away", "home")}
        self.team_names_by_nickname = {
            str((teams.get(side) or {}).get("teamName") or ""): self.team_names[side] for side in ("away", "home")
        }

        # ESPN athletes the summary still lists, per ESPN team id.
        espn_by_team: dict[str, list[dict[str, Any]]] = {}
        box = espn_summary.get("boxscore") or {}
        for team_block in box.get("players") or []:
            tid = str((team_block.get("team") or {}).get("id") or "")
            for stat_block in team_block.get("statistics") or []:
                for entry in stat_block.get("athletes") or []:
                    athlete = entry.get("athlete") or {}
                    if athlete.get("id"):
                        espn_by_team.setdefault(tid, []).append(athlete)
        for roster in espn_summary.get("rosters") or []:
            tid = str((roster.get("team") or {}).get("id") or "")
            for entry in roster.get("roster") or []:
                athlete = entry.get("athlete") or {}
                if athlete.get("id"):
                    espn_by_team.setdefault(tid, []).append(athlete)

        # ESPN's full team rosters (``flatten_espn_roster``) cover players the
        # summary doesn't list yet: everyone who hasn't appeared in the game.
        for tid, athletes in (team_rosters or {}).items():
            for athlete in athletes or []:
                if athlete.get("id"):
                    espn_by_team.setdefault(str(tid), []).append(athlete)

        # name -> athlete, keeping only names that resolve to ONE ESPN id; a
        # name shared by two different ids is no match at all.
        self._espn_by_name: dict[str, dict[str, dict[str, Any]]] = {}
        for tid, athletes in espn_by_team.items():
            by_name: dict[str, dict[str, Any]] = {}
            ambiguous: set[str] = set()
            for athlete in athletes:
                key = normalize_name(athlete.get("fullName") or athlete.get("displayName"))
                if not key:
                    continue
                seen = by_name.get(key)
                if seen is not None and str(seen.get("id")) != str(athlete.get("id")):
                    ambiguous.add(key)
                elif seen is None or len(athlete) > len(seen):
                    by_name[key] = athlete  # keep the richer copy (rosters carry lastName)
            for key in ambiguous:
                by_name.pop(key, None)
            self._espn_by_name[tid] = by_name

        self.side_of: dict[int, str] = {}
        for side in ("away", "home"):
            players = (((feed.get("liveData") or {}).get("boxscore") or {}).get("teams") or {}).get(side) or {}
            for key in players.get("players") or {}:
                pid = _safe_int(str(key).removeprefix("ID"))
                if pid:
                    self.side_of[pid] = side
        self._cache: dict[int, dict[str, Any]] = {}
        self._variants: dict[str, int] | None = None
        self._last_counts: dict[str, int] | None = None
        self._name_re: re.Pattern[str] | bool | None = None

    def athlete(self, mlb_id: Any) -> dict[str, Any]:
        pid = _safe_int(mlb_id)
        if not pid:
            return {}
        if pid in self._cache:
            return self._cache[pid]
        person = self.people.get(pid) or {}
        full = str(person.get("fullName") or "")
        side = self.side_of.get(pid, "")
        espn = self._espn_by_name.get(self.espn_team_id.get(side, ""), {}).get(normalize_name(full))
        if espn:
            result = dict(espn)
            result.setdefault("lastName", person.get("lastName") or "")
        else:
            last = str(person.get("lastName") or (full.split()[-1] if full else ""))
            first = str(person.get("useName") or person.get("firstName") or "")
            # MLB's initLastName is "J Schreiber" (no period); ESPN writes "J. Schreiber".
            short = f"{first[:1]}. {last}" if first and last else (last or full)
            result = {
                "id": f"{MLB_ID_PREFIX}{pid}",
                "displayName": full,
                "fullName": full,
                "shortName": short,
                "lastName": last,
                "headshot": {"href": MLB_HEADSHOT_URL.format(id=pid), "alt": full},
                "position": {"abbreviation": str((person.get("primaryPosition") or {}).get("abbreviation") or "")},
            }
        self._cache[pid] = result
        return result

    def last_name_for(self, full_name: str) -> str:
        """ESPN-style last name for a player named in MLB text, or the text's last word."""
        key = normalize_name(full_name)
        for pid, person in self.people.items():
            if normalize_name(person.get("fullName")) == key:
                return str(self.athlete(pid).get("lastName") or person.get("lastName") or "")
        return full_name.split()[-1] if full_name.split() else full_name

    def play_name_for_id(self, mlb_id: Any) -> str:
        """ESPN's name for a player in play text: last name, or "B. Montgomery"
        when another player in the game shares it."""
        athlete = self.athlete(mlb_id)
        person = self.people.get(_safe_int(mlb_id)) or {}
        last = str(athlete.get("lastName") or person.get("lastName") or "")
        # ESPN's athlete records often drop accents ("Jose Ramirez") while its
        # play text keeps them ("Ramírez"); take whichever source spells the
        # same name with its accents.
        for candidate in (str(person.get("lastName") or ""), str(athlete.get("lastName") or "")):
            plain = _strip_accents(candidate)
            if candidate != plain and _strip_accents(last).startswith(plain):
                last = candidate + last[len(plain) :]  # keeps a suffix: "Acuña" + " Jr."
                break
        if not last:
            return str(athlete.get("displayName") or person.get("fullName") or "")
        if self._last_name_counts().get(normalize_name(last), 0) > 1:
            short = str(athlete.get("shortName") or "")
            initial = short.split()[0] if short.split() and short.split()[0].endswith(".") else ""
            return f"{initial} {last}" if initial else (short or last)
        return last

    def play_name_for(self, name_in_text: str) -> str:
        pid = self.pid_for(name_in_text)
        return self.play_name_for_id(pid) if pid else name_in_text

    def pid_for(self, name_in_text: str) -> int | None:
        return self._pid_by_variant().get(name_in_text)

    def team_name_of(self, mlb_id: Any) -> str:
        """Full team name ("Atlanta Braves") of the side a player is on."""
        side = self.side_of.get(_safe_int(mlb_id), "")
        return self.team_names.get(side, "") if side else ""

    def name_pattern(self) -> re.Pattern[str] | None:
        """Regex matching any player's name as MLB writes it in play text."""
        if self._name_re is None:
            variants = sorted(self._pid_by_variant(), key=len, reverse=True)
            self._name_re = (
                re.compile("(?<![\\w.])(?:" + "|".join(re.escape(v) for v in variants) + ")(?!\\w)")
                if variants
                else False
            )
        return self._name_re or None

    def _pid_by_variant(self) -> dict[str, int]:
        if self._variants is None:
            self._variants = {}
            for pid, person in self.people.items():
                for key in ("fullName", "nameFirstLast", "firstLastName"):
                    name = str(person.get(key) or "").strip()
                    if name:
                        self._variants.setdefault(name, pid)
                        self._variants.setdefault(_strip_accents(name), pid)
        return self._variants

    def _last_name_counts(self) -> dict[str, int]:
        if self._last_counts is None:
            self._last_counts = {}
            for person in self.people.values():
                key = normalize_name(person.get("lastName"))
                if key:
                    self._last_counts[key] = self._last_counts.get(key, 0) + 1
        return self._last_counts

    def espn_id(self, mlb_id: Any) -> str:
        return str(self.athlete(mlb_id).get("id") or "")

    def display_name(self, mlb_id: Any) -> str:
        return str(self.athlete(mlb_id).get("displayName") or "")


def _safe_int(value: Any) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def abs_challenges(feed: dict[str, Any]) -> dict[str, Any]:
    """ABS (ball-strike) challenges per team from an MLB ``feed/live`` payload.

    Returns the ``AbsChallenges`` shape, or ``{}`` when MLB's feed carries no
    per-team challenge counts for the game. ``remaining`` is MLB's own count
    (a won challenge is retained). ``in_progress`` marks the team whose
    challenge of a pitch in the current at-bat is still under review.

    MLB's ``hasChallenges`` is NOT "this game uses ABS": it stays false until
    the first challenge is made (2026-10-07, LAD @ ATL read false with both
    sides at 2 remaining), so gating on it hid the dots until then. The
    presence of the per-team counts is the signal.
    """
    game_data = feed.get("gameData") or {}
    abs_data = game_data.get("absChallenges") or {}
    if not any(isinstance(abs_data.get(side), dict) and "remaining" in abs_data[side] for side in ("away", "home")):
        return {}
    teams = game_data.get("teams") or {}
    side_by_team = {str((teams.get(side) or {}).get("id") or ""): side for side in ("away", "home")}
    current = ((feed.get("liveData") or {}).get("plays") or {}).get("currentPlay") or {}
    reviews = [current.get("reviewDetails")] + [ev.get("reviewDetails") for ev in current.get("playEvents") or []]
    pending = {
        side_by_team.get(str(rd.get("challengeTeamId") or ""))
        for rd in reviews
        if isinstance(rd, dict) and rd.get("inProgress") and rd.get("reviewType") == ABS_REVIEW_TYPE
    }
    result: dict[str, Any] = {"has_challenges": True}
    for side in ("away", "home"):
        counts = abs_data.get(side) or {}
        result[side] = {
            "remaining": _safe_int(counts.get("remaining")),
            "used_successful": _safe_int(counts.get("usedSuccessful")),
            "used_failed": _safe_int(counts.get("usedFailed")),
            "in_progress": side in pending,
        }
    return result


def _bat_order(box_player: dict[str, Any]) -> int:
    return _safe_int(box_player.get("battingOrder")) // 100


def _pitch_entry(ev: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(espn_type_text, espn_type_slug, text_body)`` for one pitch."""
    details = ev.get("details") or {}
    code = str((details.get("call") or {}).get("code") or details.get("code") or "")
    count = ev.get("count") or {}
    balls, strikes = _safe_int(count.get("balls")), _safe_int(count.get("strikes"))
    desc = str(details.get("description") or "")
    if details.get("isInPlay") or code in ("X", "D", "E"):
        return "In Play", "in-play", "Ball In Play"
    if code in ("C",):
        return "Strike Looking", "strike-looking", f"Strike {strikes} Looking"
    if code in ("S", "W", "M", "Q", "T", "O"):
        return "Strike Swinging", "strike-swinging", f"Strike {strikes} Swinging"
    if code in ("F", "L", "R"):
        return "Foul Ball", "foul-ball", f"Strike {strikes} Foul" if strikes < 3 else "Foul Ball"
    if code == "H":
        return "Hit By Pitch", "hit-by-pitch", "Hit By Pitch"
    if code == "I":
        return "Automatic Ball - IBB", "automatic-ball---ibb", f"Automatic Ball - IBB {balls}"
    if code == "V":
        return "Automatic Ball", "automatic-ball", f"Automatic Ball {balls}"
    if code == "A":
        return "Automatic Strike", "automatic-strike", f"Automatic Strike {strikes}"
    if details.get("isBall") or code in ("B", "*B", "P"):
        return "Ball", "ball", f"Ball {balls}"
    if details.get("isStrike"):
        return "Strike", "strike", f"Strike {strikes}"
    return desc or "Pitch", _slug(desc) or "pitch", desc or "Pitch"


def _espn_lineup_change_text(desc: str, roster: _Roster) -> str:
    """MLB's substitution sentence in ESPN's terse form; unrecognised text is kept.

    MLB: "Pitching Change: A replaces B." / "Offensive Substitution:
    Pinch-hitter A replaces B." / "Defensive Substitution: A replaces B,
    batting 6th, playing center field." / "A remains in the game as the
    designated hitter."  ESPN: "A relieved B" / "A hit for B" / "A in center
    field." / "A as designated hitter."
    """
    last = roster.last_name_for
    if m := _PITCHING_CHANGE_RE.match(desc):
        return f"{last(m.group(1))} relieved {last(m.group(2))}"
    if m := _PINCH_RE.match(desc):
        verb = "hit" if m.group(1) == "hitter" else "ran"
        return f"{last(m.group(2))} {verb} for {last(m.group(3))}"
    if m := _DEFENSIVE_SUB_RE.match(desc):
        return f"{last(m.group(1))} in {m.group(2)}."
    if m := _REMAINS_DH_RE.match(desc):
        return f"{last(m.group(1))} as designated hitter."
    return desc


def _play(common: dict[str, Any], seq: int, away_score: int, home_score: int, **extra: Any) -> dict[str, Any]:
    """One ESPN-shaped play: the at-bat's shared fields + the score so far + ``extra``."""
    play = {
        "id": f"{common['atBatId']}{seq:03d}",
        "awayScore": away_score,
        "homeScore": home_score,
        "scoringPlay": False,
        "scoreValue": 0,
        **common,
    }
    play.update(extra)
    return play


def _build_plays(feed: dict[str, Any], roster: _Roster) -> list[dict[str, Any]]:
    """MLB ``allPlays`` -> ESPN-shaped ``plays[]`` (start / pitches / result / end)."""
    live = feed.get("liveData") or {}
    all_plays = (live.get("plays") or {}).get("allPlays") or []
    game_pk = str((feed.get("gameData") or {}).get("game", {}).get("pk") or "")
    out: list[dict[str, Any]] = []
    away_score = home_score = 0

    for ap in all_plays:
        about = ap.get("about") or {}
        matchup = ap.get("matchup") or {}
        result = ap.get("result") or {}
        inning = _safe_int(about.get("inning"))
        if not inning:
            continue
        top = bool(about.get("isTopInning"))
        half = "Top" if top else "Bottom"
        period = {"type": half, "number": inning, "displayValue": f"{inning} Inning"}
        batting_side, fielding_side = ("away", "home") if top else ("home", "away")
        batter_mlb = (matchup.get("batter") or {}).get("id")
        pitcher_mlb = (matchup.get("pitcher") or {}).get("id")
        batter_id, pitcher_id = roster.espn_id(batter_mlb), roster.espn_id(pitcher_mlb)
        participants = [
            {"athlete": {"id": pitcher_id}, "type": "pitcher"},
            {"athlete": {"id": batter_id}, "type": "batter"},
        ]
        idx = _safe_int(about.get("atBatIndex"))
        at_bat_id = f"{game_pk}{idx:04d}"
        box_batter = (
            (((live.get("boxscore") or {}).get("teams") or {}).get(batting_side) or {}).get("players") or {}
        ).get(f"ID{batter_mlb}") or {}
        bat_order = _bat_order(box_batter)

        common = {
            "period": period,
            "participants": participants,
            "atBatId": at_bat_id,
            "batOrder": bat_order,
            "team": {"id": roster.espn_team_id.get(fielding_side, "")},
        }

        events = ap.get("playEvents") or []
        start_outs = 0
        for ev in events:
            start_outs = _safe_int((ev.get("count") or {}).get("outs"))
            break
        out.append(
            _play(
                common,
                0,
                away_score,
                home_score,
                type={"id": "1", "text": "Start Batter/Pitcher", "type": "start-batterpitcher"},
                text=f"{roster.display_name(pitcher_mlb)} pitches to {roster.display_name(batter_mlb)}",
                wallclock=about.get("startTime"),
                outs=start_outs,
                pitchCount={"balls": 0, "strikes": 0},
            )
        )

        prev_count = {"balls": 0, "strikes": 0}
        seq = 1
        for ev in events:
            details = ev.get("details") or {}
            count = ev.get("count") or {}
            outs = _safe_int(count.get("outs"))
            if ev.get("isPitch"):
                type_text, type_slug, body = _pitch_entry(ev)
                pitch = {
                    "type": {"text": type_text, "type": type_slug},
                    "text": f"Pitch {_safe_int(ev.get('pitchNumber'))} : {body}",
                    "wallclock": ev.get("startTime"),
                    "outs": outs,
                    "atBatPitchNumber": _safe_int(ev.get("pitchNumber")),
                    "pitchCount": dict(prev_count),
                    "resultCount": {"balls": _safe_int(count.get("balls")), "strikes": _safe_int(count.get("strikes"))},
                }
                code = str((details.get("type") or {}).get("code") or "")
                if code:
                    pitch["pitchType"] = {
                        "text": PITCH_TYPE_TEXT.get(code)
                        or str((details.get("type") or {}).get("description") or code),
                        "abbreviation": code,
                    }
                pitch_data = ev.get("pitchData") or {}
                speed = pitch_data.get("startSpeed")
                if isinstance(speed, (int, float)) and speed > 0:
                    pitch["pitchVelocity"] = int(speed)
                coords = pitch_data.get("coordinates") or {}
                if isinstance(coords.get("x"), (int, float)) and isinstance(coords.get("y"), (int, float)):
                    pitch["pitchCoordinate"] = {"x": int(coords["x"]), "y": int(coords["y"])}
                out.append(_play(common, seq, away_score, home_score, **pitch))
                prev_count = dict(pitch["resultCount"])
                seq += 1
                continue
            event_type = str(details.get("eventType") or "")
            desc = str(details.get("description") or "").strip()
            # Only real "action" events with a type: step-offs and pickoff
            # ATTEMPTS arrive as other event kinds with no eventType at all.
            if ev.get("type") != "action" or not event_type or not desc:
                continue
            if event_type in _SKIPPED_ACTION_EVENT_TYPES:
                continue
            if event_type in _LINEUP_CHANGE_EVENT_TYPES:
                desc = _espn_lineup_change_text(desc, roster)
            if details.get("awayScore") is not None:
                away_score = _safe_int(details.get("awayScore"))
            if details.get("homeScore") is not None:
                home_score = _safe_int(details.get("homeScore"))
            alt = (
                "lineup-change"
                if event_type in _LINEUP_CHANGE_EVENT_TYPES
                else _slug(event_type or details.get("event"))
            )
            out.append(
                _play(
                    common,
                    seq,
                    away_score,
                    home_score,
                    type={"id": "57", "text": "Play Result", "type": "play-result"},
                    alternativeType={"text": str(details.get("event") or ""), "type": alt},
                    text=desc if event_type in _LINEUP_CHANGE_EVENT_TYPES else espn_play_text(desc, roster),
                    wallclock=ev.get("startTime"),
                    outs=outs,
                    scoringPlay=bool(details.get("isScoringPlay")),
                )
            )
            seq += 1

        if not about.get("isComplete"):
            # The at-bat in progress: start marker + pitches so far, nothing else.
            break

        before = away_score + home_score
        away_score = _safe_int(result.get("awayScore"))
        home_score = _safe_int(result.get("homeScore"))
        result_outs = _safe_int((ap.get("count") or {}).get("outs"))
        event_type = str(result.get("eventType") or "")
        batter_name = roster.play_name_for_id(batter_mlb)
        if event_type == "intent_walk" and batter_name:
            # MLB words it from the pitcher's side ("Newcomb intentionally
            # walks Adell."); ESPN, and the outcome matching, lead with the batter.
            text = f"{batter_name} intentionally walked."
        else:
            hit_distance = next(
                (
                    (ev.get("hitData") or {}).get("totalDistance")
                    for ev in reversed(events)
                    if (ev.get("hitData") or {}).get("totalDistance")
                ),
                None,
            )
            text = espn_play_text(
                str(result.get("description") or result.get("event") or ""), roster, batter_mlb, hit_distance
            )
        out.append(
            _play(
                common,
                seq,
                away_score,
                home_score,
                type={"id": "57", "text": "Play Result", "type": "play-result"},
                alternativeType={"text": str(result.get("event") or ""), "type": _slug(result.get("event"))},
                text=text,
                wallclock=about.get("endTime"),
                outs=result_outs,
                scoringPlay=bool(about.get("isScoringPlay")),
                scoreValue=max(0, away_score + home_score - before),
                pitchCount=dict(prev_count),
            )
        )
        if event_type.startswith(_RUNNER_OUT_RESULT_PREFIXES):
            # The out was made on the bases; the batter's plate appearance
            # carries over, exactly as ESPN marks it (no end marker).
            continue
        out.append(
            _play(
                common,
                seq + 1,
                away_score,
                home_score,
                type={"id": "99", "text": "End Batter/Pitcher", "type": "end-batterpitcher"},
                wallclock=about.get("endTime"),
                outs=result_outs,
                pitchCount=dict(prev_count),
            )
        )
    return out


def _build_situation(feed: dict[str, Any], roster: _Roster) -> dict[str, Any]:
    """MLB ``linescore`` -> ESPN ``situation`` (count, runners, batter/pitcher, dueUp)."""
    live = feed.get("liveData") or {}
    linescore = live.get("linescore") or {}
    offense = linescore.get("offense") or {}
    defense = linescore.get("defense") or {}
    state = str(linescore.get("inningState") or "").lower()
    between = state in ("middle", "end")

    situation: dict[str, Any] = {
        "balls": _safe_int(linescore.get("balls")),
        "strikes": _safe_int(linescore.get("strikes")),
        "outs": _safe_int(linescore.get("outs")),
    }

    if between:
        # MLB has already rolled `offense` to the side due up. ESPN keeps the
        # batter who made the third out in `situation.batter` through the
        # break, and the coordinator's stale-situation bridge depends on that
        # (it compares the next half's batter against the one captured here),
        # so name the last completed at-bat's batter — not the leadoff man.
        plays = (live.get("plays") or {}).get("allPlays") or []
        last = next((p for p in reversed(plays) if (p.get("about") or {}).get("isComplete")), None) or {}
        matchup = last.get("matchup") or {}
        batter, pitcher = (matchup.get("batter") or {}).get("id"), (matchup.get("pitcher") or {}).get("id")
        situation["outs"] = 3
        situation["balls"] = situation["strikes"] = 0
        due: list[dict[str, Any]] = []
        side = "home" if state == "middle" else "away"
        team_players = (((live.get("boxscore") or {}).get("teams") or {}).get(side) or {}).get("players") or {}
        for key in ("batter", "onDeck", "inHole"):
            pid = (offense.get(key) or {}).get("id")
            if not pid:
                continue
            due.append(
                {
                    "playerId": roster.espn_id(pid),
                    "batOrder": _bat_order(team_players.get(f"ID{pid}") or {}),
                }
            )
        if due:
            situation["dueUp"] = due
    else:
        batter, pitcher = (offense.get("batter") or {}).get("id"), (defense.get("pitcher") or {}).get("id")
        for base_key, espn_key in (("first", "onFirst"), ("second", "onSecond"), ("third", "onThird")):
            runner = (offense.get(base_key) or {}).get("id")
            if runner:
                situation[espn_key] = {"playerId": roster.espn_id(runner)}

    if batter:
        situation["batter"] = {"playerId": roster.espn_id(batter)}
    if pitcher:
        situation["pitcher"] = {"playerId": roster.espn_id(pitcher)}
    return situation


def _fmt(value: Any, default: str = "0") -> str:
    if value in (None, ""):
        return default
    return str(value)


def _build_boxscore(
    espn_summary: dict[str, Any], feed: dict[str, Any], roster: _Roster
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """MLB ``boxscore`` -> ESPN ``boxscore`` + ``rosters`` (ESPN key order)."""
    live = feed.get("liveData") or {}
    mlb_teams = (live.get("boxscore") or {}).get("teams") or {}
    espn_box = espn_summary.get("boxscore") or {}

    espn_team_dict: dict[str, dict[str, Any]] = {}
    for team_block in espn_box.get("players") or []:
        team = team_block.get("team") or {}
        if team.get("id"):
            espn_team_dict[str(team["id"])] = team
    for comp in _competition(espn_summary).get("competitors") or []:
        team = comp.get("team") or {}
        if team.get("id"):
            espn_team_dict.setdefault(str(team["id"]), team)

    teams = espn_box.get("teams") or [
        {"team": espn_team_dict.get(roster.espn_team_id[side], {"id": roster.espn_team_id[side]}), "homeAway": side}
        for side in ("away", "home")
    ]

    players_blocks: list[dict[str, Any]] = []
    rosters: list[dict[str, Any]] = []
    for side in ("away", "home"):
        mlb_team = mlb_teams.get(side) or {}
        box_players = mlb_team.get("players") or {}
        current_order = {_safe_int(p) for p in (mlb_team.get("battingOrder") or [])}
        team_dict = espn_team_dict.get(roster.espn_team_id[side], {"id": roster.espn_team_id[side]})

        batting_rows: list[dict[str, Any]] = []
        roster_rows: list[dict[str, Any]] = []
        for pid in mlb_team.get("batters") or []:
            bp = box_players.get(f"ID{pid}") or {}
            order = _bat_order(bp)
            if not order:
                continue  # a pitcher who never batted
            bat = (bp.get("stats") or {}).get("batting") or {}
            season = (bp.get("seasonStats") or {}).get("batting") or {}
            ab, h = _fmt(bat.get("atBats")), _fmt(bat.get("hits"))
            athlete = roster.athlete(pid)
            row = {
                "active": _safe_int(pid) in current_order,
                "starter": str(bp.get("battingOrder") or "").endswith("00"),
                "athlete": athlete,
                "position": {"abbreviation": str((bp.get("position") or {}).get("abbreviation") or "")},
                "batOrder": order,
                "stats": [
                    f"{h}-{ab}",
                    ab,
                    _fmt(bat.get("runs")),
                    h,
                    _fmt(bat.get("rbi")),
                    _fmt(bat.get("homeRuns")),
                    _fmt(bat.get("baseOnBalls")),
                    _fmt(bat.get("strikeOuts")),
                    _fmt(bat.get("pitchesThrown"), "0"),
                    _fmt(season.get("avg"), ""),
                    _fmt(season.get("obp"), ""),
                    _fmt(season.get("slg"), ""),
                ],
            }
            batting_rows.append(row)
            roster_rows.append({k: row[k] for k in ("active", "starter", "athlete", "position", "batOrder")})

        pitcher_ids = list(mlb_team.get("pitchers") or [])
        pitching_rows: list[dict[str, Any]] = []
        for index, pid in enumerate(pitcher_ids):
            bp = box_players.get(f"ID{pid}") or {}
            pit = (bp.get("stats") or {}).get("pitching") or {}
            season = (bp.get("seasonStats") or {}).get("pitching") or {}
            pitches, strikes = _fmt(pit.get("numberOfPitches", pit.get("pitchesThrown"))), _fmt(pit.get("strikes"))
            athlete = roster.athlete(pid)
            row = {
                "active": index == len(pitcher_ids) - 1,
                "starter": index == 0,
                "athlete": athlete,
                "position": {"abbreviation": "P"},
                "batOrder": 0,
                "stats": [
                    _fmt(pit.get("inningsPitched"), "0.0"),
                    _fmt(pit.get("hits")),
                    _fmt(pit.get("runs")),
                    _fmt(pit.get("earnedRuns")),
                    _fmt(pit.get("baseOnBalls")),
                    _fmt(pit.get("strikeOuts")),
                    _fmt(pit.get("homeRuns")),
                    f"{pitches}-{strikes}",
                    _fmt(season.get("era"), ""),
                    pitches,
                ],
            }
            pitching_rows.append(row)
            roster_rows.append({k: row[k] for k in ("active", "starter", "athlete", "position", "batOrder")})

        players_blocks.append(
            {
                "team": team_dict,
                "statistics": [
                    {"type": "batting", "keys": BATTING_KEYS, "labels": BATTING_LABELS, "athletes": batting_rows},
                    {"type": "pitching", "keys": PITCHING_KEYS, "labels": PITCHING_LABELS, "athletes": pitching_rows},
                ],
            }
        )
        rosters.append({"team": team_dict, "homeAway": side, "roster": roster_rows})

    boxscore = dict(espn_box)
    boxscore["teams"] = teams
    boxscore["players"] = players_blocks
    return boxscore, rosters


# MLB linescore.inningState -> (ESPN periodPrefix, detail word, shortDetail word).
_INNING_STATE_TO_ESPN: dict[str, tuple[str, str, str]] = {
    "top": ("Top", "Top", "Top"),
    "middle": ("Mid", "Middle", "Mid"),
    "bottom": ("Bottom", "Bottom", "Bot"),
    "end": ("End", "End", "End"),
}


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _competitor_with_mlb_score(competitor: dict[str, Any], linescore: dict[str, Any]) -> dict[str, Any]:
    """Copy of one ESPN competitor with MLB's runs / hits / errors / innings."""
    side = competitor.get("homeAway")
    totals = (linescore.get("teams") or {}).get(side) if side in ("away", "home") else None
    if not isinstance(totals, dict) or totals.get("runs") is None:
        return competitor
    out = dict(competitor, score=str(_safe_int(totals.get("runs"))))
    for key in ("hits", "errors"):
        if totals.get(key) is not None:
            out[key] = _safe_int(totals.get(key))
    innings = []
    for inning in linescore.get("innings") or []:
        half = (inning or {}).get(side) or {}
        if half.get("runs") is None:
            break  # this side hasn't batted in that inning yet
        runs = _safe_int(half.get("runs"))
        innings.append(
            {
                "value": runs,
                "displayValue": str(runs),
                "hits": _safe_int(half.get("hits")),
                "errors": _safe_int(half.get("errors")),
            }
        )
    if innings:
        out["linescores"] = innings
    return out


# MLB ``codedGameState`` values for a game that is over and stands ("O" =
# Game Over, the moment of the last out; "F" = Final). Postponed / suspended
# games also read abstractGameState "Final" but carry other codes.
_MLB_FINAL_CODES = frozenset({"F", "O"})


def _header_with_mlb_live_state(header: dict[str, Any], feed: dict[str, Any]) -> dict[str, Any]:
    """Copy of ESPN's header whose inning, score and end of game are MLB's.

    ESPN's status block lagged MLB by up to a minute all through the uncovered
    game, so the card sat on a three-out matchup under "Bottom 7th" while the
    plays and situation -- already MLB's -- had moved to the break. ESPN's
    score lags the same way: a run showed in the (MLB) play-by-play several
    seconds before the score above it moved. Taking the inning, runs, hits,
    errors and per-inning line from the same feed keeps them on one clock.

    The end of the game follows MLB too: its feed reads "Game Over" at the
    final out, while ESPN stayed "in progress" for several seconds. A game
    ending on the third out of a top half (home team ahead) is left by MLB
    at "Top 9th, 3 outs", which the card took for the break before a bottom
    half that is never played, flashing its Due Up panel until ESPN caught
    up. Otherwise the live/final state stays ESPN's.
    """
    linescore = (feed.get("liveData") or {}).get("linescore") or {}
    comps = header.get("competitions") or []
    if not linescore or not comps or not isinstance(comps[0], dict):
        return header
    comp = dict(comps[0])
    comp["competitors"] = [
        _competitor_with_mlb_score(c, linescore) if isinstance(c, dict) else c for c in comp.get("competitors") or []
    ]
    inning = _safe_int(linescore.get("currentInning"))
    names = _INNING_STATE_TO_ESPN.get(str(linescore.get("inningState") or "").lower())
    if inning and names:
        prefix, detail_word, short_word = names
        status = dict(comp.get("status") or {})
        status_type = dict(status.get("type") or {})
        ordinal = _ordinal(inning)
        status.update({"period": inning, "periodPrefix": prefix, "displayPeriod": ordinal})
        if str(status_type.get("state") or "").lower() == "in":
            status_type.update(
                {
                    "detail": f"{detail_word} {ordinal}",
                    "shortDetail": f"{short_word} {ordinal}",
                    "statusPrimary": f"{short_word} {ordinal}",
                }
            )
        status["type"] = status_type
        comp["status"] = status
    game_status = (feed.get("gameData") or {}).get("status") or {}
    if (
        str(game_status.get("abstractGameState") or "") == "Final"
        and str(game_status.get("codedGameState") or "") in _MLB_FINAL_CODES
    ):
        comp = _final_competition(comp, linescore, inning)
    return dict(header, competitions=[comp, *comps[1:]])


def _final_competition(comp: dict[str, Any], linescore: dict[str, Any], inning: int) -> dict[str, Any]:
    """Mark ``comp`` final in ESPN's shape ("Final", or "Final/10" in extras)."""
    label = f"Final/{inning}" if inning > 9 else "Final"
    status = dict(comp.get("status") or {})
    status["type"] = dict(
        status.get("type") or {},
        state="post",
        completed=True,
        name="STATUS_FINAL",
        description="Final",
        detail=label,
        shortDetail=label,
        statusPrimary=label,
    )
    out = dict(comp, status=status)
    teams = linescore.get("teams") or {}
    away_runs = (teams.get("away") or {}).get("runs")
    home_runs = (teams.get("home") or {}).get("runs")
    if away_runs is None or home_runs is None or away_runs == home_runs:
        return out
    winning_side = "away" if _safe_int(away_runs) > _safe_int(home_runs) else "home"
    competitors = []
    for competitor in out.get("competitors") or []:
        if isinstance(competitor, dict) and competitor.get("homeAway") in ("away", "home"):
            competitor = dict(competitor, winner=competitor["homeAway"] == winning_side)
        competitors.append(competitor)
    out["competitors"] = competitors
    return out


def flatten_espn_roster(payload: Any) -> list[dict[str, Any]]:
    """ESPN ``teams/{id}/roster`` -> athlete dicts trimmed to what matching uses.

    The endpoint groups athletes by position (``athletes[].items[]``); a flat
    ``athletes[]`` list is accepted too.
    """
    groups = (payload or {}).get("athletes") if isinstance(payload, dict) else None
    out: list[dict[str, Any]] = []
    for group in groups or []:
        items = group.get("items") if isinstance(group, dict) and "items" in group else [group]
        for athlete in items or []:
            if not isinstance(athlete, dict) or not athlete.get("id"):
                continue
            trimmed = {
                key: athlete[key]
                for key in ("id", "fullName", "displayName", "shortName", "firstName", "lastName", "jersey")
                if athlete.get(key)
            }
            href = (athlete.get("headshot") or {}).get("href")
            if href:
                trimmed["headshot"] = {"href": href, "alt": str(athlete.get("displayName") or "")}
            abbr = (athlete.get("position") or {}).get("abbreviation")
            if abbr:
                trimmed["position"] = {"abbreviation": abbr}
            out.append(trimmed)
    return out


def summary_from_statsapi(
    espn_summary: dict[str, Any],
    feed: dict[str, Any],
    team_rosters: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``espn_summary`` with MLB's live data filled in.

    Everything ESPN DID publish (game state, series, competitors and score,
    linescore runs, standings, leaders) is kept. The keys ESPN left empty --
    ``plays``, ``situation``, ``boxscore``, ``rosters`` -- are rebuilt from
    MLB's feed, in ESPN's shape, with ESPN athlete ids wherever a name match
    exists, and the status block's INNING follows MLB so it agrees with them.
    """
    roster = _Roster(espn_summary, feed, team_rosters)
    boxscore, rosters = _build_boxscore(espn_summary, feed, roster)
    summary = dict(espn_summary)
    if isinstance(espn_summary.get("header"), dict):
        summary["header"] = _header_with_mlb_live_state(espn_summary["header"], feed)
    summary["plays"] = _build_plays(feed, roster)
    summary["situation"] = _build_situation(feed, roster)
    summary["boxscore"] = boxscore
    summary["rosters"] = rosters
    return summary
