"""Unit tests for pure helper functions in :mod:`coordinator`.

These exercise small, pure transformations of ESPN payload shapes. The
fixtures are hand-crafted minimal payloads — they reflect only the keys the
helpers actually read, not full ESPN responses.
"""

from __future__ import annotations

from datetime import UTC
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.mlb_live_scoreboard.const import (
    EVENT_GAME_ENDED,
    EVENT_GAME_LOST,
    EVENT_GAME_STARTED,
    EVENT_GAME_WON,
    EVENT_OPPONENT_SCORED,
    EVENT_TEAM_SCORED,
    OPT_ON_GAME_WON,
    OPT_ON_TEAM_SCORED,
)
from custom_components.mlb_live_scoreboard.coordinator import (
    MlbLiveScoreboardCoordinator as Coord,
)
from custom_components.mlb_live_scoreboard.coordinator import (
    MlbLiveScoreboardData,
    _parse_iso_ts,
)

# ---------------------------------------------------------------------------
# _parse_iso_ts
# ---------------------------------------------------------------------------


def test_parse_iso_ts_handles_z_suffix():
    ts = _parse_iso_ts("2024-04-01T18:30:00Z")
    assert ts is not None
    assert ts > 0


def test_parse_iso_ts_handles_offset_suffix():
    ts = _parse_iso_ts("2024-04-01T18:30:00+00:00")
    assert ts is not None


def test_parse_iso_ts_returns_none_for_empty():
    assert _parse_iso_ts(None) is None
    assert _parse_iso_ts("") is None
    assert _parse_iso_ts(0) is None


def test_parse_iso_ts_returns_none_for_garbage():
    assert _parse_iso_ts("not-a-date") is None
    assert _parse_iso_ts("2024-13-99T99:99:99Z") is None


# ---------------------------------------------------------------------------
# _format_batter_outcomes
# ---------------------------------------------------------------------------


def test_format_batter_outcomes_orders_and_counts():
    # 2 HRs, single, walk, strikeout
    assert (
        Coord._format_batter_outcomes(["HR", "HR", "1B", "BB", "K"])
        == "2HR, 1B, BB, K"
    )


def test_format_batter_outcomes_excludes_routine_outs():
    # GO/FO/PO/HBP/FC/GIDP are excluded entirely; HR remains
    assert Coord._format_batter_outcomes(["GO", "FO", "PO", "HR"]) == "HR"


def test_format_batter_outcomes_returns_empty_for_all_excluded():
    assert Coord._format_batter_outcomes(["GO", "FO", "HBP", "FC"]) == ""


def test_format_batter_outcomes_returns_empty_for_empty_input():
    assert Coord._format_batter_outcomes([]) == ""


def test_format_batter_outcomes_keeps_unknown_at_end():
    # Unknown abbreviations should still surface, after the ordered ones
    out = Coord._format_batter_outcomes(["HR", "XYZ"])
    assert out.startswith("HR")
    assert "XYZ" in out


# ---------------------------------------------------------------------------
# _normalize_team_payload
# ---------------------------------------------------------------------------


def test_normalize_team_payload_extracts_overall_record():
    payload = {
        "team": {
            "id": 19,
            "abbreviation": "LAD",
            "displayName": "Los Angeles Dodgers",
            "shortDisplayName": "Dodgers",
            "logo": "https://example.com/lad.png",
            "record": {
                "items": [
                    {"description": "Home Record", "summary": "5-2"},
                    {"description": "Overall Record", "summary": "12-5"},
                ]
            },
        }
    }
    out = Coord._normalize_team_payload(payload)
    assert out["abbreviation"] == "LAD"
    assert out["name"] == "Los Angeles Dodgers"
    assert out["short_name"] == "Dodgers"
    assert out["record_summary"] == "12-5"
    assert out["logo"] == "https://example.com/lad.png"
    assert out["id"] == "19"


def test_normalize_team_payload_falls_back_to_first_record():
    payload = {
        "team": {
            "abbreviation": "ARI",
            "name": "Diamondbacks",
            "record": {"items": [{"description": "Some Other", "summary": "3-3"}]},
        }
    }
    out = Coord._normalize_team_payload(payload)
    assert out["record_summary"] == "3-3"


def test_normalize_team_payload_handles_missing_team():
    out = Coord._normalize_team_payload({})
    assert out["abbreviation"] == ""
    assert out["record_summary"] == ""


