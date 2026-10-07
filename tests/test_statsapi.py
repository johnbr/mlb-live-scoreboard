"""Tests for the MLB Stats API fallback (:mod:`statsapi`).

Fixtures are real payloads captured 2026-10-05, trimmed to the keys the code
reads (``tests/fixtures/statsapi/README.md``):

* NYY @ TB, ALDS G2 -- ESPN event 401907986 published NO play-by-play
  (``playByPlaySource: "none"``) while MLB's feed (gamePk 849839) had it all.
  This is the case the fallback exists for.
* CHW @ CLE, the same day -- ESPN 401907991 fully covered, MLB gamePk 849834.
  Having the SAME complete game in both feeds makes it an oracle: translate
  MLB's copy and the existing normalizers must read it the way they read
  ESPN's own.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from custom_components.mlb_live_scoreboard import statsapi as sa
from custom_components.mlb_live_scoreboard.const import OPT_DATA_SOURCE_PREFERENCE
from custom_components.mlb_live_scoreboard.coordinator import (
    MlbLiveScoreboardCoordinator as Coord,
)

FIXTURES = Path(__file__).parent / "fixtures" / "statsapi"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _tb_end_of_b6() -> tuple[dict, dict]:
    return _load("espn_summary_401907986_pbp_none.json"), _load("mlb_feed_849839_b6_end.json")


def _tb_mid_at_bat() -> tuple[dict, dict]:
    return _load("espn_summary_401907986_t7_mid_at_bat.json"), _load("mlb_feed_849839_t7_mid_at_bat.json")


def _cle_oracle() -> tuple[dict, dict]:
    """(ESPN's real summary, the same summary rebuilt from MLB's feed)."""
    espn = _load("espn_summary_401907991_full.json")
    feed = _load("mlb_feed_849834_final.json")
    stripped = dict(espn, plays=[])
    stripped.pop("situation", None)
    return espn, sa.summary_from_statsapi(stripped, feed)


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------


def test_trigger_fires_on_espn_pbp_none_with_no_plays():
    espn, _feed = _tb_end_of_b6()
    assert sa.espn_lacks_play_by_play(espn) is True


def test_trigger_ignores_a_covered_game():
    espn = _load("espn_summary_401907991_full.json")
    assert sa.espn_lacks_play_by_play(espn) is False


def test_trigger_needs_both_the_flag_and_empty_plays():
    espn, _feed = _tb_end_of_b6()
    # ESPN switching coverage on mid-game: plays arrive, so ESPN wins again.
    assert sa.espn_lacks_play_by_play(dict(espn, plays=[{"id": "1"}])) is False
    # The first seconds of a covered game: no plays yet, but ESPN says "full".
    covered = json.loads(json.dumps(espn))
    covered["header"]["competitions"][0]["playByPlaySource"] = "full"
    assert sa.espn_lacks_play_by_play(covered) is False
    assert sa.espn_lacks_play_by_play({}) is False


def test_switching_back_waits_for_espn_plays_not_just_the_flag():
    # 2026-10-05 20:06: ESPN flipped to "full" with 0 plays, plays ~1 min later.
    espn, _feed = _tb_end_of_b6()
    flipped = json.loads(json.dumps(espn))
    flipped["header"]["competitions"][0]["playByPlaySource"] = "full"
    assert sa.should_use_statsapi(espn, already_using=False) is True
    assert sa.should_use_statsapi(flipped, already_using=True) is True  # stay on MLB
    assert sa.should_use_statsapi(flipped, already_using=False) is False  # a covered game's first seconds
    assert sa.should_use_statsapi(dict(flipped, plays=[{"id": "1"}]), already_using=True) is False


# ---------------------------------------------------------------------------
# Game matching
# ---------------------------------------------------------------------------


def _schedule(*games: tuple[int, int, int, str]) -> dict:
    return {
        "dates": [
            {
                "games": [
                    {
                        "gamePk": pk,
                        "gameDate": date,
                        "teams": {"away": {"team": {"id": away}}, "home": {"team": {"id": home}}},
                    }
                    for pk, away, home, date in games
                ]
            }
        ]
    }


def test_find_game_pk_matches_teams_via_id_map():
    # ESPN NYY=10 @ TB=30 -> MLB 147 @ 139.
    schedule = _schedule((849839, 147, 139, "2026-10-06T00:00:00Z"), (849834, 145, 114, "2026-10-05T21:00:00Z"))
    assert sa.find_game_pk(schedule, "10", "30", "2026-10-06T00:00Z") == 849839


def test_find_game_pk_picks_the_nearer_doubleheader_game():
    schedule = _schedule((1, 147, 139, "2026-07-04T17:05:00Z"), (2, 147, 139, "2026-07-04T23:10:00Z"))
    assert sa.find_game_pk(schedule, "10", "30", "2026-07-04T23:10Z") == 2
    assert sa.find_game_pk(schedule, "10", "30", "2026-07-04T17:05Z") == 1


def test_find_game_pk_rejects_wrong_teams_far_times_and_allstar():
    schedule = _schedule((9, 147, 139, "2026-10-06T00:00:00Z"))
    assert sa.find_game_pk(schedule, "30", "10", "2026-10-06T00:00Z") is None  # home/away swapped
    assert sa.find_game_pk(schedule, "10", "30", "2026-10-07T00:00Z") is None  # a day off
    assert sa.find_game_pk(schedule, "31", "32", "2026-07-14T00:00Z") is None  # All-Star pseudo-teams


def test_schedule_window_spans_the_utc_day_before():
    # A 5 pm Pacific start is 00:00Z the next day; MLB files it under the 5th.
    assert sa.schedule_window("2026-10-06T00:00Z") == ("2026-10-05", "2026-10-06")
    assert sa.schedule_window("") is None


def test_team_map_covers_every_club_both_ways():
    assert len(sa.ESPN_TO_MLB_TEAM_ID) == 30
    assert len(sa.MLB_TO_ESPN_TEAM_ID) == 30


# ---------------------------------------------------------------------------
# Text and names
# ---------------------------------------------------------------------------


def test_normalize_name_ignores_accents_case_punctuation_and_suffix():
    assert sa.normalize_name("Luis García Jr.") == sa.normalize_name("Luis Garcia Jr") == "luis garcia"
    assert sa.normalize_name("Ji-Hwan Bae") == "ji hwan bae"
    assert sa.normalize_name("Ken Griffey III") == "ken griffey"


def test_past_tense_matches_espn_wording():
    assert sa.past_tense("Junior Caminero grounds out, shortstop X to first.") == (
        "Junior Caminero grounded out, shortstop X to first."
    )
    assert sa.past_tense("Yandy Diaz called out on strikes.") == "Yandy Diaz struck out looking."
    assert sa.past_tense("Ben Rice homers (3) on a fly ball.") == "Ben Rice homered (3) on a fly ball."
    assert sa.past_tense("Chandler Simpson steals (1) 2nd base.") == "Chandler Simpson stole (1) 2nd base."
    assert "home run" in sa.past_tense("Mookie Betts hits a grand slam (3) to left.")


# ---------------------------------------------------------------------------
# Oracle: the same complete game through both sources
# ---------------------------------------------------------------------------


def _at_bats(summary: dict) -> list[tuple[str, int, str]]:
    return [
        (
            p["period"]["type"],
            p["period"]["number"],
            next(x["athlete"]["id"] for x in p["participants"] if x["type"] == "batter"),
        )
        for p in summary["plays"]
        if (p.get("type") or {}).get("type") == "start-batterpitcher"
    ]


def test_oracle_every_at_bat_matches_espn_batter_for_batter():
    espn, mlb = _cle_oracle()
    assert _at_bats(mlb) == _at_bats(espn)
    assert len(_at_bats(espn)) == 71


def test_oracle_every_player_resolves_to_an_espn_id():
    _espn, mlb = _cle_oracle()
    ids = {x["athlete"]["id"] for p in mlb["plays"] for x in p["participants"]}
    assert ids and not [i for i in ids if sa.is_mlb_id(i)]


def test_oracle_box_score_lines_match_espn():
    espn, mlb = _cle_oracle()
    ours = Coord._normalize_lineups(mlb, "", None, False)
    theirs = Coord._normalize_lineups(espn, "", None, False)
    for side in ("away", "home"):
        hit_keys = ("id", "bat_order", "ab", "r", "h", "rbi", "hr", "bb", "k", "starter", "active")
        assert [tuple(h[k] for k in hit_keys) for h in ours[side]["hitters"]] == [
            tuple(h[k] for k in hit_keys) for h in theirs[side]["hitters"]
        ]
        pit_keys = ("id", "ip", "h", "r", "er", "bb", "k", "pc", "starter", "active")
        assert [tuple(p[k] for k in pit_keys) for p in ours[side]["pitchers"]] == [
            tuple(p[k] for k in pit_keys) for p in theirs[side]["pitchers"]
        ]


def test_oracle_play_results_and_scoring_plays_line_up():
    espn, mlb = _cle_oracle()

    def results(s: dict) -> int:
        return sum(1 for p in s["plays"] if (p.get("type") or {}).get("type") == "play-result")

    assert results(mlb) == results(espn)
    assert len(Coord._normalize_scoring_plays(mlb)) == len(Coord._normalize_scoring_plays(espn))


def test_oracle_batter_outcomes_agree_where_espn_can_read_them():
    espn, mlb = _cle_oracle()
    batters = {b for _h, _i, b in _at_bats(espn)}
    for batter in batters:
        theirs = Coord._extract_batter_game_outcomes(espn, batter)
        ours = Coord._extract_batter_game_outcomes(mlb, batter)
        if not theirs:
            # ESPN's own text misses accented names ("Ramírez" vs "Ramirez");
            # MLB's copy is the better read there, not a regression.
            continue
        # The two scorers occasionally differ on fly ball vs pop-up.
        assert [o.replace("PO", "FO") for o in ours] == [o.replace("PO", "FO") for o in theirs], batter


# ---------------------------------------------------------------------------
# The uncovered game, through the real normalizers
# ---------------------------------------------------------------------------


def _ctx(summary: dict) -> dict:
    comp = Coord._resolve_display_comp(summary, str(summary.get("id") or ""), None)
    return Coord._normalize_inning_context(summary, comp)


def test_uncovered_game_fills_batter_pitcher_and_box_score():
    espn, feed = _tb_end_of_b6()
    s = sa.summary_from_statsapi(espn, feed)
    batter_id, pitcher_id = Coord._resolve_batter_pitcher_ids(s)
    batter = Coord._normalize_current_batter(s, batter_id)
    pitcher = Coord._normalize_current_pitcher(s, pitcher_id)
    assert batter["display_name"] == "Junior Caminero"
    assert batter_id == "4905921"  # ESPN's own id, so ESPN headshot / popup
    assert pitcher["display_name"] == "John Schreiber"
    # ESPN never listed this reliever: synthetic id, MLB headshot, no popup.
    assert sa.is_mlb_id(pitcher_id)
    assert urlparse(pitcher["headshot"]).hostname == "img.mlbstatic.com"
    stats = Coord._normalize_batter_stats(s, batter_id, {}, is_live=True)
    assert stats["hits_ab"] == "2-4"
    lineups = Coord._normalize_lineups(s, batter_id, _ctx(s), True)
    assert len(lineups["home"]["hitters"]) == 9 and lineups["home"]["is_batting"] is True


def test_uncovered_game_keeps_espn_ids_for_every_listed_starter():
    espn, feed = _tb_end_of_b6()
    s = sa.summary_from_statsapi(espn, feed)
    espn_starters = {e["athlete"]["id"] for r in espn["rosters"] for e in r["roster"]}
    ours = {
        h["athlete"]["id"]
        for p in s["boxscore"]["players"]
        for b in p["statistics"]
        if b["type"] == "batting"
        for h in b["athletes"]
    }
    assert espn_starters <= ours


def test_uncovered_game_mid_at_bat_count_and_pitches():
    espn, feed = _tb_mid_at_bat()
    s = sa.summary_from_statsapi(espn, feed)
    ctx = _ctx(s)
    situation = Coord._normalize_situation(s)
    assert (situation["balls"], situation["strikes"], situation["outs"]) == (1, 2, 2)
    pitches = Coord._normalize_current_pitches(s, ctx)
    assert len(pitches) == 3
    assert all(p["text"].startswith("Pitch ") for p in pitches)
    assert pitches[-1]["strikes"] == 2
    batter_id, _pitcher_id = Coord._resolve_batter_pitcher_ids(s)
    assert Coord._normalize_current_batter(s, batter_id)["display_name"] == "Austin Wells"


def test_espn_fields_that_were_published_are_kept():
    espn, feed = _tb_end_of_b6()
    s = sa.summary_from_statsapi(espn, feed)
    assert s["id"] == espn["id"]
    ours, theirs = s["header"]["competitions"][0], espn["header"]["competitions"][0]
    assert ours["competitors"] == theirs["competitors"]  # the score stays ESPN's
    assert ours["status"]["type"]["state"] == theirs["status"]["type"]["state"] == "in"


def test_inning_follows_mlb_when_espn_status_lags():
    # Captured 19:34:55: MLB had moved to the bottom of the 7th while ESPN's
    # status still read "Top 7th".
    espn = _load("espn_summary_401907986_t7_mid_at_bat.json")
    feed = _load("mlb_feed_849839_t7_mid_at_bat.json")
    feed["liveData"]["linescore"].update({"inningState": "Middle", "currentInning": 7})
    s = sa.summary_from_statsapi(espn, feed)
    status = s["header"]["competitions"][0]["status"]
    assert (status["periodPrefix"], status["period"], status["type"]["shortDetail"]) == ("Mid", 7, "Mid 7th")
    assert _ctx(s)["is_between_halves"] is True
    # ESPN's own dict is not mutated.
    assert espn["header"]["competitions"][0]["status"]["periodPrefix"] != "Mid"


# ---------------------------------------------------------------------------
# Small synthetic shapes
# ---------------------------------------------------------------------------


def _feed_with(plays: list[dict], linescore: dict | None = None) -> dict:
    return {
        "gameData": {
            "game": {"pk": 1},
            "teams": {"away": {"id": 147}, "home": {"id": 139}},
            "players": {
                "ID1": {"id": 1, "fullName": "Al Batter", "firstName": "Al", "lastName": "Batter"},
                "ID2": {"id": 2, "fullName": "Pat Pitcher", "firstName": "Pat", "lastName": "Pitcher"},
            },
        },
        "liveData": {
            "plays": {"allPlays": plays},
            "linescore": linescore or {},
            "boxscore": {
                "teams": {
                    "away": {
                        "battingOrder": [1],
                        "batters": [1],
                        "pitchers": [],
                        "players": {"ID1": {"battingOrder": "100"}},
                    },
                    "home": {"battingOrder": [], "batters": [], "pitchers": [2], "players": {"ID2": {}}},
                }
            },
        },
    }


def _at_bat(event_type: str, description: str, complete: bool = True) -> dict:
    return {
        "about": {"atBatIndex": 0, "inning": 3, "isTopInning": True, "isComplete": complete},
        "matchup": {"batter": {"id": 1, "fullName": "Al Batter"}, "pitcher": {"id": 2, "fullName": "Pat Pitcher"}},
        "result": {
            "event": event_type,
            "eventType": event_type,
            "description": description,
            "awayScore": 0,
            "homeScore": 0,
        },
        "count": {"balls": 0, "strikes": 0, "outs": 3},
        "playEvents": [],
    }


def _types_of(summary: dict) -> list[str]:
    return [p["type"]["type"] for p in summary["plays"]]


def test_completed_at_bat_gets_result_and_end_marker():
    s = sa.summary_from_statsapi({}, _feed_with([_at_bat("field_out", "Al Batter flies out to center.")]))
    assert _types_of(s) == ["start-batterpitcher", "play-result", "end-batterpitcher"]
    assert s["plays"][1]["text"] == "Batter flied out to center."


def test_out_on_the_bases_leaves_the_at_bat_open_like_espn():
    # Caught stealing for the third out: the batter keeps his plate
    # appearance and leads off next inning, which ESPN marks by emitting no
    # End Batter/Pitcher -- `_last_batter_of_half` reads exactly that.
    s = sa.summary_from_statsapi({}, _feed_with([_at_bat("caught_stealing_2b", "Runner caught stealing 2nd base.")]))
    assert _types_of(s) == ["start-batterpitcher", "play-result"]
    _batter, completed = Coord._last_batter_of_half(s, "top", 3)
    assert completed is False


def test_in_progress_at_bat_has_no_result():
    s = sa.summary_from_statsapi({}, _feed_with([_at_bat("", "", complete=False)]))
    assert _types_of(s) == ["start-batterpitcher"]


def test_unmatched_player_gets_synthetic_id_and_espn_style_short_name():
    s = sa.summary_from_statsapi({}, _feed_with([_at_bat("field_out", "Al Batter flies out.")]))
    batter = next(x["athlete"]["id"] for x in s["plays"][0]["participants"] if x["type"] == "batter")
    assert batter == "mlb-1"
    athlete = Coord._find_any_athlete(s, batter)
    assert athlete["shortName"] == "A. Batter"


def test_between_halves_keeps_the_third_out_batter_and_lists_due_up():
    feed = _feed_with(
        [_at_bat("field_out", "Al Batter flies out.")],
        {"inningState": "Middle", "outs": 3, "offense": {"batter": {"id": 2}}, "defense": {"pitcher": {"id": 1}}},
    )
    situation = sa.summary_from_statsapi({}, feed)["situation"]
    # Not the leadoff man MLB already rolled `offense` to: the coordinator's
    # stale-situation bridge compares against the batter who made the out.
    assert situation["batter"] == {"playerId": "mlb-1"}
    assert [d["playerId"] for d in situation["dueUp"]] == ["mlb-2"]


# ---------------------------------------------------------------------------
# Coordinator wiring
# ---------------------------------------------------------------------------


def _coord(options: dict | None = None, responses: dict | None = None) -> Coord:
    coord = Coord.__new__(Coord)
    coord.entry = SimpleNamespace(options=options or {})
    coord.team_abbr = "TB"
    coord._statsapi_game_pk_cache = {}
    coord._statsapi_feed_cache = None
    coord._abs_challenges_cache = None
    coord._team_roster_cache = {}
    coord._data_source_logged = None
    calls: list[str] = []

    async def fake_get_json(url: str) -> dict:
        calls.append(url)
        for fragment, payload in (responses or {}).items():
            if fragment in url:
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise RuntimeError(f"unexpected {url}")

    coord._get_json = fake_get_json  # type: ignore[method-assign]
    coord.calls = calls  # type: ignore[attr-defined]
    return coord


def test_preference_defaults_to_mlb():
    assert _coord()._prefer_mlb() is True
    assert _coord({OPT_DATA_SOURCE_PREFERENCE: "espn"})._prefer_mlb() is False
    assert _coord({OPT_DATA_SOURCE_PREFERENCE: "mlb"})._prefer_mlb() is True


def test_mlb_preferred_always_tries_mlb_even_when_espn_is_complete():
    espn = _load("espn_summary_401907991_full.json")
    assert sa.wants_statsapi(espn, prefer_mlb=True, already_using=False) is True
    assert sa.wants_statsapi(espn, prefer_mlb=False, already_using=False) is False


def test_espn_preferred_uses_mlb_only_as_the_fallback():
    espn, _feed = _tb_end_of_b6()
    assert sa.wants_statsapi(espn, prefer_mlb=False, already_using=False) is True
    assert sa.wants_statsapi(dict(espn, plays=[{"id": "1"}]), prefer_mlb=False, already_using=True) is False


def test_mlb_preferred_on_a_fully_covered_game_keeps_espn_ids():
    # MLB preferred replaces ESPN's own plays on a covered game; every player
    # must still resolve to ESPN's id, or headshots/popups would degrade.
    espn = _load("espn_summary_401907991_full.json")
    full = sa.summary_from_statsapi(espn, _load("mlb_feed_849834_final.json"))
    assert _at_bats(full) == _at_bats(espn)
    ids = {x["athlete"]["id"] for p in full["plays"] for x in p["participants"]}
    assert not [i for i in ids if sa.is_mlb_id(i)]


def test_statsapi_summary_end_to_end_and_game_pk_cached():
    espn, feed = _tb_end_of_b6()
    comp = espn["header"]["competitions"][0]
    schedule = _schedule((849839, 147, 139, "2026-10-06T00:00:00Z"))
    coord = _coord(responses={"/schedule": schedule, "/feed/live": feed})
    out = asyncio.run(coord._statsapi_summary("401907986", espn, comp))
    assert out is not None and out["plays"]
    asyncio.run(coord._statsapi_summary("401907986", espn, comp))
    assert sum("/schedule" in u for u in coord.calls) == 1  # looked up once per game
    assert sum("/feed/live" in u for u in coord.calls) == 2  # fetched every poll


def test_statsapi_summary_failures_fall_back_to_espn():
    espn, _feed = _tb_end_of_b6()
    comp = espn["header"]["competitions"][0]
    no_game = _coord(responses={"/schedule": {"dates": []}})
    assert asyncio.run(no_game._statsapi_summary("401907986", espn, comp)) is None
    feed_down = _coord(
        responses={
            "/schedule": _schedule((849839, 147, 139, "2026-10-06T00:00:00Z")),
            "/feed/live": RuntimeError("503"),
        }
    )
    assert asyncio.run(feed_down._statsapi_summary("401907986", espn, comp)) is None


def test_feed_failure_reuses_the_last_good_copy_briefly():
    _espn, feed = _tb_end_of_b6()
    coord = _coord(responses={"/feed/live": feed})
    assert asyncio.run(coord._statsapi_feed(849839)) is feed
    coord._get_json = _coord(responses={"/feed/live": RuntimeError("503")})._get_json  # type: ignore[method-assign]
    assert asyncio.run(coord._statsapi_feed(849839)) is feed
    gpk, _ts, cached = coord._statsapi_feed_cache
    coord._statsapi_feed_cache = (gpk, 0.0, cached)  # long expired
    assert asyncio.run(coord._statsapi_feed(849839)) is None


def test_espn_athlete_fetches_skip_synthetic_ids():
    coord = _coord()
    assert asyncio.run(coord._get_public_batter_stats("mlb-670167")) == {}
    assert asyncio.run(coord._get_public_pitcher_stats("mlb-670167")) == {}
    assert asyncio.run(coord._get_player_card("mlb-670167")) == {}
    assert asyncio.run(coord._get_one_season_line("mlb-670167")) == {}
    assert coord.calls == []


def test_runner_suffix_is_not_doubled_when_last_name_already_has_it():
    # ESPN's roster lastName is "Mesa Jr."; the runner label once read "Mesa Jr. Jr.".
    summary = {
        "rosters": [{"roster": [{"athlete": {"id": "7", "displayName": "Victor Mesa Jr.", "lastName": "Mesa Jr."}}]}],
        "situation": {"onFirst": {"playerId": "7"}},
    }
    assert Coord._normalize_situation(summary)["first_last_name"] == "Mesa Jr."


def test_break_shows_due_up_in_batting_order():
    # Captured 19:46:58 at the end of the 7th: Wells (8th) made the last out of
    # the top half, so the Yankees' 8th opens 9-1-2.
    espn = _load("espn_summary_401907986_end7.json")
    feed = _load("mlb_feed_849839_end7.json")
    s = sa.summary_from_statsapi(espn, feed)
    ctx = _ctx(s)
    assert ctx["is_between_halves"] is True
    names = [d["short_name"] for d in Coord._normalize_due_up(s, ctx)]
    assert names == ["R. McMahon", "B. Rice", "C. Bellinger"]


def test_substitutions_read_like_espn():
    # ESPN's own texts for the CLE game's changes, against MLB's sentences.
    espn, mlb = _cle_oracle()

    def changes(summary: dict) -> list[str]:
        return [p["text"] for p in summary["plays"] if (p.get("alternativeType") or {}).get("type") == "lineup-change"]

    assert sorted(changes(mlb)) == sorted(changes(espn))


# ---------------------------------------------------------------------------
# ABS (ball-strike) challenges
# ---------------------------------------------------------------------------


def _abs_feed(remaining: tuple[int, int] = (2, 2), reviews: list[dict] | None = None) -> dict:
    return {
        "gameData": {
            "teams": {"away": {"id": 147}, "home": {"id": 139}},
            "absChallenges": {
                "hasChallenges": True,
                "away": {"usedSuccessful": 0, "usedFailed": 2 - remaining[0], "remaining": remaining[0]},
                "home": {"usedSuccessful": 0, "usedFailed": 2 - remaining[1], "remaining": remaining[1]},
            },
        },
        "liveData": {"plays": {"currentPlay": {"playEvents": [{"reviewDetails": r} for r in reviews or []]}}},
    }


def test_abs_challenges_read_mlb_counts():
    # The real filtered response: each side won its only challenge, so both keep 2.
    out = sa.abs_challenges(_load("mlb_abs_849839_final.json"))
    assert out == {
        "has_challenges": True,
        "away": {"remaining": 2, "used_successful": 1, "used_failed": 0, "in_progress": False},
        "home": {"remaining": 2, "used_successful": 1, "used_failed": 0, "in_progress": False},
    }


def test_abs_challenges_empty_without_abs():
    assert sa.abs_challenges({}) == {}
    assert sa.abs_challenges({"gameData": {"absChallenges": {"hasChallenges": False}}}) == {}


def test_abs_challenge_in_progress_marks_only_the_challenging_side():
    reviews = [
        {"inProgress": True, "reviewType": "MJ", "challengeTeamId": 139},
        # A finished ABS review and a pending manager replay review don't count.
        {"inProgress": False, "reviewType": "MJ", "challengeTeamId": 147},
        {"inProgress": True, "reviewType": "MA", "challengeTeamId": 147},
    ]
    out = sa.abs_challenges(_abs_feed((1, 2), reviews))
    assert out["away"]["remaining"] == 1 and out["away"]["used_failed"] == 1
    assert out["away"]["in_progress"] is False
    assert out["home"]["in_progress"] is True


def test_abs_challenges_poll_the_filtered_feed_on_espn():
    espn, _feed = _tb_end_of_b6()
    comp = espn["header"]["competitions"][0]
    schedule = _schedule((849839, 147, 139, "2026-10-06T00:00:00Z"))
    coord = _coord(responses={"/schedule": schedule, "/feed/live?fields=": _abs_feed((1, 2))})
    out = asyncio.run(coord._abs_challenges("401907986", comp, sa.DATA_SOURCE_ESPN))
    assert out["away"]["remaining"] == 1
    assert [u for u in coord.calls if "/feed/live" in u] == [sa.STATSAPI_CHALLENGES_URL.format(game_pk=849839)]


def test_abs_challenges_reuse_the_full_feed_when_mlb_is_the_source():
    espn, _feed = _tb_end_of_b6()
    comp = espn["header"]["competitions"][0]
    coord = _coord(responses={"/schedule": _schedule((849839, 147, 139, "2026-10-06T00:00:00Z"))})
    coord._statsapi_feed_cache = (849839, 0.0, _abs_feed((2, 0)))
    out = asyncio.run(coord._abs_challenges("401907986", comp, sa.DATA_SOURCE_MLB))
    assert out["home"]["remaining"] == 0
    assert not [u for u in coord.calls if "/feed/live" in u]


def test_abs_challenge_failure_reuses_the_last_good_count_briefly():
    espn, _feed = _tb_end_of_b6()
    comp = espn["header"]["competitions"][0]
    schedule = _schedule((849839, 147, 139, "2026-10-06T00:00:00Z"))
    coord = _coord(responses={"/schedule": schedule, "/feed/live": _abs_feed((1, 1))})
    good = asyncio.run(coord._abs_challenges("401907986", comp, sa.DATA_SOURCE_ESPN))
    coord._get_json = _coord(responses={"/feed/live": RuntimeError("503")})._get_json  # type: ignore[method-assign]
    assert asyncio.run(coord._abs_challenges("401907986", comp, sa.DATA_SOURCE_ESPN)) == good
    gpk, _ts, cached = coord._abs_challenges_cache
    coord._abs_challenges_cache = (gpk, 0.0, cached)  # long expired
    assert asyncio.run(coord._abs_challenges("401907986", comp, sa.DATA_SOURCE_ESPN)) == {}


# ---------------------------------------------------------------------------
# ESPN-style play text
# ---------------------------------------------------------------------------


def _text_roster() -> sa._Roster:
    people = {
        10: ("Kyle Tucker", "Kyle", "Tucker", "away"),
        11: ("Yoshinobu Yamamoto", "Yoshinobu", "Yamamoto", "away"),
        12: ("Braden Montgomery", "Braden", "Montgomery", "away"),
        13: ("Colson Montgomery", "Colson", "Montgomery", "away"),
        20: ("Mauricio Dubón", "Mauricio", "Dubón", "home"),
        21: ("Ronald Acuña Jr.", "Ronald", "Acuña Jr.", "home"),
        22: ("Michael Harris II", "Michael", "Harris II", "home"),
        23: ("José Ramírez", "José", "Ramírez", "home"),
        24: ("Chris Sale", "Chris", "Sale", "home"),
    }
    feed = {
        "gameData": {
            "teams": {
                "away": {"id": 119, "name": "Los Angeles Dodgers", "teamName": "Dodgers"},
                "home": {"id": 144, "name": "Atlanta Braves", "teamName": "Braves"},
            },
            "players": {
                f"ID{pid}": {"id": pid, "fullName": full, "firstName": first, "lastName": last}
                for pid, (full, first, last, _side) in people.items()
            },
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    side: {"players": {f"ID{pid}": {} for pid, p in people.items() if p[3] == side}}
                    for side in ("away", "home")
                }
            }
        },
    }
    return sa._Roster({}, feed)


def _espn(text: str, batter: int | None = None, distance: int | None = None) -> str:
    return sa.espn_play_text(text, _text_roster(), batter, distance)


def test_espn_text_hits_and_steals():
    # The two Tucker plays from LAD @ ATL, NLDS 2026-10-06, as ESPN wrote them.
    assert _espn("Kyle Tucker singles on a line drive to left fielder Mauricio Dubón.", 10) == "Tucker singled to left."
    assert _espn("Kyle Tucker steals (2) 2nd base.") == "Tucker stole second."


def test_espn_text_outs_keep_only_the_first_fielder():
    assert (
        _espn("José Ramírez grounds out sharply, shortstop Kyle Tucker to first baseman Chris Sale.", 23)
        == "Ramírez grounded out to shortstop."
    )
    assert _espn("Jose Ramirez pops out to third baseman Kyle Tucker in foul territory.", 23) == (
        "Ramírez fouled out to third."
    )


def test_espn_text_runners_fold_into_one_sentence():
    assert (
        _espn("Michael Harris II doubles (1) on a sharp line drive to right fielder Kyle Tucker. "
              "José Ramírez scores. Ronald Acuña Jr. scores. Mauricio Dubón to 3rd.", 22)
        == "Harris II doubled to right, Ramírez scored and Acuña Jr. scored, Dubón to third."
    )


def test_espn_text_double_play_drops_the_batters_own_out():
    assert (
        _espn("José Ramírez grounds into a double play, second baseman Kyle Tucker to first baseman Chris Sale. "
              "Mauricio Dubón out at 2nd. José Ramírez out at 1st.", 23)
        == "Ramírez grounded into double play, second to first, Dubón out at second."
    )


def test_espn_text_home_run_distance_and_shared_last_names():
    assert (
        _espn("Braden Montgomery homers (12) on a fly ball to right center field. Colson Montgomery scores.", 12, 412)
        == "B. Montgomery homered to right center (412 feet), C. Montgomery scored."
    )


def test_espn_text_wild_pitch_credits_each_runner():
    assert (
        _espn("Wild pitch by pitcher Chris Sale. Kyle Tucker to 3rd.")
        == "Tucker to third on wild pitch by Sale."
    )


def test_espn_text_infield_and_bunt_singles():
    assert _espn("Michael Harris II singles on a soft bunt ground ball to pitcher Yoshinobu Yamamoto.", 22) == (
        "Harris II reached on bunt single to pitcher."
    )
    assert _espn("Kyle Tucker singles on a ground ball to first baseman Chris Sale.", 10) == (
        "Tucker reached on infield single to first."
    )


def test_espn_text_challenge_leads_with_the_play_and_credits_the_team():
    assert (
        _espn("Ronald Acuña Jr. challenged (pitch result), call on the field was confirmed: "
              "Ronald Acuña Jr. called out on strikes.", 21)
        == "Acuña Jr. struck out looking. Atlanta Braves challenged: call on the field was upheld."
    )
    assert _espn("Dodgers challenged (tag play), call on the field was overturned: Kyle Tucker steals (3) 3rd base.") == (
        "Tucker stole third. Los Angeles Dodgers challenged: call on the field was overturned."
    )


def test_espn_text_unrecognised_wording_is_kept_but_shortened():
    assert _espn("Throwing error by pitcher Chris Sale on the pickoff attempt.") == (
        "Throwing error by pitcher Sale on the pickoff attempt."
    )


def test_oracle_play_text_reads_like_espn():
    # CHW @ CLE was covered by both feeds: nearly every translated play must
    # match ESPN's text exactly. The few left differ only in runner order or
    # error wording ("reached on" vs ESPN's "safe at first").
    espn, mine = _cle_oracle()

    def texts(s: dict) -> list[tuple]:
        return [
            (p["period"]["type"], p["period"]["number"], p.get("text", ""))
            for p in s["plays"]
            if p["type"]["text"] not in ("Start Inning", "End Inning", "Start Batter/Pitcher", "End Batter/Pitcher")
            and not p.get("text", "").startswith("Pitch ")
        ]

    espn_texts = set(texts(espn))
    ours = texts(mine)
    exact = sum(t in espn_texts for t in ours)
    assert exact >= 79, f"{exact}/{len(ours)}"


# ---------------------------------------------------------------------------
# ESPN team rosters: every player keeps an ESPN id
# ---------------------------------------------------------------------------


def _espn_roster_payload(*athletes: tuple[str, str, str]) -> dict:
    return {
        "athletes": [
            {
                "position": "Pitchers",
                "items": [
                    {
                        "id": aid,
                        "fullName": name,
                        "displayName": name,
                        "shortName": f"{name[0]}. {name.split()[-1]}",
                        "lastName": name.split()[-1],
                        "headshot": {"href": f"https://a.espncdn.com/{aid}.png"},
                        "position": {"abbreviation": pos},
                        "links": [{"href": "dropped"}],
                    }
                    for aid, name, pos in athletes
                ],
            }
        ]
    }


def test_flatten_espn_roster_trims_to_matching_fields():
    out = sa.flatten_espn_roster(_espn_roster_payload(("4242", "Ray Kerr", "RP")))
    assert out == [
        {
            "id": "4242",
            "fullName": "Ray Kerr",
            "displayName": "Ray Kerr",
            "shortName": "R. Kerr",
            "lastName": "Kerr",
            "headshot": {"href": "https://a.espncdn.com/4242.png", "alt": "Ray Kerr"},
            "position": {"abbreviation": "RP"},
        }
    ]
    assert sa.flatten_espn_roster(None) == []


def test_team_roster_gives_a_player_espn_has_not_listed_yet_an_espn_id():
    # A reliever MLB already shows but ESPN's summary doesn't list yet.
    feed = _feed_with([])
    feed["gameData"]["players"]["ID3"] = {"id": 3, "fullName": "Ray Kerr", "firstName": "Ray", "lastName": "Kerr"}
    feed["liveData"]["boxscore"]["teams"]["home"]["players"]["ID3"] = {}
    home_espn = sa.MLB_TO_ESPN_TEAM_ID[139]
    assert sa.is_mlb_id(sa._Roster({}, feed).espn_id(3))
    rosters = {home_espn: sa.flatten_espn_roster(_espn_roster_payload(("4242", "Ray Kerr", "RP")))}
    assert sa._Roster({}, feed, rosters).espn_id(3) == "4242"


def test_statsapi_summary_fetches_both_rosters_once_per_hour():
    espn, feed = _tb_end_of_b6()
    comp = espn["header"]["competitions"][0]
    schedule = _schedule((849839, 147, 139, "2026-10-06T00:00:00Z"))
    coord = _coord(responses={"/schedule": schedule, "/feed/live": feed, "/roster": _espn_roster_payload()})
    assert asyncio.run(coord._statsapi_summary("401907986", espn, comp)) is not None
    asyncio.run(coord._statsapi_summary("401907986", espn, comp))
    assert sum("/roster" in u for u in coord.calls) == 2  # away + home, then cached



# ---------------------------------------------------------------------------
# Score in sync with MLB's plays
# ---------------------------------------------------------------------------


def _espn_header(away_score: str, home_score: str, state: str = "in") -> dict:
    return {
        "competitions": [
            {
                "status": {"period": 5, "type": {"state": state, "detail": "Top 5th"}},
                "competitors": [
                    {"homeAway": "home", "score": home_score, "hits": 2, "errors": 0, "linescores": []},
                    {"homeAway": "away", "score": away_score, "hits": 3, "errors": 0, "linescores": []},
                ],
            }
        ]
    }


def _mlb_linescore_feed() -> dict:
    # Shape of LAD @ ATL's feed (2026-10-06): mid-5th, the home side has
    # batted 4 innings; Dodgers just scored the run ESPN hasn't shown yet.
    innings = [{"num": n, "away": {"runs": 0, "hits": 1, "errors": 0}, "home": {"runs": 0, "hits": 0, "errors": 1}} for n in range(1, 5)]
    innings[3]["away"]["runs"] = 0
    innings.append({"num": 5, "away": {"runs": 2, "hits": 2, "errors": 0}, "home": {}})
    return {
        "liveData": {
            "linescore": {
                "currentInning": 5,
                "inningState": "Middle",
                "teams": {"away": {"runs": 2, "hits": 6, "errors": 0}, "home": {"runs": 0, "hits": 0, "errors": 4}},
                "innings": innings,
            }
        }
    }


def test_score_hits_errors_and_innings_follow_mlb():
    header = sa._header_with_mlb_live_state(_espn_header("1", "0"), _mlb_linescore_feed())
    comp = header["competitions"][0]
    away = next(c for c in comp["competitors"] if c["homeAway"] == "away")
    home = next(c for c in comp["competitors"] if c["homeAway"] == "home")
    assert (away["score"], away["hits"], away["errors"]) == ("2", 6, 0)
    assert (home["score"], home["hits"], home["errors"]) == ("0", 0, 4)
    assert [i["displayValue"] for i in away["linescores"]] == ["0", "0", "0", "0", "2"]
    assert len(home["linescores"]) == 4  # hasn't batted in the 5th yet
    # The inning follows MLB too; live/final state stays ESPN's.
    assert (comp["status"]["period"], comp["status"]["periodPrefix"]) == (5, "Mid")
    assert comp["status"]["type"]["state"] == "in"


def test_score_stays_espns_without_mlb_totals():
    feed = {"liveData": {"linescore": {"currentInning": 5, "inningState": "Top"}}}
    comp = sa._header_with_mlb_live_state(_espn_header("1", "0"), feed)["competitions"][0]
    assert [c["score"] for c in comp["competitors"]] == ["0", "1"]


def test_espn_header_is_not_mutated():
    espn = _espn_header("1", "0")
    sa._header_with_mlb_live_state(espn, _mlb_linescore_feed())
    assert espn["competitions"][0]["competitors"][1]["score"] == "1"
