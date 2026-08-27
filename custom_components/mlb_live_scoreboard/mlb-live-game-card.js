const CARD_TAG = "mlb-live-game-card";
const CARD_VERSION = "1.28.1"; // x-release-please-version
console.info(`[${CARD_TAG}] ${CARD_VERSION} loaded`);

// After navigating the schedule away from the default game, auto-snap back to
// the live/auto-selected view this long after the last arrow tap. Each tap
// re-arms the timer; returning to the default view cancels it.
const NAV_IDLE_RETURN_MS = 60000;

// Same idea for the inning pager: after paging the play-by-play back to an
// earlier half-inning, snap back to the live half this long after the last tap.
const INNING_IDLE_RETURN_MS = 20000;

// Number of seconds the card keeps showing the third-out play after it occurs,
// before yielding to the due-up panel for the next half-inning.
const THIRD_OUT_HOLD_SECONDS = 30;
// Wallclock window (seconds) within which a stored play is treated as the
// just-completed third out, even without an explicit `third_out_play` attribute.
const THIRD_OUT_RECENT_WINDOW_SECONDS = 8;

const EDITOR_TAG = "mlb-live-game-card-editor";
// Integration domain — used by the visual editor's entity picker to filter
// the dropdown to this integration's sensors only (robust even if the user
// renamed the entity_id away from the default `sensor.mlb_live_scoreboard_*`).
const INTEGRATION_DOMAIN = "mlb_live_scoreboard";
const DOCS_URL = "https://github.com/johnbr/mlb-live-scoreboard";

window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "MLB Live Game Card",
    description: `MLB Live Scoreboard (${CARD_VERSION})`,
    // Live preview tile + "Documentation" link in the dashboard card picker.
    preview: true,
    documentationURL: DOCS_URL,
  });
}

// Default card config. Shared by the card's setConfig() and the visual
// editor so the editor's toggles reflect the real defaults (a value the
// user never set still shows its true on/off state in the UI).
const CARD_DEFAULTS = {
  title: "",
  show_batter: true,
  show_records: true,
  show_linescore: false,
  show_pitches: true,
  show_play_results: true,
  show_on_deck: true,
  show_base_occupancy: true,
  show_diamond: true,
  show_count: true,
  show_win_probability: true,
  // SVG strike-zone with one numbered, color-coded dot per pitch in the
  // in-progress at-bat. Off by default; opt in per card. Only shows up
  // while pitches with coordinates are present (between at-bats it
  // disappears and the diamond reflows back to center).
  show_pitch_zone: false,
  // Post-final ESPN highlights link. Defaults to off — the link only
  // appears 30-90 min after the final pitch (when ESPN publishes clips)
  // and most users won't want it in their dashboard. Opt in per card.
  show_highlights: false,
  // seconds, 0 = disabled (rely on hass state updates)
  refresh_rate: 0,
  // Clicking a (yellow) player name: "popup" opens the in-card career
  // popup (default); "espn" opens ESPN's player page directly (the
  // original Option A behavior). ESPN stays reachable either way — the
  // popup footer always carries a "View on ESPN" link.
  player_link_target: "popup",
  // Clicking a team's side of the matchup (anywhere but the yellow
  // player name) opens an in-card popup of that team's full lineup +
  // everyone who appeared in the game, with a Game/Season toggle.
  show_lineup_popup: true,
  // Which view that popup defaults to: "auto" (Game while the game is
  // live, Season otherwise) or a forced "game" / "season".
  lineup_default_view: "auto",
  // Left/right arrows on the non-live card to page through this team's
  // schedule: back through previous results, forward through upcoming
  // games. Hidden while the displayed game is live. On by default.
  show_schedule_nav: true,
  // Left/right arrows above the play-by-play on the live card to page back
  // through earlier half-innings ("what happened in the 4th?"). Snaps back to
  // the live half after a short idle. On by default.
  show_inning_nav: true,
  // How much of the live card is showing when a game goes live:
  // "collapsed" (default) shows only the two score rows + inning marker —
  // clicking that header expands to the fully configured live view;
  // "expanded" starts fully open (the pre-collapse behavior). Either way the
  // toggle is card-local and re-baselines to this value on a new game.
  live_default_view: "collapsed",
  // Inline player-headshot size for the matchup portraits, due-up
  // thumbnails, and probable-pitcher portraits. "auto" tracks the card's
  // actual width via a CSS container query (responsive to HA's per-column
  // dashboards, not the viewport). The explicit presets pin a fixed size
  // for users who prefer a specific look. Modal popup avatars are
  // unaffected — they size against the viewport, not the card.
  headshot_size: "auto",
};

// Pixel size for each `headshot_size` preset. Used by the CSS rules near
// the bottom of CARD_CSS to override the "auto" clamp(). Keep these in
// sync with the rule block — there's no single source of truth in
// CSS since we hand-write each preset's rule for clarity.
const HEADSHOT_SIZE_PRESETS = {
  small: 40,
  medium: 56,
  large: 72,
  xlarge: 88,
};

// Locate this integration's sensor for getStubConfig(), so adding the card
// from the picker lands on a working entity instead of an empty config.
function findMlbEntity(hass) {
  if (!hass || !hass.states) return "";
  const ids = Object.keys(hass.states);
  // Prefer the integration's conventional entity_id prefix.
  const byPrefix = ids.find((id) =>
    id.startsWith("sensor.mlb_live_scoreboard"),
  );
  if (byPrefix) return byPrefix;
  // Fallback: match the sensor's distinctive attribute signature so a
  // user-renamed entity_id is still discovered.
  const bySignature = ids.find((id) => {
    if (!id.startsWith("sensor.")) return false;
    const a = hass.states[id].attributes || {};
    return (
      a.team_abbr !== undefined &&
      a.display_event_id !== undefined &&
      a.inning_context !== undefined
    );
  });
  return bySignature || "";
}

// ha-form schema for the visual editor. `type: "grid"` blocks are layout
// only (ha-form does not nest data for them), so every key stays flat in
// the dashboard YAML — identical shape to a hand-written config.
const EDITOR_SCHEMA = [
  {
    name: "entity",
    required: true,
    selector: { entity: { integration: INTEGRATION_DOMAIN, domain: "sensor" } },
  },
  { name: "title", selector: { text: {} } },
  {
    type: "grid",
    schema: [
      {
        name: "refresh_rate",
        selector: {
          number: {
            min: 0,
            max: 300,
            step: 1,
            mode: "box",
            unit_of_measurement: "s",
          },
        },
      },
      {
        name: "player_link_target",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "popup", label: "In-card career popup" },
              { value: "espn", label: "ESPN player page" },
            ],
          },
        },
      },
      {
        name: "lineup_default_view",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "auto", label: "Auto (Game while live, else Season)" },
              { value: "game", label: "Game" },
              { value: "season", label: "Season" },
            ],
          },
        },
      },
      {
        name: "live_default_view",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "collapsed", label: "Collapsed (score line only)" },
              { value: "expanded", label: "Expanded (full live card)" },
            ],
          },
        },
      },
      {
        name: "headshot_size",
        selector: {
          select: {
            mode: "dropdown",
            options: [
              { value: "auto", label: "Auto (scale to card width)" },
              { value: "small", label: "Small (40px)" },
              { value: "medium", label: "Medium (56px)" },
              { value: "large", label: "Large (72px)" },
              { value: "xlarge", label: "X-Large (88px)" },
            ],
          },
        },
      },
      { name: "show_lineup_popup", selector: { boolean: {} } },
      { name: "show_schedule_nav", selector: { boolean: {} } },
      { name: "show_inning_nav", selector: { boolean: {} } },
    ],
  },
  {
    type: "grid",
    schema: [
      { name: "show_batter", selector: { boolean: {} } },
      { name: "show_records", selector: { boolean: {} } },
      { name: "show_linescore", selector: { boolean: {} } },
      { name: "show_pitches", selector: { boolean: {} } },
      { name: "show_play_results", selector: { boolean: {} } },
      { name: "show_on_deck", selector: { boolean: {} } },
      { name: "show_base_occupancy", selector: { boolean: {} } },
      { name: "show_diamond", selector: { boolean: {} } },
      { name: "show_pitch_zone", selector: { boolean: {} } },
      { name: "show_count", selector: { boolean: {} } },
      { name: "show_win_probability", selector: { boolean: {} } },
      { name: "show_highlights", selector: { boolean: {} } },
    ],
  },
];

const EDITOR_LABELS = {
  entity: "MLB Live Scoreboard entity",
  title: "Card title (optional)",
  refresh_rate: "Refresh rate (s, 0 = HA updates)",
  player_link_target: "Player name click",
  lineup_default_view: "Lineup popup default view",
  live_default_view: "Live game default view",
  headshot_size: "Headshot size",
  show_lineup_popup: "Enable team lineup popup",
  show_schedule_nav: "Schedule navigation arrows",
  show_inning_nav: "Past half-inning pager",
  show_batter: "Batter",
  show_records: "Team records",
  show_linescore: "Linescore",
  show_pitches: "Pitch sequence",
  show_play_results: "Play results",
  show_on_deck: "On-deck",
  show_base_occupancy: "Base occupancy",
  show_diamond: "Base diamond",
  show_pitch_zone: "Pitch zone (live at-bat)",
  show_count: "Count",
  show_win_probability: "Win probability",
  show_highlights: "Highlights link (final)",
};

const EDITOR_HELPERS = {
  entity: "Pick the sensor created by the MLB Live Scoreboard integration.",
  refresh_rate: "0 leaves refreshing to Home Assistant's own state updates.",
  live_default_view:
    "Collapsed keeps the live card at just the two score rows + inning marker; click that header (or the ⌄ strip under it) to expand to the full live view. Expanded starts fully open. Resets to this choice when a new game starts.",
  headshot_size:
    "Auto scales headshots with the card's width (responsive to HA's per-column dashboards). Presets pin a fixed pixel size.",
  show_schedule_nav:
    "Adds ‹ › arrows beside the date/status on the non-live card to page back through previous results and forward through upcoming games. Hidden while a game is live.",
  show_inning_nav:
    "Adds a small ▾ strip below the live play-by-play that swaps the panel to the previous half-inning (one half at a time; the inning marker by the box score shows which). Snaps back to the live half after ~20s of no taps.",
  show_pitch_zone:
    "Adds a small strike-zone graphic below the base diamond with one colored dot per pitch in the current at-bat. Auto-hides between at-bats.",
  show_highlights:
    "Shows a 'Watch highlights on ESPN' link in the final-game panel once ESPN publishes clips (typically 30-90 min after the final pitch).",
};

// Visual (no-YAML) config editor. Plain custom element wrapping HA's native
// <ha-form> so it needs no build step or imports — ha-form is already
// registered by Home Assistant in the Lovelace editor context.
class MlbLiveGameCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config || {};
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._form) this._form.hass = hass;
  }

  _render() {
    if (!this._form) {
      const wrap = document.createElement("div");
      wrap.style.padding = "8px 0";
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (s) => EDITOR_LABELS[s.name] || s.name;
      this._form.computeHelper = (s) => EDITOR_HELPERS[s.name] || "";
      this._form.schema = EDITOR_SCHEMA;
      this._form.addEventListener("value-changed", (ev) => {
        ev.stopPropagation();
        this.dispatchEvent(
          new CustomEvent("config-changed", {
            detail: { config: ev.detail.value },
            bubbles: true,
            composed: true,
          }),
        );
      });
      wrap.appendChild(this._form);
      this.appendChild(wrap);
    }
    if (this._hass) this._form.hass = this._hass;
    // Seed with defaults so unset toggles show their true state; keep `type`
    // (and any other passthrough keys) so the emitted config stays valid.
    this._form.data = { ...CARD_DEFAULTS, ...(this._config || {}) };
  }
}

if (!customElements.get(EDITOR_TAG)) {
  customElements.define(EDITOR_TAG, MlbLiveGameCardEditor);
}

// Image cache shared across all card instances on the page.
//
// Each entry is { src, status }, where:
//   - src:    the URL to use as <img> src — initially the remote URL,
//             upgraded to a blob: URL once the image has been fetched once.
//   - status: "pending" while a fetch is in flight, "ready" once the blob
//             URL is stored, "failed" if the fetch failed (we keep using
//             the remote URL in that case so the image still loads).
//
// Why blob URLs? When the card re-renders we replace innerHTML, which
// destroys and re-creates every <img>. Even when the URL string is
// identical, ESPN's responses often arrive with cache-control headers
// that force the browser to revalidate (= a fresh network request per
// render). A blob: URL is a local in-memory reference — the browser
// never makes another network request for it.
window.__mlbLiveLogoCache = window.__mlbLiveLogoCache || new Map();

function _scheduleRerender(card) {
  // Cards that triggered a fetch should re-render once the blob URL is
  // ready, so the new <img> uses the cached source. We rAF-coalesce so
  // many concurrent fetch resolutions only fire one render.
  if (!card || typeof card.render !== "function") return;
  if (card._cacheRerenderPending) return;
  card._cacheRerenderPending = true;
  requestAnimationFrame(() => {
    card._cacheRerenderPending = false;
    // Clear the fingerprint so render() doesn't short-circuit.
    card._lastFingerprint = "";
    card.render();
  });
}

function _prefetchImage(card, normalized) {
  const cache = window.__mlbLiveLogoCache;
  const entry = cache.get(normalized);
  if (entry && entry.status !== "pending") return entry.src;
  if (entry && entry.status === "pending") return entry.src;

  // Mark as pending immediately so concurrent requests don't double-fetch.
  cache.set(normalized, { src: normalized, status: "pending" });

  // mode: "no-cors" works for cross-origin image hosts that don't send
  // CORS headers — the resulting opaque blob still works as an <img> src.
  // cache: "force-cache" lets the browser reuse its HTTP cache aggressively.
  fetch(normalized, {
    mode: "no-cors",
    cache: "force-cache",
    referrerPolicy: "no-referrer",
    credentials: "omit",
  })
    .then((resp) => resp.blob())
    .then((blob) => {
      if (!blob || !blob.size) {
        cache.set(normalized, { src: normalized, status: "failed" });
        return;
      }
      const objUrl = URL.createObjectURL(blob);
      cache.set(normalized, { src: objUrl, status: "ready" });
      _scheduleRerender(card);
    })
    .catch(() => {
      // Fetch failed (network, CORS opaque-with-error, etc.). Fall back to
      // the remote URL — the <img> tag still works, we just don't get the
      // blob-URL benefit for this asset.
      cache.set(normalized, { src: normalized, status: "failed" });
    });

  return normalized;
}

function requestCachedLogo(card, url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  const normalized = raw.replace(/^http:/i, "https:");
  const cache = window.__mlbLiveLogoCache;
  const entry = cache.get(normalized);
  if (entry) return entry.src || normalized;
  return _prefetchImage(card, normalized);
}

function get(obj, path, fallback = undefined) {
  let cur = obj;
  for (const key of path) {
    if (cur == null) return fallback;
    cur = cur[key];
  }
  return cur ?? fallback;
}

function parseScore(scoreObj) {
  if (scoreObj && typeof scoreObj === "object") {
    const text =
      scoreObj.displayValue ??
      (scoreObj.value != null ? String(scoreObj.value) : "");
    const num = scoreObj.value != null ? Number(scoreObj.value) : null;
    return { text, num: Number.isFinite(num) ? num : null };
  }
  if (scoreObj == null || scoreObj === "") return { text: "", num: null };
  const num = Number(scoreObj);
  return { text: String(scoreObj), num: Number.isFinite(num) ? num : null };
}

function competitorRecord(competitor, teamPayload) {
  if (competitor?.recordSummary) return String(competitor.recordSummary);
  const records = competitor?.records;
  if (Array.isArray(records) && records.length) {
    const overall =
      records.find((r) => String(r?.type || "").toLowerCase() === "total") ||
      records[0];
    if (overall?.summary) return String(overall.summary);
  }
  if (teamPayload?.record_summary) return String(teamPayload.record_summary);
  return "";
}

function currentBatterName(attrs) {
  return (
    attrs?.current_batter?.display_name ||
    attrs?.current_batter?.short_name ||
    ""
  );
}

// Which lineup side ("away"|"home") a matchup half belongs to:
//   role "batter"  -> the batting team's side
//   role "pitcher" -> the fielding team's side
// Primary signal is the coordinator-set `lineups[*].is_batting` flag (only
// truthy during a live at-bat). When that's unresolvable (pre-game / completed
// game has no current batter to flag), fall back to locating the current
// pitcher/batter id within each side's roster — both signals named in the
// handoff §5 Chunk 4. Returns "" when nothing resolves (side stays inert).
function lineupSideForRole(attrs, role) {
  const lu = attrs && attrs.lineups;
  if (!lu || typeof lu !== "object") return "";
  const away = lu.away || null;
  const home = lu.home || null;
  let battingSide = "";
  if (away && away.is_batting) battingSide = "away";
  else if (home && home.is_batting) battingSide = "home";
  if (battingSide) {
    const fieldingSide = battingSide === "away" ? "home" : "away";
    return role === "pitcher" ? fieldingSide : battingSide;
  }
  const src = role === "pitcher" ? attrs.current_pitcher : attrs.current_batter;
  const id = String((src && src.id) ?? "").trim();
  if (id) {
    const listKey = role === "pitcher" ? "pitchers" : "hitters";
    for (const side of ["away", "home"]) {
      const team = lu[side];
      const arr = team && Array.isArray(team[listKey]) ? team[listKey] : [];
      if (arr.some((p) => String(p && p.id) === id)) return side;
    }
  }
  return "";
}

function formatEventDate(dateRaw) {
  if (!dateRaw) return "";
  const dt = new Date(dateRaw);
  if (Number.isNaN(dt.getTime())) return "";
  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  );
  const startOfTarget = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  const dayDiff = Math.round((startOfTarget - startOfToday) / 86400000);
  const timeText = dt.toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
  if (dayDiff === 0) return `Today ${timeText}`;
  if (dayDiff === 1) return `Tomorrow ${timeText}`;
  if (dayDiff === -1) return `Yesterday ${timeText}`;
  const dateText = dt.toLocaleDateString([], {
    month: "numeric",
    day: "numeric",
  });
  return `${dateText} ${timeText}`;
}

function deriveGameState(attrs) {
  const competition = attrs.competition || {};
  const status = competition?.status || {};
  const type = status?.type || {};
  const mode = String(attrs.mode || "previous").toLowerCase();
  const state = String(type?.state || "").toLowerCase();
  const name = String(type?.name || "").toUpperCase();
  const detail = String(
    type?.detail ||
      type?.shortDetail ||
      type?.statusPrimary ||
      type?.description ||
      attrs.status_text ||
      "",
  ).trim();
  const eventDate = get(competition, ["date"], "");
  const scheduledText = formatEventDate(eventDate);
  // ESPN uses several status flavors for an interrupted live game
  // (STATUS_DELAYED, STATUS_RAIN_DELAY, STATUS_SUSPENDED, ...) and several
  // detail strings ("Delayed", "Rain Delay", "Weather Delay", "Delay: Rain").
  // Match on the shorter "delay" stem and on "suspend" so we flip the live
  // matchup panel off whenever the game is paused, not only when ESPN sends
  // the exact "Delayed" wording.
  const detailLower = detail.toLowerCase();
  const isDelayed =
    attrs.is_delayed === true ||
    name === "STATUS_DELAYED" ||
    name.includes("DELAY") ||
    name.includes("SUSPEND") ||
    detailLower.includes("delay") ||
    detailLower.includes("suspend");
  const isLive =
    attrs.is_live === true ||
    state === "in" ||
    state === "live" ||
    name === "STATUS_IN_PROGRESS" ||
    isDelayed;
  // Postponed/canceled games carry state="post" with completed=false. They
  // must be detected before isFinal — otherwise the "Final" pill + "F" marker
  // render over a misleading 0-0 score.
  const isPostponed =
    name === "STATUS_POSTPONED" ||
    name === "STATUS_CANCELED" ||
    (state === "post" && type?.completed === false) ||
    detail.toLowerCase().startsWith("postponed") ||
    detail.toLowerCase().startsWith("canceled");
  const isFinal =
    !isPostponed &&
    (state === "post" ||
      type?.completed === true ||
      name === "STATUS_FINAL" ||
      detail.toLowerCase().startsWith("final"));
  const isPregame =
    state === "pre" || name === "STATUS_SCHEDULED" || mode === "next";

  if (isDelayed) {
    return {
      pillText: "Delayed",
      pillClass: "delayed",
      statusText: detail || scheduledText || "Game delayed",
    };
  }

  if (isLive) {
    return {
      pillText: "Live",
      pillClass: "live",
      statusText: detail || "In progress",
    };
  }

  if (isPostponed) {
    return {
      pillText: "Postponed",
      pillClass: "postponed",
      statusText: detail || "Postponed",
    };
  }

  if (isFinal) {
    return {
      pillText: "Final",
      pillClass: "final",
      statusText: detail || "Final",
    };
  }

  if (isPregame) {
    return {
      pillText: "Next",
      pillClass: "next",
      statusText: detail || scheduledText || "Scheduled",
    };
  }

  return {
    pillText: mode === "previous" ? "Prev" : mode === "next" ? "Next" : "Game",
    pillClass: "idle",
    statusText: detail || scheduledText || "No game data",
  };
}

function renderDots(count, total, klass) {
  return Array.from(
    { length: total },
    (_, i) => `<span class="dot ${klass} ${i < count ? "on" : ""}"></span>`,
  ).join("");
}

function renderBaseOccupancyRow(situation) {
  const first = situation?.first_last_name || "Empty";
  const second = situation?.second_last_name || "Empty";
  const third = situation?.third_last_name || "Empty";
  const val = (v) =>
    `<span class="base-value ${v === "Empty" ? "empty-value" : "occupied-value"}">${escapeHtml(v)}</span>`;
  return `
    <div class="bases-occupancy-row">
      <div class="base-slot"><span class="base-label">1B:</span> ${val(first)}</div>
      <div class="base-slot"><span class="base-label">2B:</span> ${val(second)}</div>
      <div class="base-slot"><span class="base-label">3B:</span> ${val(third)}</div>
    </div>`;
}

function renderOnDeckRow(onDeck) {
  if (!onDeck || !onDeck.short_name) return "";
  const name = shortPersonName(onDeck.short_name || onDeck.display_name || "");
  if (!name) return "";
  const stats = onDeck.hits_ab || "";
  return `
    <div class="on-deck-row">
      <span class="on-deck-label">On Deck:</span>
      <span class="on-deck-name">${playerNameMarkup(name, onDeck.id)}</span>
      ${stats ? `<span class="on-deck-stats">${escapeHtml(stats)}</span>` : ""}
    </div>`;
}

function renderCountDotsRow(situation, currentPitches = []) {
  const activePitches = Array.isArray(currentPitches)
    ? currentPitches.filter(Boolean)
    : [];

  // Derive balls/strikes from the pitches array when available for consistency
  // with the pitch-by-pitch display, otherwise fall back to situation.
  // Prefer the structured count from the latest pitch's `resultCount`; fall
  // back to text-parsing for entries that pre-date the structured shape or
  // when ESPN omits the resultCount block.
  let balls = 0;
  let strikes = 0;
  if (activePitches.length) {
    const latest = activePitches[activePitches.length - 1];
    const latestObj = latest && typeof latest === "object" ? latest : null;
    if (
      latestObj &&
      Number.isFinite(latestObj.balls) &&
      Number.isFinite(latestObj.strikes)
    ) {
      balls = Number(latestObj.balls);
      strikes = Number(latestObj.strikes);
    } else {
      for (const pitch of activePitches) {
        const p = String(
          (pitch && typeof pitch === "object" ? pitch.text : pitch) || "",
        ).toLowerCase();
        if (p.includes("ball") && !p.includes("foul")) {
          balls++;
        } else if (
          p.includes("strike") ||
          p.includes("foul") ||
          p.includes("swinging") ||
          p === "in play"
        ) {
          // Foul with 2 strikes doesn't add a strike, but we cap at 2 anyway
          strikes++;
        }
      }
    }
    // Cap at max values (4 balls ends at-bat, 3 strikes ends at-bat but foul can exceed)
    balls = Math.min(balls, 3);
    strikes = Math.min(strikes, 2);
  }
  const outs = Number(situation?.outs ?? 0);
  return `
    <div class="count-dots-row prominent verbose">
      <span class="count-pack"><span class="dots-label verbose">Balls:</span><span class="dots">${renderDots(balls, 3, "ball")}</span></span>
      <span class="count-pack"><span class="dots-label verbose">Strikes:</span><span class="dots">${renderDots(strikes, 2, "strike")}</span></span>
      <span class="count-pack"><span class="dots-label verbose">Outs:</span><span class="dots">${renderDots(outs, 3, "out")}</span></span>
    </div>`;
}

