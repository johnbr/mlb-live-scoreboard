# Plan: MLB Stats API fallback when ESPN has no play-by-play

**Status: IMPLEMENTED 2026-10-05** (`statsapi.py`); the design record is `ARCHITECTURE.md` → "MLB Stats API fallback". Two changes from this plan, both found live: the status block's *inning* is also taken from MLB (ESPN's lagged), and substitutions are reworded into ESPN's form.

## The problem, as observed

2026-10-05, ALDS Game 2, NYY @ TB (ESPN event `401907986`, MLB gamePk
`849839`). Bottom 6th, Rays 5–1, and the expanded live card was blank: no
batter, no pitcher, no count, no plays, box score all `--`.

The integration was doing its job. ESPN had nothing for that game:

| ESPN `summary` field | NYY @ TB (blank) | CHW @ CLE, same day (fine) |
|---|---|---|
| `header.competitions[0].playByPlaySource` | `"none"` | `"full"` |
| `header.competitions[0].boxscoreSource` | `"none"` | `"full"` |
| `plays` / `atBats` / `playsMap` | 0 / 0 / 0 | 561 / 71 / … |
| `situation` | absent | (final) |
| box score stat cells | all `--` | real |
| `rosters` | starting nine only | full |
| `winprobability` | empty | populated |

ESPN still published the score, the inning, linescore runs and the series line.
Everything else the live view draws was missing. The scoreboard and CDN
(`cdn.espn.com/core/mlb/game`) feeds were empty the same way. Every other
postseason game checked (SD @ MIL, ATL @ LAD on 10-04, CHW @ CLE on 10-05)
had full play-by-play, so this is an ESPN coverage gap on one game, not an
API change.

MLB's own feed had all of it at the same moment
(`statsapi.mlb.com/api/v1.1/game/849839/feed/live`): Caminero batting,
Schreiber pitching, 0-2 count, 2 outs, 53 plays.

## Goal

When ESPN reports **no play-by-play for a live game**, fill the live-view
fields from MLB's Stats API, so the card renders normally. Nothing changes
for games ESPN covers, and nothing in the card changes.

Out of scope: win probability (MLB has an endpoint, but it is a separate
feature), and highlights. Both already render as absent when empty.

## Design

### Trigger: ESPN's own flag, not an emptiness guess

Use the fallback only when **all** of these hold:

- the displayed game is live (`is_live`);
- `header.competitions[0].playByPlaySource == "none"`;
- `summary.plays` is empty.

The flag is what ESPN itself says. An empty `plays` on its own also happens in
the first seconds of a game, and that must not trigger the fallback. Requiring
both keeps a covered game on ESPN even if a single poll comes back empty.

Re-check every poll. If ESPN's coverage switches on mid-game (it sometimes
fills in late), the next poll goes back to ESPN with no state to unwind.

### Approach: translate MLB's feed INTO an ESPN-shaped summary

The ~40 `_normalize_*` helpers read a handful of ESPN summary keys: `plays`,
`situation`, `boxscore`, `rosters`. The fallback is **one pure translator**,
`_summary_from_statsapi(espn_summary, mlb_feed) -> dict`. It returns a copy
of the ESPN summary with those keys filled in from MLB's data. Everything
downstream runs unchanged. That includes the at-bat hand-off, due-up
re-anchoring, the third-out hold, current pitches, the lineup popup's Game
view and the inning pager, which reads `_live_summary_cache`.

The rejected alternative was to map MLB data straight onto the output
fields. That would duplicate every normalizer's logic, including the
carry-over at-bat and due-up wrap rules in `ARCHITECTURE.md`, in a code path
that runs a few times a season and would silently rot.

What the translator has to emit. The field list comes from what
`coordinator.py` actually reads:

| ESPN key | Built from (MLB `feed/live`) | Notes |
|---|---|---|
| `plays[]` | `liveData.plays.allPlays[]` → for each at-bat: one `start-batterpitcher`, one per `playEvents[]` pitch (`ball` / `strike-looking` / `strike-swinging` / `foul-ball` / in-play), one `play-result` carrying `text = result.description` + `alternativeType`, one `end-batterpitcher` | Per play: `id`, `type.{type,text}`, `text`, `period.{type: Top/Bottom, number}`, `participants[{type: batter/pitcher, athlete.id}]`, `awayScore`/`homeScore`, `outs`, `scoringPlay`/`scoreValue`, `wallclock`, `pitchCount`, `pitchCoordinate`, `pitchVelocity`/`pitchType`, `batOrder`, `atBatId`. Inning starts and ends come from `about.halfInning` changes. Non-pitch `playEvents` (substitutions, pickoffs, stolen bases) become `type.type` entries with their description |
| `situation` | `liveData.linescore` (`balls`/`strikes`/`outs`, `offense.first/second/third`, `offense.batter`, `defense.pitcher`) and `offense.onDeck` / due-up | Mirror the ESPN keys the normalizers try: `batter.playerId`, `pitcher.playerId`, `onFirst`…, runner refs. Between halves, emit `dueUp` from `linescore.offense` of the team due up |
| `boxscore.players[].statistics[].athletes[]` | `liveData.boxscore.teams.{away,home}.players` (`stats.batting/pitching`, `seasonStats`, `battingOrder`, `gameStatus.isCurrentBatter/isOnBench`) | Fill the stat cells in the **labels order ESPN already declares** in the same summary (`H-AB, AB, R, H, RBI, HR, BB, K, #P, AVG, …`), so `_stat_from_entry` reads them by label. Add the relievers and pinch hitters ESPN didn't list |
| `rosters[].roster[]` | the same boxscore players + `gameData.players` (names, positions) | `batOrder`, `starter`, `active`, `subbedIn`/`subbedOut` |

The ESPN summary keeps everything it **did** publish (header, series,
competitors, linescore, standings), so only the empty parts are filled.

### Player identity: the hard part

Every downstream field keys on **ESPN athlete ids**: headshots, the
career popup, the season-stat fetches, lineup rows. MLB uses MLBAM ids.

1. **Match MLB players to the ESPN ids already in the summary.** In the
   failing game, ESPN's `rosters` and `boxscore` still listed both starting
   nines and the starting and current pitchers, with ids and full names.
   Match within a team on a normalized name: casefold, strip accents (MLB
   sends "Luis García Jr.", ESPN "Luis Garcia Jr."), strip a trailing
   Jr./Sr./II/III. MLB's batting order serves as a tiebreak.
2. **Players ESPN doesn't list** (relievers and subs after the summary froze;
   Schreiber was one) get a synthetic id `mlb-<mlbamId>`, with:
   - headshot `https://img.mlbstatic.com/mlb-photos/image/upload/w_213,q_auto:best/v1/people/<id>/headshot/67/current`;
   - season line taken from that player's `seasonStats` in MLB's boxscore
     rather than ESPN's athlete endpoint. `_get_public_batter_stats` and
     `_get_public_pitcher_stats` **must skip `mlb-` ids**, or they would
     request a 404 from ESPN every 60 s;
   - **the career popup is unavailable** for them. The player-card websocket
     command returns a clean "not available" for an `mlb-` id, and the card
     shows the name unlinked (plain text, not yellow), so a click never
     dead-ends.
3. A matched player keeps his ESPN id, so his popup, headshot and season
   stats stay exactly as on a normal day.

### Matching ESPN's game to MLB's gamePk

`statsapi.mlb.com/api/v1/schedule?sportId=1&date=<local date>` (~1 KB).
Choose the game whose home and away teams match the ESPN competitors and
whose start time is closest to ESPN's `competition.date`, which also
handles doubleheaders.

Team matching uses a **static ESPN team id → MLB team id map** in
`const.py`, beside `MLB_TEAM_MAP`. It doesn't use abbreviations, because the
two disagree (ESPN `CHW` vs MLB `CWS`, `ARI` vs `AZ`, and others). The
All-Star pseudo-teams 31/32 stay unmapped, so the fallback simply doesn't
apply to the All-Star Game.

Cache the event-id → gamePk mapping for the life of the game, so the
schedule call happens once per game, not once per poll.

### Fetch cost