def test_normalize_team_payload_uses_logos_array_when_logo_missing():
    payload = {
        "team": {
            "abbreviation": "BOS",
            "logos": [{"href": "https://cdn/bos.png"}],
        }
    }
    out = Coord._normalize_team_payload(payload)
    assert out["logo"] == "https://cdn/bos.png"


# ---------------------------------------------------------------------------
# _normalize_inning_context
# ---------------------------------------------------------------------------


def test_normalize_inning_context_top_of_inning():
    summary = {"situation": {"dueUp": []}}
    comp = {"status": {"periodPrefix": "Top", "period": 3, "displayPeriod": "3rd"}}
    ctx = Coord._normalize_inning_context(summary, comp)
    assert ctx["period"] == 3
    assert ctx["period_prefix"] == "Top"
    assert ctx["is_between_halves"] is False
    assert ctx["has_due_up"] is False


def test_normalize_inning_context_between_halves():
    summary = {"situation": {"dueUp": [{"id": "1"}]}}
    comp = {"status": {"periodPrefix": "Mid", "period": 5, "displayPeriod": "5th"}}
    ctx = Coord._normalize_inning_context(summary, comp)
    assert ctx["is_between_halves"] is True
    assert ctx["has_due_up"] is True


def test_normalize_inning_context_end_of_inning():
    ctx = Coord._normalize_inning_context(
        {}, {"status": {"periodPrefix": "End", "period": 7}}
    )
    assert ctx["is_between_halves"] is True


def test_normalize_inning_context_handles_missing_comp():
    ctx = Coord._normalize_inning_context({}, None)
    assert ctx["period"] == 0
    assert ctx["period_prefix"] == ""
    assert ctx["is_between_halves"] is False


# ---------------------------------------------------------------------------
# _normalize_recent_plays
# ---------------------------------------------------------------------------


def _make_play(*, period: int, half: str, text: str, play_type: str = "play result", outs=None, play_id="x"):
    return {
        "id": play_id,
        "period": {"number": period, "type": half},
        "type": {"text": play_type},
        "text": text,
        "outs": outs,
    }


def test_normalize_recent_plays_filters_to_target_half():
    plays = [
        _make_play(period=1, half="top", text="A grounded out.", outs=1, play_id="p1"),
        _make_play(period=1, half="bottom", text="B singled.", outs=0, play_id="p2"),
        _make_play(period=2, half="top", text="C struck out.", outs=1, play_id="p3"),
    ]
    summary = {"plays": plays}
    ctx = {"period": 1, "period_prefix": "Bottom 1st", "is_between_halves": False}
    out = Coord._normalize_recent_plays(summary, ctx)
    assert [p["id"] for p in out] == ["p2"]


def test_normalize_recent_plays_skips_blank_text():
    plays = [
        _make_play(period=1, half="top", text="", outs=0, play_id="blank"),
        _make_play(period=1, half="top", text="Hit.", outs=0, play_id="real"),
    ]
    out = Coord._normalize_recent_plays(
        {"plays": plays},
        {"period": 1, "period_prefix": "Top 1st", "is_between_halves": False},
    )
    assert [p["id"] for p in out] == ["real"]


def test_normalize_recent_plays_returns_empty_for_no_plays():
    assert Coord._normalize_recent_plays({}, {"period": 1, "period_prefix": "Top"}) == []
    assert Coord._normalize_recent_plays({"plays": []}, {"period": 1, "period_prefix": "Top"}) == []


def test_normalize_recent_plays_excludes_unsupported_types():
    plays = [
        _make_play(period=1, half="top", text="Pitch 1: ball.", play_type="pitch", play_id="pitch"),
        _make_play(period=1, half="top", text="Singled.", play_type="play result", play_id="result"),
    ]
    out = Coord._normalize_recent_plays(
        {"plays": plays},
        {"period": 1, "period_prefix": "Top 1st", "is_between_halves": False},
    )
    assert [p["id"] for p in out] == ["result"]


def test_normalize_third_out_play_returns_latest_third_out():
    plays = [
        _make_play(period=1, half="top", text="One out.", outs=1, play_id="o1"),
        _make_play(period=1, half="top", text="Two outs.", outs=2, play_id="o2"),
        _make_play(period=1, half="top", text="Inning over.", outs=3, play_id="o3"),
    ]
    out = Coord._normalize_third_out_play(
        {"plays": plays},
        {"period": 1, "period_prefix": "Top 1st", "is_between_halves": False},
    )
    assert out.get("id") == "o3"


