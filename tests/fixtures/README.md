# ESPN athlete fixtures (Option B, Chunk 0)

Trimmed real responses captured 2026-05-17 for the in-card player career
popup work (see `OPTION_B_HANDOFF.md`). Used by Chunk 1's pure-parser tests.

## Files

| Athlete | ESPN id | Archetype | Files |
|---|---|---|---|
| Mike Trout | 30836 | Hitter | `athlete_30836_trout_bio.json`, `_stats.json` |
| Clayton Kershaw | 28963 | Pitcher | `athlete_28963_kershaw_bio.json`, `_stats.json` |
| Shohei Ohtani | 39832 | Two-way (listed SP) | `athlete_39832_ohtani_bio.json`, `_stats.json` |

## Provenance / how to regenerate

```
# stats:
https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/{ID}/stats?region=us&lang=en&contentorigin=espn
# bio:
https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/{ID}?region=us&lang=en&contentorigin=espn
```

Trimming applied:
- **bio**: kept the real `{"athlete": {...}}` wrapper (so the fixture
  mirrors the live endpoint and exercises the parser's real code path),
  with the inner object reduced to bio-relevant keys
  (`id, firstName, lastName, displayName, fullName, jersey, headshot,
  position, team, age, displayHeight, displayWeight, displayBirthPlace,
  displayDOB, displayBatsThrows, displayExperience, displayDraft, debutYear,
  active, status, statsSummary`). Dropped `videos/standings/quicklinks/...`.
- **stats**: kept `categories` (verbatim — the point of the fixture),
  `glossary`, and a **trimmed** `teams` map (id-keyed →
  `{id, abbreviation, displayName, shortDisplayName}`; the raw block is
  dual-keyed by id+slug with full team objects). Dropped `filters`. Season
  rows reference a team via numeric `teamId`; the `teams` map resolves it to
  an abbreviation (e.g. `"3"` → `LAA`).

These are stable veteran/active players; stat *values* will grow over time
but the **structure** (the thing parsers test) is stable. Prefer asserting
on structure + a few frozen early-career numbers, not latest-season values.

## Structural notes for Chunk 1

A stats `categories[]` entry has, index-parallel:
- `labels[]`   — short column headers (e.g. `GP AB R H 2B 3B HR RBI ...`)
- `names[]`    — machine keys (`gamesPlayed atBats runs hits ...`)
- `displayNames[]`, `descriptions[]` — long names / tooltips
- `statistics[]` — **one row per season**: `{teamId, teamSlug,
  season:{year,displayName}, stats:[...], position}` (`stats` parallel to
  `labels`/`names`)
- `totals[]`, `averages[]` — career aggregate rows (also parallel)
- top-level `glossary[]` — `{abbreviation, displayName}`
- top-level `teams` (trimmed map) — `teamId` → `{abbreviation, displayName,
  shortDisplayName}`, to resolve each season row's `teamId`

Category names are position-driven:
- Hitter (Trout): `career-batting, postseason-batting, expanded-batting,
  advanced-batting`
- Pitcher (Kershaw): `pitching, postseason-pitching, opponent-batting,
  expanded-pitching`

**Two-way limitation (important):** the `/stats` endpoint returns categories
based on the player's *currently listed position*. Ohtani is listed `SP`, so
his `_stats.json` contains **pitching only — no batting category**. A single
`/stats` call cannot render both sides of a two-way player. Treat full
two-way support as a known limitation / deferred (see handoff Open Questions).

Bio (`athlete`) carries: `displayName, position.abbreviation, team,
displayBatsThrows, displayHeight, displayWeight, age, jersey, headshot.href,
displayDraft, debutYear, statsSummary`. The `/stats` payload has **no** bio
block, so Chunk 1 needs **two** fetches (bio + stats) per player.

---

# Box-score fixture (Lineup Popup, Chunk 0)

Trimmed real response captured 2026-05-18 for the team-lineup popup work
(see `LINEUP_POPUP_HANDOFF.md`). Drives Chunk 1's pure `_normalize_lineups`
parser tests. No live games on 2026-05-18 → captured from a recently
**completed** game (completed games retain their full box score).

## File

| File | Game | Event id |
|---|---|---|
| `summary_401815376_boxscore.json` | Red Sox @ Braves, Final, 2026-05-17 | `401815376` |

## Provenance / how to regenerate

```
https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event={ID}
```
Pick an event id from a team schedule
(`…/teams/{TEAM}/schedule` → `events[].id` where
`competitions[0].status.type.completed` is true).

Trimming applied (the **real wrapper shape is preserved** so the parser
exercises its live code path):
- Kept only `boxscore.players[]` and `boxscore.teams[]`. Dropped the other
  ~30 top-level summary keys (`plays`, `winprobability`, `rosters`, …).
- `boxscore.players[].team` reduced to `{id, abbreviation, displayName,
  name, logo}`; `boxscore.teams[]` reduced to `{homeAway, displayOrder,
  team:{…same…}}`. Dropped colors, `statistics`, `details`, etc.
- Each athlete entry reduced to `{batOrder, starter, active, atBats,
  stats, notes?, position, athlete:{id, displayName, shortName, position,
  headshot}}`. Dropped `hotZones`, `links`, `positions`, `guid`, `uid`.
- **All ~25 athletes kept** (not sampled) — the point of the fixture is
  the substitution / pitching-change structure.

Stat *values* are frozen for this game; assert on structure + these exact
numbers (they will never change — the game is final).

## Structural notes for Chunk 1 (`_normalize_lineups`)

`boxscore.players[]` — one block **per team**, order matches
`boxscore.teams[]` (here `[away BOS, home ATL]`). **Do not rely on order**:
join `boxscore.players[].team.id` → `boxscore.teams[].team.id` and read
`homeAway` from the teams block. Each players block:

- `.team` → `{id, abbreviation, displayName, name, logo}` — everything the
  popup header needs (no separate `header` lookup required).
- `.statistics[]` → blocks with `type` `"batting"` and `"pitching"`, each
  carrying `keys[]` and `athletes[]` (`stats[]` is index-parallel to
  `keys[]`).

`batting` `keys`:
`hits-atBats, atBats, runs, hits, RBIs, homeRuns, walks, strikeouts,
pitches, avg, onBasePct, slugAvg`
→ Game hitter cols: AB=`atBats`, R=`runs`, H=`hits`, HR=`homeRuns`,
RBI=`RBIs`, BB=`walks`, K=`strikeouts`, **AVG=`avg` (this is the player's
*season* average, e.g. `.301` — not a game value)**.

`pitching` `keys`:
`fullInnings.partInnings, hits, runs, earnedRuns, walks, strikeouts,
homeRuns, pitches-strikes, ERA, pitches`
→ Game pitcher cols: IP=`fullInnings.partInnings` (e.g. `"6.0"`), H=`hits`,
R=`runs`, ER=`earnedRuns`, BB=`walks`, K=`strikeouts`, PC=`pitches` (int;
`pitches-strikes` is the `"87-58"` form), **ERA=`ERA` (season ERA)**.

Per-entry fields:
- `batOrder` — 1–9 for hitters; **`0` for pitchers**. **Substitutions
  share a slot**: e.g. BOS batOrder 4 = Contreras (`starter:true,
  active:false` — subbed out) then Kiner-Falefa (`starter:false,
  active:true`). Array order already lists starter before sub within a
  slot → preserve array order, group by `batOrder`.
- `starter` (bool) — in the starting lineup. `active` (bool) — currently
  in the game (`false` = subbed out / pitcher removed).
- `position` (entry) — the **in-game fielding position** (e.g. sub at
  `1B`); `athlete.position` — the player's **listed** position (e.g.
  `2B`). They differ for utility players — use the entry `position` for
  the lineup display.
- `notes[]` (pitchers) — `[{type:"pitchingDecision", text:"L, 2-5"}]` →
  the W/L/SV/HLD decision (absent for no-decision).
- `athlete.headshot` is an **object** `{href, alt}` (not a bare string).
- `atBats[]` is a list of at-bat reference ids (not a count).

**Which side is batting (`is_batting`):** the box score has no batting
flag. Resolve it the way `_normalize_on_deck` does — the box-score team
whose block contains the *current batter*. For a **completed** game (this
fixture) there is no current batter, so Chunk 1 must treat `is_batting`
as unresolved (`None`/both `false`) and the test should pass the
current-batter team explicitly to exercise the live path.