`v1.1/game/<pk>/feed/live` measured **~92 KB compressed** on this game,
about the same as an ESPN summary. MLB's CDN serves it with
`max-age=10, stale-while-revalidate=30`, so at the 5 s live poll about every
other request is a CDN hit. It is fetched **only while the fallback is
active**. On a normal game, the cost is zero extra requests.

If the feed fails, use the last good MLB snapshot for up to ~60 s (the same
spirit as the schedule stale-fallback), then fall back to ESPN's empty
summary, which is exactly today's behavior. **A failed fallback can never
make the card worse than it is now.**

### Visibility

- Add a sensor attribute, `data_source`: `"espn"` or `"mlb_statsapi"`, so a
  dashboard or the user can tell which source is in use.
- Log once at INFO when a game switches to MLB, naming the event and gamePk,
  and once when it switches back.
- **Card:** a small "via MLB" tag in the expanded live view while the
  attribute reads `mlb_statsapi`, so differences between the two sources
  are explained on screen.

### Config

An options-flow toggle, "Use MLB Stats API when ESPN has no play-by-play",
**on by default**. The integration is published via HACS, so it should be
possible to turn off a second, unaffiliated data source.

## Phases

1. **Fixtures (partly done).** Live captures from 10-05 are saved:
   - the ESPN `pbp: none` summary;
   - MLB `feed/live` for gamePk 849839;
   - a full-coverage ESPN summary from the same day;
   - a series of paired ESPN + MLB snapshots, captured every minute through
     the end of the game, which covers between-halves and mid-at-bat states.

   Trim them to the keys the code reads (per `tests/fixtures/README.md`
   conventions) and commit them. **This is the only time these payloads can
   be captured.**
2. **Pure translator + id matching**, unit-tested against the fixtures. The
   assertions run the translated summary through the existing normalizers
   and check:
   - the current batter and pitcher match MLB's;
   - the count, outs and runners match;
   - the recent plays read like ESPN's;
   - due-up is correct between halves;
   - no `mlb-` id reaches an ESPN fetch.
3. **Wiring:**
   - the trigger check in `_assemble_game_data`, right after the summary
     fetch;
   - gamePk resolution and its cache;
   - the feed fetch with stale fallback;
   - skipping `mlb-` ids in the season-stat fetches;
   - the `data_source` attribute;
   - the options flow;
   - updating `_make_data` in tests if the dataclass gains `data_source`.
4. **Card:** unlinked names for `mlb-` ids, a "not available" result from the
   player-card command, and the optional "via MLB" tag.
5. **Docs + release:** `ARCHITECTURE.md` gets a section on the fallback and
   the endpoints table gains the two MLB URLs. Commit with `feat:` so
   release-please cuts a minor version. Deploy to norm, restart HA, bump the
   card resource.

**Verifying it live is opportunistic.** The fallback path only runs when
ESPN drops a game, which can't be arranged on demand. So the fixture tests
are the real proof. The `data_source` attribute and the INFO log line show
whether it has fired since.

## Risks

- **Two sources disagree at the seams.** The score and linescore stay
  ESPN's; the plays and count come from MLB. MLB's feed can run a few seconds
  ahead of ESPN's score, so the play text can briefly show a run the
  scoreboard hasn't counted. The bus events (`team_scored` etc.) are driven
  by ESPN's score, so they are unaffected. That is acceptable for a
  fallback.
- **Name matching misses.** A miss degrades to an `mlb-` id, which has a
  headshot and stats but no popup. It never produces a wrong player:
  matching runs within a team, and an ambiguous name is treated as no match.
- **MLB changes its feed.** The translator only runs when ESPN has already
  failed, and an unrecognized shape falls through to today's empty card.
- **ESPN partially covers a game** (plays without a box score, say): the
  trigger needs `playByPlaySource == "none"`, so a partial game stays on
  ESPN. Revisit only if it is seen in practice.

## Decisions (2026-10-05)

- **Live games only.** A final game ESPN never covered stays as it is today.
- **The "via MLB" tag ships**: shown in the expanded live view only while
  `data_source` is `mlb_statsapi`.
