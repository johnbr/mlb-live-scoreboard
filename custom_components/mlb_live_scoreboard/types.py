"""TypedDict definitions for sensor attribute shapes.

These types describe the runtime-built dictionaries produced by the
``_normalize_*`` helpers in :mod:`coordinator` and exposed via
:attr:`MlbLiveScoreboardSensor.extra_state_attributes`. They are used purely
for documentation and static type checking — no runtime validation is
performed, and missing keys are tolerated by all consumers.

All TypedDicts use ``total=False`` because ESPN payloads are not always
complete and the normalizers may legitimately omit fields when source data
is missing.
"""

from __future__ import annotations

from typing import Any, TypedDict


class TeamMetadata(TypedDict, total=False):
    """Shape of normalized team metadata (``away_team`` / ``home_team``).

    Produced by :meth:`MlbLiveScoreboardCoordinator._normalize_team_payload`.
    """

    id: str
    abbreviation: str
    name: str
    short_name: str
    logo: str
    record_summary: str


class CurrentBatter(TypedDict, total=False):
    """Shape of ``current_batter`` and ``current_pitcher`` attributes."""

    id: str
    display_name: str
    short_name: str
    headshot: str


class CurrentPitcher(CurrentBatter, total=False):
    """Alias for :class:`CurrentBatter` — same shape, separate name for clarity."""


class BatterStats(TypedDict, total=False):
    """Shape of ``batter_stats`` attribute.

    ``hits_ab`` is a pre-formatted ``"H-AB"`` string (or empty when either
    component is missing). ``game_outcomes_display`` is the compact comma-
    separated outcome string consumed by the card.
    """

    avg: str
    ab: str
    h: str
    hr: str
    rbi: str
    game_hr: str
    game_rbi: str
    season_hr: str
    season_rbi: str
    hits_ab: str
    game_outcomes: list[str]
    game_outcomes_display: str


class PitcherStats(TypedDict, total=False):
    """Shape of ``pitcher_stats`` attribute."""

    era: str
    innings_pitched: str
    ip: str
    pitches_strikes: str
    strikeouts: str


class Situation(TypedDict, total=False):
    """Shape of ``situation`` attribute.

    Note: the ESPN raw ``competition.situation`` keys are camelCase
    (``onFirst``/``onSecond``/``onThird``); the *normalized* dict produced
    by :meth:`_normalize_situation` uses snake_case (``on_first``/etc.).
    """

    balls: int
    strikes: int
    outs: int
    on_first: bool
    on_second: bool
    on_third: bool
    first_last_name: str
    second_last_name: str
    third_last_name: str


class InningContext(TypedDict, total=False):
    """Shape of ``inning_context`` attribute."""

    period: int
    period_prefix: str
    display_period: str
    is_between_halves: bool
    has_due_up: bool


class RecentPlay(TypedDict, total=False):
    """Shape of an entry in ``recent_plays``.

    The sensor projects this to a smaller subset (``id``, ``text``, ``outs``,
    ``away_score``, ``home_score``, ``wallclock_ts``) before publishing to HA
    state, but the coordinator's internal list contains the fuller shape
    declared here.
    """

    id: str
    text: str
    outs: int | None
    away_score: int | None
    home_score: int | None
    wallclock_ts: float | None
    scoring_play: bool
    score_value: int
    play_type: str
    alternative_type: str


class ScoringPlay(TypedDict, total=False):
    """Shape of an entry in the ``scoring_plays`` attribute.

    Built from ESPN's top-level ``summary.scoringPlays`` (which is distinct
    from ``summary.plays``: it spans the full game and is prefiltered to
    scoring plays only, whereas :class:`RecentPlay` is bounded to the current
    half-inning). Powers the final-game expand panel's "Scoring Plays" block.

    ``period_type`` is ESPN's raw label, typically ``"Top"`` or ``"Bottom"``;
    ``period_number`` is the inning. ``team_id`` is the ESPN id of the team
    that scored on the play when ESPN supplies it (empty otherwise).
    """

    id: str
    text: str
    period_type: str
    period_number: int
    away_score: int | None
    home_score: int | None
    score_value: int
    team_id: str


class DueUpEntry(TypedDict, total=False):
    """Shape of an entry in ``due_up``."""

    id: str
    display_name: str
    short_name: str
    avg: str
    hr: str
    rbi: str


class OnDeck(TypedDict, total=False):
    """Shape of ``on_deck`` attribute."""

    id: str
    display_name: str
    short_name: str
    headshot: str
    avg: str
    hr: str
    rbi: str


class ProbablePitcher(TypedDict, total=False):
    """Shape of an entry in ``probable_pitchers`` (``away``/``home``).

    ``record`` is a pre-formatted ``"W-L"`` string when both wins and losses
    are present; otherwise empty. ``headshot`` is the player headshot URL
    when ESPN provides one in the probables payload.
    """

    id: str
    name: str
    short_name: str
    era: str
    wins: str
    losses: str
    record: str
    headshot: str


class ProbablePitchers(TypedDict, total=False):
    """Shape of ``probable_pitchers`` attribute."""

    away: ProbablePitcher
    home: ProbablePitcher


class WinProbability(TypedDict, total=False):
    """Shape of ``win_probability`` attribute.

    Values are percentages in the range 0..100 (one decimal place).
    Missing entirely when ESPN omits the series (typically pre-game).
    """

    home: float
    away: float


