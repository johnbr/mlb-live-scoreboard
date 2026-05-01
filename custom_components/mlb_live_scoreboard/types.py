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
    """Shape of an entry in ``probable_pitchers`` (``away``/``home``)."""

    name: str
    short_name: str
    era: str


class ProbablePitchers(TypedDict, total=False):
    """Shape of ``probable_pitchers`` attribute."""

    away: ProbablePitcher
    home: ProbablePitcher


class LeaderEntry(TypedDict, total=False):
    """Shape of an entry in ``leaders[side]``."""

    category: str
    value: str
    name: str


class Leaders(TypedDict, total=False):
    """Shape of ``leaders`` attribute."""

    away: list[LeaderEntry]
    home: list[LeaderEntry]


# Competition is the raw ESPN competition object passed through unchanged.
# We keep it as a loose mapping rather than enumerate ESPN's schema, since
# the card and coordinator only read a small subset and ESPN occasionally
# adds fields.
Competition = dict[str, Any]
