# Option B Handoff — In-card Player Career Stats popup

> **Status:** Chunks 0–1 DONE. Branch `feat/player-career-card`, based on `main` @ v1.10.0.
> **Audience:** A fresh Claude instance or developer resuming this work on any machine.
> This file is the single source of truth for resuming. Keep the **Progress Log**
> (bottom) updated as the *last* step of every chunk before committing.

---

## 0. How to use this document (cold-start protocol)

1. Read this whole file.
2. Read `CLAUDE.md`, `ARCHITECTURE.md`, and the **Key Code Anchors** section below.
3. Open the **Progress Log** table at the bottom. Pick the first chunk whose
   status is `TODO` and whose dependencies are all `DONE`.
4. Implement only that chunk. Keep diffs scoped to the chunk.
5. Validate using **Section 6 — Validation**.
6. Update the Progress Log row (status, date, commit SHA, notes/decisions).
7. Commit with a conventional-commit message (see Section 7). One commit (or a
   few) per chunk is fine — the branch becomes one PR at the end.
8. Stop, or continue to the next chunk.

Never skip step 6. The Progress Log is what makes this resumable on another
computer — it records *what was decided and why*, not just what was done.

---

## 1. Goal

Make the clickable player names (shipped in Option A, v1.10.0) optionally open
an **in-card popup that reproduces the useful content of ESPN's player page**
(bio + career stats), instead of (or in addition to) navigating to espn.com.

Scope target: "who is this player and how good are they" — headshot, bio
(team, position, bats/throws, height/weight, age), and a career stats table
(season-by-season). A *pixel-faithful* ESPN clone is explicitly **out of
scope** — that was the rejected high-cost path in the original evaluation.

## 2. Prerequisite / relationship to Option A — DONE

Option A is **merged to `main`** (commit `7a1786c`, released as v1.10.0). This
branch is based on it. The relevant Option A primitives this work builds on:

- `playerNameMarkup(name, athleteId)` emits
  `<span class="player-link" role="link" tabindex="0" data-athlete-id="…">`.
- `_openPlayerProfile(el)` currently does `window.open(espnUrl)`.
- Delegated listeners `_onContentClick` / `_onContentKeydown` on `.card-content`.
- Backend exposes athlete `id` everywhere a name renders (batter, pitcher,
  on-deck, due-up, leaders, probable pitchers).

Option B **repurposes the existing `.player-link[data-athlete-id]` click
target** — no new per-name wiring is needed.

## 3. Architecture decision

The integration is push-only (`DataUpdateCoordinator` → sensor attributes,
fixed 5 s cadence). The hard part is fetching career stats **on demand** for
an arbitrary player the user just clicked. Three routes were evaluated:

| Route | Summary | Verdict |
|---|---|---|
| **R1** Browser fetches ESPN directly | Card `fetch()`s the public ESPN athlete endpoint client-side | Fallback only — depends on ESPN sending permissive CORS headers (UNVERIFIED — resolve in Chunk 0). No backend change. |
| **R2** Coordinator pre-resolves every on-screen athlete into attributes | Many extra ESPN calls/tick + large sensor state | **Rejected** — bloat, wasteful. |
| **R3** Integration registers a WebSocket command; card calls it with an athlete id | Server-side fetch, reuses our `_get_json` + TTL cache, no CORS, HA-idiomatic | **Recommended primary.** |

**Decision (confirmed by Chunk 0): pursue R3, with R1 as a now-viable
fallback.** Both ESPN endpoints return `access-control-allow-origin: *`, so a
browser `fetch()` from a HA dashboard origin works cross-origin — R1 is a
real fallback. R3 stays primary for the original reasons (one shared cached
fetch vs. every dashboard client hitting ESPN, our `_get_json`
resilience/timeout/headers, no ESPN rate-limit exposure, no sensor bloat).