def test_normalize_third_out_play_returns_empty_when_no_third_out():
    plays = [
        _make_play(period=1, half="top", text="Single.", outs=0, play_id="o1"),
    ]
    assert Coord._normalize_third_out_play(
        {"plays": plays},
        {"period": 1, "period_prefix": "Top 1st", "is_between_halves": False},
    ) == {}


# ---------------------------------------------------------------------------
# _extract_batter_game_outcomes
# ---------------------------------------------------------------------------


def test_extract_batter_game_outcomes_matches_by_last_name():
    summary = {
        "rosters": [
            {
                "roster": [
                    {"athlete": {"id": "42", "displayName": "Mookie Betts", "lastName": "Betts"}}
                ]
            }
        ],
        "plays": [
            {"type": {"text": "play result"}, "text": "Betts singled to right."},
            {"type": {"text": "play result"}, "text": "Betts homered to left."},
            {"type": {"text": "play result"}, "text": "Smith walked."},
        ],
    }
    out = Coord._extract_batter_game_outcomes(summary, "42")
    assert out == ["1B", "HR"]


def test_extract_batter_game_outcomes_returns_empty_for_unknown_id():
    assert Coord._extract_batter_game_outcomes({"plays": []}, "") == []
    assert Coord._extract_batter_game_outcomes({"plays": []}, "999") == []


# ---------------------------------------------------------------------------
# _select_event — live > prev > next priority logic
# ---------------------------------------------------------------------------


def _ev(eid: str, *, date: str | None = None, state: str = "pre", name: str = "STATUS_SCHEDULED"):
    return {
        "id": eid,
        "date": date,
        "competitions": [
            {"status": {"type": {"state": state, "name": name}}}
        ],
    }


def test_select_event_picks_live_when_in_progress():
    import time as _time
    now = _time.time()
    from datetime import datetime, timezone

    past = datetime.fromtimestamp(now - 3600, tz=UTC).isoformat().replace("+00:00", "Z")
    future = datetime.fromtimestamp(now + 3600, tz=UTC).isoformat().replace("+00:00", "Z")

    events = [
        _ev("A", date=past, state="post", name="STATUS_FINAL"),
        _ev("B", date=past, state="in", name="STATUS_IN_PROGRESS"),
        _ev("C", date=future, state="pre", name="STATUS_SCHEDULED"),
    ]
    _prev_id, next_id, live_id, display_id, _display = Coord._select_event(None, events)
    assert live_id == "B"
    assert display_id == "B"
    assert next_id == "C"


def test_select_event_picks_next_when_no_live_no_prev():
    import time as _time
    from datetime import datetime, timezone
    future = datetime.fromtimestamp(_time.time() + 3600, tz=UTC).isoformat().replace("+00:00", "Z")
    events = [_ev("A", date=future)]
    _prev, next_id, live_id, display_id, _disp = Coord._select_event(None, events)
    assert live_id == ""
    assert next_id == "A"
    assert display_id == "A"


def test_select_event_handles_empty_list():
    prev_id, next_id, live_id, display_id, display = Coord._select_event(None, [])
    assert (prev_id, next_id, live_id, display_id) == ("", "", "", "")
    assert display is None


# ---------------------------------------------------------------------------
# _detect_game_events
# ---------------------------------------------------------------------------


def _make_data(
    *,
    my_score: int,
    opp_score: int,
    is_live: bool = True,
    is_delayed: bool = False,
    state: str = "in",
    completed: bool = False,
    event_id: str = "G1",
    my_side: str = "home",
    my_team_id: str = "19",
    opp_team_id: str = "26",
    opp_abbr: str = "SF",
    opp_name: str = "San Francisco Giants",
    recent_plays: list | None = None,
) -> MlbLiveScoreboardData:
    """Build a minimal MlbLiveScoreboardData for detector tests."""
    opp_side = "away" if my_side == "home" else "home"
    competitors = [
        {
            "homeAway": my_side,
            "score": my_score,
            "team": {"id": my_team_id, "abbreviation": "LAD", "displayName": "Los Angeles Dodgers"},
        },
        {
            "homeAway": opp_side,
            "score": opp_score,
            "team": {"id": opp_team_id, "abbreviation": opp_abbr, "displayName": opp_name},
        },
    ]
    comp = {
        "id": event_id,
        "status": {"type": {"state": state, "completed": completed}},
        "competitors": competitors,
    }
    return MlbLiveScoreboardData(
        team_abbr="LAD",
        team_id=int(my_team_id),
        team_name="Los Angeles Dodgers",
        display_event_id=event_id,
        live_event_id=event_id if is_live else "",
        previous_event_id="",
        next_event_id="",
        selected_competition=comp,
        inning_context={"period": 5, "period_prefix": "Top 5th"},
        recent_plays=recent_plays or [],
        current_pitches=[],
        away_team={},
        home_team={},
        current_batter={},
        current_pitcher={},
        batter_stats={},
        pitcher_stats={},
        situation={},
        probable_pitchers={"away": {}, "home": {}},
        due_up=[],
        third_out_play={},
        on_deck={},
        leaders={},
        mode="live" if is_live else "previous",
        status_text="Top 5th",
        is_live=is_live,
        is_delayed=is_delayed,
    )