function renderWinProbabilityRow(
  winProb,
  ownerSide,
  ownerLabel,
  opponentLabel,
) {
  if (!winProb || typeof winProb !== "object") return "";
  const ownerKey = ownerSide === "home" ? "home" : "away";
  const opponentKey = ownerKey === "home" ? "away" : "home";
  const ownerRaw = Number(winProb[ownerKey]);
  const opponentRaw = Number(winProb[opponentKey]);
  if (!Number.isFinite(ownerRaw) || !Number.isFinite(opponentRaw)) return "";
  if (ownerRaw <= 0 && opponentRaw <= 0) return "";
  // Normalize the two sides so the bar always spans the full width even when
  // ESPN includes a small tiePercentage (which we drop).
  const total = ownerRaw + opponentRaw;
  const ownerPct = total > 0 ? (ownerRaw / total) * 100 : 50;
  const ownerTag = ownerLabel ? `${ownerLabel} ` : "";
  const opponentTag = opponentLabel ? ` ${opponentLabel}` : "";
  // Labels are absolutely positioned at the bar's two edges so they remain
  // fully readable regardless of how lopsided the probabilities are. The
  // colored fill underneath still visualizes the actual ratio.
  return `
    <div class="win-prob-row" title="Win probability">
      <div class="win-prob-bar">
        <div class="win-prob-fill" style="width:${ownerPct.toFixed(1)}%"></div>
        <span class="win-prob-label win-prob-label-owner">${ownerTag}${Math.round(ownerRaw)}%</span>
        <span class="win-prob-label win-prob-label-opponent">${Math.round(opponentRaw)}%${opponentTag}</span>
      </div>
    </div>`;
}

function renderBaseDiamond(situation) {
  const onFirst = !!situation?.on_first;
  const onSecond = !!situation?.on_second;
  const onThird = !!situation?.on_third;
  return `
    <div class="matchup-center diamond-center" aria-label="Base runners">
      <div class="diamond-graphic" role="img" aria-hidden="true">
        <div class="diamond-field"></div>
        <div class="diamond-base home"></div>
        <div class="diamond-base first ${onFirst ? "on" : ""}"></div>
        <div class="diamond-base second ${onSecond ? "on" : ""}"></div>
        <div class="diamond-base third ${onThird ? "on" : ""}"></div>
        <div class="diamond-mound"></div>
      </div>
    </div>`;
}

function renderPitcherLinePrimary(stats) {
  if (!stats || typeof stats !== "object") return "";
  const pitches = String(stats.pitches_strikes || "").trim();
  const pitchCount = pitches ? String(pitches).split("-")[0] : "";
  const strikeouts = String(stats.strikeouts || "").trim();
  const parts = [];
  if (pitchCount) parts.push(`${pitchCount}p`);
  if (strikeouts) parts.push(`${strikeouts}k`);
  return parts.join(" • ");
}

function renderPitcherLineSecondary(stats) {
  const inningsPitched = String(
    stats?.innings_pitched ?? stats?.ip ?? "",
  ).trim();
  const era = String(stats?.era ?? "").trim();
  const parts = [];
  if (inningsPitched) parts.push(`IP: ${inningsPitched}`);
  if (era) parts.push(`ERA: ${era}`);
  return parts.join("  ");
}

function renderBatterLineSecondary(stats) {
  if (!stats || typeof stats !== "object") return "";
  const avg = String(stats.avg || "").trim();
  const hr = String(stats.hr || "").trim();
  const rbi = String(stats.rbi || "").trim();
  const parts = [];
  if (avg) parts.push(avg);
  if (hr) parts.push(`${hr}hr`);
  if (rbi) parts.push(`${rbi}rbi`);
  return parts.join(" • ");
}

function deriveInningState(attrs) {
  const competition = attrs.competition || {};
  const status = competition?.status || {};
  const type = status?.type || {};
  const period =
    Number(
      status?.period ?? type?.period ?? competition?.status?.period ?? 0,
    ) || 0;
  const prefixText = String(
    status?.periodPrefix ||
      type?.periodPrefix ||
      type?.detail ||
      type?.shortDetail ||
      type?.statusPrimary ||
      "",
  ).trim();
  const lower = prefixText.toLowerCase();
  const away =
    (competition?.competitors || []).find((c) => c?.homeAway === "away") || {};
  const home =
    (competition?.competitors || []).find((c) => c?.homeAway === "home") || {};
  const awayScore = parseScore(away?.score).num;
  const homeScore = parseScore(home?.score).num;
  const tied =
    awayScore != null && homeScore != null && awayScore === homeScore;
  const homeLeading =
    awayScore != null && homeScore != null && homeScore > awayScore;
  const pseudoFinal =
    attrs.is_live === true &&
    period >= 9 &&
    !tied &&
    (lower.startsWith("end") || (lower.startsWith("mid") && homeLeading));
  const isTop = lower.startsWith("top") || lower.startsWith("t ");
  const isBottom =
    lower.startsWith("bottom") ||
    lower.startsWith("bot") ||
    lower.startsWith("b ");
  const isMid = lower.startsWith("mid") || lower.startsWith("end");
  return { period, prefixText, lower, pseudoFinal, isTop, isBottom, isMid };
}

function teamTotals(competitor) {
  const lines = Array.isArray(competitor?.linescores)
    ? competitor.linescores
    : [];
  const hits =
    competitor?.hits ??
    lines.reduce((sum, line) => sum + (Number(line?.hits) || 0), 0);
  const errors =
    competitor?.errors ??
    lines.reduce((sum, line) => sum + (Number(line?.errors) || 0), 0);
  return {
    hits: Number.isFinite(Number(hits)) ? String(hits) : "—",
    errors: Number.isFinite(Number(errors)) ? String(errors) : "—",
  };
}

function shortPersonName(name) {
  const value = String(name || "").trim();
  if (!value) return "";
  const parts = value.split(/\s+/).filter(Boolean);
  if (parts.length < 2) return value;
  // Detect a generational suffix at the end (Jr., Sr., II, III, IV, V, etc.)
  // and keep it attached to the last name so we don't lose it when abbreviating
  // the first name (e.g. "Vladimir Guerrero Jr." -> "V. Guerrero Jr.").
  const SUFFIX_RE = /^(?:[JS]r\.?|I{1,3}|IV|VI{0,3})$/i;
  let suffix = "";
  if (parts.length >= 3 && SUFFIX_RE.test(parts[parts.length - 1])) {
    suffix = ` ${parts.pop()}`;
  }
  return `${parts[0].charAt(0)}. ${parts[parts.length - 1]}${suffix}`;
}

// Wrap an already display-formatted player name in a clickable link to that
// player's ESPN profile. Falls back to plain text when no athlete id is
// available (ESPN occasionally omits it) so the name is never lost. Click /
// keyboard activation is handled by the delegated listeners on .card-content.
function playerNameMarkup(name, athleteId) {
  const text = escapeHtml(name == null ? "" : name);
  const id = String(athleteId || "").trim();
  if (!id || !text) return text;
  return `<span class="player-link" role="link" tabindex="0" data-athlete-id="${escapeHtml(id)}" title="View ${text} on ESPN">${text}</span>`;
}

function renderPlayerHeadshot(card, url, alt = "") {
  const src = requestCachedLogo(card, url);
  return src
    ? `<img class="player-shot" src="${src}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
    : `<div class="player-shot placeholder"></div>`;
}

function escapeHtml(value) {
  return String(value == null ? "" : value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
}

// ESPN's human-facing player page. Used both for the `player_link_target:
// espn` direct-open mode (Option A behavior) and the popup footer link, so
// the URL shape lives in one place.
function espnPlayerUrl(id) {
  return `https://www.espn.com/mlb/player/_/id/${encodeURIComponent(String(id == null ? "" : id))}`;
}

// Build the player career popup body — bio header + season-by-season career
// table — from a normalized PlayerCard (see coordinator._parse_player_card /
// types.PlayerCard). Pure string builder (no DOM/`this`) so it can be
// unit-checked offline against the Chunk 0 fixtures; the caller pre-resolves
// `headshotSrc` via requestCachedLogo. Tolerates a bio-only or stats-only
// card: a missing career table degrades to a short note, the bio header to
// whatever fields are present.
function playerCardBodyHtml(card, headshotSrc) {
  const safe = card && typeof card === "object" ? card : {};
  const bio = safe.bio || {};
  const career = safe.career || {};
  const glossary = safe.glossary || {};

  const shot = headshotSrc
    ? `<img class="mlb-pc-shot" src="${escapeHtml(headshotSrc)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
    : `<div class="mlb-pc-shot mlb-pc-shot--ph" aria-hidden="true"></div>`;

  const chips = [
    bio.team,
    bio.position,
    bio.bats_throws ? `B/T ${bio.bats_throws}` : "",
    bio.height,
    bio.weight,
    bio.age ? `Age ${bio.age}` : "",
    bio.jersey ? `#${bio.jersey}` : "",
  ].filter((x) => x && String(x).trim());
  const sub = [
    bio.draft ? `Draft: ${bio.draft}` : "",
    bio.debut_year ? `MLB debut ${bio.debut_year}` : "",
  ].filter((x) => x && String(x).trim());

  const header =
    `<div class="mlb-pc-bio">${shot}<div class="mlb-pc-bio-meta">` +
    (chips.length
      ? `<div class="mlb-pc-bio-line">${chips.map((c) => escapeHtml(c)).join(" · ")}</div>`
      : "") +
    (sub.length
      ? `<div class="mlb-pc-bio-sub">${sub.map((c) => escapeHtml(c)).join(" · ")}</div>`
      : "") +
    `</div></div>`;

  const columns = Array.isArray(career.columns) ? career.columns : [];
  const seasons = Array.isArray(career.seasons) ? career.seasons : [];
  const totals = Array.isArray(career.totals) ? career.totals : [];

  let table;
  if (columns.length && seasons.length) {
    const headCells = columns
      .map((label) => {
        const tip = glossary[label];
        return `<th scope="col"${tip ? ` title="${escapeHtml(tip)}"` : ""}>${escapeHtml(label)}</th>`;
      })
      .join("");
    const bodyRows = seasons
      .map((s) => {
        const cells = columns
          .map((_, i) => `<td>${escapeHtml((s.stats || [])[i] ?? "")}</td>`)
          .join("");
        return (
          `<tr><th scope="row">${escapeHtml(s.year || "")}</th>` +
          `<td class="mlb-pc-team">${escapeHtml(s.team || "")}</td>${cells}</tr>`
        );
      })
      .join("");
    const totalRow = totals.length
      ? `<tr class="mlb-pc-total"><th scope="row">Career</th><td class="mlb-pc-team"></td>` +
        columns
          .map((_, i) => `<td>${escapeHtml(totals[i] ?? "")}</td>`)
          .join("") +
        `</tr>`
      : "";
    const kind = career.kind === "pitching" ? "Pitching" : "Batting";
    // tabindex+role so keyboard-only users can scroll the wide table while
    // focus is trapped in the dialog (WCAG 2.1.1); :focus-visible styled.
    table =
      `<div class="mlb-pc-table-wrap" tabindex="0" role="group" ` +
      `aria-label="${escapeHtml(kind)} career stats, scrollable"><table class="mlb-pc-table">` +
      `<caption class="mlb-pc-caption">${kind} — career by season</caption>` +
      `<thead><tr><th scope="col">Year</th><th scope="col">Team</th>${headCells}</tr></thead>` +
      `<tbody>${bodyRows}${totalRow}</tbody></table></div>`;
  } else {
    table = `<div class="mlb-pc-msg mlb-pc-nostats">No career stats available for this player.</div>`;
  }

  return header + table;
}

// Build the lineup popup body — a Hitters table + a Pitchers table for one
// team — from a LineupTeam (see coordinator._normalize_lineups /
// types.LineupTeam). Pure string builder (no DOM/`this`) so it can be
// unit-checked offline against the Chunk 0 box-score fixture.
//
// `view` is "game" (synchronous, straight from the entity attribute) or
// "season". For the season view, `seasonStats` maps athlete id ->
// { hitting?: {ab,h,hr,rbi,sb,avg}, pitching?: {w,l,era,ip,k,whip} } as
// returned by the mlb_live_scoreboard/team_season_stats WS command (wired
// in Chunk 5); a player with no season entry shows em dashes. Rows are
// pre-sorted by the backend (batting order, starter before sub); a
// subbed-out player (active === false) is dimmed so the table reads like a
// box score listing everyone who appeared.
//
// In the Game view only, the hitter who is "up" is flagged with a small
// arrow. The backend supplies the slot as `team.up_bat_order` for BOTH sides
// (see coordinator._up_bat_order): for the batting team it is the batter at
// the plate, for the team in the field the leadoff man of its next
// half-inning — so the marker appears either way. It is deliberately absent
// from the Season view, where a live batting order has no meaning.
function lineupTablesHtml(team, view, seasonStats) {
  const t = team && typeof team === "object" ? team : {};
  const hitters = Array.isArray(t.hitters) ? t.hitters : [];
  const pitchers = Array.isArray(t.pitchers) ? t.pitchers : [];
  if (!hitters.length && !pitchers.length) {
    return `<div class="mlb-lu-msg">Lineup not posted yet.</div>`;
  }
  const ss = seasonStats && typeof seasonStats === "object" ? seasonStats : {};
  const isSeason = view === "season";
  const DASH = "—";

  const cell = (v) =>
    `<td>${escapeHtml(v == null || v === "" ? DASH : v)}</td>`;
  const pair = (a, b) => {
    const x = a == null || a === "" ? "" : String(a);
    const y = b == null || b === "" ? "" : String(b);
    return x === "" && y === "" ? DASH : `${x || "0"}-${y || "0"}`;
  };

  const hitCols = isSeason
    ? ["H-AB", "HR", "RBI", "SB", "AVG"]
    : ["AB", "R", "H", "HR", "RBI", "BB", "K", "AVG"];
  const hitHead =
    `<tr><th scope="col" class="mlb-lu-num">#</th>` +
    `<th scope="col" class="mlb-lu-pos">Pos</th>` +
    `<th scope="col" class="mlb-lu-name">Hitter</th>` +
    hitCols.map((c) => `<th scope="col">${escapeHtml(c)}</th>`).join("") +
    `</tr>`;
  // Slot to mark, or 0 for "no marker" (not live, anchor unresolved, or
  // Season view). A substitution leaves two rows sharing one slot, so the
  // marker also requires the row to be the one still in the game — otherwise
  // it would land on the player who was lifted.
  const upSlot = isSeason ? 0 : Number(t.up_bat_order) || 0;
  // `is_batting` is derived from the current batter, so it is true exactly
  // when the marked slot is the man in the box rather than the one due up —
  // which is what the announced label needs to distinguish.
  const upIsAtPlate = !!t.is_batting;
  const hitRows = hitters
    .map((h) => {
      const cls = h.active === false ? ' class="mlb-lu-out"' : "";
      const ord = h.bat_order ? String(h.bat_order) : "";
      const isUp =
        upSlot > 0 && Number(h.bat_order) === upSlot && h.active !== false;
      const s = (ss[String(h.id)] || {}).hitting || {};
      const cells = isSeason
        ? cell(pair(s.h, s.ab)) + [s.hr, s.rbi, s.sb, s.avg].map(cell).join("")
        : [h.ab, h.r, h.h, h.hr, h.rbi, h.bb, h.k, h.avg].map(cell).join("");
      // The arrow lives in a fixed-width span present on EVERY row, so the
      // names stay aligned in the column instead of the marked one jogging
      // right. aria-hidden on the glyph + a visually-hidden phrase keeps it
      // from being announced as punctuation.
      const marker =
        `<span class="mlb-lu-up" aria-hidden="true">${isUp ? "\u25B8" : ""}</span>` +
        (isUp
          ? `<span class="mlb-lu-sr">${upIsAtPlate ? "Now batting" : "Batting next"}: </span>`
          : "");
      return (
        `<tr${cls}>` +
        `<th scope="row" class="mlb-lu-num">${escapeHtml(ord)}</th>` +
        `<td class="mlb-lu-pos">${escapeHtml(h.position || "")}</td>` +
        `<td class="mlb-lu-name">${marker}${escapeHtml(
          shortPersonName(h.name || h.short_name || DASH),
        )}</td>` +
        cells +
        `</tr>`
      );
    })
    .join("");

  const pitCols = isSeason
    ? ["W-L", "ERA", "IP", "K", "WHIP"]
    : ["Dec", "IP", "H", "R", "ER", "BB", "K", "PC", "ERA"];
  const pitHead =
    `<tr><th scope="col" class="mlb-lu-name">Pitcher</th>` +
    pitCols.map((c) => `<th scope="col">${escapeHtml(c)}</th>`).join("") +
    `</tr>`;
  const pitRows = pitchers
    .map((p) => {
      const cls = p.active === false ? ' class="mlb-lu-out"' : "";
      const s = (ss[String(p.id)] || {}).pitching || {};
      const cells = isSeason
        ? cell(pair(s.w, s.l)) + [s.era, s.ip, s.k, s.whip].map(cell).join("")
        : [p.decision, p.ip, p.h, p.r, p.er, p.bb, p.k, p.pc, p.era]
            .map(cell)
            .join("");
      return (
        `<tr${cls}>` +
        `<th scope="row" class="mlb-lu-name">${escapeHtml(
          shortPersonName(p.name || p.short_name || DASH),
        )}</th>` +
        cells +
        `</tr>`
      );
    })
    .join("");

  const section = (label, head, rows, n) =>
    rows
      ? `<div class="mlb-lu-table-wrap" tabindex="0" role="group" ` +
        `aria-label="${escapeHtml(label)}, scrollable">` +
        `<table class="mlb-lu-table">` +
        `<caption class="mlb-lu-caption">${escapeHtml(label)} (${n})</caption>` +
        `<thead>${head}</thead><tbody>${rows}</tbody></table></div>`
      : "";

  return (
    section("Hitters", hitHead, hitRows, hitters.length) +
    section("Pitchers", pitHead, pitRows, pitchers.length)
  );
}

function renderDueUpCards(card, dueUp, inningDescription = "") {
  const list = Array.isArray(dueUp) ? dueUp.filter(Boolean).slice(0, 3) : [];
  if (!list.length) return "";
  const desc = String(
    inningDescription ||
      (Array.isArray(dueUp) && dueUp.length
        ? (dueUp[0] && dueUp[0].inning_desc) ||
          [dueUp[0]?.period_prefix, dueUp[0]?.display_period]
            .filter(Boolean)
            .join(" ") ||
          ""
        : ""),
  ).trim();
  return `
    <div class="dueup-panel">
      <div class="dueup-title">${desc ? `${escapeHtml(desc)}&nbsp;&nbsp;Due up` : "Due up"}</div>
      <div class="dueup-grid">
        ${list
          .map(
            (item) => `
          <div class="dueup-card">
            ${renderPlayerHeadshot(card, item.headshot || "", item.short_name || item.display_name || "")}
            <div class="dueup-name">${playerNameMarkup(shortPersonName(item.short_name || item.display_name || "—"), item.id)}</div>
            <div class="dueup-stat">${escapeHtml([item.avg, item.hits_ab].filter(Boolean).join(" • ") || "—")}</div>
          </div>`,
          )
          .join("")}
      </div>
    </div>`;
}

function renderCompactStatLine(stats, pairs) {
  if (!stats || typeof stats !== "object") return "";
  const out = [];
  for (const [label, key] of pairs) {
    const val = String(stats?.[key] ?? "").trim();
    if (val) out.push(`${label} ${val}`);
  }
  return out.join(" • ");
}

function renderUpcomingPitcherSide(
  card,
  pitcher,
  fallbackLogo,
  alignRight = false,
) {
  const safe = pitcher || {};
  const name = String(safe.short_name || safe.name || "").trim();
  const displayName = name ? shortPersonName(name) : "TBD";
  const headshot = safe.headshot ? requestCachedLogo(card, safe.headshot) : "";
  const logo =
    !headshot && fallbackLogo ? requestCachedLogo(card, fallbackLogo) : "";
  const portrait = headshot
    ? `<img class="upcoming-pitcher-img" src="${headshot}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
    : logo
      ? `<img class="upcoming-pitcher-img logo-fallback" src="${logo}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
      : `<div class="upcoming-pitcher-img placeholder"></div>`;
  const record = String(safe.record || "").trim();
  const era = String(safe.era || "").trim();
  const recordLine = record ? `${record}` : "";
  const eraLine = era ? `${era} ERA` : "";
  return `
    <div class="upcoming-pitcher-side ${alignRight ? "align-right" : ""}">
      ${portrait}
      <div class="upcoming-pitcher-name">${playerNameMarkup(displayName, safe.id)}</div>
      <div class="upcoming-pitcher-stat">${escapeHtml(recordLine || "—")}</div>
      <div class="upcoming-pitcher-stat secondary">${escapeHtml(eraLine || "")}</div>
    </div>`;
}

// Compact label for a scoring play's inning, e.g. "T3" / "B9". ESPN's
// `period_type` is typically "Top"/"Bottom" — we keep the first character
// and append the inning number. Falls back to just the number when ESPN
// omits the half-inning label.
function formatScoringPlayPeriod(periodType, periodNumber) {
  const half = String(periodType || "")
    .trim()
    .toUpperCase();
  const num = Number(periodNumber);
  if (!Number.isFinite(num) || num <= 0) return half ? half[0] : "";
  const letter = half ? half[0] : "";
  return letter ? `${letter}${num}` : `${num}`;
}