Rationale for R3: keeps ESPN access server-side (one cached fetch shared by
all dashboard clients, our existing resilience/headers/timeout via
`_get_json`), no dependency on ESPN's CORS policy, no sensor-state growth,
and HA gives custom integrations a clean `websocket_api` registration path.

## 4. Key Code Anchors

Reference by **function/symbol name** (line numbers drift). All paths under
`custom_components/mlb_live_scoreboard/`.

### Backend — `coordinator.py`
- `_get_json(self, url)` — shared async fetch: headers, 20 s timeout, raises
  `UpdateFailed`. Reuse this for any new fetch.
- `_get_public_batter_stats(self, athlete_id)` — **the model to generalize.**
  TTL-cached (`self._batter_stats_cache`, `BATTER_SEASON_STATS_TTL_SECONDS`),
  stale-fallback on error. Hits
  `https://site.web.api.espn.com/apis/common/v3/sports/baseball/mlb/athletes/{ID}/stats?region=us&lang=en&contentorigin=espn`.
- `_extract_current_season_batter_stats(stats_payload)` — shows the payload
  shape: `categories[]` each with `names[]` (stat keys) + `statistics[]`
  (one row per season, `row["season"]["year"]`, `row["stats"][]`). The full
  career table is **already in this payload** — just not currently surfaced.
- `_find_any_athlete` / `_find_boxscore_athlete` / `_find_roster_athlete` —
  athlete lookups within the game summary (source of bio bits already seen:
  `displayName`, `headshot`, `lastName`, `suffix`). Note: the summary only
  has athletes *in the current game*; the dedicated athlete endpoint is
  needed for arbitrary career bio.

### Backend — `__init__.py`
- Integration `async_setup_entry`; registers static path + Lovelace resource.
  This is where an R3 WebSocket command would be registered via
  `homeassistant.components.websocket_api.async_register_command`. Note the
  command name should be namespaced, e.g. `mlb_live_scoreboard/player_card`.

### Backend — `const.py` / `types.py`
- `const.py`: `DOMAIN`, TTL constants, `MLB_TEAM_MAP`. Add a new TTL constant
  for career stats here.
- `types.py`: `TypedDict` shapes (`total=False`). Add a `PlayerCard` shape.

### Frontend — `mlb-live-game-card.js`
- `playerNameMarkup(name, athleteId)` — click target emitter (Option A).
- `_openPlayerProfile(el)` — **the behavior to fork.** Currently
  `window.open` to ESPN. Option B routes the primary action to the popup.
- `_onContentClick` / `_onContentKeydown` — delegated listeners. Extend here.
- `_upcomingExpanded` + `.upcoming-expandable` + the forced-rerender pattern
  (`this._lastFingerprint = ""`) — the **only** existing in-card
  "interactive expand" precedent. There is **no modal/dialog in the codebase
  yet** — Chunk 3 builds the first one.
- `requestCachedLogo` + `window.__mlbLiveLogoCache` — established image-cache
  pattern; reuse for the popup's headshot.
- `shortPersonName` — name formatting helper.
- The card renders by building **HTML strings → innerHTML** with a single
  delegated listener. Follow this pattern; do not introduce a framework.

### ESPN endpoints
- Stats (have): `…/athletes/{ID}/stats?region=us&lang=en&contentorigin=espn`
- Bio/overview (verify in Chunk 0): `…/athletes/{ID}` (same host:
  `site.web.api.espn.com/apis/common/v3/sports/baseball/mlb`)
- Human page (Option A target, keep as secondary link): `https://www.espn.com/mlb/player/_/id/{ID}`

## 5. Chunked implementation plan

Each chunk is independently committable and testable. Dependencies noted.

### Chunk 0 — Spike: verify CORS + capture payloads *(no production code)*
- **Dep:** none.
- Determine whether a browser `fetch()` to the ESPN athlete `stats` (and
  `…/athletes/{ID}`) endpoints succeeds cross-origin (check
  `Access-Control-Allow-Origin`). This decides if R1 is a viable fallback.
