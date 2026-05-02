const CARD_TAG = "mlb-live-game-card";
const CARD_VERSION = "1.6.0";
console.info(`[${CARD_TAG}] ${CARD_VERSION} loaded`);

// Number of seconds the card keeps showing the third-out play after it occurs,
// before yielding to the due-up panel for the next half-inning.
const THIRD_OUT_HOLD_SECONDS = 30;
// Wallclock window (seconds) within which a stored play is treated as the
// just-completed third out, even without an explicit `third_out_play` attribute.
const THIRD_OUT_RECENT_WINDOW_SECONDS = 8;

window.customCards = window.customCards || [];
if (!window.customCards.find((c) => c.type === CARD_TAG)) {
  window.customCards.push({
    type: CARD_TAG,
    name: "MLB Live Game Card",
    description: `MLB Live Scoreboard (${CARD_VERSION})`,
  });
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
    const text = scoreObj.displayValue ?? (scoreObj.value != null ? String(scoreObj.value) : "");
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
    const overall = records.find((r) => String(r?.type || "").toLowerCase() === "total") || records[0];
    if (overall?.summary) return String(overall.summary);
  }
  if (teamPayload?.record_summary) return String(teamPayload.record_summary);
  return "";
}

function currentBatterName(attrs) {
  return attrs?.current_batter?.display_name || attrs?.current_batter?.short_name || "";
}

function formatEventDate(dateRaw) {
  if (!dateRaw) return "";
  const dt = new Date(dateRaw);
  if (Number.isNaN(dt.getTime())) return "";
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfTarget = new Date(dt.getFullYear(), dt.getMonth(), dt.getDate());
  const dayDiff = Math.round((startOfTarget - startOfToday) / 86400000);
  const timeText = dt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  if (dayDiff === 0) return `Today ${timeText}`;
  if (dayDiff === 1) return `Tomorrow ${timeText}`;
  if (dayDiff === -1) return `Yesterday ${timeText}`;
  const dateText = dt.toLocaleDateString([], { month: "numeric", day: "numeric" });
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
    type?.detail || type?.shortDetail || type?.statusPrimary || type?.description || attrs.status_text || ""
  ).trim();
  const eventDate = get(competition, ["date"], "");
  const scheduledText = formatEventDate(eventDate);
  const isDelayed = attrs.is_delayed === true || name === "STATUS_DELAYED" || detail.toLowerCase().includes("delayed");
  const isLive = attrs.is_live === true || state === "in" || state === "live" || name === "STATUS_IN_PROGRESS" || isDelayed;
  const isFinal = state === "post" || type?.completed === true || name === "STATUS_FINAL" || detail.toLowerCase().startsWith("final");
  const isPregame = state === "pre" || name === "STATUS_SCHEDULED" || mode === "next";

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

function buildCountText(situation) {
  if (!situation) return "";
  const balls = Number(situation.balls ?? 0);
  const strikes = Number(situation.strikes ?? 0);
  const outs = Number(situation.outs ?? 0);
  return `${balls}-${strikes} • ${outs} out${outs === 1 ? "" : "s"}`;
}

function renderDots(count, total, klass) {
  return Array.from({ length: total }, (_, i) => `<span class="dot ${klass} ${i < count ? "on" : ""}"></span>`).join("");
}

function buildBasesText(situation) {
  if (!situation) return "";
  const bases = [];
  if (situation.on_first) bases.push("1B");
  if (situation.on_second) bases.push("2B");
  if (situation.on_third) bases.push("3B");
  if (!bases.length) return "Bases empty";
  return `Runners on ${bases.join(", ")}`;
}