// Build the decisions sub-panel for the final-game expand view —
// winning pitcher, losing pitcher, and save (when there is one). Reads
// `attrs.decisions` (W/L/SV id + record from box-score notes) and
// cross-references each pitcher's game line in `attrs.lineups` to avoid
// duplicating per-pitcher stats in the sensor payload. Empty (no panel)
// until ESPN attaches the decision notes (post-final).
function renderDecisionsPanel(card, attrs) {
  const decisions = attrs?.decisions || {};
  const lineups = attrs?.lineups || {};
  const findPitcher = (side, id) => {
    if (!id) return null;
    const pitchers = (lineups[side] && lineups[side].pitchers) || [];
    return pitchers.find((p) => String(p?.id || "") === String(id)) || null;
  };
  // Compact `IP · H · ER · K · BB` line using the card's standard dot
  // separator. Space between value and unit (`0.2 IP`) for readability;
  // separator stays tight (no surrounding spaces) so the line wraps as
  // few times as possible in the narrow 3-column grid.
  const formatStatLine = (pitcher) => {
    if (!pitcher) return "";
    const parts = [
      ["ip", "IP"],
      ["h", "H"],
      ["er", "ER"],
      ["k", "K"],
      ["bb", "BB"],
    ]
      .map(([key, unit]) => {
        const v = String(pitcher[key] ?? "").trim();
        return v ? `${v} ${unit}` : "";
      })
      .filter(Boolean);
    return parts.join("·");
  };
  const order = [
    ["win", "Win"],
    ["loss", "Loss"],
    ["save", "Save"],
  ];
  const cells = order
    .map(([key, label]) => {
      const d = decisions[key];
      if (!d || !(d.id || d.name)) return "";
      const name = String(d.short_name || d.name || "").trim();
      const displayName = name ? shortPersonName(name) : "";
      // ESPN's "record" tail is a W-L for win/loss and a single season
      // save count for SV. The label above already disambiguates, so we
      // just wrap whatever's there in parens.
      const recordRaw = String(d.record || "").trim();
      const recordParen = recordRaw ? `(${recordRaw})` : "";
      const headshot = d.headshot ? requestCachedLogo(card, d.headshot) : "";
      const portrait = headshot
        ? `<img class="decision-img" src="${headshot}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
        : `<div class="decision-img placeholder"></div>`;
      const statLine = formatStatLine(findPitcher(d.team_side, d.id));
      return `
        <div class="decision-cell">
          ${portrait}
          <div class="decision-label">${escapeHtml(label)}</div>
          <div class="decision-name">${playerNameMarkup(displayName, d.id)}${recordParen ? ` <span class="decision-record">${escapeHtml(recordParen)}</span>` : ""}</div>
          ${statLine ? `<div class="decision-stats">${escapeHtml(statLine)}</div>` : ""}
        </div>`;
    })
    .filter(Boolean)
    .join("");
  if (!cells) return "";
  return `
      <div class="decisions-panel">
        <div class="decisions-grid">${cells}</div>
      </div>`;
}

// Build the per-game "Scoring Plays" sub-panel for the final-game expand
// view. Reads `attrs.scoring_plays` (whole-game, chronological — distinct
// from `recent_plays` which is half-inning-bounded). Each row is a compact
// `inning · scorer · play text · score-after` line; we mark the scoring
// team by comparing each play's score to its predecessor's.
function renderScoringPlaysPanel(attrs, awayMeta, homeMeta) {
  const plays = Array.isArray(attrs?.scoring_plays) ? attrs.scoring_plays : [];
  if (!plays.length) return "";
  const awayId = String(awayMeta?.id || "");
  const homeId = String(homeMeta?.id || "");
  const awayAbbr = String(
    awayMeta?.abbreviation || awayMeta?.short_name || "AWAY",
  ).toUpperCase();
  const homeAbbr = String(
    homeMeta?.abbreviation || homeMeta?.short_name || "HOME",
  ).toUpperCase();
  let prevAway = null;
  let prevHome = null;
  const rows = plays
    .map((play) => {
      const period = formatScoringPlayPeriod(
        play?.period_type,
        play?.period_number,
      );
      const away = play?.away_score;
      const home = play?.home_score;
      // Identify the scoring side: prefer ESPN's team_id (most reliable;
      // present on home runs and most scoring plays); fall back to the score
      // delta vs. the previous scoring play so we still tag the side when
      // team_id is missing.
      let scorerAbbr = "";
      const teamId = String(play?.team_id || "");
      if (teamId && teamId === awayId) scorerAbbr = awayAbbr;
      else if (teamId && teamId === homeId) scorerAbbr = homeAbbr;
      else if (away != null && home != null) {
        if (prevAway != null && Number(away) > Number(prevAway))
          scorerAbbr = awayAbbr;
        else if (prevHome != null && Number(home) > Number(prevHome))
          scorerAbbr = homeAbbr;
      }
      if (away != null && away !== "") prevAway = away;
      if (home != null && home !== "") prevHome = home;
      const scoreAfter = away != null && home != null ? `${away}-${home}` : "";
      const text = escapeHtml(String(play?.text || "").trim());
      return `
        <div class="scoring-play-row">
          <span class="scoring-play-period">${period || "—"}</span>
          <span class="scoring-play-team">${scorerAbbr || ""}</span>
          <span class="scoring-play-text">${text}</span>
          <span class="scoring-play-score">${scoreAfter}</span>
        </div>`;
    })
    .join("");
  return `
      <div class="scoring-plays-panel">
        <div class="panel-heading">Scoring Plays</div>
        ${rows}
      </div>`;
}

// Build the "Game Leaders" sub-panel — two columns (away / home), each a
// short list of top-N stat-category leaders. Reads `attrs.leaders`, which
// the coordinator normalizes from ESPN's per-team leaders block. Player
// names route through `playerNameMarkup` so they open the career popup.
function renderGameLeadersPanel(attrs, awayMeta, homeMeta) {
  const leaders = attrs?.leaders || {};
  const awayList = Array.isArray(leaders.away)
    ? leaders.away.filter(Boolean)
    : [];
  const homeList = Array.isArray(leaders.home)
    ? leaders.home.filter(Boolean)
    : [];
  if (!awayList.length && !homeList.length) return "";
  const awayAbbr = String(
    awayMeta?.abbreviation || awayMeta?.short_name || "AWAY",
  ).toUpperCase();
  const homeAbbr = String(
    homeMeta?.abbreviation || homeMeta?.short_name || "HOME",
  ).toUpperCase();
  const buildColumn = (items, abbr) => {
    const rows = items
      .map(
        (item) => `
            <div class="leader-item">
              <span class="leader-cat">${escapeHtml(item.category || "")}</span>
              <span class="leader-name">${playerNameMarkup(shortPersonName(item.name || ""), item.id)}</span>
              <span class="leader-val">${escapeHtml(item.value || "")}</span>
            </div>`,
      )
      .join("");
    return `
        <div class="leaders-col">
          <div class="leaders-head">${escapeHtml(abbr)}</div>
          ${rows || `<div class="leader-item leader-empty">—</div>`}
        </div>`;
  };
  return `
      <div class="leaders-panel">
        <div class="panel-heading">Game Leaders</div>
        <div class="leaders-grid">
          ${buildColumn(awayList, awayAbbr)}
          ${buildColumn(homeList, homeAbbr)}
        </div>
      </div>`;
}

function renderUpcomingDetails(
  card,
  attrs,
  awayMeta,
  homeMeta,
  awayLogo,
  homeLogo,
  options = {},
) {
  // `kind` selects the layout. The historical default is the upcoming-game
  // panel (probable pitchers + standings). When called with kind: "final",
  // we additionally render scoring plays + game leaders above the standings.
  // `includePitchers` is retained for back-compat with older call sites.
  const kind =
    options.kind || (options.includePitchers === false ? "final" : "upcoming");
  const includePitchers = kind === "upcoming";
  const probables = attrs?.probable_pitchers || {};
  const standings = attrs?.division_standings || {};
  const teamId = String(attrs?.team_id || "");
  const entries = Array.isArray(standings.entries) ? standings.entries : [];
  const pitchersHtml = includePitchers
    ? `
    <div class="upcoming-pitchers-grid">
      ${renderUpcomingPitcherSide(card, probables.away, awayLogo || awayMeta?.logo || "", false)}
      <div class="upcoming-pitchers-vs">vs</div>
      ${renderUpcomingPitcherSide(card, probables.home, homeLogo || homeMeta?.logo || "", true)}
    </div>`
    : "";
  const decisionsHtml =
    kind === "final" ? renderDecisionsPanel(card, attrs) : "";
  const scoringPlaysHtml =
    kind === "final" ? renderScoringPlaysPanel(attrs, awayMeta, homeMeta) : "";
  const leadersHtml =
    kind === "final" ? renderGameLeadersPanel(attrs, awayMeta, homeMeta) : "";
  // Highlights gallery link — ESPN populates this 30-90 min after the final
  // pitch. Hidden until the URL is present, so the panel stays clean in the
  // immediate post-final window before clips are published. Opt-in via
  // `show_highlights` because most users don't want it in their dashboard.
  const highlightsEnabled = card?.config?.show_highlights === true;
  const highlightsUrl =
    kind === "final" && highlightsEnabled
      ? String(attrs?.highlights_url || "").trim()
      : "";
  const highlightsHtml = highlightsUrl
    ? `
    <div class="final-highlights-row">
      <a class="final-highlights-link" href="${highlightsUrl}" target="_blank" rel="noopener noreferrer">
        <span class="final-highlights-icon" aria-hidden="true">▶</span>
        <span class="final-highlights-label">Watch highlights on ESPN</span>
      </a>
    </div>`
    : "";
  // Standings reflect *today's* table, not the table on a past game's date, so
  // they're misleading on an older final reached via schedule navigation (and
  // the current game's card already shows them). Only surface standings for an
  // upcoming game or for the single most-recent final — identifiable because
  // its own event id equals the schedule's `previous_event_id`.
  const displayId = String(attrs?.display_event_id || "");
  const isMostRecentFinal =
    displayId !== "" && displayId === String(attrs?.previous_event_id || "");
  const showStandings = kind !== "final" || isMostRecentFinal;
  let standingsHtml = "";
  if (entries.length && showStandings) {
    const rows = entries
      .map((entry) => {
        const isMyTeam =
          String(entry.team_id || "") === teamId && teamId !== "";
        const wl = `${entry.wins || "—"}-${entry.losses || "—"}`;
        const gb = String(entry.games_back || "").trim() || "—";
        const name =
          String(entry.team_short_name || entry.team_name || "").trim() || "—";
        return `
        <div class="standings-row ${isMyTeam ? "my-team" : ""}">
          <span class="standings-name">${escapeHtml(name)}</span>
          <span class="standings-wl">${escapeHtml(wl)}</span>
          <span class="standings-gb">${escapeHtml(gb)}</span>
        </div>`;
      })
      .join("");
    const heading = standings.division_name
      ? `<div class="standings-heading">${escapeHtml(standings.division_name)}</div>`
      : "";
    standingsHtml = `
      <div class="upcoming-standings">
        ${heading}
        <div class="standings-row standings-header">
          <span class="standings-name">Team</span>
          <span class="standings-wl">W-L</span>
          <span class="standings-gb">GB</span>
        </div>
        ${rows}
      </div>`;
  }
  return `
    <div class="upcoming-details-panel">
      ${pitchersHtml}
      ${decisionsHtml}
      ${scoringPlaysHtml}
      ${leadersHtml}
      ${highlightsHtml}
      ${standingsHtml}
    </div>`;
}

function renderPlayIndicator(play, previousContext = {}) {
  const playText = String(play?.text || "").toLowerCase();
  const altType = String(
    play?.alternative_type || play?.alternativeType || "",
  ).toLowerCase();
  const typeText = String(play?.type || play?.play_type || "").toLowerCase();

  const away = play?.away_score;
  const home = play?.home_score;
  const prevAway = previousContext?.away_score;
  const prevHome = previousContext?.home_score;
  const prevOuts = previousContext?.outs;

  const scoreValue = Number(play?.score_value ?? play?.scoreValue ?? 0);
  const scoringFlag =
    play?.scoring_play === true || play?.scoringPlay === true || scoreValue > 0;
  const looksLikePitchingChange =
    playText.includes(" relieved ") ||
    playText.includes(" replaces ") ||
    playText.includes(" comes in for ") ||
    altType.includes("lineup-change") ||
    typeText.includes("lineup-change");

  if (
    !looksLikePitchingChange &&
    away != null &&
    home != null &&
    away !== "" &&
    home !== ""
  ) {
    const changedVsPrevious =
      prevAway != null &&
      prevHome != null &&
      (String(away) !== String(prevAway) || String(home) !== String(prevHome));
    if (scoringFlag || changedVsPrevious) return `${away}-${home}`;
  }

  const outs = Number(play?.outs);
  if (Number.isFinite(outs)) {
    const previousKnownOuts = Number.isFinite(Number(prevOuts))
      ? Number(prevOuts)
      : null;
    if (outs > 0 && previousKnownOuts != null && outs > previousKnownOuts)
      return `${"•".repeat(Math.max(outs, 1))} Outs`;
    if (outs === 3 && previousKnownOuts == null) return `${"•".repeat(3)} Outs`;
  }
  return "";
}

// Color palette for pitch-zone dots, keyed by ESPN's `pitch_type_abbr`. The
// chosen hues are roughly distinct under both light and dark HA themes and
// keep the family separation that broadcasters use (fastballs warm, breaking
// balls cool, off-speed magenta/brown). Unknown abbreviations fall back to
// a neutral gray so wild/unmapped pitches still plot.
const PITCH_TYPE_COLOR = {
  FF: "#e53935", // Four-seam Fastball
  FT: "#fb8c00", // Two-seam Fastball
  SI: "#fb8c00", // Sinker (warm, fastball family)
  FC: "#fbc02d", // Cutter
  SL: "#43a047", // Slider
  ST: "#26a69a", // Sweeper
  CU: "#1e88e5", // Curveball
  KC: "#7e57c2", // Knuckle Curve
  CH: "#ec407a", // Changeup
  FS: "#8d6e63", // Splitter
  KN: "#90a4ae", // Knuckleball
  EP: "#90a4ae", // Eephus
};
const PITCH_TYPE_COLOR_FALLBACK = "#9e9e9e";

// Shorten the few ESPN pitch-type strings that are verbose. Anything not in
// the map is kept as-is — ESPN's `pitchType.text` is already reasonably
// short for the common pitches (Slider, Changeup, Cutter, Sinker, …).
const PITCH_TYPE_DISPLAY = {
  "Four-seam FB": "Fastball",
  "Four-seam Fastball": "Fastball",
  "Two-seam FB": "Two-seam",
  "Two-seam Fastball": "Two-seam",
  "Knuckle Curve": "Knuckle Cv",
};

function prettyPitchType(name) {
  const trimmed = String(name || "").trim();
  if (!trimmed) return "";
  return PITCH_TYPE_DISPLAY[trimmed] || trimmed;
}

// Format one entry of `current_pitches`. Accepts the structured shape
// emitted by the coordinator ({text, pitch_type, velocity, result, ...}) and
// falls back to the bare string the coordinator used to emit. Output:
// "Fastball 95 (Strike Looking) · 1" — content first, sequence number
// trailing after the project's usual middle-dot separator.
function formatPitchLine(pitch, { html = false } = {}) {
  // ESPN-sourced strings are escaped only on the HTML path — the plain-text
  // path feeds SVG <title> tooltips whose caller escapes the whole line.
  const esc = html ? escapeHtml : (s) => String(s);
  if (!pitch) return "";
  if (typeof pitch === "string") return esc(pitch.trim());
  if (typeof pitch !== "object") return esc(String(pitch).trim());
  const text = String(pitch.text || "").trim();
  const seqMatch = text.match(/^Pitch\s+(\d+)\s*:/i);
  const seqNum = seqMatch ? seqMatch[1] : "";
  const typeLabel = prettyPitchType(pitch.pitch_type);
  const vel = Number(pitch.velocity);
  const result = String(pitch.result || "").trim();
  const pieces = [];
  if (typeLabel) pieces.push(esc(typeLabel));
  if (Number.isFinite(vel) && vel > 0) {
    // Triple-digit velocities get a cosmetic red/bold highlight, but only in
    // the HTML render path — the plain-text path feeds the pitch-zone tooltip
    // (escaped), where markup would show up literally.
    pieces.push(
      html && vel >= 100
        ? `<span class="pitch-velo-hot">${vel}</span>`
        : String(vel),
    );
  }
  let line = pieces.join(" ");
  if (result) line += `${line ? " " : ""}(${esc(result)})`;
  // If we have nothing structured to add, the original ESPN text is more
  // informative than just a bare sequence number.
  if (!pieces.length && !result) return esc(text);
  return seqNum ? `${line} · ${seqNum}` : line;
}

// Render the in-at-bat pitch-zone SVG. One numbered dot per pitch, colored
// by pitch type, with a regulation-zone outline for reference and a home
// plate icon for orientation. Returns "" when no pitch has a coordinate so
// the caller can skip layout for empty at-bats.
//
// Coordinate system: ESPN supplies catcher's-POV pixel coords on a roughly
// 0-220 wide canvas; we keep the data as-is and rely on viewBox scaling.
// The viewBox is cropped to y=80..270 (190 units tall) so the visible
// canvas hugs the strike zone — the upper 80 units of ESPN's nominal
// 0-270 range were nearly always blank padding and pushed the zone rect
// uncomfortably far below the diamond.
function renderPitchZone(pitches) {
  if (!Array.isArray(pitches) || !pitches.length) return "";
  const plottable = pitches.filter(
    (p) =>
      p &&
      typeof p === "object" &&
      p.pitch_coordinate &&
      Number.isFinite(Number(p.pitch_coordinate.x)) &&
      Number.isFinite(Number(p.pitch_coordinate.y)),
  );
  if (!plottable.length) return "";
  // Regulation-zone box, empirically tuned from called-strike clusters
  // (x in [78,144], y in [132,197] in ESPN's units).
  const ZONE = { x: 78, y: 132, w: 66, h: 65 };
  // Cropped viewBox — start at y=80 (the top of ESPN's canvas is mostly
  // empty padding above the zone) so the rect hugs the diamond above.
  const VB = { x: 0, y: 80, w: 220, h: 190 };
  const dots = plottable
    .map((p, idx) => {
      // Number dots by the pitch's own sequence ("Pitch N: ...") so labels
      // stay aligned with the pitch list even when a pitch without a
      // coordinate was filtered out above; fall back to the plot index.
      const seqMatch = String(p.text || "").match(/^Pitch\s+(\d+)\s*:/i);
      const seq = seqMatch ? seqMatch[1] : idx + 1;
      const cx = Number(p.pitch_coordinate.x);
      const cy = Number(p.pitch_coordinate.y);
      const abbr = String(p.pitch_type_abbr || "").trim();
      const fill = PITCH_TYPE_COLOR[abbr] || PITCH_TYPE_COLOR_FALLBACK;
      const title = escapeHtml(formatPitchLine(p));
      return `
        <g class="pitch-zone-dot">
          <circle cx="${cx}" cy="${cy}" r="13" fill="${fill}" stroke="white" stroke-width="1.5"></circle>
          <text x="${cx}" y="${cy + 4.5}" text-anchor="middle" font-size="13" font-weight="700" fill="white">${seq}</text>
          <title>${title}</title>
        </g>`;
    })
    .join("");
  return `
    <div class="pitch-zone" role="img" aria-label="Pitch locations for current at-bat">
      <svg viewBox="${VB.x} ${VB.y} ${VB.w} ${VB.h}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
        <rect class="pitch-zone-frame" x="${ZONE.x}" y="${ZONE.y}" width="${ZONE.w}" height="${ZONE.h}" rx="2" ry="2"></rect>
        <polygon class="pitch-zone-plate" points="78,230 144,230 144,242 111,255 78,242"></polygon>
        ${dots}
      </svg>
    </div>`;
}

// Resolve the live half-inning ({inning, half}) the card is showing from the
// inning_context, mirroring the backend's _resolve_target_half (top/bottom,
// with the between-halves case mapped to the just-ended half). Returns null
// when it can't be determined (e.g. pregame).
function resolveLiveHalf(inningContext) {
  const inning = Number(inningContext?.period || 0);
  if (!(inning > 0)) return null;
  const prefix = String(inningContext?.period_prefix || "").toLowerCase();
  if (prefix.startsWith("top")) return { inning, half: "top" };
  if (prefix.startsWith("bot")) return { inning, half: "bottom" };
  if (inningContext?.is_between_halves)
    return { inning, half: prefix.startsWith("mid") ? "top" : "bottom" };
  return null;
}

// Append the inning-pager strip below the play-by-play body: a thin row with
// a small ▾ that swaps the panel to the previous half-inning. While viewing a
// past half a ▴ pages back toward live; which half is shown is signalled by
// the inning marker beside the box score, not here.
function renderInningStrip(body, { atLive, downDisabled }) {
  // CSS border-triangles, not text glyphs — a glyph's em box is mostly
  // whitespace, so the strip height and visible arrow size can't be
  // controlled independently with a character.
  const btn = (cls, tri, aria, disabled) =>
    `<button class="inning-nav-btn ${cls}" type="button" aria-label="${aria}" title="${aria}"${
      disabled ? " disabled" : ""
    }><span class="tri ${tri}"></span></button>`;
  return `${body}
    <div class="inning-strip">
      ${atLive ? "" : btn("inning-nav-next", "tri-up", "Later half-inning", false)}
      ${btn("inning-nav-prev", "tri-down", "Previous half-inning", downDisabled)}
    </div>`;
}

// Scoreboard totals as they stood at the end of a viewed past half-inning,
// reconstructed from the per-inning linescores (final once a half ends).
// Pass includeViewedInning=false for the home side while viewing a top half
// (it hadn't batted that inning yet). Hits/errors only sum when ESPN provides
// a per-inning breakdown; null means "not reconstructable" (render as "—").
function totalsThroughHalf(competitor, inning, includeViewedInning) {
  const lines = Array.isArray(competitor?.linescores)
    ? competitor.linescores
    : [];
  const upto = Math.min(
    includeViewedInning ? inning : inning - 1,
    lines.length,
  );
  let runs = 0;
  let hits = 0;
  let errors = 0;
  let hasRuns = upto === 0;
  let hasHits = false;
  let hasErrors = false;
  // ESPN's per-inning entries put runs in displayValue (a string) and often
  // omit value entirely (null). Number(null) is 0, not NaN — so missing
  // fields must be screened out before coercion or they count as 0 runs.
  const num = (v) => (v == null || v === "" ? NaN : Number(v));
  for (let i = 0; i < upto; i++) {
    const r = num(lines[i]?.value ?? lines[i]?.displayValue);
    if (Number.isFinite(r)) {
      runs += r;
      hasRuns = true;
    }
    const h = num(lines[i]?.hits);
    if (Number.isFinite(h)) {
      hits += h;
      hasHits = true;
    }
    const e = num(lines[i]?.errors);
    if (Number.isFinite(e)) {
      errors += e;
      hasErrors = true;
    }
  }
  return {
    runs: hasRuns ? runs : null,
    hits: hasHits ? hits : null,
    errors: hasErrors ? errors : null,
  };
}

// Inning marker (▲/▼ stack) for the past half-inning the pager is viewing —
// same shape as the live marker so the box-score indicator simply flips to
// the viewed half while paging.
function renderPastHalfMarker(view) {
  const num = Number(view?.inning) || 0;
  if (String(view?.half) === "top")
    return `<div class="inning-stack up"><div class="arrow">▲</div><div class="num">${num}</div></div>`;
  return `<div class="inning-stack down"><div class="num">${num}</div><div class="arrow">▼</div></div>`;
}

function renderRecentPlays(
  plays,
  currentPitches = [],
  situation = {},
  config = {},
) {
  const showPitches = config.show_pitches !== false;
  const showPlayResults = config.show_play_results !== false;
  const chronological = Array.isArray(plays)
    ? plays.filter((p) => p && p.text)
    : [];
  const list = showPlayResults ? [...chronological] : [];
  const pitches =
    showPitches && Array.isArray(currentPitches)
      ? currentPitches
          .filter(Boolean)
          .map((p) => formatPitchLine(p, { html: true }))
          .filter(Boolean)
          .reverse()
      : [];
  if (!list.length && !pitches.length) return "";
  const pitchHtml = pitches
    .map(
      (pitch) => `
        <div class="play-row pitch-row">
          <div class="play-text">${pitch}</div>
          <div class="play-indicator"></div>
        </div>`,
    )
    .join("");
  let previousKnownOuts = null;
  let previousKnownAway = null;
  let previousKnownHome = null;
  const playHtml = list
    .map((play) => {
      const indicator = renderPlayIndicator(play, {
        outs: previousKnownOuts,
        away_score: previousKnownAway,
        home_score: previousKnownHome,
      });
      const outs = Number(play?.outs);
      // Outs never decrease within a half-inning, so keep the running baseline
      // monotonic. ESPN intermittently serves a transient low/zero `outs` on an
      // intervening play (e.g. a mid-inning pitching change), and letting that
      // clobber the baseline makes the NEXT real play look like it added an
      // out — surfacing a spurious "Outs" badge on a non-out play (a walk, hit,
      // etc.). Taking the max keeps the badge tied to actual out-making plays.
      if (Number.isFinite(outs)) {
        previousKnownOuts =
          previousKnownOuts == null ? outs : Math.max(previousKnownOuts, outs);
      }
      if (play?.away_score != null && play?.away_score !== "")
        previousKnownAway = play.away_score;
      if (play?.home_score != null && play?.home_score !== "")
        previousKnownHome = play.home_score;
      return `
        <div class="play-row">
          <div class="play-text">${escapeHtml(play.text)}</div>
          <div class="play-indicator">${indicator}</div>
        </div>`;
    })
    .join("");
  return `
    <div class="plays-panel">${pitchHtml}${playHtml}
    </div>`;
}

class MlbLiveGameCard extends HTMLElement {
  // Visual config editor — makes the "Edit card" dialog open the no-YAML
  // form instead of falling back to the raw code editor.
  static getConfigElement() {
    return document.createElement(EDITOR_TAG);
  }

  // Default config when the card is added from the dashboard picker, so it
  // lands on a working entity instead of erroring on an empty config.
  static getStubConfig(hass) {
    return { entity: findMlbEntity(hass) };
  }

  setConfig(config) {
    // Intentionally no throw on a missing entity: the visual editor and the
    // picker preview both rely on the card surviving an empty config and
    // showing a friendly "configure me" placeholder (see render()).
    this.config = { ...CARD_DEFAULTS, ...(config || {}) };
    // Clear any existing refresh timer when config changes
    this._clearRefreshTimer();
    // Schedule-navigation view state (card-local; never touches the sensor).
    this._resetScheduleNav();
    // Inning-pager view state (card-local; live play-by-play only).
    this._resetInningNav();
    // Live collapse/expand view state — re-seeded from the (possibly new)
    // `live_default_view` on the next render via the game-key check.
    this._liveExpanded = this._liveDefaultExpanded();
    this._liveExpandedGameKey = undefined;
    // Force the anchor-change checks on the next render to re-baseline.
    this._navAnchorId = undefined;
    this._inningAnchorKey = undefined;
  }

  // The state a freshly-displayed live game starts in. Anything other than an
  // explicit "expanded" collapses, so a typo in hand-written YAML lands on the
  // documented default rather than silently opening the card.
  _liveDefaultExpanded() {
    return (
      String(this.config?.live_default_view || "collapsed").toLowerCase() ===
      "expanded"
    );
  }

  // Reset schedule navigation back to the live/auto-selected game. Called on
  // config change and whenever the underlying live game advances to a new
  // event so a stale navigated view never sticks.
  _resetScheduleNav() {
    this._navOffset = 0;
    this._navGameData = null;
    // Optimistic until a fetch tells us otherwise — mid-season a team almost
    // always has both a previous and an upcoming game, so the arrows show
    // enabled at offset 0 and only disable once we hit a schedule boundary.
    this._navHasPrev = true;
    this._navHasNext = true;
    this._navInflight = null;
    if (this._navCache instanceof Map) this._navCache.clear();
    else this._navCache = new Map();
    this._clearNavIdleTimer();
  }

  // Reset the inning pager back to the live half-inning. Called on config
  // change and whenever the live half advances so a stale past-inning view
  // (and its now-misaligned per-offset cache) never sticks.
  _resetInningNav() {
    this._inningOffset = 0;
    this._inningView = null;
    this._inningHasPrev = true;
    this._inningHasNext = true;
    this._inningInflight = null;
    if (this._inningCache instanceof Map) this._inningCache.clear();
    else this._inningCache = new Map();
    this._clearInningIdleTimer();
  }

  _clearRefreshTimer() {
    if (this._refreshInterval) {
      clearInterval(this._refreshInterval);
      this._refreshInterval = null;
    }
  }

  // Returns the wrapper CSS class that drives headshot sizing. CARD_CSS
  // scopes container-query and fixed-size rules under these classes.
  // Unknown / unset values fall back to "auto" so a stale or hand-edited
  // YAML never breaks the layout.
  _headshotSizeClass() {
    const v = String(this.config?.headshot_size || "auto").toLowerCase();
    if (v === "auto" || HEADSHOT_SIZE_PRESETS[v] != null) {
      return `headshot-size-${v}`;
    }
    return "headshot-size-auto";
  }

  // The third-out → due-up transition is driven entirely by the coordinator-
  // supplied `third_out_hold_until` epoch timestamp so that every card on the
  // dashboard flips at the same moment. We only keep a local timer to force a
  // re-render at the deadline (otherwise we'd wait until the next HA state
  // update to swap panels).
  _scheduleHoldExpiryRender(holdUntilEpochSeconds) {
    const target = Number(holdUntilEpochSeconds);
    if (!Number.isFinite(target)) return;
    if (this._holdExpiryTarget === target && this._holdExpiryTimer) return;
    this._clearHoldExpiryTimer();
    const delayMs = Math.max(0, target * 1000 - Date.now()) + 50;
    this._holdExpiryTarget = target;
    this._holdExpiryTimer = setTimeout(() => {
      this._holdExpiryTimer = null;
      this._holdExpiryTarget = 0;
      this._lastFingerprint = "";
      this.render();
    }, delayMs);
  }

  _clearHoldExpiryTimer() {
    if (this._holdExpiryTimer) {
      clearTimeout(this._holdExpiryTimer);
      this._holdExpiryTimer = null;
    }
    this._holdExpiryTarget = 0;
  }

  _upcomingDetailsFingerprint(attrs) {
    // Compact fingerprint of details that affect the expanded panel, so the
    // compact card re-renders when probable pitchers, standings, scoring
    // plays, or game leaders change. The final-game panel pulls scoring
    // plays + leaders; the upcoming-game panel ignores those fields and
    // they remain empty, so adding them is cheap on both paths.
    const probables = attrs?.probable_pitchers || {};
    const a = probables.away || {};
    const h = probables.home || {};
    const standings = attrs?.division_standings || {};
    const entries = Array.isArray(standings.entries) ? standings.entries : [];
    const standingsFp = entries
      .map(
        (e) =>
          `${e.team_id || ""}:${e.wins || ""}-${e.losses || ""}:${e.games_back || ""}`,
      )
      .join(",");
    const scoring = Array.isArray(attrs?.scoring_plays)
      ? attrs.scoring_plays
      : [];
    const lastScoring = scoring.length ? scoring[scoring.length - 1] : null;
    const leaders = attrs?.leaders || {};
    const al = Array.isArray(leaders.away) ? leaders.away : [];
    const hl = Array.isArray(leaders.home) ? leaders.home : [];
    const leadersFp =
      `${al.length}/${al.map((x) => `${x.id || ""}:${x.value || ""}`).join(",")}|` +
      `${hl.length}/${hl.map((x) => `${x.id || ""}:${x.value || ""}`).join(",")}`;
    return [
      a.name || "",
      a.record || "",
      a.era || "",
      h.name || "",
      h.record || "",
      h.era || "",
      standings.division_name || "",
      standingsFp,
      scoring.length,
      lastScoring?.id || "",
      lastScoring?.away_score ?? "",
      lastScoring?.home_score ?? "",
      leadersFp,
      // Highlights URL pops in 30-90 min post-final; repaint when it appears.
      attrs?.highlights_url || "",
    ].join("|");
  }

  _openPlayerProfile(el) {
    const link = el instanceof Element ? el.closest(".player-link") : null;
    if (!link || !this.content.contains(link)) return false;
    const id = link.getAttribute("data-athlete-id");
    if (id) {
      // `player_link_target` (default "popup") chooses the action. "espn"
      // restores Option A's direct-to-ESPN behavior; either way ESPN stays
      // reachable (the popup footer always links out). Both click and
      // keyboard activation funnel through here, so parity is automatic.
      const target = String(
        this.config?.player_link_target || "popup",
      ).toLowerCase();
      if (target === "espn") {
        window.open(espnPlayerUrl(id), "_blank", "noopener");
      } else {
        this._openPlayerCardPopup(id);
      }
    }
    return true;
  }

  // Handle activation of a matchup half (the click/keydown delegates call
  // this AFTER _openPlayerProfile, so a player-name click already returned
  // and never reaches here — names keep opening the career popup). Returns
  // true when it consumed the event. Same side again -> close; the other
  // side -> _openLineupPopup switches team while the overlay stays open.
  _toggleLineupFromMatchup(el) {
    const side = el instanceof Element ? el.closest(".matchup-side") : null;
    if (!side || !this.content.contains(side)) return false;
    const popupSide = side.getAttribute("data-team-popup");
    if (popupSide !== "away" && popupSide !== "home") return false;
    if (this._isLineupPopupOpen(popupSide)) this._closeLineupPopup();
    else this._openLineupPopup(popupSide);
    return true;
  }

  // Fetch a player's career card via the integration's WebSocket command
  // (server-side fetch reuses the coordinator's TTL cache; see
  // OPTION_B_HANDOFF.md route R3). Per-card result cache + in-flight de-dupe
  // so repeated clicks on the same name don't re-hit the socket. Resolves to
  // the card object, or null on any failure (caller renders an error state).
  _fetchPlayerCard(athleteId) {
    const id = String(athleteId == null ? "" : athleteId).trim();
    if (!id || !this._hass || !this._hass.connection)
      return Promise.resolve(null);
    this._playerCardCache = this._playerCardCache || new Map();
    this._playerCardInflight = this._playerCardInflight || new Map();
    if (this._playerCardCache.has(id))
      return Promise.resolve(this._playerCardCache.get(id));
    if (this._playerCardInflight.has(id))
      return this._playerCardInflight.get(id);
    const req = this._hass.connection
      .sendMessagePromise({
        type: "mlb_live_scoreboard/player_card",
        athlete_id: id,
      })
      .then((res) => {
        const card = (res && res.player_card) || null;
        if (card) this._playerCardCache.set(id, card);
        return card;
      })
      .catch((err) => {
        console.debug(`[${CARD_TAG}] player_card fetch failed for ${id}:`, err);
        return null;
      })
      .finally(() => {
        this._playerCardInflight.delete(id);
      });
    this._playerCardInflight.set(id, req);
    return req;
  }

  // --- Player career popup. Chunk 3 built the modal shell + loading/error/
  // empty states; Chunk 4 fills the "ready" branch (bio header + career
  // table) via the module-level playerCardBodyHtml builder. ---

  _ensurePlayerCardPopup() {
    if (this._pcOverlay) return;
    if (!document.getElementById("mlb-pc-style")) {
      const style = document.createElement("style");
      style.id = "mlb-pc-style";
      style.textContent = `
        .mlb-pc-overlay {
          position: fixed; inset: 0; z-index: 99999;
          display: flex; align-items: center; justify-content: center;
          padding: 16px; background: rgba(0,0,0,0.6);
        }
        .mlb-pc-overlay[hidden] { display: none; }
        .mlb-pc-dialog {
          width: min(560px, 94vw); max-height: 86vh;
          display: flex; flex-direction: column; overflow: hidden;
          background: var(--card-background-color, var(--ha-card-background, #1c1c1c));
          color: var(--primary-text-color, #e1e1e1);
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: 0 8px 32px rgba(0,0,0,0.5);
          outline: none;
        }
        .mlb-pc-header {
          display: flex; align-items: center; gap: 8px;
          padding: 12px 14px;
          border-bottom: 1px solid var(--divider-color, rgba(255,255,255,0.12));
        }
        .mlb-pc-title {
          flex: 1; min-width: 0; font-weight: 600; font-size: 1.05em;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          color: var(--warning-color, #ffb300);
        }
        .mlb-pc-close {
          flex: none; cursor: pointer; border: 0; border-radius: 50%;
          width: 30px; height: 30px; line-height: 30px; padding: 0;
          font-size: 16px; background: transparent;
          color: var(--secondary-text-color, #9b9b9b);
        }
        .mlb-pc-close:hover { color: var(--primary-text-color, #fff); }
        .mlb-pc-close:focus-visible,
        .mlb-pc-espn:focus-visible,
        .mlb-pc-table-wrap:focus-visible {
          outline: 2px solid var(--warning-color, #ffb300); outline-offset: 2px;
        }
        .mlb-pc-body {
          padding: 18px 16px; overflow: auto;
          min-height: 96px; display: flex;
          align-items: center; justify-content: center; text-align: center;
        }
        .mlb-pc-msg { color: var(--secondary-text-color, #9b9b9b); }
        .mlb-pc-retry {
          margin-top: 10px; cursor: pointer;
          background: transparent; color: var(--warning-color, #ffb300);
          border: 1px solid var(--divider-color, rgba(255,255,255,0.2));
          border-radius: 6px; padding: 5px 12px;
        }
        .mlb-pc-spinner {
          width: 26px; height: 26px; border-radius: 50%;
          border: 3px solid var(--divider-color, rgba(255,255,255,0.2));
          border-top-color: var(--warning-color, #ffb300);
          animation: mlb-pc-spin 0.8s linear infinite; margin: 0 auto 10px;
        }
        @keyframes mlb-pc-spin { to { transform: rotate(360deg); } }
        @media (prefers-reduced-motion: reduce) {
          .mlb-pc-spinner { animation-duration: 2s; }
        }
        .mlb-pc-footer {
          padding: 10px 14px; text-align: right;
          border-top: 1px solid var(--divider-color, rgba(255,255,255,0.12));
        }
        .mlb-pc-espn {
          color: var(--warning-color, #ffb300);
          text-decoration: none; font-size: 0.92em;
        }
        .mlb-pc-espn:hover { text-decoration: underline; }
        .mlb-pc-body--ready {
          display: block; text-align: left; align-items: stretch;
        }
        .mlb-pc-bio {
          display: flex; align-items: center; gap: 14px; margin-bottom: 14px;
        }
        .mlb-pc-shot {
          flex: none; width: 64px; height: 64px; border-radius: 50%;
          object-fit: cover;
          background: var(--divider-color, rgba(255,255,255,0.12));
        }
        .mlb-pc-shot--ph { display: block; }
        .mlb-pc-bio-meta { min-width: 0; }
        .mlb-pc-bio-line {
          font-size: 0.9em; color: var(--primary-text-color, #e1e1e1);
        }
        .mlb-pc-bio-sub {
          font-size: 0.82em; color: var(--secondary-text-color, #9b9b9b);
          margin-top: 4px;
        }
        .mlb-pc-table-wrap {
          overflow-x: auto; -webkit-overflow-scrolling: touch;
        }
        .mlb-pc-table {
          border-collapse: collapse; width: 100%;
          font-size: 0.82em; white-space: nowrap;
        }
        .mlb-pc-caption {
          caption-side: top; text-align: left; padding: 0 0 6px;
          font-size: 0.92em; color: var(--secondary-text-color, #9b9b9b);
        }
        .mlb-pc-table th, .mlb-pc-table td {
          padding: 5px 8px; text-align: right;
          border-bottom: 1px solid var(--divider-color, rgba(255,255,255,0.08));
        }
        .mlb-pc-table thead th {
          color: var(--secondary-text-color, #9b9b9b);
          font-weight: 600; cursor: help;
        }
        .mlb-pc-table th[scope="row"] { text-align: left; }
        .mlb-pc-table td.mlb-pc-team { text-align: left; }
        .mlb-pc-total th, .mlb-pc-total td {
          font-weight: 700;
          color: var(--primary-text-color, #e1e1e1);
          border-top: 2px solid var(--divider-color, rgba(255,255,255,0.2));
          border-bottom: 0;
        }
      `;
      document.head.appendChild(style);
    }

    const overlay = document.createElement("div");
    overlay.className = "mlb-pc-overlay";
    overlay.hidden = true;
    // Unique per instance: multiple cards on one dashboard would otherwise
    // emit duplicate ids and break aria-labelledby resolution.
    const titleId = `mlb-pc-title-${Math.random().toString(36).slice(2, 9)}`;
    overlay.innerHTML = `
      <div class="mlb-pc-dialog" role="dialog" aria-modal="true"
           aria-labelledby="${titleId}" tabindex="-1">
        <div class="mlb-pc-header">
          <div class="mlb-pc-title" id="${titleId}" role="heading" aria-level="2">Player</div>
          <button class="mlb-pc-close" type="button" aria-label="Close">✕</button>
        </div>
        <div class="mlb-pc-body" aria-live="polite"></div>
        <div class="mlb-pc-footer">
          <a class="mlb-pc-espn" target="_blank" rel="noopener noreferrer">View on ESPN ↗</a>
        </div>
      </div>`;

    this._pcOverlay = overlay;
    this._pcDialog = overlay.querySelector(".mlb-pc-dialog");
    this._pcTitle = overlay.querySelector(".mlb-pc-title");
    this._pcBody = overlay.querySelector(".mlb-pc-body");
    this._pcEspn = overlay.querySelector(".mlb-pc-espn");

    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) return this._closePlayerCardPopup();
      const t = ev.target instanceof Element ? ev.target : null;
      if (t && t.closest(".mlb-pc-close")) return this._closePlayerCardPopup();
      if (t && t.closest(".mlb-pc-retry") && this._pcAthleteId) {
        this._openPlayerCardPopup(this._pcAthleteId);
      }
    });
    overlay.addEventListener("keydown", (ev) => this._onPcKeydown(ev));
    document.body.appendChild(overlay);
  }

  _onPcKeydown(ev) {
    if (ev.key === "Escape") {
      ev.preventDefault();
      this._closePlayerCardPopup();
      return;
    }
    if (ev.key !== "Tab") return;
    const focusables = this._pcDialog.querySelectorAll(
      'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusables.length) {
      ev.preventDefault();
      this._pcDialog.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (ev.shiftKey && (active === first || active === this._pcDialog)) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && active === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  _setPlayerCardState(state, card) {
    if (!this._pcBody) return;
    // Tell assistive tech the dialog is fetching; flipping it back to
    // "false" is what makes the polite live region announce the result.
    if (this._pcDialog) {
      this._pcDialog.setAttribute(
        "aria-busy",
        state === "loading" ? "true" : "false",
      );
    }
    // The "ready" body is a left-aligned scrolling layout; the message
    // states stay centered (default .mlb-pc-body flexbox).
    this._pcBody.className =
      state === "ready" ? "mlb-pc-body mlb-pc-body--ready" : "mlb-pc-body";
    if (state === "loading") {
      this._pcBody.innerHTML = `<div><div class="mlb-pc-spinner"></div><div class="mlb-pc-msg">Loading player…</div></div>`;
    } else if (state === "error") {
      this._pcBody.innerHTML =
        `<div><div class="mlb-pc-msg">Couldn't load this player.</div>` +
        `<button class="mlb-pc-retry" type="button" data-pc-retry>Retry</button></div>`;
    } else if (state === "empty") {
      this._pcBody.innerHTML = `<div class="mlb-pc-msg">No stats available for this player.</div>`;
    } else {
      // "ready": bio header + career table. requestCachedLogo reuses the
      // shared image cache (returns the remote URL synchronously on a miss,
      // so the <img> always loads even before the blob resolves).
      const safe = card || {};
      const shot = requestCachedLogo(
        this,
        (safe.bio && safe.bio.headshot) || "",
      );
      this._pcBody.innerHTML = playerCardBodyHtml(safe, shot);
    }
  }

  _openPlayerCardPopup(athleteId) {
    const id = String(athleteId == null ? "" : athleteId).trim();
    if (!id) return;
    this._ensurePlayerCardPopup();
    this._pcAthleteId = id;
    if (this._pcOverlay.hidden) {
      this._pcReturnFocus =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      this._pcPrevBodyOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      this._pcOverlay.hidden = false;
    }
    this._pcTitle.textContent = "Player";
    this._pcEspn.href = espnPlayerUrl(id);
    this._setPlayerCardState("loading");
    this._pcDialog.focus();

    const token = (this._pcToken || 0) + 1;
    this._pcToken = token;
    this._fetchPlayerCard(id).then((card) => {
      // Ignore a resolution that lost the race to a newer open()/close().
      if (token !== this._pcToken || this._pcOverlay.hidden) return;
      if (!card) {
        this._setPlayerCardState("error");
      } else if (!card.bio?.name && !(card.career?.seasons || []).length) {
        this._setPlayerCardState("empty");
      } else {
        if (card.bio?.name) this._pcTitle.textContent = card.bio.name;
        this._setPlayerCardState("ready", card);
      }
    });
  }

  _closePlayerCardPopup() {
    if (!this._pcOverlay || this._pcOverlay.hidden) return;
    this._pcToken = (this._pcToken || 0) + 1; // invalidate any in-flight render
    this._pcOverlay.hidden = true;
    document.body.style.overflow = this._pcPrevBodyOverflow || "";
    if (this._pcReturnFocus && document.contains(this._pcReturnFocus)) {
      this._pcReturnFocus.focus();
    }
    this._pcReturnFocus = null;
  }

  _destroyPlayerCardPopup() {
    if (!this._pcOverlay) return;
    this._closePlayerCardPopup();
    this._pcOverlay.remove();
    this._pcOverlay = null;
    this._pcDialog = this._pcTitle = this._pcBody = this._pcEspn = null;
  }

  // --- Team lineup popup (separate modal from the player career popup).
  // Mirrors the mlb-pc-* scaffolding/a11y verbatim. Chunk 3 builds the
  // shell + Game view (synchronous, straight from the `lineups` entity
  // attribute). Chunk 4 wires the matchup-side click target that opens it;
  // Chunk 5 fills the Season view via the team_season_stats WS command. ---

  // Lazily fetch every rostered player's season line for `side`'s team via
  // the integration's WebSocket batch command (server-side fetch reuses the
  // coordinator's TTL cache; see LINEUP_POPUP_HANDOFF.md §3 / Chunk 2).
  // Per-team result cache + in-flight de-dupe (mirrors _fetchPlayerCard) so
  // flipping Game<->Season, switching sides, or reopening the popup doesn't
  // re-hit the socket. Resolves to a { id: { hitting?, pitching? } } map
  // (possibly empty), or null on transport failure (caller shows retry).
  _fetchTeamSeasonStats(side) {
    const s = side === "home" ? "home" : "away";
    const team = this._lineupTeam(s);
    if (!team || !this._hass || !this._hass.connection)
      return Promise.resolve(null);
    const ids = [];
    const seen = new Set();
    for (const list of [team.hitters, team.pitchers]) {
      if (!Array.isArray(list)) continue;
      for (const p of list) {
        const id = String((p && p.id) ?? "").trim();
        if (id && !seen.has(id)) {
          seen.add(id);
          ids.push(id);
        }
      }
    }
    const teamKey = String((team && team.team_id) ?? s);
    this._luSeasonCache = this._luSeasonCache || new Map();
    this._luSeasonInflight = this._luSeasonInflight || new Map();
    if (this._luSeasonCache.has(teamKey)) {
      return Promise.resolve(this._luSeasonCache.get(teamKey));
    }
    if (this._luSeasonInflight.has(teamKey))
      return this._luSeasonInflight.get(teamKey);
    if (!ids.length) {
      const empty = {};
      this._luSeasonCache.set(teamKey, empty);
      return Promise.resolve(empty);
    }
    const req = this._hass.connection
      .sendMessagePromise({
        type: "mlb_live_scoreboard/team_season_stats",
        athlete_ids: ids,
      })
      .then((res) => {
        const map = (res && res.season_stats) || {};
        this._luSeasonCache.set(teamKey, map);
        return map;
      })
      .catch((err) => {
        console.debug(
          `[${CARD_TAG}] team_season_stats fetch failed for ${teamKey}:`,
          err,
        );
        return null;
      })
      .finally(() => {
        this._luSeasonInflight.delete(teamKey);
      });
    this._luSeasonInflight.set(teamKey, req);
    return req;
  }

  _lineupTeam(side) {
    const ent = this.config && this.config.entity;
    const st = ent && this._hass ? this._hass.states[ent] : null;
    const lu = st && st.attributes ? st.attributes.lineups : null;
    return lu && typeof lu === "object" ? lu[side] || null : null;
  }

  _ensureLineupPopup() {
    if (this._luOverlay) return;
    if (!document.getElementById("mlb-lu-style")) {
      const style = document.createElement("style");
      style.id = "mlb-lu-style";
      style.textContent = `
        .mlb-lu-overlay {
          position: fixed; inset: 0; z-index: 99999;
          display: flex; align-items: center; justify-content: center;
          padding: 16px; background: rgba(0,0,0,0.6);
        }
        .mlb-lu-overlay[hidden] { display: none; }
        .mlb-lu-dialog {
          width: min(640px, 95vw); max-height: 88vh;
          display: flex; flex-direction: column; overflow: hidden;
          background: var(--card-background-color, var(--ha-card-background, #1c1c1c));
          color: var(--primary-text-color, #e1e1e1);
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: 0 8px 32px rgba(0,0,0,0.5);
          outline: none;
        }
        .mlb-lu-header {
          display: flex; align-items: center; gap: 10px;
          padding: 12px 14px;
          border-bottom: 1px solid var(--divider-color, rgba(255,255,255,0.12));
        }
        .mlb-lu-logo {
          flex: none; width: 30px; height: 30px; object-fit: contain;
        }
        .mlb-lu-title {
          flex: 1; min-width: 0; font-weight: 600; font-size: 1.05em;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
          color: var(--warning-color, #ffb300);
        }
        .mlb-lu-views {
          flex: none; display: flex; gap: 2px; padding: 2px;
          border: 1px solid var(--divider-color, rgba(255,255,255,0.2));
          border-radius: 8px;
        }
        .mlb-lu-views label {
          cursor: pointer; font-size: 0.82em; line-height: 1;
          padding: 5px 10px; border-radius: 6px;
          color: var(--secondary-text-color, #9b9b9b);
        }
        .mlb-lu-views input { position: absolute; opacity: 0; pointer-events: none; }
        .mlb-lu-views label:has(input:checked) {
          background: var(--warning-color, #ffb300);
          color: var(--text-primary-color, #111);
        }
        .mlb-lu-views label:has(input:focus-visible) {
          outline: 2px solid var(--warning-color, #ffb300); outline-offset: 2px;
        }
        .mlb-lu-close {
          flex: none; cursor: pointer; border: 0; border-radius: 50%;
          width: 30px; height: 30px; line-height: 30px; padding: 0;
          font-size: 16px; background: transparent;
          color: var(--secondary-text-color, #9b9b9b);
        }
        .mlb-lu-close:hover { color: var(--primary-text-color, #fff); }
        .mlb-lu-close:focus-visible,
        .mlb-lu-table-wrap:focus-visible {
          outline: 2px solid var(--warning-color, #ffb300); outline-offset: 2px;
        }
        .mlb-lu-body {
          padding: 14px 16px; overflow: auto; min-height: 96px;
        }
        .mlb-lu-body--msg {
          display: flex; align-items: center; justify-content: center;
          text-align: center;
        }
        .mlb-lu-msg { color: var(--secondary-text-color, #9b9b9b); }
        .mlb-lu-retry {
          margin-top: 10px; cursor: pointer;
          background: transparent; color: var(--warning-color, #ffb300);
          border: 1px solid var(--divider-color, rgba(255,255,255,0.2));
          border-radius: 6px; padding: 5px 12px;
        }
        .mlb-lu-spinner {
          width: 26px; height: 26px; border-radius: 50%;
          border: 3px solid var(--divider-color, rgba(255,255,255,0.2));
          border-top-color: var(--warning-color, #ffb300);
          animation: mlb-lu-spin 0.8s linear infinite; margin: 0 auto 10px;
        }
        @keyframes mlb-lu-spin { to { transform: rotate(360deg); } }
        @media (prefers-reduced-motion: reduce) {
          .mlb-lu-spinner { animation-duration: 2s; }
        }
        .mlb-lu-table-wrap {
          overflow-x: auto; -webkit-overflow-scrolling: touch;
          margin-bottom: 14px;
        }
        .mlb-lu-table-wrap:last-child { margin-bottom: 0; }
        .mlb-lu-table {
          border-collapse: collapse; width: 100%;
          font-size: 0.82em; white-space: nowrap;
        }
        .mlb-lu-caption {
          caption-side: top; text-align: left; padding: 2px 0 6px;
          font-size: 0.92em; font-weight: 600;
          color: var(--secondary-text-color, #9b9b9b);
        }
        .mlb-lu-table th, .mlb-lu-table td {
          padding: 5px 8px; text-align: right;
          border-bottom: 1px solid var(--divider-color, rgba(255,255,255,0.08));
        }
        .mlb-lu-table thead th {
          color: var(--secondary-text-color, #9b9b9b); font-weight: 600;
        }
        .mlb-lu-table th.mlb-lu-name, .mlb-lu-table td.mlb-lu-name,
        .mlb-lu-table th.mlb-lu-pos, .mlb-lu-table td.mlb-lu-pos,
        .mlb-lu-table th.mlb-lu-num, .mlb-lu-table td.mlb-lu-num {
          text-align: left;
        }
        .mlb-lu-table td.mlb-lu-name { color: var(--primary-text-color, #e1e1e1); }
        /* "Up" arrow — the batter at the plate for the batting side, the
           next half's leadoff man for the fielding side. Reserved on every
           hitter row (empty on all but one) so the name column stays
           aligned. */
        .mlb-lu-up {
          display: inline-block;
          width: 0.8em;
          margin-right: 2px;
          color: var(--accent-color, #f5c518);
          font-size: 0.9em;
          line-height: 1;
        }
        /* Visually hidden, still announced. */
        .mlb-lu-sr {
          position: absolute;
          width: 1px; height: 1px;
          margin: -1px; padding: 0; border: 0;
          overflow: hidden;
          clip: rect(0 0 0 0);
          clip-path: inset(50%);
          white-space: nowrap;
        }
        .mlb-lu-table .mlb-lu-num { width: 1.5em; }
        .mlb-lu-table tr.mlb-lu-out td, .mlb-lu-table tr.mlb-lu-out th {
          opacity: 0.55;
        }
      `;
      document.head.appendChild(style);
    }

    const overlay = document.createElement("div");
    overlay.className = "mlb-lu-overlay";
    overlay.hidden = true;
    // Unique per instance: two cards on one dashboard would otherwise emit
    // duplicate ids / radio names and break aria-labelledby + grouping.
    const uid = Math.random().toString(36).slice(2, 9);
    const titleId = `mlb-lu-title-${uid}`;
    const grp = `mlb-lu-view-${uid}`;
    overlay.innerHTML = `
      <div class="mlb-lu-dialog" role="dialog" aria-modal="true"
           aria-labelledby="${titleId}" tabindex="-1">
        <div class="mlb-lu-header">
          <img class="mlb-lu-logo" alt="" aria-hidden="true" hidden
               loading="lazy" decoding="async" referrerpolicy="no-referrer">
          <div class="mlb-lu-title" id="${titleId}" role="heading" aria-level="2">Lineup</div>
          <div class="mlb-lu-views" role="radiogroup" aria-label="Stats view">
            <label><input type="radio" name="${grp}" class="mlb-lu-view" value="game" checked>Game</label>
            <label><input type="radio" name="${grp}" class="mlb-lu-view" value="season">Season</label>
          </div>
          <button class="mlb-lu-close" type="button" aria-label="Close">✕</button>
        </div>
        <div class="mlb-lu-body" aria-live="polite"></div>
      </div>`;

    this._luOverlay = overlay;
    this._luDialog = overlay.querySelector(".mlb-lu-dialog");
    this._luTitle = overlay.querySelector(".mlb-lu-title");
    this._luLogo = overlay.querySelector(".mlb-lu-logo");
    this._luBody = overlay.querySelector(".mlb-lu-body");

    overlay.addEventListener("click", (ev) => {
      if (ev.target === overlay) return this._closeLineupPopup();
      const t = ev.target instanceof Element ? ev.target : null;
      if (t && t.closest(".mlb-lu-close")) return this._closeLineupPopup();
      if (t && t.closest(".mlb-lu-retry")) return this._renderLineupBody();
    });
    overlay.addEventListener("change", (ev) => {
      const t = ev.target instanceof Element ? ev.target : null;
      if (t && t.classList.contains("mlb-lu-view")) {
        this._setLineupView(t.value === "season" ? "season" : "game");
      }
    });
    overlay.addEventListener("keydown", (ev) => this._onLuKeydown(ev));
    document.body.appendChild(overlay);
  }

  _onLuKeydown(ev) {
    if (ev.key === "Escape") {
      ev.preventDefault();
      this._closeLineupPopup();
      return;
    }
    if (ev.key !== "Tab") return;
    // `input` included so the Game/Season radios stay inside the trap.
    const focusables = this._luDialog.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusables.length) {
      ev.preventDefault();
      this._luDialog.focus();
      return;
    }
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (ev.shiftKey && (active === first || active === this._luDialog)) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && active === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  _setLineupState(state) {
    if (!this._luBody) return;
    if (this._luDialog) {
      this._luDialog.setAttribute(
        "aria-busy",
        state === "loading" ? "true" : "false",
      );
    }
    this._luBody.className =
      state === "ready" ? "mlb-lu-body" : "mlb-lu-body mlb-lu-body--msg";
    if (state === "loading") {
      this._luBody.innerHTML = `<div><div class="mlb-lu-spinner"></div><div class="mlb-lu-msg">Loading season stats…</div></div>`;
    } else if (state === "error") {
      this._luBody.innerHTML =
        `<div><div class="mlb-lu-msg">Couldn't load season stats.</div>` +
        `<button class="mlb-lu-retry" type="button">Retry</button></div>`;
    } else if (state === "pregame") {
      this._luBody.innerHTML = `<div class="mlb-lu-msg">Lineup not posted yet.</div>`;
    }
  }

  // Render the body for the current side + view. Game is synchronous from
  // the entity attribute; Season is delegated to _renderLineupSeason, which
  // lazily batch-fetches via the team_season_stats WS command and caches.
  _renderLineupBody() {
    if (!this._luBody) return;
    const side = this._luSide;
    const team = this._lineupTeam(side);
    const name = (team && (team.name || team.abbreviation)) || "Lineup";
    if (this._luTitle) this._luTitle.textContent = name;
    if (this._luLogo) {
      const src = team ? requestCachedLogo(this, team.logo || "") : "";
      if (src) {
        this._luLogo.src = src;
        this._luLogo.hidden = false;
      } else {
        this._luLogo.hidden = true;
      }
    }
    const hasPlayers =
      team &&
      ((Array.isArray(team.hitters) && team.hitters.length) ||
        (Array.isArray(team.pitchers) && team.pitchers.length));
    if (!hasPlayers) {
      this._setLineupState("pregame");
      return;
    }
    if (this._luView === "season") {
      this._renderLineupSeason(side, team);
      return;
    }
    this._setLineupState("ready");
    this._luBody.innerHTML = lineupTablesHtml(team, "game", {});
  }

  // Season view: instant from the per-team cache when warm, otherwise show
  // the loading state and lazily batch-fetch via _fetchTeamSeasonStats. The
  // fetch is async, so re-validate side+view on resolve — the user may have
  // closed the popup, switched teams, or flipped back to Game meanwhile.
  // A failed fetch is not cached, so the Retry button (→ _renderLineupBody)
  // and a later Season switch both naturally re-request.
  _renderLineupSeason(side, team) {
    const teamKey = String((team && team.team_id) ?? side);
    const cached =
      this._luSeasonCache instanceof Map
        ? this._luSeasonCache.get(teamKey)
        : undefined;
    if (cached !== undefined) {
      this._luSeasonStats = cached;
      this._setLineupState("ready");
      this._luBody.innerHTML = lineupTablesHtml(team, "season", cached);
      return;
    }
    this._setLineupState("loading");
    this._fetchTeamSeasonStats(side).then((map) => {
      if (!this._isLineupPopupOpen(side) || this._luView !== "season") return;
      if (map == null) {
        this._setLineupState("error");
        return;
      }
      this._luSeasonStats = map;
      this._setLineupState("ready");
      this._luBody.innerHTML = lineupTablesHtml(
        this._lineupTeam(side) || team,
        "season",
        map,
      );
    });
  }

  _setLineupView(view) {
    const v = view === "season" ? "season" : "game";
    if (this._luView === v) return;
    this._luView = v;
    this._renderLineupBody();
  }

  // side: "away" | "home" (Chunk 4 resolves which from the matchup click).
  _openLineupPopup(side) {
    const s = side === "home" ? "home" : "away";
    this._ensureLineupPopup();
    this._luSide = s;
    // Default view: an explicit lineup_default_view ("game"/"season") wins;
    // "auto" (default) → Game while the game is live, Season otherwise.
    const pref = String(
      this.config.lineup_default_view || "auto",
    ).toLowerCase();
    if (pref === "game" || pref === "season") {
      this._luView = pref;
    } else {
      const ent = this.config && this.config.entity;
      const st = ent && this._hass ? this._hass.states[ent] : null;
      const live = !!(st && st.attributes && st.attributes.is_live);
      this._luView = live ? "game" : "season";
    }
    const radios = this._luOverlay.querySelectorAll(".mlb-lu-view");
    radios.forEach((r) => {
      r.checked = r.value === this._luView;
    });
    if (this._luOverlay.hidden) {
      this._luReturnFocus =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      this._luPrevBodyOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      this._luOverlay.hidden = false;
    }
    this._renderLineupBody();
    this._luDialog.focus();
  }

  _closeLineupPopup() {
    if (!this._luOverlay || this._luOverlay.hidden) return;
    this._luOverlay.hidden = true;
    document.body.style.overflow = this._luPrevBodyOverflow || "";
    if (this._luReturnFocus && document.contains(this._luReturnFocus)) {
      this._luReturnFocus.focus();
    }
    this._luReturnFocus = null;
  }

  // Whether `side`'s lineup popup is currently open (Chunk 4 toggles on it).
  _isLineupPopupOpen(side) {
    return !!(
      this._luOverlay &&
      !this._luOverlay.hidden &&
      (side == null || this._luSide === (side === "home" ? "home" : "away"))
    );
  }

  _destroyLineupPopup() {
    if (!this._luOverlay) return;
    this._closeLineupPopup();
    this._luOverlay.remove();
    this._luOverlay = null;
    this._luDialog = this._luTitle = this._luLogo = this._luBody = null;
  }

  _onContentClick(ev) {
    // Schedule-nav arrows sit inside the expandable compact wrapper, so handle
    // them first and stop propagation to keep a tap from also toggling expand.
    const navBtn =
      ev.target instanceof Element
        ? ev.target.closest(".schedule-nav-btn")
        : null;
    if (navBtn && this.content.contains(navBtn)) {
      ev.preventDefault();
      ev.stopPropagation();
      if (navBtn.disabled || navBtn.getAttribute("aria-disabled") === "true")
        return;
      this._navigateSchedule(
        navBtn.classList.contains("schedule-nav-next") ? 1 : -1,
      );
      return;
    }
    const inningBtn =
      ev.target instanceof Element
        ? ev.target.closest(".inning-nav-btn")
        : null;
    if (inningBtn && this.content.contains(inningBtn)) {
      ev.preventDefault();
      ev.stopPropagation();
      if (
        inningBtn.disabled ||
        inningBtn.getAttribute("aria-disabled") === "true"
      )
        return;
      this._navigateInning(
        inningBtn.classList.contains("inning-nav-next") ? 1 : -1,
      );
      return;
    }
    // Live header (score rows + chevron strip) toggles the details below it.
    // It holds no player links or lineup targets of its own, so it can be
    // handled ahead of those without swallowing their clicks.
    const liveHeader =
      ev.target instanceof Element
        ? ev.target.closest(".live-expandable")
        : null;
    if (liveHeader && this.content.contains(liveHeader)) {
      ev.preventDefault();
      this._toggleLiveExpand();
      return;
    }
    if (this._openPlayerProfile(ev.target)) return;
    if (this._toggleLineupFromMatchup(ev.target)) return;
    const target =
      ev.target instanceof Element
        ? ev.target.closest(".upcoming-expandable")
        : null;
    if (!target || !this.content.contains(target)) return;
    this._upcomingExpanded = !this._upcomingExpanded;
    // Force a re-render even if the upstream fingerprint is otherwise unchanged.
    this._lastFingerprint = "";
    this._lastCompactFp = "";
    this.render();
  }

  // Flip the live card between "score rows only" and the fully configured
  // view. Card-local: nothing is written back to the sensor or the config, so
  // the next new game re-baselines to `live_default_view`.
  _toggleLiveExpand() {
    this._liveExpanded = this._liveExpanded !== true;
    // Collapsing hides the inning pager, and a paged-back view rolls the
    // header's score/marker to that half — leaving it engaged would strand a
    // stale score on the one row still on screen. Exiting the paged view on
    // collapse keeps the collapsed header honest about the live score.
    if (!this._liveExpanded) this._resetInningNav();
    // The upstream data hasn't changed, so bust both render memos or the
    // toggle would be a no-op paint.
    this._lastFingerprint = "";
    this._lastLiveHtml = "";
    this.render();
  }

  _onContentKeydown(ev) {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    // Native <button> arrows already fire a click on Enter/Space; let that
    // path drive navigation and don't also toggle the surrounding expander.
    if (
      ev.target instanceof Element &&
      ev.target.closest(".schedule-nav-btn, .inning-nav-btn")
    )
      return;
    const playerLink =
      ev.target instanceof Element ? ev.target.closest(".player-link") : null;
    if (playerLink && this.content.contains(playerLink)) {
      ev.preventDefault();
      this._openPlayerProfile(playerLink);
      return;
    }
    const luSide =
      ev.target instanceof Element ? ev.target.closest(".matchup-side") : null;
    if (
      luSide &&
      this.content.contains(luSide) &&
      ["away", "home"].includes(luSide.getAttribute("data-team-popup"))
    ) {
      ev.preventDefault();
      this._toggleLineupFromMatchup(luSide);
      return;
    }
    const liveHeader =
      ev.target instanceof Element
        ? ev.target.closest(".live-expandable")
        : null;
    if (liveHeader && this.content.contains(liveHeader)) {
      ev.preventDefault();
      this._toggleLiveExpand();
      return;
    }
    const target =
      ev.target instanceof Element
        ? ev.target.closest(".upcoming-expandable")
        : null;
    if (!target || !this.content.contains(target)) return;
    ev.preventDefault();
    this._upcomingExpanded = !this._upcomingExpanded;
    this._lastFingerprint = "";
    this._lastCompactFp = "";
    this.render();
  }

  // Step the schedule view by `delta` (-1 = previous game, +1 = next),
  // fetching the neighbor over WebSocket (cached per offset). Offset 0 is the
  // live/auto-selected game; a result with offset 0 drops back to the sensor.
  _navigateSchedule(delta) {
    const target = this._navOffset + delta;
    if (target === 0) {
      // Back to the live/auto-selected game — fall through to the sensor, no
      // fetch needed. Reset arrows to their offset-0 optimistic state.
      this._navOffset = 0;
      this._navGameData = null;
      this._navHasPrev = true;
      this._navHasNext = true;
      this._forceScheduleRerender();
      return;
    }
    if (this._navCache instanceof Map && this._navCache.has(target)) {
      this._applyNavResult(this._navCache.get(target));
      return;
    }
    if (this._navInflight) return; // de-dupe rapid taps
    const entity = this.config?.entity;
    const conn = this._hass?.connection;
    if (!entity || !conn) return;
    this._navInflight = conn
      .sendMessagePromise({
        type: "mlb_live_scoreboard/game_at_offset",
        entity_id: entity,
        offset: target,
      })
      .then((res) => {
        if (!res || !res.game_data) {
          // No game that direction — we're at a schedule boundary. Disable the
          // arrow so further taps don't re-hit the backend.
          if (delta < 0) this._navHasPrev = false;
          else this._navHasNext = false;
          this._forceScheduleRerender();
          return;
        }
        if (this._navCache instanceof Map) this._navCache.set(res.offset, res);
        this._applyNavResult(res);
      })
      .catch((err) => {
        console.debug(`[${CARD_TAG}] game_at_offset fetch failed:`, err);
      })
      .finally(() => {
        this._navInflight = null;
      });
  }

  _applyNavResult(res) {
    this._navOffset = res.offset;
    // Offset 0 means we've stepped back to the live game — fall through to the
    // sensor attributes rather than pinning the fetched copy.
    this._navGameData = res.offset === 0 ? null : res.game_data;
    this._navHasPrev = res.has_prev !== false;
    this._navHasNext = res.has_next !== false;
    this._forceScheduleRerender();
  }

  // A navigated view can carry the same upstream fingerprint as the live game,
  // so bust every render memo to guarantee the swap actually paints.
  _forceScheduleRerender() {
    this._lastFingerprint = "";
    this._lastCompactFp = "";
    this._lastLiveHtml = "";
    // Off the default view → arm the idle auto-return; back on it → cancel.
    if (this._navOffset !== 0) this._armNavIdleTimer();
    else this._clearNavIdleTimer();
    this.render();
  }

  _armNavIdleTimer() {
    this._clearNavIdleTimer();
    this._navIdleTimer = setTimeout(() => {
      this._navIdleTimer = null;
      if (this._navOffset !== 0) {
        this._resetScheduleNav();
        this._forceScheduleRerender();
      }
    }, NAV_IDLE_RETURN_MS);
  }

  _clearNavIdleTimer() {
    if (this._navIdleTimer) {
      clearTimeout(this._navIdleTimer);
      this._navIdleTimer = null;
    }
  }

  // Step the play-by-play view by `delta` half-innings (-1 = one half earlier,
  // +1 = toward live). The view never goes past the live half (offset capped at
  // 0); offset 0 falls through to the live `recent_plays`. Past halves are
  // fetched over WebSocket and cached per offset.
  _navigateInning(delta) {
    const target = Math.min(0, this._inningOffset + delta);
    if (target === this._inningOffset) return; // already at the live half
    if (target === 0) {
      this._inningOffset = 0;
      this._inningView = null;
      this._inningHasPrev = true;
      this._forceInningRerender();
      return;
    }
    if (this._inningCache instanceof Map && this._inningCache.has(target)) {
      this._applyInningResult(this._inningCache.get(target));
      return;
    }
    if (this._inningInflight) return; // de-dupe rapid taps
    const entity = this.config?.entity;
    const conn = this._hass?.connection;
    if (!entity || !conn) return;
    this._inningInflight = conn
      .sendMessagePromise({
        type: "mlb_live_scoreboard/half_inning_at_offset",
        entity_id: entity,
        offset: target,
      })
      .then((res) => {
        if (!res || !Array.isArray(res.plays)) {
          // Nothing further back — we're at the oldest played half. Disable the
          // back arrow so further taps don't re-hit the backend.
          if (delta < 0) this._inningHasPrev = false;
          this._forceInningRerender();
          return;
        }
        if (this._inningCache instanceof Map)
          this._inningCache.set(res.offset, res);
        this._applyInningResult(res);
      })
      .catch((err) => {
        console.debug(`[${CARD_TAG}] half_inning_at_offset fetch failed:`, err);
      })
      .finally(() => {
        this._inningInflight = null;
      });
  }

  _applyInningResult(res) {
    this._inningOffset = res.offset;
    // Offset 0 means we've paged back up to the live half — fall through to the
    // live recent_plays rather than pinning the fetched copy.
    this._inningView = res.offset === 0 ? null : res;
    this._inningHasPrev = res.has_prev !== false;
    this._inningHasNext = res.has_next !== false;
    this._forceInningRerender();
  }

  _forceInningRerender() {
    this._lastFingerprint = "";
    this._lastCompactFp = "";
    this._lastLiveHtml = "";
    // Viewing a past half → arm the idle auto-return; back on live → cancel.
    if (this._inningOffset !== 0) this._armInningIdleTimer();
    else this._clearInningIdleTimer();
    this.render();
  }

  _armInningIdleTimer() {
    this._clearInningIdleTimer();
    this._inningIdleTimer = setTimeout(() => {
      this._inningIdleTimer = null;
      if (this._inningOffset !== 0) {
        this._resetInningNav();
        this._forceInningRerender();
      }
    }, INNING_IDLE_RETURN_MS);
  }

  _clearInningIdleTimer() {
    if (this._inningIdleTimer) {
      clearTimeout(this._inningIdleTimer);
      this._inningIdleTimer = null;
    }
  }

  _setupRefreshTimer() {
    this._clearRefreshTimer();
    const rate = Number(this.config.refresh_rate);
    if (rate > 0 && this._hass) {
      this._refreshInterval = setInterval(() => {
        if (this._hass && this.config?.entity) {
          // Bust the render memos first — state-driven repaints already
          // happen via the hass setter, so without this the fingerprint
          // short-circuit made every tick a no-op. The periodic repaint
          // exists to refresh time-derived text ("Today 7:10 PM" etc.).
          this._lastFingerprint = "";
          this._lastCompactFp = "";
          this.render();
        }
      }, rate * 1000);
    }
  }

  disconnectedCallback() {
    this._clearRefreshTimer();
    this._clearHoldExpiryTimer();
    this._clearNavIdleTimer();
    this._clearInningIdleTimer();
    this._destroyPlayerCardPopup();
    this._destroyLineupPopup();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this.card) {
      ensureCardStyles(this);
      this.card = document.createElement("ha-card");
      this.card.className = "mlb-live-game-card";
      this.content = document.createElement("div");
      this.content.className = "card-content";
      this.card.appendChild(this.content);
      this.appendChild(this.card);
      this.content.addEventListener("click", (ev) => this._onContentClick(ev));
      this.content.addEventListener("keydown", (ev) =>
        this._onContentKeydown(ev),
      );
    }
    this.render();
    // (Re-)arm the refresh timer whenever it's absent, not only on first
    // load — setConfig() and disconnectedCallback() both clear it, so the
    // old first-load-only arming permanently killed refresh_rate after a
    // card edit or a dashboard tab switch.
    if (!this._refreshInterval) {
      this._setupRefreshTimer();
    }
  }

  getCardSize() {
    return 4;
  }

  renderLinescore(competition, viewedHalf = null) {
    const competitors = competition?.competitors || [];
    const away = competitors.find((c) => c?.homeAway === "away") || {};
    const home = competitors.find((c) => c?.homeAway === "home") || {};
    const awayLines = Array.isArray(away?.linescores) ? away.linescores : [];
    const homeLines = Array.isArray(home?.linescores) ? home.linescores : [];
    let innings = Math.max(awayLines.length, homeLines.length);
    if (!innings) return "";
    // While the inning pager views a past half, truncate to that point in
    // the game: no columns past the viewed inning, and the home cell of the
    // viewed inning stays blank when viewing its top half (home hadn't
    // batted yet). Totals roll back the same way.
    if (viewedHalf && viewedHalf.inning > 0) {
      innings = Math.min(innings, viewedHalf.inning);
    }
    const headers = Array.from(
      { length: innings },
      (_, i) => `<div class="inning-head">${i + 1}</div>`,
    ).join("");
    const awayCells = Array.from(
      { length: innings },
      (_, i) =>
        `<div class="inning-cell">${escapeHtml(awayLines[i]?.displayValue ?? awayLines[i]?.value ?? "")}</div>`,
    ).join("");
    const homeCells = Array.from(
      { length: innings },
      (_, i) =>
        `<div class="inning-cell">${
          viewedHalf && viewedHalf.half === "top" && i + 1 === viewedHalf.inning
            ? ""
            : escapeHtml(homeLines[i]?.displayValue ?? homeLines[i]?.value ?? "")
        }</div>`,
    ).join("");
    const awayTotalText = viewedHalf
      ? (() => {
          const t = totalsThroughHalf(away, viewedHalf.inning, true);
          return t.runs != null ? String(t.runs) : "—";
        })()
      : parseScore(away?.score).text || "—";
    const homeTotalText = viewedHalf
      ? (() => {
          const t = totalsThroughHalf(
            home,
            viewedHalf.inning,
            viewedHalf.half === "bottom",
          );
          return t.runs != null ? String(t.runs) : "—";
        })()
      : parseScore(home?.score).text || "—";
    // Compute grid-template-columns inline: team-abbr | N inning cells | R total.
    // Using `repeat(auto-fit, minmax(X, max-content))` is invalid per CSS Grid
    // spec and causes browsers to collapse to a single column, which stacks
    // every cell vertically. Setting an explicit track count avoids that.
    const gridCols = `max-content repeat(${innings}, minmax(18px, 1fr)) max-content`;
    return `
      <div class="linescore">
        <div class="linescore-grid" style="grid-template-columns: ${gridCols};">
          <div></div>${headers}<div class="inning-head">R</div>
          <div class="team-abbr">${escapeHtml(away?.team?.abbreviation || "A")}</div>${awayCells}<div class="inning-total">${escapeHtml(awayTotalText)}</div>
          <div class="team-abbr">${escapeHtml(home?.team?.abbreviation || "H")}</div>${homeCells}<div class="inning-total">${escapeHtml(homeTotalText)}</div>
        </div>
      </div>
    `;
  }

  _computeRenderFingerprint(stateObj) {
    // Cheap, scalar-only fingerprint: avoid JSON.stringify over arrays of
    // objects (was costly on long play lists). Each field below is a single
    // primitive whose change should trigger a re-render. Anything not listed
    // here is either derived from these fields or visually irrelevant.
    const attrs = stateObj?.attributes || {};
    const comp = attrs.competition || {};
    const status = comp.status || {};
    const competitors = Array.isArray(comp.competitors) ? comp.competitors : [];
    const away = competitors.find((c) => c?.homeAway === "away") || {};
    const home = competitors.find((c) => c?.homeAway === "home") || {};
    const sit = attrs.situation || {};
    const bs = attrs.batter_stats || {};
    const ps = attrs.pitcher_stats || {};
    const plays = Array.isArray(attrs.recent_plays) ? attrs.recent_plays : [];
    const lastPlay = plays.length ? plays[plays.length - 1] : null;
    // Server-anchored hold deadline: include the bucket (active vs expired)
    // so the card re-renders when the hold flips.
    const holdUntil = Number(attrs.third_out_hold_until);
    const holdActiveFp =
      Number.isFinite(holdUntil) && holdUntil > Date.now() / 1000
        ? `hold:${holdUntil}`
        : "";
    return [
      stateObj?.state,
      attrs.mode,
      // Visible scoreboard inputs
      away.score,
      away.recordSummary,
      home.score,
      home.recordSummary,
      status.type?.state,
      status.type?.name,
      status.period,
      // Matchup
      attrs.current_batter?.display_name,
      attrs.current_pitcher?.display_name,
      bs.avg,
      bs.hits_ab,
      bs.hr,
      bs.rbi,
      bs.game_rbi,
      bs.game_outcomes_display,
      ps.era,
      ps.ip,
      ps.pitches_strikes,
      ps.strikeouts,
      // Count + bases. The situation attribute uses snake_case keys — the
      // camelCase spellings this block once read were always undefined, so
      // base-runner changes alone never triggered a repaint.
      sit.balls,
      sit.strikes,
      sit.outs,
      sit.on_first,
      sit.on_second,
      sit.on_third,
      sit.first_last_name,
      sit.second_last_name,
      sit.third_last_name,
      // Recent plays — only the tail-most play affects rendering thresholds
      plays.length,
      lastPlay?.id,
      lastPlay?.outs,
      lastPlay?.away_score,
      lastPlay?.home_score,
      // Inning + third-out hold (server-anchored)
      attrs.inning_context?.is_between_halves,
      holdActiveFp,
      // Win probability — re-render the bar when either side shifts.
      attrs.win_probability?.away,
      attrs.win_probability?.home,
    ].join("||");
  }

  render() {
    if (!this._hass || !this.config) return;
    if (!this.config.entity) {
      if (this._lastFingerprint !== "__NO_ENTITY__") {
        this._lastFingerprint = "__NO_ENTITY__";
        this.content.innerHTML = `<div class="empty">Configure this card — choose an MLB Live Scoreboard entity.</div>`;
      }
      return;
    }
    const stateObj = this._hass.states[this.config.entity];
    if (!stateObj) {
      if (this._lastFingerprint !== "__NOT_FOUND__") {
        this._lastFingerprint = "__NOT_FOUND__";
        this.content.innerHTML = `<div class="empty">Entity not found: ${escapeHtml(this.config.entity)}</div>`;
      }
      return;
    }

    // Schedule navigation: when the live game advances to a new event, the
    // anchor has moved out from under any navigated view — snap back to live.
    const liveAttrs = stateObj.attributes || {};
    const liveDisplayId = String(liveAttrs.display_event_id || "");
    if (this._navAnchorId === undefined) {
      this._navAnchorId = liveDisplayId;
    } else if (this._navAnchorId !== liveDisplayId) {
      this._navAnchorId = liveDisplayId;
      this._resetScheduleNav();
    }
    // Inning pager anchors to the live half-inning. When the live half advances
    // (or the live game changes), snap the pager back so a stale past-inning
    // view and its per-offset cache never linger.
    const liveHalf = resolveLiveHalf(liveAttrs.inning_context || {});
    const inningAnchorKey = `${liveDisplayId}|${
      liveHalf ? `${liveHalf.inning}-${liveHalf.half}` : ""
    }`;
    if (this._inningAnchorKey === undefined) {
      this._inningAnchorKey = inningAnchorKey;
    } else if (this._inningAnchorKey !== inningAnchorKey) {
      this._inningAnchorKey = inningAnchorKey;
      this._resetInningNav();
    }
    // While navigated, render the fetched neighbor in place of the live game.
    const navActive = this._navOffset !== 0 && !!this._navGameData;
    const attrs = navActive ? this._navGameData : liveAttrs;
    // Live collapse/expand is card-local view state that re-baselines to the
    // configured default whenever the displayed game changes — an expand for
    // yesterday's game shouldn't carry into tonight's first pitch. Resolved
    // before the fingerprint so the flag below reflects this render's game.
    const liveExpandKey = String(
      attrs.display_event_id || attrs.competition?.id || "",
    );
    if (this._liveExpandedGameKey !== liveExpandKey) {
      this._liveExpandedGameKey = liveExpandKey;
      this._liveExpanded = this._liveDefaultExpanded();
    }
    const fpSource = navActive
      ? { state: String(this._navGameData.display_event_id || ""), attributes: this._navGameData }
      : stateObj;
    // The inning pager swaps only the play-by-play panel (live game otherwise
    // unchanged), so its offset/flags must be in the fingerprint or the swap
    // won't paint when the upstream data is otherwise unchanged.
    const inningFp = `inn:${this._inningOffset}:${this._inningHasPrev ? 1 : 0}${this._inningHasNext ? 1 : 0}`;
    const fingerprint = `nav:${this._navOffset}|${this._navHasPrev ? 1 : 0}${this._navHasNext ? 1 : 0}|${inningFp}|lx:${this._liveExpanded ? 1 : 0}|${this._computeRenderFingerprint(fpSource)}`;
    if (fingerprint === this._lastFingerprint) {
      return; // No changes, skip DOM update
    }
    this._lastFingerprint = fingerprint;
    const competition = attrs.competition || {};
    const competitors = competition?.competitors || [];
    const away = competitors.find((c) => c?.homeAway === "away") || {};
    const home = competitors.find((c) => c?.homeAway === "home") || {};
    const awayTeam = away?.team || {};
    const homeTeam = home?.team || {};
    const awayMeta = attrs.away_team || {};
    const homeMeta = attrs.home_team || {};
    const awayScore = parseScore(away?.score);
    const homeScore = parseScore(home?.score);
    const stateInfo = deriveGameState(attrs);
    const inningState = deriveInningState(attrs);
    // Collapsed live card = score rows + inning marker only; everything below
    // (win probability, linescore, matchup, play-by-play…) waits for a tap on
    // the header. Only the live view collapses — a delayed game still needs to
    // show its banner, and the non-live views have their own expander.
    const isLiveCard = stateInfo.pillClass === "live";
    const liveCollapsed = isLiveCard && this._liveExpanded !== true;
    const batter = currentBatterName(attrs);
    const pitcher =
      attrs.current_pitcher?.display_name ||
      attrs.current_pitcher?.short_name ||
      "";
    const batterStats = renderBatterLineSecondary(attrs.batter_stats);
    const batterHitsAb =
      attrs.batter_stats?.hits_ab ||
      renderCompactStatLine(attrs.batter_stats, [
        ["AB", "ab"],
        ["H", "h"],
      ]);
    const batterOutcomes = (
      attrs.batter_stats?.game_outcomes_display || ""
    ).trim();
    // Game-RBI shows alongside the at-bat outcomes so the line carries the
    // full "what happened today" picture (the secondary line is season totals).
    // Suppress when zero / missing — an empty " • 0 RBI" tail just adds noise.
    const gameRbiRaw = String(attrs.batter_stats?.game_rbi ?? "").trim();
    const gameRbiNum = Number(gameRbiRaw);
    const gameRbiBadge =
      gameRbiRaw && Number.isFinite(gameRbiNum) && gameRbiNum > 0
        ? `${gameRbiNum} RBI`
        : "";
    const batterPrimaryLine = [batterHitsAb, batterOutcomes, gameRbiBadge]
      .filter(Boolean)
      .join(" • ");
    const pitcherPrimaryLine = renderPitcherLinePrimary(attrs.pitcher_stats);
    const pitcherSecondaryLine = renderPitcherLineSecondary(
      attrs.pitcher_stats,
    );
    const awayRecord = this.config.show_records
      ? competitorRecord(away, awayMeta)
      : "";
    const homeRecord = this.config.show_records
      ? competitorRecord(home, homeMeta)
      : "";
    const awayWon =
      stateInfo.pillClass === "final" &&
      awayScore.num != null &&
      homeScore.num != null &&
      awayScore.num > homeScore.num;
    const homeWon =
      stateInfo.pillClass === "final" &&
      awayScore.num != null &&
      homeScore.num != null &&
      homeScore.num > awayScore.num;
    const marker = this.renderInningMarker(stateInfo, inningState);
    const awayTotals = teamTotals(away);
    const homeTotals = teamTotals(home);
    const periodLower = String(inningState.lower || "");
    const inningContext = attrs.inning_context || {};
    const ownerAbbr = String(attrs.team_abbr || "").toUpperCase();
    const awayAbbr = String(
      awayMeta?.abbreviation || awayTeam?.abbreviation || "",
    ).toUpperCase();
    const homeAbbr = String(
      homeMeta?.abbreviation || homeTeam?.abbreviation || "",
    ).toUpperCase();
    const ownerTeamId = String(attrs.team_id ?? "");
    const awayTeamId = String(awayTeam?.id ?? "");
    const homeTeamId = String(homeTeam?.id ?? "");
    // Prefer team-id match (stable), fall back to abbreviation. Defaults to
    // "away" only when we genuinely can't identify the owner — that puts the
    // configured team on the left in the common case and degrades gracefully.
    let ownerSide = "away";
    if (ownerTeamId && ownerTeamId === homeTeamId) ownerSide = "home";
    else if (ownerTeamId && ownerTeamId === awayTeamId) ownerSide = "away";
    else if (ownerAbbr && ownerAbbr === homeAbbr) ownerSide = "home";
    else if (ownerAbbr && ownerAbbr === awayAbbr) ownerSide = "away";
    const ownerLabel = ownerSide === "home" ? homeAbbr : awayAbbr;
    const opponentLabel = ownerSide === "home" ? awayAbbr : homeAbbr;
    const winProbabilityPanel =
      this.config.show_win_probability !== false &&
      isLiveCard &&
      !liveCollapsed
        ? renderWinProbabilityRow(
            attrs.win_probability || {},
            ownerSide,
            ownerLabel,
            opponentLabel,
          )
        : "";
    // Inning pager: while live, page the play-by-play back through earlier
    // half-innings. Swaps only this panel (rest of the card stays live).
    const inningActive =
      !navActive && this._inningOffset !== 0 && !!this._inningView;
    const hasPastHalf =
      !!liveHalf && !(liveHalf.inning <= 1 && liveHalf.half === "top");
    const showInningNav =
      isLiveCard &&
      !liveCollapsed &&
      this.config.show_inning_nav !== false &&
      !navActive &&
      (inningActive || hasPastHalf);
    const recentPlaysBody = renderRecentPlays(
      inningActive ? this._inningView.plays || [] : attrs.recent_plays || [],
      inningActive ? [] : attrs.current_pitches || [],
      attrs.situation || {},
      this.config,
    );
    const recentPlaysPanel = showInningNav
      ? renderInningStrip(recentPlaysBody, {
          atLive: !inningActive,
          downDisabled: inningActive ? !this._inningHasPrev : !hasPastHalf,
        })
      : recentPlaysBody;
    // While paging, the box-score inning marker flips to the viewed half (in
    // a distinct color) so it's always clear which half-inning is on screen.
    const displayMarker = inningActive
      ? renderPastHalfMarker(this._inningView)
      : marker;
    const markerClass = inningActive ? "history" : stateInfo.pillClass;
    // ...and the scoreboard numbers roll back to their values at the end of
    // the viewed half, so the score agrees with the plays on screen.
    const viewedHalf = inningActive
      ? {
          inning: Number(this._inningView.inning) || 0,
          half: String(this._inningView.half || ""),
        }
      : null;
    let dispAwayScore = awayScore;
    let dispHomeScore = homeScore;
    let dispAwayTotals = awayTotals;
    let dispHomeTotals = homeTotals;
    if (viewedHalf && viewedHalf.inning > 0) {
      const a = totalsThroughHalf(away, viewedHalf.inning, true);
      const h = totalsThroughHalf(
        home,
        viewedHalf.inning,
        viewedHalf.half === "bottom",
      );
      dispAwayScore = { text: a.runs != null ? String(a.runs) : "—", num: a.runs };
      dispHomeScore = { text: h.runs != null ? String(h.runs) : "—", num: h.runs };
      dispAwayTotals = { hits: a.hits ?? "—", errors: a.errors ?? "—" };
      dispHomeTotals = { hits: h.hits ?? "—", errors: h.errors ?? "—" };
    }
    const latestRecentPlay =
      Array.isArray(attrs.recent_plays) && attrs.recent_plays.length
        ? attrs.recent_plays[attrs.recent_plays.length - 1]
        : null;
    const explicitThirdOutPlay = attrs.third_out_play || null;
    const betweenHalves =
      periodLower.startsWith("mid") ||
      periodLower.startsWith("end") ||
      inningContext.is_between_halves;
    const nowTs = Date.now() / 1000;
    // The coordinator publishes a wallclock-anchored deadline so every card
    // (including ones rendered after the hold has already begun) flips from
    // the third-out result to the Due Up panel at the same moment. Falls back
    // to the play's wallclock for older coordinator builds.
    const serverHoldUntil = Number(attrs.third_out_hold_until);
    const explicitThirdOutTs = Number(explicitThirdOutPlay?.wallclock_ts);
    const explicitHoldActive = Number.isFinite(serverHoldUntil)
      ? serverHoldUntil > nowTs
      : !!explicitThirdOutPlay &&
        Number.isFinite(explicitThirdOutTs) &&
        Number(explicitThirdOutPlay?.outs) === 3 &&
        nowTs - explicitThirdOutTs < THIRD_OUT_HOLD_SECONDS;
    // Short grace window: a 3rd out just landed in recent_plays but the
    // coordinator hasn't promoted it to `third_out_play` yet.
    const recentFallbackThirdOut =
      !explicitThirdOutPlay &&
      latestRecentPlay &&
      Number(latestRecentPlay?.outs) === 3 &&
      String(latestRecentPlay?.text || "").trim() &&
      Number.isFinite(Number(latestRecentPlay?.wallclock_ts)) &&
      nowTs - Number(latestRecentPlay.wallclock_ts) <
        THIRD_OUT_RECENT_WINDOW_SECONDS
        ? latestRecentPlay
        : null;
    const dueUpDesc = [
      String(inningContext.period_prefix || "").trim(),
      String(
        inningContext.display_period ||
          attrs.competition?.status?.displayPeriod ||
          "",
      ).trim(),
    ]
      .filter(Boolean)
      .join(" ")
      .trim();
    const dueUpList = Array.isArray(attrs.due_up)
      ? attrs.due_up.filter(Boolean)
      : [];
    const dueUpReady = betweenHalves && dueUpList.length > 0;
    const holdThirdOut =
      betweenHalves &&
      (explicitHoldActive || (!dueUpReady && !!recentFallbackThirdOut));
    // Schedule a re-render at the exact moment the server-side hold expires so
    // the card flips to Due Up without waiting for the next HA state update.
    if (
      holdThirdOut &&
      !liveCollapsed &&
      Number.isFinite(serverHoldUntil) &&
      serverHoldUntil > nowTs
    ) {
      this._scheduleHoldExpiryRender(serverHoldUntil);
    }
    // Skipped while collapsed — this one pulls player headshots, so building
    // markup we'd only throw away would kick off pointless image fetches.
    const dueUpPanel =
      betweenHalves &&
      !holdThirdOut &&
      !inningState.pseudoFinal &&
      !liveCollapsed
        ? renderDueUpCards(this, dueUpList, dueUpDesc)
        : "";
    const countDotsPanel =
      this.config.show_count !== false &&
      stateInfo.pillClass === "live" &&
      !betweenHalves &&
      !holdThirdOut
        ? renderCountDotsRow(attrs.situation || {}, attrs.current_pitches || [])
        : "";
    // Special count dots showing 3 outs during the hold period
    const thirdOutCountDotsPanel =
      this.config.show_count !== false && holdThirdOut
        ? renderCountDotsRow({ balls: 0, strikes: 0, outs: 3 }, [])
        : "";
    const diamondHtml =
      this.config.show_diamond !== false
        ? renderBaseDiamond(attrs.situation || {})
        : "";
    // Pitch zone is opt-in (default off) and only renders during a live at-bat
    // with pitches that carry coordinates. `renderPitchZone` returns "" for
    // empty inputs so the surrounding wrapper stays inert between at-bats.
    const pitchZoneHtml =
      this.config.show_pitch_zone === true &&
      stateInfo.pillClass === "live" &&
      !betweenHalves &&
      !holdThirdOut
        ? renderPitchZone(attrs.current_pitches || [])
        : "";
    // Clicking a matchup half (anywhere but the player-name link) opens that
    // team's lineup popup: pitcher half -> fielding team, batter half ->
    // batting team. Resolve + bake the side into the markup so the delegated
    // handler stays a dumb attribute read (no stale-hass re-resolution).
    const luPitcherSide = lineupSideForRole(attrs, "pitcher");
    const luBatterSide = lineupSideForRole(attrs, "batter");
    const luSideAttrs = (side) => {
      if (this.config.show_lineup_popup === false) return "";
      if (side !== "away" && side !== "home") return "";
      const t = (attrs.lineups && attrs.lineups[side]) || {};
      const nm = escapeHtml(t.name || t.abbreviation || "team");
      return (
        ` data-team-popup="${side}" role="button" tabindex="0"` +
        ` aria-label="Show ${nm} lineup" title="Show ${nm} lineup"`
      );
    };
    // Combine diamond + pitch-zone into the matchup grid's center column.
    // Stack them only when both are present; otherwise emit the diamond (or
    // the zone) bare so the layout stays unchanged.
    const stackCenter = !!(diamondHtml && pitchZoneHtml);
    const centerColumnHtml = stackCenter
      ? `<div class="matchup-center stack-center">${diamondHtml}${pitchZoneHtml}</div>`
      : diamondHtml || pitchZoneHtml;
    // Also headshot-bearing, so it too stays unbuilt while collapsed.
    const matchupPanel =
      this.config.show_batter && !liveCollapsed
        ? `
            <div class="matchup-block ${batter || pitcher ? "" : "muted-block"}">
              <div class="matchup-grid enhanced productionish ${this.config.show_diamond !== false ? "with-diamond" : ""} ${pitchZoneHtml ? "with-pitch-zone" : ""}">
                <div class="matchup-side with-headshot stacked centered-half"${luSideAttrs(luPitcherSide)}>
                  ${renderPlayerHeadshot(this, attrs.current_pitcher?.headshot || "", pitcher || "Pitcher")}
                  <div class="matchup-copy centered-copy">
                    <div class="matchup-value">${playerNameMarkup(shortPersonName(pitcher || "TBD"), attrs.current_pitcher?.id)}</div>
                    <div class="matchup-subtle strongish stat-line">${pitcherPrimaryLine || ""}</div>
                    <div class="matchup-subtle secondary stat-line">${pitcherSecondaryLine || ""}</div>
                  </div>
                </div>
                ${centerColumnHtml}
                <div class="matchup-side with-headshot stacked centered-half align-right"${luSideAttrs(luBatterSide)}>
                  ${renderPlayerHeadshot(this, attrs.current_batter?.headshot || "", batter || "Batter")}
                  <div class="matchup-copy centered-copy">
                    <div class="matchup-value">${playerNameMarkup(shortPersonName(batter || "TBD"), attrs.current_batter?.id)}</div>
                    <div class="matchup-subtle strongish stat-line">${batterPrimaryLine || "—"}</div>
                    <div class="matchup-subtle secondary stat-line">${batterStats || ""}</div>
                  </div>
                </div>
              </div>
            </div>`
      : "";
    const onDeckHtml =
      this.config.show_on_deck !== false
        ? renderOnDeckRow(attrs.on_deck || {})
        : "";
    const baseOccupancyHtml =
      this.config.show_base_occupancy !== false
        ? renderBaseOccupancyRow(attrs.situation || {})
        : "";
    const liveExtras =
      isLiveCard && !liveCollapsed
        ? `
        <div class="live-panel productionish">
          ${
            holdThirdOut
              ? `${thirdOutCountDotsPanel}${matchupPanel}${recentPlaysPanel}`
              : dueUpPanel ||
                `${countDotsPanel}${matchupPanel}${onDeckHtml}${baseOccupancyHtml}${recentPlaysPanel}`
          }
        </div>`
        : "";
    // Prefer ESPN's specific detail ("Rain Delay", "Weather Delay", "Suspended")
    // as the primary label so the banner doesn't stack a generic "Game delayed"
    // on top of it. Fall back to the generic phrase when ESPN gives us nothing
    // recognisable.
    const delayedDetail = String(stateInfo.statusText || "").trim();
    const delayedHasSpecific =
      /\b(delay|suspend|rain|weather)\b/i.test(delayedDetail);
    const delayedPrimary = delayedHasSpecific ? delayedDetail : "Game delayed";
    const delayedExtras =
      stateInfo.pillClass === "delayed"
        ? `<div class="state-panel delayed-panel"><span class="mini-state warning">DLY</span><span>${escapeHtml(delayedPrimary)}</span></div>`
        : "";
    if (
      stateInfo.pillClass === "next" ||
      stateInfo.pillClass === "final" ||
      stateInfo.pillClass === "postponed"
    ) {
      // Reset expanded state when the displayed game changes (different opponent / date).
      const gameKeyForExpand = String(
        competition?.id ||
          `${awayTeam?.abbreviation || awayMeta?.abbreviation || "A"}-${homeTeam?.abbreviation || homeMeta?.abbreviation || "H"}-${competition?.date || ""}`,
      );
      if (this._upcomingExpandedGameKey !== gameKeyForExpand) {
        this._upcomingExpandedGameKey = gameKeyForExpand;
        this._upcomingExpanded = false;
      }
      const isUpcoming = stateInfo.pillClass === "next";
      const isFinalCompact = stateInfo.pillClass === "final";
      const canExpand = isUpcoming || isFinalCompact;
      const expanded = canExpand && this._upcomingExpanded === true;
      // renderCompactNonLive fingerprints its inputs itself and returns null
      // when the DOM is already up to date (the same fingerprint used to be
      // computed twice — here and again inside the method).
      const compactHtml = this.renderCompactNonLive(
        stateInfo,
        competition,
        awayTeam,
        awayMeta,
        awayRecord,
        awayScore,
        homeTeam,
        homeMeta,
        homeRecord,
        homeScore,
        attrs,
        expanded,
      );
      if (compactHtml !== null) this.content.innerHTML = compactHtml;
      return;
    }

    // The score rows double as the live card's expander. The chevron strip
    // underneath is the visible affordance (without it a collapsed card looks
    // like a card that's simply missing its details) and shares the same click
    // target, so tapping either one toggles.
    const scoreboardMain = `
        <div class="scoreboard-main">
          <div class="scoreboard scoreboard-rich">
            ${this.teamRow(awayTeam, awayMeta, "", dispAwayScore, awayWon, false, away, dispAwayTotals)}
            ${this.teamRow(homeTeam, homeMeta, "", dispHomeScore, homeWon, true, home, dispHomeTotals)}
          </div>
          <div class="inning-marker-side">
            <div class="inning-marker-wrap"><div class="inning-marker ${markerClass}">${displayMarker}</div></div>
          </div>
        </div>`;
    const headerHtml = isLiveCard
      ? `
      <div class="live-expandable${liveCollapsed ? "" : " expanded"}" role="button" tabindex="0" aria-expanded="${liveCollapsed ? "false" : "true"}" title="${liveCollapsed ? "Show game details" : "Hide game details"}">
        ${scoreboardMain}
        <div class="live-expand-strip"><span class="tri ${liveCollapsed ? "tri-down" : "tri-up"}"></span></div>
      </div>`
      : scoreboardMain;
    const liveHtml = `
      <div class="wrapper ${this._headshotSizeClass()}">
        ${headerHtml}
        ${winProbabilityPanel}
        ${this.config.show_linescore && !liveCollapsed ? this.renderLinescore(competition, viewedHalf) : ""}
        ${delayedExtras}
        ${liveExtras}
      </div>
    `;
    // Only update DOM if HTML actually changed
    if (liveHtml !== this._lastLiveHtml) {
      this._lastLiveHtml = liveHtml;
      this.content.innerHTML = liveHtml;
    }
  }

  formatCompactDateTime(dateValue) {
    const d = dateValue ? new Date(dateValue) : null;
    if (!d || Number.isNaN(d.getTime()))
      return { date: "", time: "", isToday: false };
    const now = new Date();
    const startOfToday = new Date(
      now.getFullYear(),
      now.getMonth(),
      now.getDate(),
    );
    const startOfTarget = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const isToday = startOfTarget.getTime() === startOfToday.getTime();
    const dayDiff = Math.round((startOfTarget - startOfToday) / 86400000);
    const DAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const dateText =
      dayDiff > 0 && dayDiff <= 7
        ? DAY_ABBR[d.getDay()]
        : `${d.getMonth() + 1}/${d.getDate()}`;
    return {
      date: dateText,
      time: d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
      isToday,
    };
  }

  renderCompactNonLive(
    stateInfo,
    competition,
    awayTeam,
    awayMeta,
    awayRecord,
    awayScore,
    homeTeam,
    homeMeta,
    homeRecord,
    homeScore,
    attrs = {},
    expanded = false,
  ) {
    // Compute a compact-specific fingerprint to avoid unnecessary DOM updates
    const isUpcoming = stateInfo.pillClass === "next";
    const isFinalCompact = stateInfo.pillClass === "final";
    const canExpand = isUpcoming || isFinalCompact;
    const compactFp = [
      stateInfo.pillClass,
      awayTeam?.abbreviation || awayMeta?.abbreviation,
      homeTeam?.abbreviation || homeMeta?.abbreviation,
      competition?.date,
      awayScore.text,
      homeScore.text,
      awayRecord,
      homeRecord,
      expanded ? "exp" : "col",
      canExpand ? this._upcomingDetailsFingerprint(attrs) : "",
      `nav:${this._navOffset}:${this._navHasPrev ? 1 : 0}${this._navHasNext ? 1 : 0}`,
    ].join("|");
    if (compactFp === this._lastCompactFp) {
      return null; // DOM already reflects this state — skip the update
    }
    this._lastCompactFp = compactFp;

    const when = this.formatCompactDateTime(competition?.date);
    const awayLogo = requestCachedLogo(
      this,
      awayTeam?.logo ||
        get(awayTeam, ["logos", 0, "href"], "") ||
        awayMeta?.logo ||
        "",
    );
    const homeLogo = requestCachedLogo(
      this,
      homeTeam?.logo ||
        get(homeTeam, ["logos", 0, "href"], "") ||
        homeMeta?.logo ||
        "",
    );
    const awayName =
      awayTeam?.name ||
      awayTeam?.displayName ||
      awayTeam?.shortDisplayName ||
      awayMeta?.name ||
      awayMeta?.short_name ||
      awayTeam?.abbreviation ||
      "—";
    const homeName =
      homeTeam?.name ||
      homeTeam?.displayName ||
      homeTeam?.shortDisplayName ||
      homeMeta?.name ||
      homeMeta?.short_name ||
      homeTeam?.abbreviation ||
      "—";
    const isFinal = stateInfo.pillClass === "final";
    const isPostponed = stateInfo.pillClass === "postponed";
    const awayWon =
      isFinal &&
      awayScore.num != null &&
      homeScore.num != null &&
      awayScore.num > homeScore.num;
    const homeWon =
      isFinal &&
      awayScore.num != null &&
      homeScore.num != null &&
      homeScore.num > awayScore.num;
    const finalMarker = `<div class="compact-final-marker">${
      when.date
        ? `<div class="compact-date compact-final-date">${when.date}</div>`
        : ""
    }</div>`;
    const postponedMarker = `<div class="compact-final-marker"><div class="compact-pill compact-pill-postponed">PPD</div></div>`;
    const nextRight = when.isToday
      ? `<div class="compact-next-wrap today-only">
          <div class="compact-time">${when.time || ""}</div>
        </div>`
      : `<div class="compact-next-wrap">
          <div class="compact-date">${when.date || ""}</div>
          <div class="compact-time">${when.time || ""}</div>
        </div>`;
    const rightHtml = isPostponed
      ? postponedMarker
      : isFinal
        ? finalMarker
        : nextRight;
    const expandable = isUpcoming || isFinalCompact;
    const detailsPanel =
      expandable && expanded
        ? renderUpcomingDetails(
            this,
            attrs,
            awayMeta,
            homeMeta,
            awayLogo,
            homeLogo,
            { kind: isUpcoming ? "upcoming" : "final" },
          )
        : "";
    const expandTitle = isUpcoming
      ? expanded
        ? "Hide details"
        : "Show pitchers & standings"
      : expanded
        ? "Hide game summary"
        : "Show game summary";
    // Prev/next schedule arrows flank the date/status. Hidden via config; the
    // whole branch is already non-live so no extra live-state guard is needed.
    // Optimistic-enabled at offset 0 (we don't hold the full schedule
    // client-side); the backend's has_prev/has_next disable them at the ends.
    const showScheduleNav = this.config?.show_schedule_nav !== false;
    const prevDisabled = !this._navHasPrev;
    const nextDisabled = !this._navHasNext;
    const markerCore = `<div class="inning-marker-wrap">${rightHtml}</div>`;
    const innerMarkerSide = showScheduleNav
      ? `<button class="schedule-nav-btn schedule-nav-prev" type="button" aria-label="Previous game" title="Previous game"${prevDisabled ? " disabled" : ""}>‹</button>${markerCore}<button class="schedule-nav-btn schedule-nav-next" type="button" aria-label="Next game" title="Next game"${nextDisabled ? " disabled" : ""}>›</button>`
      : markerCore;
    const markerSide = `<div class="inning-marker-side${showScheduleNav ? " has-schedule-nav" : ""}">${innerMarkerSide}</div>`;
    const wrapperClasses = [
      "wrapper",
      "compact-mode",
      this._headshotSizeClass(),
    ];
    if (expandable) wrapperClasses.push("upcoming-expandable");
    if (expanded) wrapperClasses.push("expanded");
    const html = `
      <div class="${wrapperClasses.join(" ")}"${expandable ? ` role="button" tabindex="0" aria-expanded="${expanded ? "true" : "false"}" title="${expandTitle}"` : ""}>
        <div class="scoreboard-main">
          <div class="scoreboard scoreboard-rich">
            <div class="team-row away ${awayWon ? "winner" : ""}">
              <div class="team-left">
                ${awayLogo ? `<img class="logo" src="${awayLogo}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : `<div class="logo placeholder"></div>`}
                <div class="meta">
                  <div class="name">${escapeHtml(awayName)}${awayRecord ? ` <span class="record-inline">(${escapeHtml(awayRecord)})</span>` : ""}</div>
                </div>
              </div>
              ${isFinal ? `<div class="team-right compact-final-score-right"><div class="score final-score">${awayScore.text || "—"}</div></div>` : ""}
            </div>
            <div class="team-row home ${homeWon ? "winner" : ""}">
              <div class="team-left">
                ${homeLogo ? `<img class="logo" src="${homeLogo}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : `<div class="logo placeholder"></div>`}
                <div class="meta">
                  <div class="name">${escapeHtml(homeName)}${homeRecord ? ` <span class="record-inline">(${escapeHtml(homeRecord)})</span>` : ""}</div>
                </div>
              </div>
              ${isFinal ? `<div class="team-right compact-final-score-right"><div class="score final-score">${homeScore.text || "—"}</div></div>` : ""}
            </div>
          </div>
          ${markerSide}
        </div>
        ${detailsPanel}
      </div>`;
    // Identical output (e.g. a forced repaint whose time-derived text didn't
    // actually change) — skip the DOM write but keep the new fingerprint.
    if (html === this._lastCompactHtml) return null;
    this._lastCompactHtml = html;
    return html;
  }

  renderInningMarker(stateInfo, inningState) {
    if (stateInfo.pillClass === "delayed")
      return `<div class="marker-text">DLY</div>`;
    if (stateInfo.pillClass === "postponed")
      return `<div class="marker-text">PPD</div>`;
    if (stateInfo.pillClass === "final" || inningState.pseudoFinal)
      return `<div class="marker-text">F</div>`;
    if (stateInfo.pillClass !== "live") return "";
    const period = inningState.period || "";
    if (inningState.isTop)
      return `<div class="inning-stack up"><div class="arrow">▲</div><div class="num">${period}</div></div>`;
    if (inningState.isBottom)
      return `<div class="inning-stack down"><div class="num">${period}</div><div class="arrow">▼</div></div>`;
    if (inningState.isMid)
      return `<div class="inning-stack mid"><div class="num">${period}</div></div>`;
    return `<div class="marker-text">LIVE</div>`;
  }

  teamRow(
    team,
    teamMeta,
    record,
    score,
    winner = false,
    isHome = false,
    competitor = {},
    totals = { hits: "—", errors: "—" },
  ) {
    const logoRaw =
      team?.logo || get(team, ["logos", 0, "href"], "") || teamMeta?.logo || "";
    const logo = requestCachedLogo(this, logoRaw);
    const displayName =
      team?.name ||
      team?.displayName ||
      team?.shortDisplayName ||
      teamMeta?.name ||
      teamMeta?.short_name ||
      team?.abbreviation ||
      "—";
    return `
      <div class="team-row ${winner ? "winner" : ""} ${isHome ? "home" : "away"}">
        <div class="team-left">
          ${logo ? `<img class="logo" src="${logo}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : `<div class="logo placeholder"></div>`}
          <div class="meta">
            <div class="name">${escapeHtml(displayName)}${record ? ` <span class="record-inline">(${escapeHtml(record)})</span>` : ""}</div>
          </div>
        </div>
        <div class="team-right rhe-values">
          <div class="score rhe-score">${score.text || "—"}</div>
          <div class="rhe-num hits">${totals.hits}</div>
          <div class="rhe-num errors">${totals.errors}</div>
        </div>
      </div>
    `;
  }

}

// Card styles, injected once into document.head (guarded by id) instead of
// being re-parsed on every render and duplicated per card instance. The card
// renders in light DOM, so a <style> tag is document-global wherever it
// lives — this is behavior-identical to the previous per-render inline
// injection, minus the repeated parse cost.
const CARD_STYLE_ID = "mlb-live-game-card-style";
const CARD_CSS = `
        .wrapper {
          padding: 0 1px 0;
          /* Make the card its own size-query container so headshots (and any
             future width-aware element) can size in 'cqi' units. 'inline-size'
             tracks the card's width, which is the right axis for HA's
             per-column dashboards — viewport-relative units would be wrong
             when one tab has 1 wide card and another has 4 narrow ones. */
          container-type: inline-size;
          container-name: mlb-card;
        }
        .header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 6px;
          margin-bottom: 6px;
        }
        .title-wrap {
          display:flex;
          align-items:center;
          gap:6px;
          min-width:0;
        }
        .title {
line-height: 1.2;
        }
        .version {
line-height:1;
          color: var(--secondary-text-color);
          border: 1px solid rgba(255,255,255,0.12);
          border-radius: 999px;
          padding: 2px 6px;
        }
        .pill {
border-radius: 999px;
          padding: 4px 8px;
          background: transparent;
        }
        .pill.live { color: var(--success-color); }
        .pill.delayed { color: var(--warning-color); }
        .pill.final { color: var(--primary-text-color); }
        .pill.postponed { color: var(--warning-color); }
        .pill.next { color: var(--secondary-text-color); }
        .pill.idle { color: var(--secondary-text-color); }
        .status {
          color: var(--secondary-text-color);
line-height: 1.25;
          margin-bottom: 10px;
        }
        .scoreboard {
          display: grid;
          gap: 0;
        }
        .scoreboard-rich {
          grid-template-columns: minmax(0, 1fr);
        }
        .team-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 10px;
          padding: 1px 0;
          border-top: none;
          opacity: 0.9;
}
        .team-row.winner {
          opacity: 1;
        }
        .team-row.winner .name, .team-row.winner .score {
}
        .team-row:first-child {
          border-top: none;
        }
        .team-left {
          display: flex;
          align-items: center;
          gap: 10px;
          min-width: 0;
        }
        .logo {
          width: 28px;
          height: 28px;
          object-fit: contain;
          flex: 0 0 28px;
        }
        .logo.placeholder {
          border-radius: 50%;
          background: rgba(255,255,255,0.08);
        }
        .player-shot {
          /* Default ("auto") behavior — scale headshots to ~14% of the card's
             inline (width) size, clamped to a sane min/max. The clamp is
             chosen so a 400px-wide card lands near the historic 56px size
             (14% = 56), while wider cards grow and narrower cards shrink.
             The headshot-size-* preset rules below replace this clamp with
             a fixed pixel value when the user opts out of auto.

             The ceiling is deliberately high (128px, reached at a ~915px
             card). It used to be 80px, which saturated at only ~570px — so
             every dashboard wider than that rendered an identical 80px
             circle and the "auto" scaling looked broken on wide views.
             128px keeps the same headshot-to-card ratio a 400px card has. */
          width: clamp(40px, 14cqi, 128px);
          /* Square via aspect-ratio, never an explicit height: if the box is
             ever shrunk by its container, a fixed height would not shrink
             with it and border-radius:50% would render an ellipse. Same
             lesson as .diamond-graphic below. */
          aspect-ratio: 1 / 1;
          object-fit: cover;
          border-radius: 50%;
          /* 'flex: 0 0 auto' lets the box's own width (the clamp) act as
             the flex-basis, so the headshot shrinks/grows with the card. */
          flex: 0 0 auto;
          background: rgba(255,255,255,0.06);
        }
        .player-shot.placeholder {
          background: rgba(255,255,255,0.08);
        }
        .meta {
          min-width: 0;
        }
        .name {
          line-height: 1.15;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          font-size: 16px !important;
          font-weight: 500;
        }
        .record {
color: var(--secondary-text-color);
          line-height: 1.15;
        }
        .record-inline {
color: var(--primary-text-color);
          opacity: 0.92;
          font-size: 0.9em;
          font-weight: 400;
}
        .team-right {
          display:flex;
          align-items:center;
          gap:6px;
          margin-left:auto;
        }
        .rhe-header {
          display:flex;
          align-items:center;
          justify-content:space-between;
          margin: 0 0 -1px;
          color: var(--secondary-text-color);
line-height: 1;
          padding: 0 0 2px;
        }
        .rhe-spacer {
          flex: 1 1 auto;
        }
        .rhe-cols, .rhe-values {
          display:grid;
          grid-template-columns: 28px 22px 22px;
          align-items:center;
          justify-items:end;
          column-gap: 8px;
          white-space: nowrap;
        }
        .rhe-col {
          text-align:right;
}
        .rhe-col.score {
}
        .rhe-score, .score {
min-width: 20px;
          text-align: right;
          font-variant-numeric: tabular-nums;
          font-size: 1.05em;
          font-weight: 500;
}
        .rhe-num {
color: var(--primary-text-color);
          font-variant-numeric: tabular-nums;
          text-align:right;
          font-size: 1.05em;
          font-weight: 500;
        }

.scoreboard-main {
          display:grid;
          grid-template-columns:minmax(0,1fr) auto;
          column-gap:6px;
          align-items:start;
        }
        .inning-marker-side {
          display:flex;
          align-items:center;
          justify-content:center;
          align-self:stretch;
          min-width:28px;
          padding-top: 0;
        }
        .inning-marker-side.has-schedule-nav {
          gap: 2px;
          min-width: 64px;
        }
        .schedule-nav-btn {
          flex: 0 0 auto;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 22px;
          height: 28px;
          padding: 0;
          margin: 0;
          border: none;
          background: none;
          cursor: pointer;
          font-size: 1.4em;
          line-height: 1;
          color: var(--secondary-text-color);
          border-radius: 4px;
          -webkit-tap-highlight-color: transparent;
        }
        .schedule-nav-btn:hover:not([disabled]) {
          color: var(--primary-text-color);
          background: var(--secondary-background-color);
        }
        .schedule-nav-btn:focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: 1px;
        }
        .schedule-nav-btn[disabled] {
          opacity: 0.25;
          cursor: default;
        }
        .inning-strip {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 24px;
          /* The card's own bottom padding sits right under this strip and
             visually pins the arrows against the divider. The negative
             bottom margin reclaims most of it so the arrows sit centered
             in the remaining band and the row stays short. */
          margin: 2px 0 -12px;
          padding: 2px 0;
          border-top: 1px solid var(--divider-color, rgba(127,127,127,0.2));
        }
        .inning-nav-btn {
          flex: 0 0 auto;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 34px;
          height: 9px;
          padding: 0;
          margin: 0;
          border: none;
          background: none;
          cursor: pointer;
          color: var(--secondary-text-color);
          border-radius: 4px;
          -webkit-tap-highlight-color: transparent;
        }
        /* Selectors stay scoped to their container: the card renders in light
           DOM, so an unqualified .tri rule would both leak out to the
           dashboard and be overridable by it. */
        .inning-nav-btn .tri,
        .live-expand-strip .tri {
          width: 0;
          height: 0;
          border-left: 6px solid transparent;
          border-right: 6px solid transparent;
        }
        .inning-nav-btn .tri-down,
        .live-expand-strip .tri-down { border-top: 7px solid currentColor; }
        .inning-nav-btn .tri-up,
        .live-expand-strip .tri-up { border-bottom: 7px solid currentColor; }
        .inning-nav-btn:hover:not([disabled]) {
          color: var(--primary-text-color);
          background: var(--secondary-background-color);
        }
        .inning-nav-btn:focus-visible {
          outline: 2px solid var(--primary-color);
          outline-offset: 1px;
        }
        .inning-nav-btn[disabled] {
          opacity: 0.25;
          cursor: default;
        }
        .inning-marker-wrap {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          margin: 0;
          gap: 0;
          min-height: 100%;
          height: 100%;
        }
        .inning-marker {
          min-width: 20px;
          text-align: center;
border-radius: 0;
          padding: 0;
          background: none;
          color: var(--secondary-text-color);
        }
        .inning-marker.live { color: var(--success-color); }
        .inning-marker.history { color: var(--info-color, #4a90d9); }
        .inning-marker.delayed { color: var(--warning-color); }
        .inning-marker.final { color: var(--primary-text-color); }
        .inning-marker.postponed { color: var(--warning-color); }
        .inning-stack {
          display:flex;
          flex-direction:column;
          align-items:center;
          justify-content:center;
          line-height:1;
          gap:0px;
        }
        .inning-stack .arrow, .inning-stack .num, .marker-text {
line-height: 1;
        }
        .inning-stack .num {
          font-variant-numeric: tabular-nums;
        }
        .marker-text {
          display:block;
          min-width: 16px;
          text-align:center;
        }
        .state-panel {
          border-top: 1px solid rgba(255,255,255,0.08);
          margin-top: 10px;
          padding-top: 8px;
color: var(--secondary-text-color);
          text-align: center;
        }
        .delayed-panel { color: var(--warning-color); }
        .final-panel { color: var(--secondary-text-color); }
        .matchup-block {
          border-top: 0;
          margin-top: 6px;
          padding: 6px 0 0;
        }
        .matchup-grid {
          display:grid;
          grid-template-columns: minmax(0,1fr) minmax(0,1fr);
          gap: 24px;
          align-items:start;
        }

        .matchup-grid.enhanced {
          /* The center column holds the base diamond. It was a fixed 56px,
             which silently capped the diamond on wide cards (its own clamp
             could ask for more, then got flex-shrunk back down to 56 — the
             root cause of the slanted-rhombus bug, see .diamond-graphic).
             Now it scales on the same container query as everything else,
             with a floor of 56px so narrow cards are byte-identical to
             before (14cqi only exceeds 56px past a 400px card). */
          grid-template-columns: minmax(0,1fr) clamp(56px, 14cqi, 100px) minmax(0,1fr);
          column-gap: 0;
          row-gap: 0;
          align-items:start;
        }
        /* Widen the center column only when the pitch zone is present, so the
           layout stays unchanged for users who don't opt in. Sized with the
           same container-query approach as the headshots so it grows with
           the dashboard column. */
        .matchup-grid.enhanced.with-pitch-zone {
          grid-template-columns: minmax(0,1fr) clamp(140px, 42cqi, 260px) minmax(0,1fr);
        }
        .matchup-center {
          display:flex;
          align-items:center;
          justify-content:center;
        }
        .matchup-center.stack-center {
          flex-direction: column;
          gap: 0;
          align-self: start;
          justify-content: flex-start;
        }
        /* When the diamond is part of the stack-center column the wrapper's
           default 70px floor (used for vertical centering in the row-flex
           layout) just adds dead space between diamond and zone. Collapse it. */
        .matchup-center.stack-center > .diamond-center {
          min-height: 0;
        }
        .diamond-center {
          align-self:center;
          min-height: 70px;
        }
        /* Pitch-zone graphic — sits below the diamond when both render. The
           SVG scales via viewBox; the wrapper bounds its width with the same
           clamp(min, Xcqi, max) pattern so the zone tracks the card width. */
        .pitch-zone {
          width: clamp(120px, 36cqi, 240px);
          aspect-ratio: 220 / 190;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        .pitch-zone svg {
          width: 100%;
          height: 100%;
          overflow: visible;
        }
        .pitch-zone-frame {
          fill: rgba(255,255,255,0.04);
          stroke: rgba(255,255,255,0.55);
          stroke-width: 1.5;
        }
        .pitch-zone-plate {
          fill: rgba(255,255,255,0.55);
          stroke: rgba(255,255,255,0.75);
          stroke-width: 1;
        }
        .pitch-zone-dot circle {
          filter: drop-shadow(0 0 1px rgba(0,0,0,0.45));
        }
        .diamond-graphic {
          position: relative;
          /* Scales with the card's inline (width) size via the same 'cqi'
             container query used by the headshots — grows on wide
             dashboards, clamps to a sane min/max on narrow ones. The ceiling
             stays a little under the center column's own ceiling (100px) so
             the diamond keeps its historic ~6px breathing room either side
             instead of filling the column edge to edge. */
          width: clamp(44px, 13cqi, 88px);
          /* Keep the box square via aspect-ratio rather than a fixed height.
             The center grid column can be narrower than this clamp asks for,
             so the flex item's width gets shrunk to fit while an explicit
             height would NOT shrink with it — leaving a tall rectangle that
             the rotate(45deg) below renders as a slanted rhombus. Deriving
             height from the used width keeps it square at any shrink.

             For the same reason every child below is sized in PERCENT, not
             in 'cqi': percentages resolve against this box's *used* width
             (post-shrink), whereas cqi would resolve against the card and
             desynchronise from a shrunk parent — reintroducing the exact
             bug this aspect-ratio exists to prevent. The percentages are the
             old fixed pixel values over the old 56px reference box, so the
             diamond's proportions are unchanged at every size. */
          aspect-ratio: 1 / 1;
          display:flex;
          align-items:center;
          justify-content:center;
        }
        .diamond-field {
          position:absolute;
          inset: 12.5%;   /* was 7px on a 56px box */
          border: 2px solid rgba(255,255,255,0.56);
          border-radius: 2px;
          transform: rotate(45deg);
          box-sizing:border-box;
          background: rgba(255,255,255,0.03);
        }
        .diamond-base {
          position:absolute;
          width: 17.9%;   /* was 10px on a 56px box */
          aspect-ratio: 1 / 1;
          background: rgba(255,255,255,0.24);
          border: 1px solid rgba(255,255,255,0.42);
          transform: rotate(45deg);
          box-sizing:border-box;
          border-radius: 1px;
        }
        .diamond-base.on {
          background: #63a2ff;
          border-color: rgba(255,255,255,0.70);
          box-shadow: 0 0 0 1px rgba(99,162,255,0.22);
        }
        /* Percentage margins always resolve against the containing block's
           WIDTH (true for margin-top too), which is what we want to re-centre
           a percentage-width base on a square box: -8.95% is half of 17.9%. */
        .diamond-base.home { bottom: 1.8%; left: 50%; margin-left: -8.95%; }
        .diamond-base.first { top: 50%; right: 1.8%; margin-top: -8.95%; }
        .diamond-base.second { top: 1.8%; left: 50%; margin-left: -8.95%; }
        .diamond-base.third { top: 50%; left: 1.8%; margin-top: -8.95%; }
        .diamond-mound {
          position:absolute;
          width: 10.7%;   /* was 6px on a 56px box */
          aspect-ratio: 1 / 1;
          border-radius: 50%;
          background: rgba(255,255,255,0.48);
          left: 50%;
          top: 50%;
          transform: translate(-50%, -50%);
        }
        .matchup-divider {
          width:1px;
          align-self:stretch;
          justify-self:center;
          background: transparent;
        }
        .matchup-side {
          min-width:0;
          display:flex;
          flex-direction:column;
          align-items:center;
          justify-content:flex-start;
          text-align:center;
        }
        .matchup-side.centered {
          text-align:center;
        }
        .matchup-side[data-team-popup] {
          cursor: pointer;
          border-radius: 6px;
        }
        .matchup-side[data-team-popup]:hover {
          background: var(--secondary-background-color, rgba(255,255,255,0.06));
        }
        .matchup-side[data-team-popup]:focus-visible {
          outline: 2px solid var(--warning-color, #ffb300);
          outline-offset: 2px;
        }
        .matchup-copy.centered {
          width:100%;
          text-align:center;
        }
                .matchup-value {
          margin-top: 4px;
line-height:1.15;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          color: var(--warning-color);
        }
        .player-link {
          color: inherit;
          cursor: pointer;
          text-decoration: none;
        }
        .player-link:hover { text-decoration: underline; }
        .player-link:focus-visible {
          outline: 2px solid var(--warning-color);
          outline-offset: 1px;
          border-radius: 2px;
        }

        .stat-line {
color: var(--primary-text-color) !important;
        }
.matchup-subtle {
          margin-top: 3px;
line-height:1.16;
          color: var(--secondary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .label, .subtle {
          color: var(--secondary-text-color);
        }
        .value {
}
        .subtle {
          margin-top: 4px;
line-height: 1.2;
        }
        .muted-block .value {
          color: var(--secondary-text-color);
}
        .live-panel, .state-panel {
          margin-top: 8px;
          padding-top: 6px;
          border-top: 1px solid rgba(255,255,255,0.08);
        }
        .win-prob-row {
          margin-top: 8px;
          padding-top: 6px;
          border-top: 1px solid rgba(255,255,255,0.08);
        }
        .win-prob-bar {
          position: relative;
          width: 100%;
          height: 20px;
          border-radius: 10px;
          overflow: hidden;
          background: #c0392b;
          font-size: 0.86em;
          line-height: 20px;
          font-variant-numeric: tabular-nums;
        }
        .win-prob-fill {
          position: absolute;
          top: 0;
          left: 0;
          bottom: 0;
          background: #1d6bd6;
        }
        .win-prob-label {
          position: absolute;
          top: 0;
          bottom: 0;
          display: inline-flex;
          align-items: center;
          color: #ffffff;
          font-weight: 800;
          letter-spacing: 0.02em;
          white-space: nowrap;
          pointer-events: none;
          text-shadow:
            0 0 2px rgba(0,0,0,0.85),
            0 1px 2px rgba(0,0,0,0.7),
            0 0 1px rgba(0,0,0,0.9);
        }
        .win-prob-label-owner { left: 10px; }
        .win-prob-label-opponent { right: 10px; }
        .live-strip {
          display: grid;
          grid-template-columns: max-content 1fr max-content;
          gap: 10px;
          align-items: center;
        }
        .mini-state {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 34px;
          padding: 4px 8px;
          border-radius: 999px;
          background: rgba(255,255,255,0.08);
line-height: 1;
        }
        .mini-state.strong {
          color: var(--success-color);
        }
        .mini-state.warning {
          color: var(--warning-color);
        }
        .count-summary, .bases-summary {
line-height: 1.2;
        }
        .count-summary {
          color: var(--primary-text-color);
}
        .bases-summary {
          color: var(--secondary-text-color);
          text-align: right;
          white-space: nowrap;
        }
        .inning-sub {
color: var(--secondary-text-color);
          line-height: 1;
        }
        .count-dots-row {
          margin-top: 2px;
          display: flex;
          align-items: center;
          flex-wrap: wrap;
          gap: 6px 8px;
}
        .on-deck-row {
          display: flex;
          justify-content: flex-end;
          align-items: center;
          gap: 6px;
          margin-top: 6px;
          padding-top: 2px;
          font-size: 0.85em;
          line-height: 1.15;
        }
        .on-deck-label {
          color: var(--secondary-text-color);
        }
        .on-deck-name {
          color: var(--warning-color);
          font-weight: 500;
        }
        .on-deck-stats {
          color: var(--secondary-text-color);
          font-size: 0.9em;
        }
        .bases-occupancy-row {
          margin-top: 6px;
          padding-top: 2px;
          display:grid;
          grid-template-columns: repeat(3, minmax(0,1fr));
          column-gap: 10px;
          align-items:center;
line-height: 1.15;
        }
        .base-slot {
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .base-slot:nth-child(1) { text-align:left; }
        .base-slot:nth-child(2) { text-align:center; }
        .base-slot:nth-child(3) { text-align:right; }
        .base-label {
          color: var(--secondary-text-color);
}
        .base-value {
}
        .base-value.empty-value {
          color: var(--secondary-text-color); }
        .base-value.occupied-value {
color: var(--primary-text-color); }
        .count-dots-row.prominent {
          margin-top: 0;
          padding: 0 0 3px;
          justify-content: center;
          gap: 12px 18px;
        }
        .count-pack {
          display:inline-flex;
          align-items:center;
          gap:6px;
        }
        .dots-label {
          color: var(--secondary-text-color);
        }
        .dots-label.verbose {
color: var(--primary-text-color);
        }
        .count-dots-row.verbose {
          justify-content: space-between;
          gap: 10px;
        }
        .dots {
          display: inline-flex;
          gap: 4px;
          margin-right: 4px;
        }
        .dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
          background: rgba(255,255,255,0.14);
          display: inline-block;
        }
        .dot.ball.on { background: #4fc3f7; }
        .dot.strike.on { background: #ffb74d; }
        .dot.out.on { background: #ef5350; }
        .muted {
          color: var(--secondary-text-color);
          margin-left: 6px;
        }
        .totals-inline {
          color: var(--secondary-text-color);
          margin-left: 6px;
}

                .plays-panel {
          margin-top: 8px;
          padding-top: 8px;
          border-top: 1px solid rgba(255,255,255,0.08);
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .play-row {
          display: grid;
          grid-template-columns: minmax(0,1fr) max-content;
          gap: 10px;
          align-items: start;
line-height: 1.2;
}
        .play-text {
          min-width: 0;
          white-space: normal;
          word-break: break-word;
          color: var(--primary-text-color);
        }
        .pitch-row {
          grid-template-columns: minmax(0,1fr);
          margin-top: -2px;
          margin-bottom: -2px;
        }
        .pitch-row .play-text {
          text-align: right;
          justify-self: stretch;
          color: var(--secondary-text-color);
          opacity: 0.88;
          width: 100%;
          margin-left: 0;
          padding-left: 0;
line-height: 1.05;
        }
        .pitch-row .play-indicator { display: none; }
        /* Cosmetic highlight for triple-digit pitch velocities. The override
           is !important because the card renders in light DOM, where host
           dashboard CSS can otherwise win the cascade on color. */
        .pitch-velo-hot {
          color: #ff4d4d !important;
          font-weight: 700;
        }
        .play-indicator {
          color: var(--secondary-text-color);
          white-space: nowrap;
          font-variant-numeric: tabular-nums;
}
        .dueup-panel {
          margin-top: 10px;
          padding-top: 0;
          border-top: 0;
        }
        .dueup-title {
          text-align: center;
color: var(--secondary-text-color);
          font-weight: 600;
          margin-bottom: 6px;
}
        .dueup-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0,1fr));
          gap: 10px;
        }
        .dueup-card {
          display:flex;
          flex-direction:column;
          align-items:center;
          text-align:center;
          gap:2px;
          min-width:0;
        }
        .dueup-name {
line-height: 1.03;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          max-width: 100%;
          color: var(--warning-color);
        }
        .dueup-stat {
          margin-top: 1px;
          color: var(--secondary-text-color);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          max-width: 100%;
        }

.pregame-panel, .leaders-panel {
          margin-top: 10px;
          padding-top: 8px;
          border-top: 1px solid rgba(255,255,255,0.08);
        }
        .pregame-matchup, .leaders-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }
        .pitcher-side, .leaders-col {
          min-width: 0;
        }
        .subtle-inline {
          color: var(--secondary-text-color);
margin-left: 6px;
        }
        .leaders-head {
margin-bottom: 6px;
          color: var(--secondary-text-color);
        }
        .leader-item {
          display: grid;
          grid-template-columns: max-content 1fr max-content;
          gap: 6px;
          align-items: center;
line-height: 1.25;
          margin-top: 3px;
        }
        .leader-cat {
          color: var(--secondary-text-color);
          white-space: nowrap;
        }
        .leader-name {
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          color: var(--warning-color);
        }
        .leader-val {
white-space: nowrap;
        }
        .linescore {
          margin-top: 10px;
          border-top: 1px solid rgba(255,255,255,0.08);
          padding-top: 8px;
        }
        .linescore-grid {
          display: grid;
          grid-template-columns: max-content repeat(9, minmax(18px, 1fr)) max-content;
          gap: 4px 6px;
          align-items: center;
}
        .inning-head, .inning-cell, .inning-total, .team-abbr {
          text-align: center;
        }
        .inning-head, .team-abbr {
          color: var(--secondary-text-color);
        }
        .inning-total {
}
        .empty {
          padding: 16px;
          color: var(--secondary-text-color);
        }

        .wrapper.compact-mode {
          padding: 0;
          margin: 0;
        }
        .compact-board {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          column-gap: 8px;
          align-items: center;
          min-height: 32px;
        }
        .compact-left {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
        }
        .compact-team-row {
          display:flex;
          align-items:center;
          gap:6px;
          min-width:0;
          padding: 0;
          margin: 0;
        }
        .compact-logo {
          width: 24px;
          height: 24px;
          object-fit: contain;
          flex: 0 0 24px;
          margin-top: 0;
          margin-bottom: 0;
        }
        .compact-name {
line-height: 1.15;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          margin: 0;
          padding: 0;
}
        .compact-record {
          color: var(--primary-text-color);
opacity: 0.92;
          margin: 0;
          padding: 0;
}
        .compact-right {
          display:flex;
          align-items:center;
          justify-content:flex-end;
          min-width:58px;
        }
        .compact-next-wrap {
          display:flex;
          flex-direction:column;
          align-items:center;
          gap:0px;
          line-height:1;
        }
        .compact-next-wrap.today-only {
          justify-content:center;
          height:100%;
        }

        .compact-final-wrap {
          display:flex;
          flex-direction:row;
          align-items:center;
          justify-content:flex-end;
          gap:6px;
          line-height:1.05;
        }
        .compact-date, .compact-score {
white-space: nowrap;
          line-height: 1.08;
        }
        .compact-final-scores {
          display:flex;
          flex-direction:column;
          align-items:flex-end;
          justify-content:center;
          gap:1px;
        }
        .compact-next-wrap .compact-date,
        .compact-next-wrap .compact-time {
          white-space: nowrap;
          line-height: 1.05;
          font-size: 1em !important;
          font-weight: 400 !important;
        }
        .compact-next-wrap.today-only .compact-time {
          font-size: 1em !important;
          font-weight: 500 !important;
        }
        .compact-pill {
color: var(--secondary-text-color);
white-space: nowrap;
          min-width: 12px;
          text-align:center;
        }
        .compact-final-date {
          font-size: 1em;
          font-weight: 400;
        }
        .compact-pill-postponed {
          display:flex;
          align-items:center;
          align-self:center;
          min-height: 34px;
          color: var(--warning-color);
          font-size: 0.85em;
        }
        .compact-final-marker {
          display:flex;
          flex-direction:column;
          align-items:center;
          justify-content:center;
          gap:1px;
          height:100%;
        }
        .compact-final-score-right {
          display:flex;
          align-items:center;
          justify-content:flex-end;
          margin-left:auto;
        }
        .final-score {
          font-size:1.2em;
          font-weight:600;
          min-width:28px;
          text-align:right;
        }
        .compact-mode .team-row.winner .final-score {
          color: var(--primary-color, #03a9f4);
        }

        /* Live-card collapse/expand. The header (score rows + chevron strip)
           is one click target; the strip reuses the inning-pager's triangle
           glyphs so the two affordances read as the same language. */
        .live-expandable {
          cursor: pointer;
          outline: none;
          -webkit-tap-highlight-color: transparent;
        }
        .live-expandable:focus-visible {
          box-shadow: 0 0 0 2px var(--primary-color, #03a9f4);
          border-radius: 8px;
        }
        .live-expand-strip {
          display: flex;
          align-items: center;
          justify-content: center;
          height: 11px;
          margin: 2px 0 0;
          color: var(--secondary-text-color);
        }
        /* Expanded, the strip sits directly above the first panel's own
           divider, so drop its reserved band to avoid a double gap. */
        .live-expandable.expanded .live-expand-strip {
          height: 9px;
          margin-bottom: -2px;
          opacity: 0.55;
        }
        .live-expandable:hover .live-expand-strip {
          color: var(--primary-text-color);
          opacity: 1;
        }

        /* Upcoming-game expandable details */
        .upcoming-expandable {
          cursor: pointer;
          outline: none;
        }
        .upcoming-expandable:focus-visible {
          box-shadow: 0 0 0 2px var(--primary-color, #03a9f4);
          border-radius: 8px;
        }
        .upcoming-details-panel {
          display: flex;
          flex-direction: column;
          gap: 10px;
          padding: 10px 6px 4px;
          margin-top: 6px;
          border-top: 1px solid var(--divider-color, rgba(127,127,127,0.25));
        }
        /* The container's border-top already separates the panel from the
           linescore — the first sub-panel doesn't need its own divider or
           top spacing on top of that. Subsequent sub-panels keep their
           border-top so they remain visually distinct. */
        .upcoming-details-panel > :first-child {
          margin-top: 0;
          padding-top: 0;
          border-top: none;
        }
        .upcoming-pitchers-grid {
          display: grid;
          grid-template-columns: 1fr auto 1fr;
          align-items: center;
          gap: 8px;
        }
        .upcoming-pitcher-side {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 2px;
          text-align: center;
        }
        .upcoming-pitcher-side.align-right { /* symmetry hint, no-op layout */ }
        .upcoming-pitcher-img {
          /* Same auto-scale strategy as .player-shot, with a slightly larger
             ceiling because the probable-pitcher panel is dedicated to the
             portrait so it can afford more pixels on wide cards. */
          width: clamp(44px, 16cqi, 144px);
          aspect-ratio: 1 / 1;
          border-radius: 50%;
          object-fit: cover;
          background: var(--secondary-background-color, rgba(127,127,127,0.15));
        }
        .upcoming-pitcher-img.logo-fallback {
          object-fit: contain;
          padding: 4px;
        }
        .upcoming-pitcher-img.placeholder {
          background: var(--secondary-background-color, rgba(127,127,127,0.15));
        }

        /* Headshot-size presets. Active when the user picks anything other
           than "auto" in the editor. Each preset overrides both the matchup/
           due-up headshot (.player-shot) and the probable-pitcher portrait
           (.upcoming-pitcher-img) with the same fixed size so the card has a
           single consistent headshot size across all panels.

           These pixel values must stay in sync with HEADSHOT_SIZE_PRESETS at
           the top of this file. The presets are intentionally a short
           ladder (Small / Medium / Large / X-Large) rather than a free-form
           pixel input — easier to choose, and the auto clamp already covers
           the in-between cases for most viewports.

           Width only: the base rules carry 'aspect-ratio: 1 / 1', so height
           follows automatically. Don't reintroduce an explicit height here —
           that is what makes a shrunk box render as an ellipse. Note these
           are fixed sizes, so on a wide card the auto clamp now grows past
           even X-Large; that is intended, the presets exist to OPT OUT of
           scaling rather than to cap it. */
        .wrapper.headshot-size-small .player-shot,
        .wrapper.headshot-size-small .upcoming-pitcher-img {
          width: 40px;
        }
        .wrapper.headshot-size-medium .player-shot,
        .wrapper.headshot-size-medium .upcoming-pitcher-img {
          width: 56px;
        }
        .wrapper.headshot-size-large .player-shot,
        .wrapper.headshot-size-large .upcoming-pitcher-img {
          width: 72px;
        }
        .wrapper.headshot-size-xlarge .player-shot,
        .wrapper.headshot-size-xlarge .upcoming-pitcher-img {
          width: 88px;
        }
        .upcoming-pitcher-name {
          font-weight: 600;
          font-size: 0.92em;
          line-height: 1.1;
          color: var(--warning-color);
        }
        .upcoming-pitcher-stat {
          font-size: 0.85em;
          opacity: 0.9;
        }
        .upcoming-pitcher-stat.secondary {
          opacity: 0.75;
        }
        .upcoming-pitchers-vs {
          font-size: 0.8em;
          opacity: 0.6;
          font-style: italic;
        }
        .panel-heading {
          font-size: 0.78em;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          text-align: center;
          opacity: 0.85;
          margin-bottom: 4px;
        }
        .decisions-panel {
          margin-top: 10px;
          padding-top: 8px;
          border-top: 1px solid rgba(255,255,255,0.08);
        }
        .decisions-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(0, 1fr));
          gap: 10px;
          align-items: start;
        }
        .decision-cell {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 2px;
          min-width: 0;
          text-align: center;
        }
        .decision-img {
          /* W/L/SV portraits. These were pinned at 44px and never scaled at
             all; 11cqi is 44px at a 400px card, so narrow cards render
             exactly as before and only wider ones gain. */
          width: clamp(44px, 11cqi, 88px);
          aspect-ratio: 1 / 1;
          border-radius: 50%;
          object-fit: cover;
          background: rgba(255,255,255,0.04);
        }
        .decision-img.placeholder {
          background: rgba(255,255,255,0.06);
        }
        .decision-label {
          font-size: 0.72em;
          font-weight: 600;
          text-transform: uppercase;
          letter-spacing: 0.05em;
          color: var(--secondary-text-color);
        }
        .decision-name {
          font-size: 0.92em;
          line-height: 1.2;
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          max-width: 100%;
        }
        .decision-record {
          color: var(--secondary-text-color);
          font-variant-numeric: tabular-nums;
          font-weight: 500;
          margin-left: 2px;
        }
        .decision-stats {
          font-size: 0.8em;
          color: var(--secondary-text-color);
          font-variant-numeric: tabular-nums;
          font-weight: 500;
          line-height: 1.25;
          margin-top: 2px;
          overflow-wrap: anywhere;
        }
        .scoring-plays-panel {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .scoring-play-row {
          display: grid;
          grid-template-columns: 28px 38px 1fr max-content;
          gap: 6px;
          /* 'start' (not baseline) so multi-line wrapped text keeps the
             period / team / score columns aligned to the top of the row
             instead of drifting to the first wrapped line's baseline. */
          align-items: start;
          padding: 2px 4px;
          font-size: 0.86em;
          line-height: 1.3;
        }
        .scoring-play-period {
          color: var(--secondary-text-color);
          font-variant-numeric: tabular-nums;
          font-weight: 600;
        }
        .scoring-play-team {
          color: var(--secondary-text-color);
          font-weight: 600;
          letter-spacing: 0.02em;
        }
        .scoring-play-text {
          /* Wrap long play descriptions across multiple lines instead of
             truncating with an ellipsis — narrow viewports (mobile) and
             long ESPN play strings both routinely overflow a single line.
             'min-width: 0' keeps the grid column from forcing its track to
             the intrinsic text width; 'overflow-wrap: anywhere' is the
             safety net for rare unbreakable tokens (URLs, long names). */
          min-width: 0;
          white-space: normal;
          overflow-wrap: anywhere;
        }
        .scoring-play-score {
          font-variant-numeric: tabular-nums;
          color: var(--secondary-text-color);
        }
        .leader-empty {
          color: var(--secondary-text-color);
          opacity: 0.6;
        }
        .final-highlights-row {
          display: flex;
          justify-content: center;
          margin: 4px 0 2px;
        }
        .final-highlights-link {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 6px 12px;
          border-radius: 14px;
          background: rgba(255, 255, 255, 0.06);
          border: 1px solid rgba(255, 255, 255, 0.12);
          color: var(--primary-text-color);
          text-decoration: none;
          font-size: 0.88em;
          line-height: 1.2;
          transition: background 0.12s ease;
        }
        .final-highlights-link:hover,
        .final-highlights-link:focus {
          background: rgba(255, 255, 255, 0.12);
          text-decoration: none;
          outline: none;
        }
        .final-highlights-icon {
          color: var(--primary-color, #03a9f4);
          font-size: 0.85em;
        }
        .final-highlights-label {
          font-weight: 500;
        }
        .upcoming-standings {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .standings-heading {
          font-size: 0.95em;
          font-weight: 600;
          text-align: center;
          opacity: 0.85;
          margin-bottom: 4px;
        }
        .standings-row {
          display: grid;
          grid-template-columns: 1fr 56px 48px;
          align-items: center;
          gap: 6px;
          padding: 2px 4px;
          font-size: 0.88em;
          line-height: 1.25;
          border-radius: 4px;
        }
        .standings-row.standings-header {
          font-size: 0.75em;
          font-weight: 600;
          opacity: 0.65;
          text-transform: uppercase;
          letter-spacing: 0.04em;
        }
        .standings-row.my-team {
          background: var(--primary-color, #03a9f4);
          color: var(--text-primary-color, #fff);
          font-weight: 600;
        }
        .standings-name {
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .standings-wl {
          text-align: right;
          font-variant-numeric: tabular-nums;
        }
        .standings-gb {
          text-align: right;
          font-variant-numeric: tabular-nums;
        }
`;

function ensureCardStyles(host) {
  // Lovelace nests cards inside shadow roots (hui-root etc.), so a
  // document.head stylesheet can never reach the card content. A <style>
  // element applies to whichever tree scope contains it, so attach it
  // inside the card element itself — one per card instance instead of one
  // per render like the old inline interpolation.
  if (host.querySelector(`.${CARD_STYLE_ID}`)) return;
  const style = document.createElement("style");
  style.className = CARD_STYLE_ID;
  style.textContent = CARD_CSS;
  host.appendChild(style);
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, MlbLiveGameCard);
} else {
  console.info(`[mlb-live-game-card] ${CARD_VERSION} already registered`);
}