- Capture real JSON for ≥3 athlete ids: a hitter, a pitcher, a two-way/utility
  player. Save trimmed samples under `tests/fixtures/` (create dir) for use as
  test fixtures in later chunks. Pick stable retired-or-veteran ids so
  fixtures don't churn.
- **Output:** Architecture decision (R3 confirmed, R1 viable?) + fixtures +
  notes recorded in Progress Log. **Acceptance:** decision recorded; fixtures
  committed.

### Chunk 1 — Backend: generic career-stats + bio fetch/parse
- **Dep:** Chunk 0.
- Generalize `_get_public_batter_stats` into a reusable
  `_get_player_card(self, athlete_id)` (or similar) that fetches **both**
  `/athletes/{ID}` (bio) **and** `/athletes/{ID}/stats` (career) — Chunk 0
  confirmed these are separate (the stats payload has no bio block) — with
  its own TTL cache + stale fallback, reusing `_get_json`. Fetch the two
  concurrently (`asyncio.gather`).
- Add a pure parser → normalized `PlayerCard` dict: bio from `.athlete`
  (displayName, team, position.abbreviation, displayBatsThrows,
  displayHeight, displayWeight, age, jersey, headshot.href, displayDraft,
  debutYear) + a career table built from the **primary** stats category
  (`career-batting` for hitters, `pitching` for pitchers — pick by which
  category name is present): use the category's own `labels[]` as column
  headers, `names[]` as keys, `statistics[]` rows (per season), and the
  `totals[]` row as a career line. Skip postseason/advanced/expanded
  categories. For two-way players, render whichever single side `/stats`
  returns (known limitation — see §8).
- Add `PlayerCard` TypedDict to `types.py`; TTL const to `const.py`.
- **Acceptance:** new unit tests in `tests/test_coordinator_helpers.py` using
  the Chunk 0 fixtures pass; parser is pure (no I/O) and unit-tested for
  hitter, pitcher, and missing/partial payloads.

### Chunk 2 — Transport wiring (R3) / fetch helper (R1 fallback)
- **Dep:** Chunk 1.
- **R3:** register `mlb_live_scoreboard/player_card` websocket command in
  `__init__.py`; handler takes `athlete_id`, returns the normalized
  `PlayerCard`. Card-side: `async _fetchPlayerCard(id)` using
  `this._hass.connection.sendMessagePromise(...)`, with an in-memory
  per-card cache + in-flight de-dupe + error/empty handling.
- **R1 fallback (only if R3 too heavy AND Chunk 0 confirmed CORS):** card
  fetches ESPN directly and runs an equivalent JS parser (port Chunk 1
  logic). Document why if this path is taken.
- **Acceptance:** clicking a name resolves real data to the console / a temp
  debug render (popup UI is Chunk 3). Backend handler unit-or-manually tested
  (note: websocket handler tests need `pytest-homeassistant-custom-component`,
  which the current harness does **not** install — see Section 6 caveat;
  manual HA test is acceptable, record it).

### Chunk 3 — Popup component (skeleton)
- **Dep:** none (can parallel 1–2; data is stubbed).
- Build the first modal in the codebase: backdrop, panel, open/close, ESC +
  backdrop-click + close-button, focus trap, restore focus on close, scroll
  lock. Themed with HA CSS vars (match existing `--warning-color` accent,
  `--card-background-color`, etc.). Responsive within narrow card widths.
- Render only: loading state, error state, empty state. No real data yet.
- **Acceptance:** popup opens/closes via keyboard and pointer; passes a
  manual a11y pass (focus trapped, ESC works, focus restored).

### Chunk 4 — Stats/bio rendering
- **Dep:** Chunks 1–3.
- Bio header (cached headshot via `requestCachedLogo`, team, pos, bats/throws,
  ht/wt, age, jersey). Career table: hitting columns for hitters, pitching
  columns for pitchers; season-by-season rows + a career/total row if ESPN
  provides one. Sensible truncation/scroll for long careers.