def test_detect_returns_empty_on_first_refresh():
    curr = _make_data(my_score=0, opp_score=0)
    assert Coord._detect_game_events(None, curr, 19) == []


def test_detect_team_scored():
    prev = _make_data(my_score=0, opp_score=0)
    curr = _make_data(my_score=2, opp_score=0)
    out = Coord._detect_game_events(prev, curr, 19)
    names = [n for n, _ in out]
    assert names == [EVENT_TEAM_SCORED]
    payload = out[0][1]
    assert payload["team_abbr"] == "LAD"
    assert payload["team_score"] == 2
    assert payload["score_delta"] == 2
    assert payload["is_home"] is True
    assert payload["opponent_abbr"] == "SF"


def test_detect_opponent_scored():
    prev = _make_data(my_score=1, opp_score=0)
    curr = _make_data(my_score=1, opp_score=1)
    out = Coord._detect_game_events(prev, curr, 19)
    names = [n for n, _ in out]
    assert names == [EVENT_OPPONENT_SCORED]
    assert out[0][1]["score_delta"] == 1


def test_detect_both_sides_scored_simultaneously():
    # Rare but possible if two polls were missed
    prev = _make_data(my_score=0, opp_score=0)
    curr = _make_data(my_score=1, opp_score=1)
    names = [n for n, _ in Coord._detect_game_events(prev, curr, 19)]
    assert EVENT_TEAM_SCORED in names
    assert EVENT_OPPONENT_SCORED in names


def test_detect_no_events_when_scores_unchanged():
    prev = _make_data(my_score=3, opp_score=2)
    curr = _make_data(my_score=3, opp_score=2)
    assert Coord._detect_game_events(prev, curr, 19) == []


def test_detect_no_score_events_while_delayed():
    prev = _make_data(my_score=0, opp_score=0, is_delayed=True)
    curr = _make_data(my_score=2, opp_score=0, is_delayed=True)
    assert Coord._detect_game_events(prev, curr, 19) == []


def test_detect_skips_across_event_id_boundary():
    # New game — don't compare scores from yesterday's game
    prev = _make_data(my_score=7, opp_score=2, event_id="G1")
    curr = _make_data(my_score=0, opp_score=1, event_id="G2")
    assert Coord._detect_game_events(prev, curr, 19) == []


def test_detect_game_started():
    prev = _make_data(my_score=0, opp_score=0, is_live=False, state="pre")
    curr = _make_data(my_score=0, opp_score=0, is_live=True, state="in")
    names = [n for n, _ in Coord._detect_game_events(prev, curr, 19)]
    assert EVENT_GAME_STARTED in names


def test_detect_game_won():
    prev = _make_data(my_score=4, opp_score=2, state="in", completed=False)
    curr = _make_data(
        my_score=4, opp_score=2, is_live=False, state="post", completed=True
    )
    names = [n for n, _ in Coord._detect_game_events(prev, curr, 19)]
    assert EVENT_GAME_ENDED in names
    assert EVENT_GAME_WON in names
    assert EVENT_GAME_LOST not in names


def test_detect_game_lost():
    prev = _make_data(my_score=2, opp_score=4, state="in", completed=False)
    curr = _make_data(
        my_score=2, opp_score=4, is_live=False, state="post", completed=True
    )
    names = [n for n, _ in Coord._detect_game_events(prev, curr, 19)]
    assert EVENT_GAME_ENDED in names
    assert EVENT_GAME_LOST in names
    assert EVENT_GAME_WON not in names