class StandingsEntry(TypedDict, total=False):
    """Shape of a single team row in ``division_standings.entries``.

    ``games_back`` is the pre-formatted ESPN display value (e.g. ``"-"``,
    ``"0.5"``, ``"3"``); empty when the source payload omits it.
    """

    team_id: str
    team_name: str
    team_short_name: str
    wins: str
    losses: str
    games_back: str


class Standings(TypedDict, total=False):
    """Shape of ``division_standings`` attribute (single division for the configured team)."""

    division_name: str
    entries: list[StandingsEntry]


class LeaderEntry(TypedDict, total=False):
    """Shape of an entry in ``leaders[side]``."""

    category: str
    value: str
    name: str
    id: str


class Leaders(TypedDict, total=False):
    """Shape of ``leaders`` attribute."""

    away: list[LeaderEntry]
    home: list[LeaderEntry]


class LineupHitter(TypedDict, total=False):
    """One hitter row in :class:`LineupTeam` ``hitters``.

    Game stats are read straight from the box score
    (``boxscore.players[].statistics[type=batting]``). ``avg`` is the
    player's *season* batting average as ESPN carries it in the box score
    (not a game value). ``position`` is the player's **in-game fielding**
    position (the entry-level position, which differs from the listed
    position for utility players). Substitutes share a ``bat_order`` slot
    with the starter they replaced; ``starter``/``active`` disambiguate.
    """

    id: str
    name: str
    short_name: str
    position: str
    bat_order: int
    starter: bool
    active: bool
    ab: str
    r: str
    h: str
    hr: str
    rbi: str
    bb: str
    k: str
    avg: str


class LineupPitcher(TypedDict, total=False):
    """One pitcher row in :class:`LineupTeam` ``pitchers``.

    Game stats from ``boxscore.players[].statistics[type=pitching]``.
    ``era`` is the *season* ERA the box score carries (not a game value).
    ``pc`` is the total pitch count. ``decision`` is the W/L/SV/HLD line
    from the entry's ``notes`` (e.g. ``"L, 2-5"``); empty for a
    no-decision. Order follows ESPN's appearance order (starter first).
    """

    id: str
    name: str
    short_name: str
    starter: bool
    active: bool
    decision: str
    ip: str
    h: str
    r: str
    er: str
    bb: str
    k: str
    pc: str
    era: str


class LineupTeam(TypedDict, total=False):
    """One side of the :class:`Lineups` attribute.

    ``is_batting`` is resolved like :meth:`_normalize_on_deck` — the
    box-score team whose batting block contains the current batter. It is
    ``False`` for both sides when there is no current batter (pre-game or a
    completed game).
    """

    team_id: str
    abbreviation: str
    name: str
    short_name: str
    logo: str
    is_batting: bool
    hitters: list[LineupHitter]
    pitchers: list[LineupPitcher]


class Lineups(TypedDict, total=False):
    """Shape of the ``lineups`` attribute.

    Built purely from the already-fetched ``summary["boxscore"]`` (zero
    extra ESPN calls). Empty (``{}``) when the box score has no usable
    players yet (typically pre-game — the card shows "Lineup not posted
    yet"). Season stats are *not* here; the card fetches those lazily via
    the ``mlb_live_scoreboard/team_season_stats`` WebSocket command.
    """

    away: LineupTeam
    home: LineupTeam


class PlayerCardBio(TypedDict, total=False):
    """Bio header of a :class:`PlayerCard` (from the ESPN ``/athletes/{id}``
    endpoint's ``athlete`` object)."""

    name: str
    team: str
    position: str
    bats_throws: str
    height: str
    weight: str
    age: str
    jersey: str
    headshot: str
    draft: str
    debut_year: str


class PlayerCareerSeason(TypedDict, total=False):
    """One season row of a :class:`PlayerCareerTable`.

    ``stats`` is index-parallel to the table's ``columns`` / ``keys``.
    """

    year: str
    team: str
    stats: list[str]


class PlayerCareerTable(TypedDict, total=False):
    """Normalized career stats table built from the primary ESPN stats
    category (``career-batting`` for hitters, ``pitching`` for pitchers).

    ``kind`` is ``"batting"`` or ``"pitching"``. ``columns`` are ESPN's own
    short labels (e.g. ``GP AB R H HR RBI AVG``); ``keys`` are the parallel
    machine names. ``totals`` is the career aggregate row (parallel to
    ``columns``), empty when ESPN omits it.
    """

    kind: str
    columns: list[str]
    keys: list[str]
    seasons: list[PlayerCareerSeason]
    totals: list[str]


class PlayerCard(TypedDict, total=False):
    """Shape returned by ``MlbLiveScoreboardCoordinator._get_player_card`` and
    its pure parser ``_parse_player_card``.

    ``career`` is empty when ESPN exposes no usable primary category.
    ``glossary`` maps a stat abbreviation to its long name (for tooltips).

    Note: ESPN's ``/stats`` endpoint returns categories by the player's
    *listed* position only, so a two-way player yields a single side here
    (e.g. pitching only). This is a documented limitation (see
    ``OPTION_B_HANDOFF.md`` and ``tests/fixtures/README.md``), not signalled
    in this shape.
    """

    id: str
    bio: PlayerCardBio
    career: PlayerCareerTable
    glossary: dict[str, str]


# Competition is the raw ESPN competition object passed through unchanged.
# We keep it as a loose mapping rather than enumerate ESPN's schema, since
# the card and coordinator only read a small subset and ESPN occasionally
# adds fields.
Competition = dict[str, Any]
