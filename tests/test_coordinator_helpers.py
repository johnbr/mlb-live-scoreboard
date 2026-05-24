"""Unit tests for pure helper functions in :mod:`coordinator`.

These exercise small, pure transformations of ESPN payload shapes. The
fixtures are hand-crafted minimal payloads — they reflect only the keys the
helpers actually read, not full ESPN responses.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    # 2 HRs, walk, strikeout. The single (1B) is intentionally suppressed —
    # it's already implicit in the H-AB count and would clutter the line.
    assert Coord._format_batter_outcomes(["HR", "HR", "1B", "BB", "K"]) == "2HR, BB, K"


def test_format_batter_outcomes_excludes_singles():
    # A pure-singles game collapses to empty (the H-AB count carries the info)
    assert Coord._format_batter_outcomes(["1B", "1B"]) == ""


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
    ctx = Coord._normalize_inning_context({}, {"status": {"periodPrefix": "End", "period": 7}})
    assert ctx["is_between_halves"] is True


def test_normalize_inning_context_handles_missing_comp():
    ctx = Coord._normalize_inning_context({}, None)
    assert ctx["period"] == 0
    assert ctx["period_prefix"] == ""
    assert ctx["is_between_halves"] is False


def test_normalize_inning_context_promotes_when_plays_ahead_of_status():
    # ESPN status pins us to Top 2, but plays[] has already moved to Bot 3.
    # The stale-status freeze observed in prod: status lags, plays don't.
    summary = {
        "situation": {"dueUp": []},
        "plays": [
            {"period": {"number": 2, "type": "Top"}, "text": "Single."},
            {"period": {"number": 2, "type": "Bottom"}, "text": "Walk."},
            {"period": {"number": 3, "type": "Top"}, "text": "Strikeout."},
            {"period": {"number": 3, "type": "Bottom"}, "text": "Wild pitch."},
        ],
    }
    comp = {"status": {"periodPrefix": "Top", "period": 2}}
    ctx = Coord._normalize_inning_context(summary, comp)
    assert ctx["period"] == 3
    assert ctx["period_prefix"] == "Bottom 3"
    assert ctx["is_between_halves"] is False


def test_normalize_inning_context_keeps_status_when_plays_match():
    # Plays are at the same period as status -> no override.
    summary = {
        "plays": [{"period": {"number": 4, "type": "Top"}, "text": "Single."}],
    }
    comp = {"status": {"periodPrefix": "Top 4th", "period": 4}}
    ctx = Coord._normalize_inning_context(summary, comp)
    assert ctx["period"] == 4
    assert ctx["period_prefix"] == "Top 4th"


def test_normalize_inning_context_ignores_plays_when_half_missing():
    # If we can't derive the half from the play, don't override.
    summary = {"plays": [{"period": {"number": 9, "type": ""}, "text": "X."}]}
    comp = {"status": {"periodPrefix": "Top 1st", "period": 1}}
    ctx = Coord._normalize_inning_context(summary, comp)
    assert ctx["period"] == 1
    assert ctx["period_prefix"] == "Top 1st"


# ---------------------------------------------------------------------------
# _resolve_status_info — delay/suspend detection
# ---------------------------------------------------------------------------


def _status_comp(name="", detail="", state="in"):
    return {"status": {"type": {"name": name, "state": state, "detail": detail}}}


def test_resolve_status_info_flags_status_delayed():
    detail, is_live, is_delayed = Coord._resolve_status_info(
        _status_comp(name="STATUS_DELAYED", detail="Delayed")
    )
    assert is_delayed is True
    assert is_live is True
    assert detail == "Delayed"


def test_resolve_status_info_flags_rain_delay_name():
    # ESPN sometimes uses a dedicated STATUS_RAIN_DELAY name without
    # the string "delayed" anywhere in the detail.
    _, is_live, is_delayed = Coord._resolve_status_info(
        _status_comp(name="STATUS_RAIN_DELAY", detail="Rain Delay")
    )
    assert is_delayed is True
    assert is_live is True


def test_resolve_status_info_flags_rain_delay_detail_only():
    # The previous "delayed" substring check missed this case: detail is
    # "Rain Delay" (no trailing -ed), name is the generic in-progress one.
    _, is_live, is_delayed = Coord._resolve_status_info(
        _status_comp(name="STATUS_IN_PROGRESS", detail="Rain Delay")
    )
    assert is_delayed is True
    assert is_live is True


def test_resolve_status_info_flags_suspended():
    _, is_live, is_delayed = Coord._resolve_status_info(
        _status_comp(name="STATUS_SUSPENDED", detail="Suspended")
    )
    assert is_delayed is True
    assert is_live is True


def test_resolve_status_info_does_not_flag_normal_inning():
    _, is_live, is_delayed = Coord._resolve_status_info(
        _status_comp(name="STATUS_IN_PROGRESS", detail="Top 4th", state="in")
    )
    assert is_delayed is False
    assert is_live is True


# ---------------------------------------------------------------------------
# _normalize_scoring_plays
# ---------------------------------------------------------------------------


def _scoring_entry(*, play_id, half, inning, text, away=0, home=0, team_id="1"):
    return {
        "id": play_id,
        "text": text,
        "period": {"type": half, "number": inning},
        "awayScore": away,
        "homeScore": home,
        "scoreValue": 1,
        "team": {"id": team_id},
    }


def test_normalize_scoring_plays_keeps_distinct_plays():
    summary = {
        "scoringPlays": [
            _scoring_entry(play_id="p1", half="Bottom", inning=2, text="Frelick groundout, Bauers scored.", away=0, home=1, team_id="158"),
            _scoring_entry(play_id="p2", half="Top", inning=4, text="T. Hernández scored on wild pitch.", away=1, home=1, team_id="19"),
            _scoring_entry(play_id="p3", half="Top", inning=5, text="Pages homered.", away=2, home=1, team_id="19"),
        ]
    }
    out = Coord._normalize_scoring_plays(summary)
    assert len(out) == 3
    assert [p["id"] for p in out] == ["p1", "p2", "p3"]


def test_normalize_scoring_plays_deduplicates_repeated_id():
    summary = {
        "scoringPlays": [
            _scoring_entry(play_id="p1", half="Top", inning=4, text="Wild pitch, runner scored.", away=1, home=0),
            _scoring_entry(play_id="p1", half="Top", inning=4, text="Wild pitch, runner scored.", away=1, home=0),
        ]
    }
    out = Coord._normalize_scoring_plays(summary)
    assert len(out) == 1
    assert out[0]["id"] == "p1"


def test_normalize_scoring_plays_deduplicates_by_content_when_ids_differ():
    # ESPN observed emitting two scoringPlays entries with identical text
    # in the same half-inning but different ids (multi-runner play split).
    summary = {
        "scoringPlays": [
            _scoring_entry(play_id="p1", half="Top", inning=4, text="Hernández scored on wild pitch, Rojas to second."),
            _scoring_entry(play_id="p2", half="Top", inning=4, text="Hernández scored on wild pitch, Rojas to second."),
        ]
    }
    out = Coord._normalize_scoring_plays(summary)
    assert len(out) == 1
    assert out[0]["id"] == "p1"  # first occurrence kept


def test_normalize_scoring_plays_preserves_same_text_in_different_innings():
    # Two plays with identical text in different half-innings are genuinely
    # distinct (rare in practice, but the signature includes the half/inning
    # to be safe).
    summary = {
        "scoringPlays": [
            _scoring_entry(play_id="p1", half="Top", inning=3, text="Solo home run."),
            _scoring_entry(play_id="p2", half="Top", inning=7, text="Solo home run."),
        ]
    }
    out = Coord._normalize_scoring_plays(summary)
    assert len(out) == 2


def test_normalize_scoring_plays_falls_back_to_plays_when_scoringplays_empty():
    summary = {
        "scoringPlays": [],
        "plays": [
            {"id": "x1", "text": "Pop out.", "scoringPlay": False, "period": {"type": "Top", "number": 1}},
            {"id": "x2", "text": "Sac fly, run scores.", "scoringPlay": True, "period": {"type": "Top", "number": 1}, "scoreValue": 1},
        ],
    }
    out = Coord._normalize_scoring_plays(summary)
    assert len(out) == 1
    assert out[0]["id"] == "x2"


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
    assert (
        Coord._normalize_third_out_play(
            {"plays": plays},
            {"period": 1, "period_prefix": "Top 1st", "is_between_halves": False},
        )
        == {}
    )


# ---------------------------------------------------------------------------
# _normalize_decisions
# ---------------------------------------------------------------------------


def _make_pitching_entry(
    *, athlete_id, name, short_name, decision_text, headshot=""
):
    athlete = {"id": athlete_id, "displayName": name, "shortName": short_name}
    if headshot:
        athlete["headshot"] = {"href": headshot, "alt": name}
    return {
        "athlete": athlete,
        "notes": [{"type": "pitchingDecision", "text": decision_text}],
    }


def _make_summary_with_pitchers(away_entries, home_entries):
    return {
        "boxscore": {
            "teams": [
                {"team": {"id": "1"}, "homeAway": "away"},
                {"team": {"id": "2"}, "homeAway": "home"},
            ],
            "players": [
                {
                    "team": {"id": "1", "abbreviation": "BOS"},
                    "statistics": [{"type": "pitching", "athletes": away_entries}],
                },
                {
                    "team": {"id": "2", "abbreviation": "ATL"},
                    "statistics": [{"type": "pitching", "athletes": home_entries}],
                },
            ],
        }
    }


def test_normalize_decisions_parses_win_loss_save():
    summary = _make_summary_with_pitchers(
        away_entries=[
            _make_pitching_entry(
                athlete_id="4720856",
                name="Brayan Bello",
                short_name="B. Bello",
                decision_text="L, 2-5",
                headshot="https://a.espncdn.com/i/headshots/mlb/players/full/4720856.png",
            ),
        ],
        home_entries=[
            _make_pitching_entry(
                athlete_id="33840",
                name="Grant Holmes",
                short_name="G. Holmes",
                decision_text="W, 3-1",
            ),
            _make_pitching_entry(
                athlete_id="40404",
                name="Closer Closington",
                short_name="C. Closington",
                decision_text="SV, 5",
            ),
        ],
    )
    result = Coord._normalize_decisions(summary)
    assert set(result.keys()) == {"win", "loss", "save"}
    assert result["win"]["id"] == "33840"
    assert result["win"]["record"] == "3-1"
    assert result["win"]["decision"] == "W"
    assert result["win"]["team_side"] == "home"
    assert result["win"]["team_abbr"] == "ATL"
    assert result["loss"]["id"] == "4720856"
    assert result["loss"]["record"] == "2-5"
    assert result["loss"]["decision"] == "L"
    assert result["loss"]["team_side"] == "away"
    assert (
        result["loss"]["headshot"]
        == "https://a.espncdn.com/i/headshots/mlb/players/full/4720856.png"
    )
    assert result["save"]["decision"] == "SV"
    assert result["save"]["record"] == "5"


def test_normalize_decisions_handles_missing_save():
    summary = _make_summary_with_pitchers(
        away_entries=[
            _make_pitching_entry(
                athlete_id="1",
                name="A Pitcher",
                short_name="A. Pitcher",
                decision_text="L, 1-2",
            ),
        ],
        home_entries=[
            _make_pitching_entry(
                athlete_id="2",
                name="B Pitcher",
                short_name="B. Pitcher",
                decision_text="W, 4-0",
            ),
        ],
    )
    result = Coord._normalize_decisions(summary)
    assert set(result.keys()) == {"win", "loss"}
    assert "save" not in result


def test_normalize_decisions_ignores_holds_and_unknown_codes():
    summary = _make_summary_with_pitchers(
        away_entries=[
            _make_pitching_entry(
                athlete_id="1",
                name="Held Hold",
                short_name="H. Hold",
                decision_text="HLD, 3",
            ),
        ],
        home_entries=[
            _make_pitching_entry(
                athlete_id="2",
                name="Blown Save",
                short_name="B. Save",
                decision_text="BSV, 1",
            ),
        ],
    )
    assert Coord._normalize_decisions(summary) == {}


def test_normalize_decisions_returns_empty_when_no_boxscore():
    assert Coord._normalize_decisions({}) == {}
    assert Coord._normalize_decisions({"boxscore": {}}) == {}


# ---------------------------------------------------------------------------
# _extract_batter_game_outcomes
# ---------------------------------------------------------------------------


def test_extract_batter_game_outcomes_matches_by_last_name():
    summary = {
        "rosters": [{"roster": [{"athlete": {"id": "42", "displayName": "Mookie Betts", "lastName": "Betts"}}]}],
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


def _ev(
    eid: str,
    *,
    date: str | None = None,
    state: str = "pre",
    name: str = "STATUS_SCHEDULED",
    completed: bool | None = None,
):
    status_type: dict[str, object] = {"state": state, "name": name}
    if completed is not None:
        status_type["completed"] = completed
    return {
        "id": eid,
        "date": date,
        "competitions": [{"status": {"type": status_type}}],
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


def test_select_event_skips_postponed_when_picking_prev():
    """Postponed games (state="post", completed=false) must not shadow the
    most-recent real final as `prev`, and the postponed game should be
    promoted to `next_ev` to fill the gap before the next scheduled matchup.
    """
    import time as _time

    now = _time.time()

    # Use times within the SHOW_NEXT_AFTER_PREV_SECONDS window (16h) so the
    # display stays on the finished game rather than skipping ahead to next.
    final_at = datetime.fromtimestamp(now - 7200, tz=UTC).isoformat().replace("+00:00", "Z")
    postponed_at = datetime.fromtimestamp(now - 3600, tz=UTC).isoformat().replace("+00:00", "Z")
    tomorrow = datetime.fromtimestamp(now + 86400, tz=UTC).isoformat().replace("+00:00", "Z")

    events = [
        _ev("A", date=final_at, state="post", name="STATUS_FINAL", completed=True),
        _ev("B", date=postponed_at, state="post", name="STATUS_POSTPONED", completed=False),
        _ev("C", date=tomorrow, state="pre", name="STATUS_SCHEDULED"),
    ]
    prev_id, next_id, live_id, display_id, _disp = Coord._select_event(None, events)
    assert prev_id == "A"
    # Postponed is promoted into `next_ev` because it falls between `prev`
    # (yesterday's final) and the actual next scheduled game (tomorrow).
    assert next_id == "B"
    assert live_id == ""
    # Display is still the final until SHOW_NEXT_AFTER_PREV_SECONDS elapses.
    assert display_id == "A"


def test_select_event_postponed_does_not_displace_closer_scheduled():
    """A postponement that's older than the next scheduled game should be
    promoted to `next_ev`; a postponement *after* the next scheduled game
    must not displace it.
    """
    import time as _time

    now = _time.time()
    prev_at = datetime.fromtimestamp(now - 7200, tz=UTC).isoformat().replace("+00:00", "Z")
    soon = datetime.fromtimestamp(now + 3600, tz=UTC).isoformat().replace("+00:00", "Z")
    postponed_at = datetime.fromtimestamp(now - 1800, tz=UTC).isoformat().replace("+00:00", "Z")

    events = [
        _ev("A", date=prev_at, state="post", name="STATUS_FINAL", completed=True),
        _ev("B", date=postponed_at, state="post", name="STATUS_POSTPONED", completed=False),
        _ev("C", date=soon, state="pre", name="STATUS_SCHEDULED"),
    ]
    _prev, next_id, _live, _disp_id, _disp = Coord._select_event(None, events)
    # Postponed (30min ago) is between prev (2h ago) and next (1h from now),
    # so it gets promoted.
    assert next_id == "B"


def test_select_event_postponed_only_no_prev_no_next():
    """When the only candidate is a postponed past event, it should still
    surface so the card can show "Postponed" instead of nothing.
    """
    import time as _time

    now = _time.time()
    postponed_at = datetime.fromtimestamp(now - 3600, tz=UTC).isoformat().replace("+00:00", "Z")

    events = [
        _ev("A", date=postponed_at, state="post", name="STATUS_POSTPONED", completed=False),
    ]
    prev_id, next_id, _live, display_id, _disp = Coord._select_event(None, events)
    assert prev_id == ""
    assert next_id == "A"
    # display_event falls back to next_ev when prev is None.
    assert display_id == "A"


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
        scoring_plays=[],
        current_pitches=[],
        away_team={},
        home_team={},
        current_batter={},
        current_pitcher={},
        batter_stats={},
        pitcher_stats={},
        situation={},
        probable_pitchers={"away": {}, "home": {}},
        win_probability={},
        due_up=[],
        third_out_play={},
        third_out_hold_until=None,
        on_deck={},
        lineups={},
        leaders={},
        decisions={},
        division_standings={"division_name": "", "entries": []},
        highlights_url="",
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
    curr = _make_data(my_score=4, opp_score=2, is_live=False, state="post", completed=True)
    names = [n for n, _ in Coord._detect_game_events(prev, curr, 19)]
    assert EVENT_GAME_ENDED in names
    assert EVENT_GAME_WON in names
    assert EVENT_GAME_LOST not in names


def test_detect_game_lost():
    prev = _make_data(my_score=2, opp_score=4, state="in", completed=False)
    curr = _make_data(my_score=2, opp_score=4, is_live=False, state="post", completed=True)
    names = [n for n, _ in Coord._detect_game_events(prev, curr, 19)]
    assert EVENT_GAME_ENDED in names
    assert EVENT_GAME_LOST in names
    assert EVENT_GAME_WON not in names


def test_detect_tie_fires_only_game_ended():
    prev = _make_data(my_score=3, opp_score=3, state="in", completed=False)
    curr = _make_data(my_score=3, opp_score=3, is_live=False, state="post", completed=True)
    names = [n for n, _ in Coord._detect_game_events(prev, curr, 19)]
    assert names == [EVENT_GAME_ENDED]


def test_detect_no_repeat_after_already_final():
    final = _make_data(my_score=4, opp_score=2, is_live=False, state="post", completed=True)
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
    coord._dispatch_game_events(
        [
            (EVENT_TEAM_SCORED, {"team_abbr": "LAD"}),
            (EVENT_GAME_WON, {"team_abbr": "LAD"}),
        ]
    )
    assert bus.async_fire.call_count == 2


# ---------------------------------------------------------------------------
# _normalize_probable_pitchers (extended fields)
# ---------------------------------------------------------------------------


def test_normalize_probable_pitchers_extracts_record_and_headshot():
    display_comp = {
        "competitors": [
            {
                "homeAway": "away",
                "probables": [
                    {
                        "athlete": {
                            "displayName": "Jane Doe",
                            "shortName": "J. Doe",
                            "headshot": {"href": "https://e.com/jane.png"},
                        },
                        "statistics": [
                            {"name": "wins", "displayValue": "10"},
                            {"name": "losses", "displayValue": "4"},
                            {"abbreviation": "ERA", "displayValue": "2.85"},
                        ],
                    }
                ],
            },
            {
                "homeAway": "home",
                "probables": [
                    {
                        "athlete": {"displayName": "John Roe", "headshot": "https://e.com/john.png"},
                        "statistics": [{"name": "ERA", "displayValue": "3.50"}],
                    }
                ],
            },
        ],
    }
    out = Coord._normalize_probable_pitchers(display_comp)
    assert out["away"]["wins"] == "10"
    assert out["away"]["losses"] == "4"
    assert out["away"]["record"] == "10-4"
    assert out["away"]["era"] == "2.85"
    assert out["away"]["headshot"] == "https://e.com/jane.png"
    # When wins or losses are missing, record is empty.
    assert out["home"]["record"] == ""
    assert out["home"]["era"] == "3.50"
    assert out["home"]["headshot"] == "https://e.com/john.png"


def test_normalize_probable_pitchers_handles_missing_competitor():
    out = Coord._normalize_probable_pitchers(None)
    assert out == {"away": {}, "home": {}}


def test_normalize_probable_pitchers_handles_summary_header_shape():
    """Summary header wraps stats as ``statistics.splits.categories[]``."""
    display_comp = {
        "competitors": [
            {
                "homeAway": "home",
                "probables": [
                    {
                        "athlete": {
                            "displayName": "Michael McGreevy",
                            "shortName": "M. McGreevy",
                            "headshot": {"href": "https://a.espncdn.com/i/headshots/mlb/players/full/4424141.png"},
                        },
                        "statistics": {
                            "splits": {
                                "categories": [
                                    {"name": "wins", "abbreviation": "W", "displayValue": "1"},
                                    {"name": "losses", "abbreviation": "L", "displayValue": "2"},
                                    {"name": "ERA", "abbreviation": "ERA", "displayValue": "2.97"},
                                ],
                            },
                        },
                    }
                ],
            },
        ],
    }
    out = Coord._normalize_probable_pitchers(display_comp)
    home = out["home"]
    assert home["wins"] == "1"
    assert home["losses"] == "2"
    assert home["record"] == "1-2"
    assert home["era"] == "2.97"
    assert home["headshot"].endswith("4424141.png")


# ---------------------------------------------------------------------------
# _normalize_standings
# ---------------------------------------------------------------------------


def _standings_payload():
    """Mirrors the real ESPN ``/standings`` shape: leagues under children[],
    each with a flat entries[] of every team in the league."""
    return {
        "children": [
            {
                "name": "American League",
                "abbreviation": "AL",
                "standings": {
                    "entries": [
                        {
                            "team": {
                                "id": "10",
                                "abbreviation": "NYY",
                                "displayName": "New York Yankees",
                                "shortDisplayName": "Yankees",
                            },
                            "stats": [
                                {"name": "wins", "abbreviation": "W", "displayValue": "62"},
                                {"name": "losses", "abbreviation": "L", "displayValue": "38"},
                                {"name": "divisionGamesBehind", "abbreviation": "DGB", "displayValue": "-"},
                                {"name": "gamesBehind", "abbreviation": "GB", "displayValue": "-"},
                            ],
                        },
                    ],
                },
            },
            {
                "name": "National League",
                "abbreviation": "NL",
                "standings": {
                    "entries": [
                        # Out-of-division NL team — should be filtered out.
                        {
                            "team": {
                                "id": "21",
                                "abbreviation": "NYM",
                                "displayName": "New York Mets",
                                "shortDisplayName": "Mets",
                            },
                            "stats": [
                                {"name": "wins", "abbreviation": "W", "displayValue": "55"},
                                {"name": "losses", "abbreviation": "L", "displayValue": "45"},
                                {"name": "divisionGamesBehind", "abbreviation": "DGB", "displayValue": "3"},
                            ],
                        },
                        # NL West teams
                        {
                            "team": {
                                "id": "19",
                                "abbreviation": "LAD",
                                "displayName": "Los Angeles Dodgers",
                                "shortDisplayName": "Dodgers",
                            },
                            "stats": [
                                {"name": "wins", "abbreviation": "W", "displayValue": "65"},
                                {"name": "losses", "abbreviation": "L", "displayValue": "35"},
                                {"name": "divisionGamesBehind", "abbreviation": "DGB", "displayValue": "-"},
                                {"name": "gamesBehind", "abbreviation": "GB", "displayValue": "-"},
                            ],
                        },
                        {
                            "team": {
                                "id": "26",
                                "abbreviation": "SF",
                                "displayName": "San Francisco Giants",
                                "shortDisplayName": "Giants",
                            },
                            "stats": [
                                {"name": "wins", "abbreviation": "W", "displayValue": "58"},
                                {"name": "losses", "abbreviation": "L", "displayValue": "42"},
                                {"name": "divisionGamesBehind", "abbreviation": "DGB", "displayValue": "7.0"},
                                {"name": "gamesBehind", "abbreviation": "GB", "displayValue": "7.0"},
                            ],
                        },
                        {
                            "team": {
                                "id": "25",
                                "abbreviation": "SD",
                                "displayName": "San Diego Padres",
                                "shortDisplayName": "Padres",
                            },
                            "stats": [
                                {"name": "wins", "abbreviation": "W", "displayValue": "60"},
                                {"name": "losses", "abbreviation": "L", "displayValue": "40"},
                                {"name": "divisionGamesBehind", "abbreviation": "DGB", "displayValue": "5.0"},
                            ],
                        },
                    ],
                },
            },
        ],
    }


def test_normalize_standings_filters_to_team_division_and_sorts():
    division_index = {
        "19": "NL West",  # LAD
        "26": "NL West",  # SF
        "25": "NL West",  # SD
        "21": "NL East",  # NYM
        "10": "AL East",  # NYY
    }
    out = Coord._normalize_standings(_standings_payload(), division_index, team_id=19)
    assert out["division_name"] == "NL West"
    # Mets (NL East) should be filtered out; only NL West teams remain.
    abbrs_in_order = [e["team_short_name"] for e in out["entries"]]
    assert abbrs_in_order == ["Dodgers", "Padres", "Giants"]  # sorted by wins desc
    first = out["entries"][0]
    assert first["team_id"] == "19"
    assert first["wins"] == "65"
    assert first["losses"] == "35"
    assert first["games_back"] == "-"  # DGB preferred
    third = out["entries"][2]
    assert third["wins"] == "58"
    assert third["games_back"] == "7.0"


def test_normalize_standings_team_not_in_payload():
    # Team known to division index but not present in the league entries.
    payload = {"children": [{"name": "AL", "standings": {"entries": []}}]}
    assert Coord._normalize_standings(payload, {"19": "NL West"}, 19) == {"division_name": "", "entries": []}


def test_normalize_standings_team_not_in_division_index():
    # Empty index means we can't determine the team's division — return empty.
    out = Coord._normalize_standings(_standings_payload(), {}, team_id=19)
    assert out == {"division_name": "", "entries": []}


def test_normalize_standings_handles_empty_payload():
    idx = {"19": "NL West"}
    assert Coord._normalize_standings(None, idx, 19) == {"division_name": "", "entries": []}
    assert Coord._normalize_standings({}, idx, 19) == {"division_name": "", "entries": []}
    assert Coord._normalize_standings({"children": "bad"}, idx, 19) == {"division_name": "", "entries": []}


# ---------------------------------------------------------------------------
# _team_id_division_index
# ---------------------------------------------------------------------------


def test_team_id_division_index_builds_from_groups():
    payload = {
        "groups": [
            {
                "name": "American League",
                "children": [
                    {
                        "name": "American League East",
                        "teams": [
                            {"id": "10", "abbreviation": "NYY"},
                            {"id": "1", "abbreviation": "BAL"},
                        ],
                    },
                ],
            },
            {
                "name": "National League",
                "children": [
                    {
                        "name": "National League West",
                        "teams": [
                            {"id": "19", "abbreviation": "LAD"},
                            {"id": "26", "abbreviation": "SF"},
                        ],
                    },
                ],
            },
        ],
    }
    idx = Coord._team_id_division_index(payload)
    assert idx["10"] == "American League East"
    assert idx["19"] == "National League West"
    assert idx["26"] == "National League West"


def test_team_id_division_index_handles_empty():
    assert Coord._team_id_division_index(None) == {}
    assert Coord._team_id_division_index({}) == {}
    assert Coord._team_id_division_index({"groups": "bad"}) == {}


def test_team_id_division_index_against_real_fixture():
    import json
    import pathlib

    fixture = pathlib.Path(__file__).resolve().parents[1] / "espn-api" / "groups.json"
    if not fixture.exists():
        return
    payload = json.loads(fixture.read_text())
    idx = Coord._team_id_division_index(payload)
    assert idx["19"] == "National League West"  # Dodgers
    assert idx["10"] == "American League East"  # Yankees
    assert len(idx) == 30


def test_normalize_standings_against_real_fixture():
    """Smoke-test with real captured ESPN payloads to lock down both schemas."""
    import json
    import pathlib

    base = pathlib.Path(__file__).resolve().parents[1] / "espn-api"
    standings_file = base / "standings.json"
    groups_file = base / "groups.json"
    if not standings_file.exists() or not groups_file.exists():
        return  # fixtures optional in CI checkouts
    standings_payload = json.loads(standings_file.read_text())
    groups_payload = json.loads(groups_file.read_text())
    division_index = Coord._team_id_division_index(groups_payload)
    out = Coord._normalize_standings(standings_payload, division_index, team_id=19)
    assert out["division_name"] == "National League West"
    abbrs = [e["team_short_name"] for e in out["entries"]]
    assert abbrs[0] == "Dodgers"
    assert out["entries"][0]["games_back"] == "-"
    assert out["entries"][0]["wins"] == "20"
    assert out["entries"][0]["losses"] == "12"
    assert len(out["entries"]) == 5
    assert set(abbrs) == {"Dodgers", "Padres", "Diamondbacks", "Rockies", "Giants"}


# ---------------------------------------------------------------------------
# _parse_player_card / _team_abbr_map (Option B, Chunk 1)
#
# Driven by the real (trimmed) ESPN fixtures captured in Chunk 0. Assertions
# target structure + frozen early-career rows (a player's debut-season line
# never changes), so they do not churn as current stats accrue.
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict:
    import json
    import pathlib

    return json.loads((pathlib.Path(__file__).resolve().parents[0] / "fixtures" / name).read_text())


def test_parse_player_card_hitter_trout():
    bio = _load_fixture("athlete_30836_trout_bio.json")
    stats = _load_fixture("athlete_30836_trout_stats.json")
    card = Coord._parse_player_card("30836", bio, stats)

    assert card["id"] == "30836"
    assert card["bio"]["name"] == "Mike Trout"
    assert card["bio"]["position"] == "CF"
    assert card["bio"]["bats_throws"] == "Right/Right"
    assert card["bio"]["height"] == "6' 1\""
    assert card["bio"]["headshot"].startswith("http")

    career = card["career"]
    assert career["kind"] == "batting"
    assert career["columns"][6] == "HR" and career["columns"][7] == "RBI"
    # Debut season (2011) is frozen history.
    debut = career["seasons"][0]
    assert debut["year"] == "2011"
    assert debut["team"] == "LAA"  # teamId 3 resolved via the teams map
    assert debut["stats"][6] == "5"  # HR
    assert debut["stats"][7] == "16"  # RBI
    assert debut["stats"][1] == "123"  # AB
    assert career["totals"]  # career aggregate row present
    assert card["glossary"].get("2B") == "Doubles"


def test_parse_player_card_pitcher_kershaw():
    bio = _load_fixture("athlete_28963_kershaw_bio.json")
    stats = _load_fixture("athlete_28963_kershaw_stats.json")
    card = Coord._parse_player_card("28963", bio, stats)

    assert card["bio"]["name"] == "Clayton Kershaw"
    career = card["career"]
    assert career["kind"] == "pitching"
    assert "ERA" in career["columns"] and "W" in career["columns"]
    debut = career["seasons"][0]
    assert debut["year"] == "2008"
    assert debut["team"] == "LAD"  # teamId 19
    era_idx = career["columns"].index("ERA")
    assert debut["stats"][era_idx] == "4.26"


def test_parse_player_card_two_way_returns_single_side():
    # Documented limitation: ESPN's /stats returns categories by listed
    # position only. Ohtani is listed SP, so only pitching is available.
    bio = _load_fixture("athlete_39832_ohtani_bio.json")
    stats = _load_fixture("athlete_39832_ohtani_stats.json")
    card = Coord._parse_player_card("39832", bio, stats)

    assert card["bio"]["name"] == "Shohei Ohtani"
    assert card["career"]["kind"] == "pitching"
    assert card["career"]["seasons"]


def test_parse_player_card_handles_both_payloads_empty():
    card = Coord._parse_player_card("999", {}, {})
    assert card["id"] == "999"
    assert card["bio"]["name"] == ""
    assert card["career"] == {}
    assert card["glossary"] == {}


def test_parse_player_card_handles_stats_only_no_bio():
    stats = _load_fixture("athlete_30836_trout_stats.json")
    card = Coord._parse_player_card("30836", {}, stats)
    assert card["bio"]["name"] == ""  # bio endpoint failed
    assert card["career"]["kind"] == "batting"  # stats still parsed
    assert card["career"]["seasons"]


def test_parse_player_card_handles_bio_only_no_stats():
    bio = _load_fixture("athlete_30836_trout_bio.json")
    card = Coord._parse_player_card("30836", bio, {})
    assert card["bio"]["name"] == "Mike Trout"
    assert card["career"] == {}  # stats endpoint failed


def test_team_abbr_map_collapses_dual_keying_and_skips_garbage():
    stats = _load_fixture("athlete_39832_ohtani_stats.json")
    mapping = Coord._team_abbr_map(stats)
    assert mapping == {"3": "LAA", "19": "LAD"}
    assert Coord._team_abbr_map({}) == {}
    assert Coord._team_abbr_map({"teams": "not-a-dict"}) == {}
    assert Coord._team_abbr_map({"teams": {"x": None, "y": {"id": "", "abbreviation": ""}}}) == {}


# ---------------------------------------------------------------------------
# _normalize_lineups (Lineup Popup, Chunk 1)
#
# Driven by the real (trimmed) box-score fixture captured in Chunk 0
# (summary_401815376_boxscore.json — BOS @ ATL 2026-05-17 Final). The game
# is final, so every asserted stat is frozen history and will never churn.
# ---------------------------------------------------------------------------


def _boxscore_fixture() -> dict:
    return _load_fixture("summary_401815376_boxscore.json")


def test_normalize_lineups_resolves_sides_and_counts():
    lineups = Coord._normalize_lineups(_boxscore_fixture(), "")

    # away/home resolved via boxscore.teams homeAway map (not array order).
    assert set(lineups) == {"away", "home"}
    assert lineups["away"]["abbreviation"] == "BOS"
    assert lineups["away"]["name"] == "Boston Red Sox"
    assert lineups["home"]["abbreviation"] == "ATL"
    assert lineups["home"]["logo"].startswith("http")

    # Every player who appeared: BOS used 11 batters (incl. subs) + 2
    # pitchers; ATL ran out 9 starters + 3 pitchers.
    assert len(lineups["away"]["hitters"]) == 11
    assert len(lineups["away"]["pitchers"]) == 2
    assert len(lineups["home"]["hitters"]) == 9
    assert len(lineups["home"]["pitchers"]) == 3

    # No current batter passed (a completed game has none) -> neither side
    # flagged as batting.
    assert lineups["away"]["is_batting"] is False
    assert lineups["home"]["is_batting"] is False


def test_normalize_lineups_hitter_game_stats_and_position():
    lineups = Coord._normalize_lineups(_boxscore_fixture(), "")
    # ATL leadoff hitter, Drake Baldwin (frozen line for this final game).
    baldwin = lineups["home"]["hitters"][0]
    assert baldwin["id"] == "4810190"
    assert baldwin["name"] == "Drake Baldwin"
    assert baldwin["bat_order"] == 1
    assert baldwin["starter"] is True
    assert baldwin["position"] == "C"  # in-game fielding position
    assert baldwin["ab"] == "2"
    assert baldwin["r"] == "0"
    assert baldwin["h"] == "0"
    assert baldwin["hr"] == "0"
    assert baldwin["rbi"] == "2"
    assert baldwin["bb"] == "2"
    assert baldwin["k"] == "0"
    assert baldwin["avg"] == ".301"  # season avg the box score carries


def test_normalize_lineups_orders_by_bat_order_starter_before_sub():
    lineups = Coord._normalize_lineups(_boxscore_fixture(), "")
    bos = lineups["away"]["hitters"]

    # Sorted ascending by batting order, 1..9.
    orders = [h["bat_order"] for h in bos]
    assert orders == sorted(orders)
    assert orders[0] == 1 and orders[-1] == 9

    # Slot 4 was a substitution: Contreras (starter, subbed out) then
    # Kiner-Falefa (sub, still active) — starter must precede the sub.
    slot4 = [h for h in bos if h["bat_order"] == 4]
    assert [h["name"] for h in slot4] == ["Willson Contreras", "Isiah Kiner-Falefa"]
    contreras, ikf = slot4
    assert contreras["starter"] is True and contreras["active"] is False
    assert ikf["starter"] is False and ikf["active"] is True
    assert ikf["id"] == "33572"
    assert ikf["position"] == "1B"  # in-game position, not his listed 2B
    assert ikf["h"] == "1" and ikf["ab"] == "1" and ikf["avg"] == ".214"


def test_normalize_lineups_pitcher_game_stats_and_decision():
    lineups = Coord._normalize_lineups(_boxscore_fixture(), "")
    bello = lineups["away"]["pitchers"][0]  # BOS starter
    assert bello["name"] == "Brayan Bello"
    assert bello["starter"] is True
    assert bello["decision"] == "L, 2-5"
    assert bello["ip"] == "5.0"  # fullInnings.partInnings
    assert bello["h"] == "8"
    assert bello["r"] == "7"
    assert bello["er"] == "7"
    assert bello["bb"] == "3"
    assert bello["k"] == "1"
    assert bello["pc"] == "98"  # `pitches`, not the "98-61" pitches-strikes
    assert bello["era"] == "7.16"  # season ERA from the box score


def test_normalize_lineups_is_batting_from_current_batter():
    # Baldwin (ATL / home) is the current batter -> home side flagged.
    lineups = Coord._normalize_lineups(_boxscore_fixture(), "4810190")
    assert lineups["home"]["is_batting"] is True
    assert lineups["away"]["is_batting"] is False


def test_normalize_lineups_unknown_batter_flags_no_side():
    lineups = Coord._normalize_lineups(_boxscore_fixture(), "0000000")
    assert lineups["away"]["is_batting"] is False
    assert lineups["home"]["is_batting"] is False


def test_normalize_lineups_empty_boxscore_returns_empty():
    assert Coord._normalize_lineups({}, "") == {}
    assert Coord._normalize_lineups({"boxscore": {}}, "") == {}
    assert Coord._normalize_lineups({"boxscore": {"players": []}}, "") == {}


def test_normalize_lineups_positional_fallback_without_teams_map():
    # No boxscore.teams -> fall back to ESPN's array convention (away first).
    summary = {
        "boxscore": {
            "players": [
                {
                    "team": {"id": "10", "abbreviation": "AAA", "displayName": "Aaa Team"},
                    "statistics": [
                        {
                            "type": "batting",
                            "keys": ["atBats", "runs", "hits", "homeRuns", "RBIs", "walks", "strikeouts", "avg"],
                            "athletes": [
                                {
                                    "batOrder": 1,
                                    "starter": True,
                                    "active": True,
                                    "athlete": {"id": "1", "displayName": "P One"},
                                    "stats": ["3", "1", "2", "0", "1", "0", "0", ".333"],
                                }
                            ],
                        }
                    ],
                },
                {
                    "team": {"id": "20", "abbreviation": "BBB", "displayName": "Bbb Team"},
                    "statistics": [
                        {
                            "type": "pitching",
                            "keys": [
                                "fullInnings.partInnings",
                                "hits",
                                "runs",
                                "earnedRuns",
                                "walks",
                                "strikeouts",
                                "ERA",
                                "pitches",
                            ],
                            "athletes": [
                                {
                                    "starter": True,
                                    "active": False,
                                    "athlete": {"id": "2", "displayName": "Q Two"},
                                    "stats": ["6.0", "4", "1", "1", "2", "7", "2.50", "95"],
                                }
                            ],
                        }
                    ],
                },
            ]
        }
    }
    lineups = Coord._normalize_lineups(summary, "")
    assert lineups["away"]["abbreviation"] == "AAA"
    assert lineups["home"]["abbreviation"] == "BBB"
    h = lineups["away"]["hitters"][0]
    assert h["name"] == "P One" and h["bat_order"] == 1 and h["position"] == ""
    p = lineups["home"]["pitchers"][0]
    # No notes -> no-decision (empty string), other game stats still parsed.
    assert p["decision"] == ""
    assert p["ip"] == "6.0" and p["pc"] == "95" and p["era"] == "2.50"


# ---------------------------------------------------------------------------
# _extract_season_line (Lineup Popup, Chunk 2)
#
# Exact-value assertions use hand-crafted minimal payloads (deterministic);
# the real captured stats fixtures are used only for structural / side
# assertions, since an active player's current-season values churn.
# ---------------------------------------------------------------------------

_THIS_YEAR = datetime.now().year


def _hitting_payload(year: int, stats: list[str]) -> dict:
    return {
        "categories": [
            {
                "name": "career-batting",
                "names": [
                    "gamesPlayed",
                    "atBats",
                    "runs",
                    "hits",
                    "homeRuns",
                    "RBIs",
                    "walks",
                    "strikeouts",
                    "stolenBases",
                    "avg",
                ],
                "statistics": [
                    {"season": {"year": year}, "stats": stats},
                ],
            }
        ]
    }


def test_extract_season_line_hitter_exact():
    payload = _hitting_payload(
        _THIS_YEAR,
        ["40", "150", "30", "48", "12", "33", "20", "35", "7", ".320"],
    )
    line = Coord._extract_season_line(payload)
    assert line == {
        "hitting": {
            "ab": "150",
            "h": "48",
            "hr": "12",
            "rbi": "33",
            "sb": "7",
            "avg": ".320",
        }
    }


def test_extract_season_line_pitcher_exact():
    payload = {
        "categories": [
            {
                "name": "pitching",
                "names": [
                    "gamesPlayed",
                    "wins",
                    "losses",
                    "ERA",
                    "WHIP",
                    "innings",
                    "strikeouts",
                ],
                "statistics": [
                    {"season": {"year": _THIS_YEAR}, "stats": ["10", "8", "3", "2.85", "1.04", "92.1", "115"]},
                ],
            }
        ]
    }
    line = Coord._extract_season_line(payload)
    assert line == {
        "pitching": {
            "w": "8",
            "l": "3",
            "era": "2.85",
            "ip": "92.1",
            "k": "115",
            "whip": "1.04",
        }
    }


def test_extract_season_line_falls_back_to_last_row_when_current_year_absent():
    # Only past seasons -> use the most recent (last) row.
    payload = {
        "categories": [
            {
                "name": "career-batting",
                "names": ["atBats", "hits", "homeRuns", "RBIs", "stolenBases", "avg"],
                "statistics": [
                    {"season": {"year": _THIS_YEAR - 2}, "stats": ["500", "140", "20", "70", "5", ".280"]},
                    {"season": {"year": _THIS_YEAR - 1}, "stats": ["520", "160", "28", "95", "9", ".308"]},
                ],
            }
        ]
    }
    line = Coord._extract_season_line(payload)
    assert line["hitting"]["avg"] == ".308"  # last row, not the older one
    assert line["hitting"]["hr"] == "28"


def test_extract_season_line_missing_value_yields_empty_string():
    payload = _hitting_payload(_THIS_YEAR, ["40", "150", "30", "48", "12", "33", "20", "35"])
    # stolenBases / avg indices past the end of stats -> "".
    line = Coord._extract_season_line(payload)
    assert line["hitting"]["sb"] == ""
    assert line["hitting"]["avg"] == ""
    assert line["hitting"]["hr"] == "12"


def test_extract_season_line_returns_empty_without_primary_category():
    assert Coord._extract_season_line({}) == {}
    assert Coord._extract_season_line({"categories": []}) == {}
    # Only a non-primary category present (postseason) -> nothing usable.
    assert (
        Coord._extract_season_line(
            {
                "categories": [
                    {
                        "name": "postseason-batting",
                        "names": ["hits"],
                        "statistics": [{"season": {"year": _THIS_YEAR}, "stats": ["3"]}],
                    }
                ]
            }
        )
        == {}
    )
    # Primary category present but no season rows -> {}.
    assert (
        Coord._extract_season_line({"categories": [{"name": "career-batting", "names": ["hits"], "statistics": []}]})
        == {}
    )


def test_extract_season_line_real_fixture_hitter_structure():
    # Trout's listed position is CF -> career-batting present.
    stats = _load_fixture("athlete_30836_trout_stats.json")
    line = Coord._extract_season_line(stats)
    assert set(line) == {"hitting"}
    h = line["hitting"]
    assert set(h) == {"ab", "h", "hr", "rbi", "sb", "avg"}
    # Structural only — active-player current-season values churn.
    assert all(isinstance(v, str) for v in h.values())


def test_extract_season_line_real_fixture_pitcher_structure():
    # Kershaw is a pitcher; his fixture has no current-year row, so this
    # also exercises the most-recent-row fallback path on real data.
    stats = _load_fixture("athlete_28963_kershaw_stats.json")
    line = Coord._extract_season_line(stats)
    assert set(line) == {"pitching"}
    p = line["pitching"]
    assert set(p) == {"w", "l", "era", "ip", "k", "whip"}
    assert all(isinstance(v, str) for v in p.values())
    assert p["era"] != ""  # a real ERA was extracted from the fallback row


def test_extract_season_line_two_way_listed_pitcher_returns_pitching():
    # Documented limitation: /stats returns categories by listed position.
    # Ohtani is listed SP, so only the pitching side is available.
    stats = _load_fixture("athlete_39832_ohtani_stats.json")
    line = Coord._extract_season_line(stats)
    assert set(line) == {"pitching"}


# ---------------------------------------------------------------------------
# _extract_current_season_batter_stats
#
# The in-card at-bat display reads season HR / RBI / AVG from
# ``/athletes/{id}/stats?category=batting``. The fixture for the two-way
# bug-fix case (Ohtani) was captured against that URL; the legacy
# default-URL fixture is reused to prove ``opponent-batting`` is no longer
# mistakenly picked up.
# ---------------------------------------------------------------------------


def test_extract_current_season_batter_stats_uses_career_batting_for_two_way_player():
    # Regression for the Ohtani-at-bat bug: with the ``?category=batting``
    # endpoint, his real ``career-batting`` line is now available even though
    # ESPN lists him as a pitcher. Asserted structurally (active-player
    # current-season values churn between releases).
    stats = _load_fixture("athlete_39832_ohtani_stats_batting.json")
    out = Coord._extract_current_season_batter_stats(stats)
    assert set(out) == {"hr", "rbi", "avg"}
    assert all(isinstance(v, str) for v in out.values())
    # career-batting always carries non-empty avg / hr / rbi for an active
    # major-leaguer with any plate appearances.
    assert out["hr"] != ""
    assert out["rbi"] != ""
    assert out["avg"] != ""


def test_extract_current_season_batter_stats_ignores_opponent_batting_category():
    # The legacy default ``/stats`` fixture for Ohtani contains only
    # pitching-side categories — including ``opponent-batting``, which has
    # ``homeRuns`` and ``RBIs`` in its key set (the HRs/RBIs opposing
    # batters have produced *against* him). The old extractor mistook those
    # for his hitting line; the new whitelist must return ``{}`` here.
    stats = _load_fixture("athlete_39832_ohtani_stats.json")
    assert Coord._extract_current_season_batter_stats(stats) == {}


def test_extract_current_season_batter_stats_picks_first_nonempty_when_duplicated():
    # ESPN's ``?category=batting`` response occasionally lists
    # ``career-batting`` twice (the second copy can be empty / postseason-
    # shaped). The extractor must take the first non-empty occurrence in
    # preference order — never silently skip past a real line to a stub.
    payload = {
        "categories": [
            {
                "name": "career-batting",
                "names": ["homeRuns", "RBIs", "avg"],
                "statistics": [{"season": {"year": _THIS_YEAR}, "stats": ["12", "40", ".275"]}],
            },
            {
                "name": "career-batting",
                "names": ["homeRuns", "RBIs", "avg"],
                "statistics": [],
            },
        ],
    }
    out = Coord._extract_current_season_batter_stats(payload)
    assert out == {"hr": "12", "rbi": "40", "avg": ".275"}


# ---------------------------------------------------------------------------
# _records_from_standings
# ---------------------------------------------------------------------------


def test_records_from_standings_builds_full_id_to_wl_map():
    # Mimics ESPN's nested shape: children=[league, league],
    # each with standings.entries[] of teams, each with a stats list
    # containing wins/losses by name and abbreviation.
    payload = {
        "children": [
            {
                "name": "American League",
                "standings": {
                    "entries": [
                        {
                            "team": {"id": "10"},
                            "stats": [
                                {"name": "wins", "abbreviation": "W", "displayValue": "30"},
                                {"name": "losses", "abbreviation": "L", "displayValue": "18"},
                            ],
                        },
                    ],
                },
            },
            {
                "name": "National League",
                "standings": {
                    "entries": [
                        {
                            "team": {"id": "19"},
                            "stats": [
                                {"name": "wins", "abbreviation": "W", "displayValue": "29"},
                                {"name": "losses", "abbreviation": "L", "displayValue": "19"},
                            ],
                        },
                    ],
                },
            },
        ],
    }
    out = Coord._records_from_standings(payload)
    assert out == {"10": "30-18", "19": "29-19"}


def test_records_from_standings_handles_missing_payload():
    assert Coord._records_from_standings(None) == {}
    assert Coord._records_from_standings({}) == {}
    assert Coord._records_from_standings({"children": []}) == {}


def test_records_from_standings_skips_entries_missing_wins_or_losses():
    # An entry with only wins (or only losses) is incomplete — skip it
    # rather than emit a malformed "W-" / "-L" string.
    payload = {
        "children": [
            {
                "standings": {
                    "entries": [
                        {
                            "team": {"id": "10"},
                            "stats": [{"name": "wins", "displayValue": "30"}],
                        },
                        {
                            "team": {"id": "11"},
                            "stats": [
                                {"name": "wins", "displayValue": "20"},
                                {"name": "losses", "displayValue": "28"},
                            ],
                        },
                    ],
                },
            },
        ],
    }
    out = Coord._records_from_standings(payload)
    assert out == {"11": "20-28"}


# ---------------------------------------------------------------------------
# _compact_competition with records_map override
# ---------------------------------------------------------------------------


def test_compact_competition_overrides_stale_record_summary_from_standings():
    # The summary endpoint returns a stale "84-67"; the standings map has the
    # fresh "85-67" (one win added post-game). The override should win.
    comp = {
        "id": "401",
        "status": {"type": {"state": "post"}},
        "competitors": [
            {
                "homeAway": "home",
                "score": "5",
                "recordSummary": "84-67",  # stale
                "team": {"id": "19", "abbreviation": "LAD"},
            },
            {
                "homeAway": "away",
                "score": "2",
                "recordSummary": "70-81",
                "team": {"id": "20", "abbreviation": "SF"},
            },
        ],
    }
    records_map = {"19": "85-67", "20": "70-82"}
    out = Coord._compact_competition(comp, records_map)
    assert out is not None
    by_side = {c["homeAway"]: c for c in out["competitors"]}
    assert by_side["home"]["recordSummary"] == "85-67"  # overridden
    assert by_side["away"]["recordSummary"] == "70-82"  # overridden


def test_compact_competition_keeps_summary_record_when_team_not_in_map():
    # Defensive: if the standings map is missing this team_id for any reason,
    # fall back to the summary value rather than blanking the field.
    comp = {
        "id": "401",
        "status": {"type": {"state": "post"}},
        "competitors": [
            {
                "homeAway": "home",
                "recordSummary": "84-67",
                "team": {"id": "19"},
            },
        ],
    }
    out = Coord._compact_competition(comp, {})
    assert out["competitors"][0]["recordSummary"] == "84-67"


def test_compact_competition_unchanged_when_no_records_map():
    # Back-compat: the original single-arg signature still works.
    comp = {
        "id": "401",
        "status": {"type": {"state": "post"}},
        "competitors": [
            {"homeAway": "home", "recordSummary": "84-67", "team": {"id": "19"}},
        ],
    }
    out = Coord._compact_competition(comp)
    assert out["competitors"][0]["recordSummary"] == "84-67"


# ---------------------------------------------------------------------------
# _extract_highlights_url
# ---------------------------------------------------------------------------


def test_extract_highlights_url_returns_videos_rel_href():
    summary = {
        "header": {
            "links": [
                {"rel": ["summary", "desktop", "event"], "href": "https://example.com/summary"},
                {"rel": ["videos", "desktop", "event"], "href": "https://www.espn.com/mlb/video?gameId=401"},
            ],
        },
    }
    assert Coord._extract_highlights_url(summary) == "https://www.espn.com/mlb/video?gameId=401"


def test_extract_highlights_url_returns_empty_when_videos_link_absent():
    # Pre-game / in-game: ESPN omits the videos rel link entirely.
    summary = {
        "header": {
            "links": [
                {"rel": ["summary", "desktop", "event"], "href": "https://example.com/summary"},
                {"rel": ["boxscore", "desktop", "event"], "href": "https://example.com/box"},
            ],
        },
    }
    assert Coord._extract_highlights_url(summary) == ""


def test_extract_highlights_url_returns_empty_for_missing_header():
    assert Coord._extract_highlights_url({}) == ""
    assert Coord._extract_highlights_url({"header": {}}) == ""
    assert Coord._extract_highlights_url({"header": {"links": "not a list"}}) == ""
