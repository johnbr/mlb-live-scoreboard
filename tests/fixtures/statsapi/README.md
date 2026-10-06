# MLB Stats API fallback fixtures

Real payloads captured live on 2026-10-05 for `tests/test_statsapi.py`. They
can't be recaptured, because the fallback only runs when ESPN drops a game.

| File | Game | State |
|---|---|---|
| `espn_summary_401907986_pbp_none.json` | NYY @ TB, ALDS G2 (ESPN 401907986) | ESPN `playByPlaySource: "none"`: score only, no plays or situation |
| `mlb_feed_849839_b6_end.json` | same game, MLB gamePk 849839 | third out of the bottom 6th just made |
| `espn_summary_401907986_t7_mid_at_bat.json`, `mlb_feed_849839_t7_mid_at_bat.json` | same game, 19:33:55 PT | top 7th, Wells at 1-2 with two out, 3 pitches |
| `espn_summary_401907986_end7.json`, `mlb_feed_849839_end7.json` | same game, 19:46:58 PT | end of the 7th, between halves |
| `mlb_abs_849839_final.json` | NYY @ TB, ALDS G2, captured 2026-10-06 after the final | `STATSAPI_CHALLENGES_URL` response (already field-filtered by MLB, not trimmed): one ABS challenge won by each side, 2 remaining each |
| `espn_summary_401907991_full.json`, `mlb_feed_849834_final.json` | CHW @ CLE, ALDS G2 (ESPN 401907991 / MLB 849834) | final. **Fully covered by both**, so it is the oracle: MLB's copy, translated, must read like ESPN's own |

## Provenance / how to regenerate

```
https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/summary?event={ESPN_ID}
https://statsapi.mlb.com/api/v1.1/game/{GAME_PK}/feed/live
```

Trimming kept only the keys `statsapi.py` and the coordinator's normalizers
read:

- **ESPN:**
  - the header competition (id, date, status, competitors, the two `*Source` flags);
  - box score teams and athletes (id, names, headshot href, position abbreviation, `batOrder`, `starter`, `active`, `stats`, `notes`);
  - rosters;
  - the play fields the normalizers read.
- **MLB:**
  - `gameData.game.pk`, team ids, and player names and positions;
  - every play's `about` / `matchup` / `result` / `count`;
  - per-event `details` (call and type codes), `count`, and pitch speed and coordinates;
  - linescore count, inning state, offense and defense;
  - box-score batting order, `batters` / `pitchers`, and the per-player game and season lines the translator maps.

Dropped: links, videos, news, odds, hot/cold zones, and full bios.
