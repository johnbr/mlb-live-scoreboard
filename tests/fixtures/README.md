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
- **bio**: kept only the `athlete` object, reduced to bio-relevant keys
  (`id, firstName, lastName, displayName, fullName, jersey, headshot,
  position, team, age, displayHeight, displayWeight, displayBirthPlace,
  displayDOB, displayBatsThrows, displayExperience, displayDraft, debutYear,
  active, status, statsSummary`). Dropped `videos/standings/quicklinks/...`.
- **stats**: kept `categories` (verbatim — the point of the fixture) and
  `glossary`. Dropped `filters`/`teams`.

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