- **Acceptance:** real data renders correctly for a hitter and a pitcher
  (use Chunk 0 sample ids in a running HA, or a fixture-driven JS dev
  harness). Numbers match ESPN's player page spot-check.

### Chunk 5 — Wire the click + behavior config
- **Dep:** Chunks 2–4.
- Fork `_openPlayerProfile`: primary action opens the popup. Keep "View on
  ESPN" reachable — recommended as a link **inside** the popup footer (so
  Option A's value is retained), plus an optional card config
  `player_link_target: popup | espn` (default `popup`).
- Update keyboard handler parity. Update `README.md` config table +
  `CLAUDE.md` "Card Configuration Options".
- **Acceptance:** both targets work; config switch respected; keyboard parity.

### Chunk 6 — Polish, docs, final validation
- **Dep:** all.
- Accessibility re-pass, error-path UX (ESPN down → graceful message),
  loading skeleton, theme check in light + dark. README/ARCHITECTURE updates.
  Full validation (Section 6). Do **not** bump versions (Section 7).
- **Acceptance:** Section 6 fully green; PR opened.

## 6. Validation

**Environment gotchas (record-keeping — these cost time if rediscovered):**

- The in-repo `.venv` is the **wrong architecture** (`Exec format error`). Do
  not use it. Create a fresh one:
  ```
  python3 -m venv /tmp/venv && /tmp/venv/bin/pip install pytest pytest-asyncio ruff
  ```
- `tests/conftest.py` **stubs Home Assistant** so pure-helper tests need no HA
  install. Anything exercising coordinator lifecycle / the websocket handler
  needs `pytest-homeassistant-custom-component` (NOT installed by the current
  harness, and the conftest stubs would conflict). Plan Chunk 2 backend
  verification as: pure-parser unit tests (Chunk 1) + **manual HA test** for
  the websocket round-trip; record the manual test in the Progress Log.
- `.prettierrc.json` is **invalid JSON** (leading `#` comment lines),
  pre-existing. Prettier config-load fails; **CI does not run prettier.**
  Don't chase prettier formatting.

**Commands (run from repo root):**
```
node --check custom_components/mlb_live_scoreboard/mlb-live-game-card.js
/tmp/venv/bin/python -m pytest -q
/tmp/venv/bin/ruff check .            # CI uses latest/unpinned ruff
python3 -m py_compile custom_components/mlb_live_scoreboard/*.py
```
**CI** (`.github/workflows/`): `tests.yml` = `ruff check .` + `pytest tests/ -v`;
plus `validate.yml` (HACS), `hassfest.yml`, `release-please.yml`. The PR is
green when `tests.yml` passes.

## 7. Versioning & PR workflow

- **Do NOT manually edit** `manifest.json` `version` or the card's
  `CARD_VERSION` / `// x-release-please-version` marker. release-please owns
  both via `release-please-config.json` `extra-files`.
- Use **conventional commits**. The user-facing feature should land as a
  `feat:` commit so release-please cuts a **minor** bump. Internal scaffold
  commits may be `chore:`/`refactor:`/`test:` — but ensure the branch
  contains at least one `feat:` describing the feature.