function renderBaseOccupancyRow(situation) {
  const first = situation?.first_last_name || "Empty";
  const second = situation?.second_last_name || "Empty";
  const third = situation?.third_last_name || "Empty";
  const val = (v) => `<span class="base-value ${v === "Empty" ? "empty-value" : "occupied-value"}">${v}</span>`;
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
      <span class="on-deck-name">${name}</span>
      ${stats ? `<span class="on-deck-stats">${stats}</span>` : ""}
    </div>`;
}

function renderCountDotsRow(situation, currentPitches = []) {
  const activePitches = Array.isArray(currentPitches) ? currentPitches.filter(Boolean) : [];
  
  // Derive balls/strikes from the pitches array when available for consistency
  // with the pitch-by-pitch display, otherwise fall back to situation
  let balls = 0;
  let strikes = 0;
  if (activePitches.length) {
    for (const pitch of activePitches) {
      const p = String(pitch).toLowerCase();
      if (p.includes('ball') && !p.includes('foul')) {
        balls++;
      } else if (p.includes('strike') || p.includes('foul') || p.includes('swinging') || p === 'in play') {
        // Foul with 2 strikes doesn't add a strike, but we cap at 2 anyway
        strikes++;
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
  const inningsPitched = String(stats?.innings_pitched ?? stats?.ip ?? "").trim();
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
  const period = Number(status?.period ?? type?.period ?? competition?.status?.period ?? 0) || 0;
  const prefixText = String(
    status?.periodPrefix || type?.periodPrefix || type?.detail || type?.shortDetail || type?.statusPrimary || ""
  ).trim();
  const lower = prefixText.toLowerCase();
  const away = (competition?.competitors || []).find((c) => c?.homeAway === "away") || {};
  const home = (competition?.competitors || []).find((c) => c?.homeAway === "home") || {};
  const awayScore = parseScore(away?.score).num;
  const homeScore = parseScore(home?.score).num;
  const tied = awayScore != null && homeScore != null && awayScore === homeScore;
  const homeLeading = awayScore != null && homeScore != null && homeScore > awayScore;
  const pseudoFinal = attrs.is_live === true && period >= 9 && !tied && (
    lower.startsWith("end") || (lower.startsWith("mid") && homeLeading)
  );
  const isTop = lower.startsWith("top") || lower.startsWith("t ");
  const isBottom = lower.startsWith("bottom") || lower.startsWith("bot") || lower.startsWith("b ");
  const isMid = lower.startsWith("mid") || lower.startsWith("end");
  return { period, prefixText, lower, pseudoFinal, isTop, isBottom, isMid };
}

function formatCountDots(situation) {
  return `
    <span class="count-pack"><span class="dots-label">B</span><span class="dots">${renderDots(Number(situation?.balls ?? 0), 3, "ball")}</span></span>
    <span class="count-pack"><span class="dots-label">S</span><span class="dots">${renderDots(Number(situation?.strikes ?? 0), 2, "strike")}</span></span>
    <span class="count-pack"><span class="dots-label">O</span><span class="dots">${renderDots(Number(situation?.outs ?? 0), 3, "out")}</span></span>
  `;
}

function teamTotals(competitor) {
  const lines = Array.isArray(competitor?.linescores) ? competitor.linescores : [];
  const hits = competitor?.hits ?? lines.reduce((sum, line) => sum + (Number(line?.hits) || 0), 0);
  const errors = competitor?.errors ?? lines.reduce((sum, line) => sum + (Number(line?.errors) || 0), 0);
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
  return `${parts[0].charAt(0)}. ${parts[parts.length - 1]}`;
}

function renderPlayerHeadshot(card, url, alt = "") {
  const src = requestCachedLogo(card, url);
  return src
    ? `<img class="player-shot" src="${src}" alt="${alt}" loading="lazy" decoding="async" referrerpolicy="no-referrer">`
    : `<div class="player-shot placeholder"></div>`;
}

function renderDueUpCards(card, dueUp, inningDescription = "") {
  const list = Array.isArray(dueUp) ? dueUp.filter(Boolean).slice(0, 3) : [];
  if (!list.length) return "";
  const desc = String(inningDescription || (Array.isArray(dueUp) && dueUp.length ? (((dueUp[0] && dueUp[0].inning_desc) || [dueUp[0]?.period_prefix, dueUp[0]?.display_period].filter(Boolean).join(" ")) || "") : "")).trim();
  return `
    <div class="dueup-panel">
      <div class="dueup-title">${desc ? `${desc}&nbsp;&nbsp;Due up` : 'Due up'}</div>
      <div class="dueup-grid">
        ${list.map((item) => `
          <div class="dueup-card">
            ${renderPlayerHeadshot(card, item.headshot || "", item.short_name || item.display_name || "")}
            <div class="dueup-name">${shortPersonName(item.short_name || item.display_name || "—")}</div>
            <div class="dueup-stat">${[item.avg, item.hits_ab].filter(Boolean).join(" • ") || "—"}</div>
          </div>`).join("")}
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

function renderBasesDiamond(situation) { return ""; }

function renderLeaderList(items) {
  const list = Array.isArray(items) ? items.filter(Boolean).slice(0, 3) : [];
  if (!list.length) return "";
  return list.map((item) => `<div class="leader-item"><span class="leader-cat">${item.category || ""}</span><span class="leader-name">${shortPersonName(item.name || "")}</span><span class="leader-val">${item.value || ""}</span></div>`).join("");
}


function renderPlayIndicator(play, previousContext = {}) {
  const playText = String(play?.text || "").toLowerCase();
  const altType = String(play?.alternative_type || play?.alternativeType || "").toLowerCase();
  const typeText = String(play?.type || play?.play_type || "").toLowerCase();

  const away = play?.away_score;
  const home = play?.home_score;
  const prevAway = previousContext?.away_score;
  const prevHome = previousContext?.home_score;
  const prevOuts = previousContext?.outs;

  const scoreValue = Number(play?.score_value ?? play?.scoreValue ?? 0);
  const scoringFlag = play?.scoring_play === true || play?.scoringPlay === true || scoreValue > 0;
  const looksLikePitchingChange = playText.includes(" relieved ") || playText.includes(" replaces ") || playText.includes(" comes in for ") || altType.includes("lineup-change") || typeText.includes("lineup-change");

  if (!looksLikePitchingChange && away != null && home != null && away !== "" && home !== "") {
    const changedVsPrevious = prevAway != null && prevHome != null && (String(away) !== String(prevAway) || String(home) !== String(prevHome));
    if (scoringFlag || changedVsPrevious) return `${away}-${home}`;
  }

  const outs = Number(play?.outs);
  if (Number.isFinite(outs)) {
    const previousKnownOuts = Number.isFinite(Number(prevOuts)) ? Number(prevOuts) : null;
    if (outs > 0 && previousKnownOuts != null && outs > previousKnownOuts) return `${"•".repeat(Math.max(outs,1))} Outs`;
    if (outs === 3 && previousKnownOuts == null) return `${"•".repeat(3)} Outs`;
  }
  return "";
}

function renderRecentPlays(plays, currentPitches = [], situation = {}, config = {}) {
  const showPitches = config.show_pitches !== false;
  const showPlayResults = config.show_play_results !== false;
  const chronological = Array.isArray(plays) ? plays.filter((p) => p && p.text) : [];
  const list = showPlayResults ? [...chronological] : [];
  const pitches = showPitches && Array.isArray(currentPitches) ? currentPitches.filter(Boolean).map((p) => String(p).trim()).filter(Boolean).reverse() : [];
  if (!list.length && !pitches.length) return "";
  const pitchHtml = pitches.map((pitch) => `
        <div class="play-row pitch-row">
          <div class="play-text">${pitch}</div>
          <div class="play-indicator"></div>
        </div>`).join("");
  let previousKnownOuts = null;
  let previousKnownAway = null;
  let previousKnownHome = null;
  const playHtml = list.map((play) => {
    const indicator = renderPlayIndicator(play, {
      outs: previousKnownOuts,
      away_score: previousKnownAway,
      home_score: previousKnownHome,
    });
    const outs = Number(play?.outs);
    if (Number.isFinite(outs)) previousKnownOuts = outs;
    if (play?.away_score != null && play?.away_score !== "") previousKnownAway = play.away_score;
    if (play?.home_score != null && play?.home_score !== "") previousKnownHome = play.home_score;
    return `
        <div class="play-row">
          <div class="play-text">${play.text}</div>
          <div class="play-indicator">${indicator}</div>
        </div>`;
  }).join("");
  return `
    <div class="plays-panel">${pitchHtml}${playHtml}
    </div>`;
}

class MlbLiveGameCard extends HTMLElement {  setConfig(config) {
    if (!config?.entity) {
      throw new Error("You need to define an entity");
    }
    this.config = {
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
      refresh_rate: 0, // seconds, 0 = disabled (rely on hass state updates)
      ...config,
    };
    // Clear any existing refresh timer when config changes
    this._clearRefreshTimer();
  }

  _clearRefreshTimer() {
    if (this._refreshInterval) {
      clearInterval(this._refreshInterval);
      this._refreshInterval = null;
    }
  }

  // State machine for the "hold the third-out play on screen" UX:
  //   gameKey               - which game this state belongs to; reset on game change
  //   playId                - identifier of the third-out play being held
  //   until                 - POSIX seconds; persistent hold expires once now > until
  //   dueUpSeenForPlayId    - playId for which we've already revealed the due-up panel,
  //                           preventing the hold from re-engaging on later renders
  _getThirdOutHold() {
    if (!this._thirdOutHold) {
      this._thirdOutHold = { gameKey: "", playId: "", until: 0, dueUpSeenForPlayId: "" };
    }
    return this._thirdOutHold;
  }

  _resetThirdOutHold() {
    this._thirdOutHold = { gameKey: "", playId: "", until: 0, dueUpSeenForPlayId: "" };
  }

  _setupRefreshTimer() {
    this._clearRefreshTimer();
    const rate = Number(this.config.refresh_rate);
    if (rate > 0 && this._hass) {
      this._refreshInterval = setInterval(() => {
        // Force a re-render by triggering state refresh
        if (this._hass && this.config?.entity) {
          this.render();
        }
      }, rate * 1000);
    }
  }

  disconnectedCallback() {
    this._clearRefreshTimer();
    clearTimeout(this._renderTimer);
  }

  set hass(hass) {
    const firstLoad = !this._hass;
    this._hass = hass;
    if (!this.card) {
      this.card = document.createElement("ha-card");
      this.card.className = "mlb-live-game-card";
      this.content = document.createElement("div");
      this.content.className = "card-content";
      this.card.appendChild(this.content);
      this.appendChild(this.card);
    }
    this.render();
    // Setup refresh timer on first load
    if (firstLoad) {
      this._setupRefreshTimer();
    }
  }

  scheduleRender() {
    clearTimeout(this._renderTimer);
    this._renderTimer = setTimeout(() => this.render(), 0);
  }

  getCardSize() {
    return 4;
  }

  renderLinescore(competition) {
    const competitors = competition?.competitors || [];
    const away = competitors.find((c) => c?.homeAway === "away") || {};
    const home = competitors.find((c) => c?.homeAway === "home") || {};
    const awayLines = Array.isArray(away?.linescores) ? away.linescores : [];
    const homeLines = Array.isArray(home?.linescores) ? home.linescores : [];
    const innings = Math.max(awayLines.length, homeLines.length);
    if (!innings) return "";
    const headers = Array.from({ length: innings }, (_, i) => `<div class="inning-head">${i + 1}</div>`).join("");
    const awayCells = Array.from({ length: innings }, (_, i) => `<div class="inning-cell">${awayLines[i]?.displayValue ?? awayLines[i]?.value ?? ""}</div>`).join("");
    const homeCells = Array.from({ length: innings }, (_, i) => `<div class="inning-cell">${homeLines[i]?.displayValue ?? homeLines[i]?.value ?? ""}</div>`).join("");
    // Compute grid-template-columns inline: team-abbr | N inning cells | R total.
    // Using `repeat(auto-fit, minmax(X, max-content))` is invalid per CSS Grid
    // spec and causes browsers to collapse to a single column, which stacks
    // every cell vertically. Setting an explicit track count avoids that.
    const gridCols = `max-content repeat(${innings}, minmax(18px, 1fr)) max-content`;
    return `
      <div class="linescore">
        <div class="linescore-grid" style="grid-template-columns: ${gridCols};">
          <div></div>${headers}<div class="inning-head">R</div>
          <div class="team-abbr">${away?.team?.abbreviation || "A"}</div>${awayCells}<div class="inning-total">${parseScore(away?.score).text || "—"}</div>
          <div class="team-abbr">${home?.team?.abbreviation || "H"}</div>${homeCells}<div class="inning-total">${parseScore(home?.score).text || "—"}</div>
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
    const away = competitors.find(c => c?.homeAway === "away") || {};
    const home = competitors.find(c => c?.homeAway === "home") || {};
    const sit = attrs.situation || {};
    const bs = attrs.batter_stats || {};
    const ps = attrs.pitcher_stats || {};
    const plays = Array.isArray(attrs.recent_plays) ? attrs.recent_plays : [];
    const lastPlay = plays.length ? plays[plays.length - 1] : null;
    const hold = this._getThirdOutHold();
    return [
      stateObj?.state,
      attrs.mode,
      attrs.game_state,
      // Visible scoreboard inputs
      away.score, away.recordSummary,
      home.score, home.recordSummary,
      status.type?.state, status.type?.name, status.period, status.displayClock,
      // Matchup
      attrs.current_batter?.display_name,
      attrs.current_pitcher?.display_name,
      bs.avg, bs.hits_ab, bs.hr, bs.rbi, bs.game_outcomes_display,
      ps.era, ps.ip, ps.pitches_strikes, ps.strikeouts,
      // Count + bases
      sit.balls, sit.strikes, sit.outs,
      sit.onFirst, sit.onSecond, sit.onThird,
      // Recent plays — only the tail-most play affects rendering thresholds
      plays.length, lastPlay?.id, lastPlay?.outs, lastPlay?.away_score, lastPlay?.home_score,
      // Inning + third-out hold
      attrs.inning_context?.is_between_halves,
      hold.until > Date.now() / 1000 ? hold.playId : "",
    ].join("||");
  }

  render() {
    if (!this._hass || !this.config) return;
    const stateObj = this._hass.states[this.config.entity];
    if (!stateObj) {
      if (this._lastFingerprint !== "__NOT_FOUND__") {
        this._lastFingerprint = "__NOT_FOUND__";
        this.content.innerHTML = `<div class="empty">Entity not found: ${this.config.entity}</div>${this.styles()}`;
      }
      return;
    }

    const fingerprint = this._computeRenderFingerprint(stateObj);
    if (fingerprint === this._lastFingerprint) {
      return; // No changes, skip DOM update
    }
    this._lastFingerprint = fingerprint;

    const attrs = stateObj.attributes || {};
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
    const title = this.config.title || attrs.team_name || attrs.team_abbr || "MLB Live";
    const batter = currentBatterName(attrs);
    const pitcher = attrs.current_pitcher?.display_name || attrs.current_pitcher?.short_name || "";
    const batterStats = renderBatterLineSecondary(attrs.batter_stats);
    const batterHitsAb = attrs.batter_stats?.hits_ab || renderCompactStatLine(attrs.batter_stats, [["AB", "ab"], ["H", "h"]]);
    const batterOutcomes = (attrs.batter_stats?.game_outcomes_display || "").trim();
    const batterPrimaryLine = batterOutcomes ? `${batterHitsAb} • ${batterOutcomes}` : batterHitsAb;
    const pitcherPrimaryLine = renderPitcherLinePrimary(attrs.pitcher_stats);
    const pitcherSecondaryLine = renderPitcherLineSecondary(attrs.pitcher_stats);
    const awayRecord = this.config.show_records ? competitorRecord(away, awayMeta) : "";
    const homeRecord = this.config.show_records ? competitorRecord(home, homeMeta) : "";
    const awayWon = stateInfo.pillClass === "final" && awayScore.num != null && homeScore.num != null && awayScore.num > homeScore.num;
    const homeWon = stateInfo.pillClass === "final" && awayScore.num != null && homeScore.num != null && homeScore.num > awayScore.num;
    const marker = this.renderInningMarker(stateInfo, inningState);
    const awayTotals = teamTotals(away);
    const homeTotals = teamTotals(home);
    const countSummary = buildCountText(attrs.situation);
    const basesSummary = buildBasesText(attrs.situation);
    const probablePitchers = attrs.probable_pitchers || {};
    const leaders = attrs.leaders || {};
    const periodLower = String(inningState.lower || "");
    const inningContext = attrs.inning_context || {};
    const recentPlaysPanel = renderRecentPlays(attrs.recent_plays || [], attrs.current_pitches || [], attrs.situation || {}, this.config);
    const latestRecentPlay = Array.isArray(attrs.recent_plays) && attrs.recent_plays.length ? attrs.recent_plays[attrs.recent_plays.length - 1] : null;
    const explicitThirdOutPlay = attrs.third_out_play || null;
    const betweenHalves = (periodLower.startsWith("mid") || periodLower.startsWith("end") || inningContext.is_between_halves);
    const nowTs = Date.now() / 1000;
    const gameKey = String(attrs.event_id || competition?.id || `${awayTeam?.abbreviation || "A"}-${homeTeam?.abbreviation || "H"}`);
    const hold = this._getThirdOutHold();
    if (hold.gameKey && hold.gameKey !== gameKey) {
      this._resetThirdOutHold();
    }
    // Look for 3rd out play - first check recent (8s window), then check if betweenHalves with any 3rd out in recent_plays
    const recentFallbackThirdOut = (!explicitThirdOutPlay && latestRecentPlay && Number(latestRecentPlay?.outs) === 3 && String(latestRecentPlay?.text || "").trim() && Number.isFinite(Number(latestRecentPlay?.wallclock_ts)) && ((nowTs - Number(latestRecentPlay.wallclock_ts)) < THIRD_OUT_RECENT_WINDOW_SECONDS))
      ? latestRecentPlay
      : null;
    // Extended fallback: if betweenHalves and no hold active, look for ANY 3rd out in recent_plays (even older ones)
    const anyThirdOutPlay = (!explicitThirdOutPlay && !recentFallbackThirdOut && betweenHalves && !this._getThirdOutHold().playId && latestRecentPlay && Number(latestRecentPlay?.outs) === 3 && String(latestRecentPlay?.text || "").trim())
      ? latestRecentPlay
      : null;
    const candidateThirdOutPlay = explicitThirdOutPlay || recentFallbackThirdOut || anyThirdOutPlay || null;
    if (candidateThirdOutPlay) {
      const candidateId = String(candidateThirdOutPlay?.id || `${gameKey}-${Number(candidateThirdOutPlay?.wallclock_ts) || nowTs}`);
      const currentHoldId = String(this._getThirdOutHold().playId || "");
      if (candidateId && candidateId !== currentHoldId) {
        this._thirdOutHold = {
          gameKey: gameKey,
          playId: candidateId,
          until: nowTs + THIRD_OUT_HOLD_SECONDS,
          dueUpSeenForPlayId: "",
        };
      }
    }
    const holdState = this._getThirdOutHold();
    const holdPlayAlreadyReleased = !!holdState.playId && holdState.dueUpSeenForPlayId === holdState.playId;
    const persistentHoldActive = betweenHalves
      && !holdPlayAlreadyReleased
      && holdState.gameKey === gameKey
      && Number.isFinite(Number(holdState.until))
      && Number(holdState.until) > nowTs;
    const explicitThirdOutTs = Number(explicitThirdOutPlay?.wallclock_ts);
    const explicitHoldActive = !!explicitThirdOutPlay
      && !holdPlayAlreadyReleased
      && Number.isFinite(explicitThirdOutTs)
      && Number(explicitThirdOutPlay?.outs) === 3
      && (nowTs - explicitThirdOutTs) < THIRD_OUT_HOLD_SECONDS;
    const graceHoldActive = !!recentFallbackThirdOut && !holdPlayAlreadyReleased;
    const dueUpDesc = [String(inningContext.period_prefix || "").trim(), String(inningContext.display_period || attrs.competition?.status?.displayPeriod || "").trim()].filter(Boolean).join(" ").trim();
    const dueUpList = Array.isArray(attrs.due_up) ? attrs.due_up.filter(Boolean) : [];
    const dueUpReady = betweenHalves && dueUpList.length > 0;
    if (dueUpReady && holdState.gameKey === gameKey && holdState.playId && Number(holdState.until || 0) <= nowTs) {
      holdState.dueUpSeenForPlayId = holdState.playId;
    }
    const holdThirdOut = betweenHalves && (persistentHoldActive || explicitHoldActive || (!dueUpReady && graceHoldActive));
    if (betweenHalves && !holdThirdOut && holdState.gameKey === gameKey && holdState.playId && holdState.dueUpSeenForPlayId !== holdState.playId) {
      holdState.dueUpSeenForPlayId = holdState.playId;
    }
    const dueUpPanel = (betweenHalves && !holdThirdOut)
      ? renderDueUpCards(this, dueUpList, dueUpDesc)
      : "";
    const countDotsPanel = (this.config.show_count !== false && stateInfo.pillClass === "live" && !betweenHalves && !holdThirdOut) ? renderCountDotsRow(attrs.situation || {}, attrs.current_pitches || []) : "";
    // Special count dots showing 3 outs during the hold period
    const thirdOutCountDotsPanel = (this.config.show_count !== false && holdThirdOut) ? renderCountDotsRow({ balls: 0, strikes: 0, outs: 3 }, []) : "";
    const diamondHtml = this.config.show_diamond !== false ? renderBaseDiamond(attrs.situation || {}) : "";
    const matchupPanel = this.config.show_batter ? `
            <div class="matchup-block ${(batter || pitcher) ? "" : "muted-block"}">
              <div class="matchup-grid enhanced productionish ${this.config.show_diamond !== false ? "with-diamond" : ""}">
                <div class="matchup-side with-headshot stacked centered-half">
                  ${renderPlayerHeadshot(this, attrs.current_pitcher?.headshot || "", pitcher || "Pitcher")}
                  <div class="matchup-copy centered-copy">
                    <div class="matchup-value">${shortPersonName(pitcher || "TBD")}</div>
                    <div class="matchup-subtle strongish stat-line">${pitcherPrimaryLine || ""}</div>
                    <div class="matchup-subtle secondary stat-line">${pitcherSecondaryLine || ""}</div>
                  </div>
                </div>
                ${diamondHtml}
                <div class="matchup-side with-headshot stacked centered-half align-right">
                  ${renderPlayerHeadshot(this, attrs.current_batter?.headshot || "", batter || "Batter")}
                  <div class="matchup-copy centered-copy">
                    <div class="matchup-value">${shortPersonName(batter || "TBD")}</div>
                    <div class="matchup-subtle strongish stat-line">${batterPrimaryLine || "—"}</div>
                    <div class="matchup-subtle secondary stat-line">${batterStats || ""}</div>
                  </div>
                </div>
              </div>
            </div>` : "";
    const onDeckHtml = this.config.show_on_deck !== false ? renderOnDeckRow(attrs.on_deck || {}) : "";
    const baseOccupancyHtml = this.config.show_base_occupancy !== false ? renderBaseOccupancyRow(attrs.situation || {}) : "";
    const liveExtras = stateInfo.pillClass === "live"
      ? `
        <div class="live-panel productionish">
          ${holdThirdOut
            ? `${thirdOutCountDotsPanel}${matchupPanel}${recentPlaysPanel}`
            : (dueUpPanel || `${countDotsPanel}${matchupPanel}${onDeckHtml}${baseOccupancyHtml}${recentPlaysPanel}`)}
        </div>`
      : "";
    const delayedExtras = stateInfo.pillClass === "delayed"
      ? `<div class="state-panel delayed-panel"><span class="mini-state warning">DLY</span><span>Game delayed</span>${stateInfo.statusText ? `<span class="muted">${stateInfo.statusText}</span>` : ""}</div>`
      : "";
    const finalExtras = stateInfo.pillClass === "final" && marker === "F"
      ? `<div class="state-panel final-panel"><span class="mini-state">F</span><span>Final</span><span class="totals-inline">Away H/E ${awayTotals.hits}/${awayTotals.errors} • Home H/E ${homeTotals.hits}/${homeTotals.errors}</span></div>`
      : "";
    if (stateInfo.pillClass === "next" || stateInfo.pillClass === "final") {
      // Compute compact fingerprint to see if we need to update DOM
      const compactFp = [
        stateInfo.pillClass,
        awayTeam?.abbreviation || awayMeta?.abbreviation,
        homeTeam?.abbreviation || homeMeta?.abbreviation,
        competition?.date,
        awayScore.text,
        homeScore.text,
        awayRecord,
        homeRecord,
      ].join("|");
      if (compactFp !== this._lastCompactFp) {
        // Fingerprint changed, need to re-render
        this.content.innerHTML = this.renderCompactNonLive(stateInfo, competition, awayTeam, awayMeta, awayRecord, awayScore, homeTeam, homeMeta, homeRecord, homeScore);
      }
      // else: fingerprint unchanged, skip DOM update entirely
      return;
    }

    const liveHtml = `
      <div class="wrapper">
        <div class="scoreboard-main">
          <div class="scoreboard scoreboard-rich">
            ${this.teamRow(awayTeam, awayMeta, awayRecord, awayScore, awayWon, false, away, awayTotals)}
            ${this.teamRow(homeTeam, homeMeta, homeRecord, homeScore, homeWon, true, home, homeTotals)}
          </div>
          <div class="inning-marker-side">
            <div class="inning-marker-wrap"><div class="inning-marker ${stateInfo.pillClass}">${marker}</div></div>
          </div>
        </div>

        ${this.config.show_linescore ? this.renderLinescore(competition) : ""}
        ${delayedExtras}
        ${finalExtras}
        ${liveExtras}
      </div>
      ${this.styles()}
    `;
    // Only update DOM if HTML actually changed
    if (liveHtml !== this._lastLiveHtml) {
      this._lastLiveHtml = liveHtml;
      this.content.innerHTML = liveHtml;
    }
  }


  formatCompactDateTime(dateValue) {
    const d = dateValue ? new Date(dateValue) : null;
    if (!d || Number.isNaN(d.getTime())) return { date: "", time: "", isToday: false };
    const now = new Date();
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const startOfTarget = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    const isToday = startOfTarget.getTime() === startOfToday.getTime();
    const dayDiff = Math.round((startOfTarget - startOfToday) / 86400000);
    const DAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const dateText = (dayDiff > 0 && dayDiff <= 7)
      ? DAY_ABBR[d.getDay()]
      : `${d.getMonth() + 1}/${d.getDate()}`;
    return {
      date: dateText,
      time: d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }),
      isToday
    };
  }

  renderCompactNonLive(stateInfo, competition, awayTeam, awayMeta, awayRecord, awayScore, homeTeam, homeMeta, homeRecord, homeScore) {
    // Compute a compact-specific fingerprint to avoid unnecessary DOM updates
    const compactFp = [
      stateInfo.pillClass,
      awayTeam?.abbreviation || awayMeta?.abbreviation,
      homeTeam?.abbreviation || homeMeta?.abbreviation,
      competition?.date,
      awayScore.text,
      homeScore.text,
      awayRecord,
      homeRecord,
    ].join("|");
    if (compactFp === this._lastCompactFp) {
      return this._lastCompactHtml; // Return cached HTML, don't recreate DOM
    }
    this._lastCompactFp = compactFp;

    const when = this.formatCompactDateTime(competition?.date);
    const awayLogo = requestCachedLogo(this, awayTeam?.logo || get(awayTeam, ["logos", 0, "href"], "") || awayMeta?.logo || "");
    const homeLogo = requestCachedLogo(this, homeTeam?.logo || get(homeTeam, ["logos", 0, "href"], "") || homeMeta?.logo || "");
    const awayName = awayTeam?.name || awayTeam?.displayName || awayTeam?.shortDisplayName || awayMeta?.name || awayMeta?.short_name || awayTeam?.abbreviation || "—";
    const homeName = homeTeam?.name || homeTeam?.displayName || homeTeam?.shortDisplayName || homeMeta?.name || homeMeta?.short_name || homeTeam?.abbreviation || "—";
    const isFinal = stateInfo.pillClass === "final";
    const awayWon = isFinal && awayScore.num != null && homeScore.num != null && awayScore.num > homeScore.num;
    const homeWon = isFinal && awayScore.num != null && homeScore.num != null && homeScore.num > awayScore.num;
    const finalMarker = `<div class="compact-final-marker"><div class="compact-pill compact-pill-final">F</div></div>`;
    const nextRight = when.isToday
      ? `<div class="compact-next-wrap today-only">
          <div class="compact-time">${when.time || ""}</div>
        </div>`
      : `<div class="compact-next-wrap">
          <div class="compact-date">${when.date || ""}</div>
          <div class="compact-time">${when.time || ""}</div>
        </div>`;
    const rightHtml = isFinal ? finalMarker : nextRight;
    const html = `
      <div class="wrapper compact-mode">
        <div class="scoreboard-main">
          <div class="scoreboard scoreboard-rich">
            <div class="team-row away ${awayWon ? "winner" : ""}">
              <div class="team-left">
                ${awayLogo ? `<img class="logo" src="${awayLogo}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : `<div class="logo placeholder"></div>`}
                <div class="meta">
                  <div class="name">${awayName}${awayRecord ? ` <span class="record-inline">(${awayRecord})</span>` : ""}</div>
                </div>
              </div>
              ${isFinal ? `<div class="team-right compact-final-score-right"><div class="score final-score">${awayScore.text || "—"}</div></div>` : ""}
            </div>
            <div class="team-row home ${homeWon ? "winner" : ""}">
              <div class="team-left">
                ${homeLogo ? `<img class="logo" src="${homeLogo}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : `<div class="logo placeholder"></div>`}
                <div class="meta">
                  <div class="name">${homeName}${homeRecord ? ` <span class="record-inline">(${homeRecord})</span>` : ""}</div>
                </div>
              </div>
              ${isFinal ? `<div class="team-right compact-final-score-right"><div class="score final-score">${homeScore.text || "—"}</div></div>` : ""}
            </div>
          </div>
          <div class="inning-marker-side">
            <div class="inning-marker-wrap">${rightHtml}</div>
          </div>
        </div>
      </div>
      ${this.styles()}`;
    this._lastCompactHtml = html;
    return html;
  }

  renderRheHeader() {
    return `
      <div class="rhe-header" aria-hidden="true">
        <div class="rhe-spacer"></div>
        <div class="rhe-cols"><span class="rhe-col score">R</span><span class="rhe-col">H</span><span class="rhe-col">E</span></div>
      </div>
    `;
  }

  renderInningMarker(stateInfo, inningState) {
    if (stateInfo.pillClass === "delayed") return `<div class="marker-text">DLY</div>`;
    if (stateInfo.pillClass === "final" || inningState.pseudoFinal) return `<div class="marker-text">F</div>`;
    if (stateInfo.pillClass !== "live") return "";
    const period = inningState.period || "";
    if (inningState.isTop) return `<div class="inning-stack up"><div class="arrow">▲</div><div class="num">${period}</div></div>`;
    if (inningState.isBottom) return `<div class="inning-stack down"><div class="num">${period}</div><div class="arrow">▼</div></div>`;
    if (inningState.isMid) return `<div class="inning-stack mid"><div class="num">${period}</div></div>`;
    return `<div class="marker-text">LIVE</div>`;
  }

  teamRow(team, teamMeta, record, score, winner = false, isHome = false, competitor = {}, totals = { hits: "—", errors: "—" }) {
    const logoRaw = team?.logo || get(team, ["logos", 0, "href"], "") || teamMeta?.logo || "";
    const logo = requestCachedLogo(this, logoRaw);
    const displayName = team?.name || team?.displayName || team?.shortDisplayName || teamMeta?.name || teamMeta?.short_name || team?.abbreviation || "—";
    return `
      <div class="team-row ${winner ? "winner" : ""} ${isHome ? "home" : "away"}">
        <div class="team-left">
          ${logo ? `<img class="logo" src="${logo}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer">` : `<div class="logo placeholder"></div>`}
          <div class="meta">
            <div class="name">${displayName}${record ? ` <span class="record-inline">(${record})</span>` : ""}</div>
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

  styles() {
    return `
      <style>
        .wrapper {
          padding: 0 1px 0;
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
          width: 46px;
          height: 46px;
          object-fit: cover;
          border-radius: 50%;
          flex: 0 0 42px;
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
        .inning-marker.delayed { color: var(--warning-color); }
        .inning-marker.final { color: var(--primary-text-color); }
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
          padding: 6px 10% 0;
        }
        .matchup-grid {
          display:grid;
          grid-template-columns: minmax(0,1fr) minmax(0,1fr);
          gap: 24px;
          align-items:start;
        }

        .matchup-grid.enhanced {
          grid-template-columns: minmax(0,1fr) 56px minmax(0,1fr);
          column-gap: 18px;
          row-gap: 0;
          align-items:start;
        }
        .matchup-center {
          display:flex;
          align-items:center;
          justify-content:center;
        }
        .diamond-center {
          align-self:center;
          min-height: 70px;
        }
        .diamond-graphic {
          position: relative;
          width: 52px;
          height: 52px;
          display:flex;
          align-items:center;
          justify-content:center;
        }
        .diamond-field {
          position:absolute;
          inset: 7px;
          border: 2px solid rgba(255,255,255,0.56);
          border-radius: 2px;
          transform: rotate(45deg);
          box-sizing:border-box;
          background: rgba(255,255,255,0.03);
        }
        .diamond-base {
          position:absolute;
          width: 10px;
          height: 10px;
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
        .diamond-base.home { bottom: 1px; left: 50%; margin-left: -5px; }
        .diamond-base.first { top: 50%; right: 1px; margin-top: -5px; }
        .diamond-base.second { top: 1px; left: 50%; margin-left: -5px; }
        .diamond-base.third { top: 50%; left: 1px; margin-top: -5px; }
        .diamond-mound {
          position:absolute;
          width: 6px;
          height: 6px;
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
          color: var(--primary-text-color);
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
          color: rgba(255,255,255,0.38); color: var(--secondary-text-color); }
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
        }
        .pitch-row .play-text {
          text-align: right;
          justify-self: stretch;
          color: var(--secondary-text-color);
          opacity: 0.88;
          width: 100%;
          margin-left: 0;
          padding-left: 0;
line-height: 1.18;
        }
        .pitch-row .play-indicator { display: none; }
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
        }
        .dueup-stat {
color: var(--primary-text-color);
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
          align-items:flex-end;
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
        .compact-pill-final {
          display:flex;
          align-items:center;
          align-self:center;
          min-height: 34px;
        }
        .compact-final-marker {
          display:flex;
          align-items:center;
          justify-content:center;
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

      </style>
    `;
  }
}

if (!customElements.get(CARD_TAG)) {
  customElements.define(CARD_TAG, MlbLiveGameCard);
} else {
  console.info(`[mlb-live-game-card] ${CARD_VERSION} already registered`);
}