def test_detect_tie_fires_only_game_ended():
    prev = _make_data(my_score=3, opp_score=3, state="in", completed=False)
    curr = _make_data(
        my_score=3, opp_score=3, is_live=False, state="post", completed=True
    )
    names = [n for n, _ in Coord._detect_game_events(prev, curr, 19)]
    assert names == [EVENT_GAME_ENDED]


def test_detect_no_repeat_after_already_final():
    final = _make_data(
        my_score=4, opp_score=2, is_live=False, state="post", completed=True
    )
    # Same final state again — nothing should fire
    assert Coord._detect_game_events(final, final, 19) == []


def test_detect_returns_empty_when_team_not_in_competition():
    prev = _make_data(my_score=0, opp_score=0, my_team_id="99")
    curr = _make_data(my_score=2, opp_score=0, my_team_id="99")
    # Looking for team_id 19, neither competitor matches
    assert Coord._detect_game_events(prev, curr, 19) == []


def test_detect_includes_scoring_play_text():
    prev = _make_data(my_score=0, opp_score=0)
    curr = _make_data(
        my_score=1,
        opp_score=0,
        recent_plays=[
            {"text": "Routine groundout.", "scoring_play": False},
            {"text": "Betts homered to left.", "scoring_play": True},
        ],
    )
    out = Coord._detect_game_events(prev, curr, 19)
    assert out[0][1]["scoring_play_text"] == "Betts homered to left."


def test_detect_score_delta_handles_string_scores():
    # ESPN sometimes returns scores as strings
    prev = _make_data(my_score=0, opp_score=0)
    # Manually substitute string scores
    curr = _make_data(my_score=0, opp_score=0)
    curr.selected_competition["competitors"][0]["score"] = "3"
    out = Coord._detect_game_events(prev, curr, 19)
    assert out and out[0][1]["score_delta"] == 3


# ---------------------------------------------------------------------------
# _dispatch_game_events — verifies bus.async_fire and configured-action wiring
# ---------------------------------------------------------------------------


def _make_coord_for_dispatch(options: dict | None = None):
    """Build a minimally-wired coordinator-like object for dispatch tests
    without exercising __init__ (which calls into HA APIs).
    """
    fake_bus = SimpleNamespace(async_fire=MagicMock())
    created_tasks: list = []
    fake_hass = SimpleNamespace(
        bus=fake_bus,
        async_create_task=lambda coro: created_tasks.append(coro) or coro.close(),
    )
    fake_entry = SimpleNamespace(options=options or {})
    coord = Coord.__new__(Coord)
    coord.hass = fake_hass
    coord.entry = fake_entry
    return coord, fake_bus, created_tasks


def test_dispatch_fires_event_on_bus_without_options():
    coord, bus, tasks = _make_coord_for_dispatch()
    payload = {"team_abbr": "LAD", "opponent_abbr": "SF", "team_score": 1, "opponent_score": 0}
    coord._dispatch_game_events([(EVENT_TEAM_SCORED, payload)])

    bus.async_fire.assert_called_once_with(EVENT_TEAM_SCORED, payload)
    # No configured action, so no task should have been scheduled
    assert tasks == []


def test_dispatch_runs_configured_action_when_present():
    options = {OPT_ON_TEAM_SCORED: [{"service": "light.turn_on"}]}
    coord, bus, tasks = _make_coord_for_dispatch(options)
    payload = {"team_abbr": "LAD", "team_score": 1}
    coord._dispatch_game_events([(EVENT_TEAM_SCORED, payload)])

    bus.async_fire.assert_called_once()
    # Action sequence configured — coordinator should schedule an action task
    assert len(tasks) == 1


def test_dispatch_skips_action_for_unmatched_event():
    options = {OPT_ON_GAME_WON: [{"service": "light.turn_on"}]}
    coord, bus, tasks = _make_coord_for_dispatch(options)
    coord._dispatch_game_events([(EVENT_TEAM_SCORED, {"team_abbr": "LAD"})])

    bus.async_fire.assert_called_once()
    # Configured for game_won, not team_scored → no task scheduled
    assert tasks == []


def test_dispatch_handles_multiple_events():
    coord, bus, _tasks = _make_coord_for_dispatch()
    coord._dispatch_game_events([
        (EVENT_TEAM_SCORED, {"team_abbr": "LAD"}),
        (EVENT_GAME_WON, {"team_abbr": "LAD"}),
    ])
    assert bus.async_fire.call_count == 2