- Flow: this branch → **one PR** targeting `main` → user reviews/merges →
  release-please opens/updates a release PR → user approves it to tag the
  release. (Confirmed working: Option A → PR #8 → release PR #9 → v1.10.0.)
- Branch: `feat/player-career-card`. Do not open the PR until Chunk 6 (or
  earlier only if explicitly asked) — but **push the branch** after each
  chunk so progress is recoverable on another machine.

## 8. Open questions / decisions log

- [x] **Chunk 0:** Does ESPN send CORS headers permitting browser `fetch()`?
      **YES** — both `/athletes/{ID}` and `/athletes/{ID}/stats` return
      `access-control-allow-origin: *` (verified via curl with an
      `Origin: http://homeassistant.local:8123` header, HTTP 200). R1 is a
      viable fallback; R3 remains primary.
- [x] **Chunk 0:** Distinct bio endpoint, or bio in the stats payload?
      **Distinct.** `/stats` has **no** bio block (top keys: `filters,
      teams, categories, glossary`). Bio comes from `/athletes/{ID}`
      (`.athlete`). **Chunk 1 needs two fetches per player** (bio + stats).
- [x] **Chunk 0:** Stat-table shape — confirmed. Each `categories[]` entry
      has parallel `labels[]` (column headers) / `names[]` (keys) /
      `statistics[]` (one row per season: `season.year`, `stats[]`) plus
      `totals[]`/`averages[]` career rows and a top-level `glossary[]`. ESPN
      gives ready-made column headers — Chunk 4 rendering is straightforward.
- [x] **Chunk 1/4 (raised by Chunk 0):** Two-way players. ESPN's `/stats`
      returns categories by the player's *currently listed position* only;
      Ohtani (listed `SP`) returns **pitching only, no batting**. **Decided
      in Chunk 1:** render whatever single side `/stats` returns; the
      limitation is documented (here + `tests/fixtures/README.md`) and
      *not* signalled at runtime — a `two_way_note` heuristic was prototyped
      and dropped because the canonical case (Ohtani = listed SP) is
      indistinguishable from a normal pitcher in the payload, so the field
      would never fire for the case that motivates it. Still open for Chunk
      4 only if a reliable dual-side source is found (extra endpoint
      spelunking, out of current scope).
- [ ] Chunk 1: Exact column sets to show for hitter vs pitcher — use ESPN's
      own `labels[]` from the primary category (`career-batting` /
      `pitching`); advanced/splits/postseason categories out of scope.
- [ ] Chunk 5: Default for `player_link_target` — proposed `popup`.

## 9. Progress Log

Update the matching row as the **last step before committing** each chunk.

| Chunk | Status | Date | Commit | Notes / decisions |
|---|---|---|---|---|
| Scaffold (this doc) | DONE | 2026-05-17 | 71667c3 | Branch off main @ v1.10.0; Option A merged. |
| 0 — Spike CORS+payloads | DONE | 2026-05-17 | af3f9b1 | CORS `*` on both endpoints → R1 viable, R3 primary. Bio + stats are **separate** endpoints (2 fetches). Stats shape confirmed (labels/names/statistics/totals/glossary). **Two-way limitation found** (Ohtani = pitching-only) — see Open Questions. Fixtures: `tests/fixtures/` (Trout/Kershaw/Ohtani bio+stats) + README. |
| 1 — Backend fetch/parse | DONE | 2026-05-17 | _this commit_ | `const.py`: `PLAYER_CARD_TTL_SECONDS` (6 h) + stale fallback (24 h). `types.py`: `PlayerCard`/`PlayerCardBio`/`PlayerCareerTable`/`PlayerCareerSeason`. `coordinator.py`: `_get_player_card` (concurrent bio+stats `asyncio.gather`, TTL cache, stale fallback), pure `_parse_player_card` + `_team_abbr_map`. 7 fixture-driven tests (hitter/pitcher/two-way/empty/partial), 65 pass, `ruff check .` clean. **Dropped** speculative `two_way_note` (can't detect Ohtani-type from payload — limitation stays documented only). **Fixture fidelity fixed:** Chunk 0 over-trimmed — bio re-wrapped as real `{"athlete":{…}}`, trimmed `teams` map re-added for per-season team abbrev. |
| 2 — Transport wiring | TODO | | | |
| 3 — Popup skeleton | TODO | | | |
| 4 — Stats/bio render | TODO | | | |
| 5 — Wire click + config | TODO | | | |
| 6 — Polish + docs | TODO | | | |
