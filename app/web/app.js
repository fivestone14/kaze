// Fetches /latest.json and /bars.json every 30 s and draws the three views from them; nothing on screen comes from
// anywhere else. One fetch, one render per block (each in its own guard), one number formatter, one clock.
"use strict";

const CHANNELS = { "5_minutes": 5, "30_minutes": 30, "60_minutes": 60 };
const FRESH_SECONDS = 6 * 60;
const CLOSE_GRACE_SECONDS = 60;
const FIRST_READ_SECONDS = 5 * 60;
const POLL_MS = 30000;
const POLL_BACKOFF_MAX_MS = 5 * 60000;
const FETCH_TIMEOUT_MS = 10000;
const LIST_ROWS_SHOWN = 12;
const LEAN_DEAD_ZONE = 0.01;
const CHART_PAD_SHARE = 0.06;
const CONE_STEPS = 4;
const FONT = "Plus Jakarta Sans, sans-serif";
const COLOUR = { ink: "#161A17", blue: "#2F44B8", rust: "#A8492F", green: "#2e9e5b", greenDeep: "#1f6b3e",
                 faint: "#5C6459", sand: "#B9B2A4", band: "#D5D0C6", rule: "#E4E0D8", cream: "#F4F1EB",
                 white: "#FFFFFF", grey: "#8a8a8a", track: "#C9D1F4" };
const CHART = { width: 336, left: 4, right: 330, top: 14, bottom: 198, labelY: 213, liveShare: 0.25 };
const STRIP = { width: 312, centre: 156, reach: 110.9, edge: 6 };
const RANGE_BAR = { width: 312, centre: 156, reach: 136.7 };
const SPEED = { width: 240, barWidth: 46, step: 84, left: 12, baseline: 50, top: 2 };
const METER = { width: 86, small: 50 };
const ROW = { width: 120, trackLeft: 20, trackRight: 100, centre: 60, reach: 40 };
const PUSH_LABELS = { price_trend: "Price Trend", straightness: "Straightness", breadth: "Breadth",
                      big_tech_vs_rest: "Big Tech vs Rest", options_buyers: "Options Buyers",
                      yield_change: "10-Year Yield", mirai_claude: "Claude" };
const FEED_LABELS = { spx_bars: "SPX Bars", watchlist_quotes: "Quotes", nyse_breadth: "Breadth",
                      options_scan: "Options Scan", options_tape: "Options Tape", futures_bars: "Futures",
                      event_calendar: "Calendar", news_headlines: "News", claude_forecast: "Claude",
                      schwab_login: "Schwab Login" };
const DIAL_LABELS = { fed_window: "Fed", release_window: "News", closing_window: "Close",
                      acceleration: "Fast vs Slow", options_vs_tape: "Options vs Tape" };
const REPLY_WORDS = { 401: "Sign-In Refused (401) · Check The Token",
                      404: "No Live Feed Yet · The Engine Has Not Written One",
                      503: "The Mac's Server Can't Read The Store (503)" };
const state = { feed: null, bars: [], channel: "30_minutes", fetchedAt: null, unreachable: false, unreadable: false,
                feedReply: null, barsReply: null, inFlight: false, failures: 0, pollTimer: null, showAll: false };

// ---- the one number formatter, the one clock formatter, small text helpers ------------------------------------------

function fmt(value, decimals = 0, signed = false) {
  const number = value === null || value === undefined || value === "" ? NaN : Number(value);
  if (Number.isNaN(number)) return "—";
  const text = Math.abs(number).toLocaleString("en-US", { minimumFractionDigits: decimals,
                                                           maximumFractionDigits: decimals });
  if (number < 0 && Number(text.replace(/,/g, "")) !== 0) return "−" + text;
  return signed ? "+" + text : text;
}

function clock(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}

/** "Fri 10-16" for a moment or a bare date; a bare date is read as a local day, never as UTC midnight. */
function dayWord(iso) {
  if (!iso) return "—";
  const when = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T00:00:00` : iso);
  return `${when.toLocaleDateString([], { weekday: "short" })} ${String(when.getMonth() + 1).padStart(2, "0")}`
       + `-${String(when.getDate()).padStart(2, "0")}`;
}

function ago(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)}m`;
  if (seconds < 36 * 3600) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function titleCase(text) {
  return String(text).replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function esc(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
                                                     "'": "&#39;" }[c]));
}

function plusMinus(value, decimals = 1) {
  return value === null || value === undefined ? "—" : `±${fmt(value, decimals)}`;
}

function el(id) {
  return document.getElementById(id);
}

function note(id, text) {
  const node = el(id);
  node.hidden = !text;
  node.textContent = text || "";
}

function svgText(x, y, text, attrs = "") {
  return `<text x="${x}" y="${y}" font-family="${FONT}" ${attrs}>${text}</text>`;
}

function svgRect(x, y, width, height, rx, fill, opacity = 1, extra = "") {
  return `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${rx}" fill="${fill}" `
       + `fill-opacity="${opacity}" ${extra}/>`;
}

function svgLine(x1, y1, x2, y2, stroke, width, extra = "") {
  return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="${stroke}" stroke-width="${width}" ${extra}/>`;
}

function svgPolyline(points, stroke, width, extra = "") {
  return `<polyline points="${points.join(" ")}" fill="none" stroke="${stroke}" stroke-width="${width}" ${extra}/>`;
}

function clamp(value, low, high) {
  return Math.min(high, Math.max(low, value));
}

function remembered(key, fallback) {
  try {
    return sessionStorage.getItem(key) || fallback;
  } catch (failure) {
    return fallback;
  }
}

function remember(key, value) {
  try {
    sessionStorage.setItem(key, value);
  } catch (failure) {
    // a private window or blocked storage: the choice lives for this page only
  }
}

// ---- what the feed holds, read one way -----------------------------------------------------------------------------

function range() {
  return state.feed && state.feed.range ? state.feed.range : null;
}

function channel() {
  const block = range();
  return block && block.channels ? block.channels[state.channel] : null;
}

function windows() {
  const block = range();
  return block && block.windows && block.windows.status !== "failed" ? block.windows : null;
}

function windowsFailure() {
  const block = range();
  return block && block.windows && block.windows.status === "failed" ? block.windows.reason : null;
}

function liveWindow() {
  const block = windows();
  return block ? block.current[state.channel] : null;
}

function halfWidth(bands, name) {
  return bands && bands[name] ? bands[name].half_width : null;
}

function bandPortion() {
  const block = range();
  return block && block.band_portion !== undefined ? block.band_portion : null;
}

function reasons() {
  const text = state.feed && state.feed.status_reason;
  return text ? text.split(",").filter(Boolean) : [];
}

function valueOf(key) {
  const values = state.feed && state.feed.values;
  return values && values[key] !== undefined ? values[key] : null;
}

function feedAgeSeconds() {
  const moment = state.feed && (state.feed.cut_at || state.feed.failed_at);
  return moment ? (Date.now() - new Date(moment).getTime()) / 1000 : null;
}

/** The feed's standing: live within six minutes of its read, late after that while the session is open, closed once
 *  the session is over, and no_read_today once the next session has had time for its first read with no newer feed. */
function feedState() {
  const feed = state.feed;
  if (!feed) return "none";
  if (state.unreachable) return "unreachable";
  if (state.unreadable) return "unreadable";
  if (feed.status === "failed") return "failed";
  const session = feed.session || {};
  const closesAt = session.closes_at ? new Date(session.closes_at).getTime() : null;
  const nextOpen = session.next_session_opens_at ? new Date(session.next_session_opens_at).getTime() : null;
  if (closesAt !== null && Date.now() > closesAt + CLOSE_GRACE_SECONDS * 1000) {
    const firstReadDue = nextOpen === null ? null : nextOpen + (FIRST_READ_SECONDS + FRESH_SECONDS) * 1000;
    return firstReadDue !== null && Date.now() > firstReadDue ? "no_read_today" : "closed";
  }
  return feedAgeSeconds() > FRESH_SECONDS ? "late" : "live";
}

function isLive() {
  return feedState() === "live";
}

/** The read a channel's screens explain: the live window's own read (its chain travels with it), else the newest
 *  read's channel block (the same read when the newest read is the headline one). One shape for Sight and Steps. */
function explainedRead() {
  const current = liveWindow();
  if (current && current.chain) {
    return { ...current.chain, read_at: current.read_at, price: current.price, bands: current.bands,
             ends_at: current.ends_at, clipped: current.clipped, status: current.status, reason: current.reason,
             rung_points: current.rung_points };
  }
  const block = range(), ch = channel();
  if (!ch) return null;
  return { read_at: block.read_at, price: block.price, bands: ch.bands, ends_at: ch.ends_at, clipped: ch.clipped,
           status: ch.status, reason: ch.reason, rung_points: ch.rung_points, speed: block.speed,
           speed_fast: block.speed_fast, speed_slow: block.speed_slow, swing: ch.swing, stretch: ch.stretch,
           average_stretch: ch.average_stretch, fit_sessions: ch.fit_sessions, fit_windows: ch.fit_windows,
           stays_inside_share: ch.stays_inside_share, dials: ch.dials, dials_product: ch.dials_product,
           straddle: block.straddle, minutes_left: block.minutes_left, options_swing: ch.options_swing,
           options_check: ch.options_check, chance_up_if_moves: ch.direction ? ch.direction.chance_up_if_moves : null,
           trust_applied: ch.direction ? ch.direction.trust_applied : null };
}

function emptyReason() {
  const feed = state.feed;
  if (!feed) {
    if (state.unreachable) return "Can't Reach The Mac";
    return state.unreadable ? "Feed Unreadable" : REPLY_WORDS[state.feedReply] || "No Live Feed Yet";
  }
  if (feed.status === "failed") return `Read Failed · ${feed.status_reason}`;
  if (!range()) return "Range Step Failed On This Read";
  const read = explainedRead();
  if (read && read.status === "blind") return `${titleCase(read.reason || "blind")} On This Read`;
  if (windowsFailure()) return `Grading Failed · ${windowsFailure()}`;
  return "No Read Yet Today For This Window";
}

function newestReadDiffers(read) {
  const block = range();
  return read && block && block.read_at !== read.read_at;
}

// ---- the fetch, the poll and the render ----------------------------------------------------------------------------

function fetchWithTimeout(path) {
  const control = new AbortController();
  const timer = setTimeout(() => control.abort(), FETCH_TIMEOUT_MS);
  return fetch(location.origin + path, { cache: "no-store", signal: control.signal })
    .finally(() => clearTimeout(timer));
}

async function loadFeed() {
  if (state.inFlight) return;
  state.inFlight = true;
  try {
    const [feedReply, barsReply] = await Promise.all([fetchWithTimeout("/latest.json"),
                                                      fetchWithTimeout("/bars.json")]);
    state.feedReply = feedReply.status;
    state.barsReply = barsReply.status;
    const feed = feedReply.ok ? await parsed(feedReply) : null;
    state.unreadable = feedReply.ok && feed === undefined;
    state.feed = state.unreadable ? state.feed : feed;
    const bars = barsReply.ok ? await parsed(barsReply) : null;
    state.bars = bars ? bars.bars : [];
    state.unreachable = false;
    state.failures = 0;
    state.fetchedAt = Date.now();
  } catch (failure) {
    state.unreachable = true;
    state.failures += 1;
  }
  state.inFlight = false;
  render();
  schedulePoll();
}

async function parsed(reply) {
  try {
    return await reply.json();
  } catch (failure) {
    return undefined;
  }
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  const wait = state.unreachable ? Math.min(POLL_MS * 2 ** (state.failures - 1), POLL_BACKOFF_MAX_MS) : POLL_MS;
  state.pollTimer = setTimeout(loadFeed, wait);
}

/** One block draws inside its own guard: a bad field empties that block with its reason, never the page. */
function guarded(draw, noteId) {
  try {
    draw();
  } catch (failure) {
    console.error(noteId, failure);
    note(noteId, `This Block Failed To Draw · ${failure.message}`);
  }
}

function render() {
  guarded(renderClock, "clock-note");
  guarded(renderNotice, "notice");
  guarded(renderSwitches, "clock-note");
  guarded(renderRead, "chart-note");
  guarded(renderStrip, "strip-note");
  guarded(renderReadsList, "list-note");
  guarded(renderScores, "list-note");
  guarded(renderSight, "sight-note");
  guarded(renderPush, "push-note");
  guarded(renderFeeds, "feeds-note");
  guarded(renderSteps, "steps-note");
  guarded(renderAnswer, "steps-note");
}

function renderClock() {
  const feed = state.feed;
  const kind = feedState();
  const cut = feed && feed.cut_at;
  const last = clock(feed && (feed.cut_at || feed.failed_at));
  const words = { none: state.unreadable ? "Feed Unreadable" : "No Feed",
                  failed: `Failed · ${clock(feed && feed.failed_at)}`,
                  unreachable: `Can't Reach · Last Read ${last}`, unreadable: `Feed Unreadable · Last Read ${last}`,
                  closed: `Closed · Last Read ${clock(cut)}`,
                  no_read_today: `No Read Yet Today · Last Read ${dayWord(cut)} ${clock(cut)}`,
                  late: `Late · Last Read ${clock(cut)} · ${ago(feedAgeSeconds())} Ago`,
                  live: `Live · ${clock(cut)}` };
  document.querySelectorAll(".clock-text").forEach((node) => { node.textContent = words[kind]; });
  document.querySelectorAll(".clock-dot").forEach((node) => { node.className = `clock-dot ${kind}`; });
  const block = range();
  const priorClose = valueOf("prior_close_points");
  const change = block && priorClose ? (block.price - priorClose) / priorClose * 100 : null;
  el("price").textContent = block ? fmt(block.price, 2) : "—";
  el("change").textContent = change === null ? "—" : `${fmt(change, 2, true)}%`;
  el("change").className = "change " + (change === null ? "faint" : change < 0 ? "down" : "up");
}

function renderNotice() {
  const feed = state.feed;
  const lines = [];
  if (state.unreachable) {
    lines.push(`Can't Reach The Mac${state.fetchedAt ? " · Last Fetched " + clock(state.fetchedAt) : ""}`);
  } else if (state.unreadable) {
    lines.push(`Feed Unreadable${feed ? " · Showing The Last Good Read" : ""}`);
  } else if (!feed && state.feedReply) {
    lines.push(REPLY_WORDS[state.feedReply] || `Server Error (${state.feedReply})`);
  }
  if (feed && feed.status === "failed") {
    lines.push(`Read Failed At ${clock(feed.failed_at)} · ${feed.status_reason}`);
  } else {
    reasons().forEach((reason) => lines.push(`${titleCase(reason)} On This Read`));
  }
  if (windowsFailure()) lines.push(`Grading: ${windowsFailure()}`);
  note("notice", lines.join(" · "));
}

function renderSwitches() {
  document.querySelectorAll("[data-channel]").forEach((button) => {
    button.setAttribute("aria-selected", button.dataset.channel === state.channel ? "true" : "false");
  });
}

// ---- Read: the range line, the chart, the strip, the list, the scores ---------------------------------------------

function renderRead() {
  const current = liveWindow();
  const minutes = CHANNELS[state.channel];
  const band = current && current.bands["80"];
  el("range-line").textContent = band && band.half_width !== null ? `${fmt(band.low)} – ${fmt(band.high)}` : "—";
  const standing = isLive() ? "" : current && current.ended ? " · Ended" : " · Last";
  el("held").textContent = current ? `${fmt(current.minutes_held)} / ${minutes} Min${standing}`
                                   : `— / ${minutes} Min`;
  renderChart(current, windows() ? windows().finished[state.channel] : [], minutes);
}

function renderChart(current, finished, minutes) {
  const chart = el("chart");
  const anchorEnd = current ? current.ends_at : finished[0] ? finished[0].ends_at
                  : state.bars.length ? state.bars[state.bars.length - 1].at : null;
  const notes = anchorEnd && current ? [] : [emptyReason()];
  if (!state.bars.length && state.barsReply && state.barsReply !== 200) {
    notes.push(`Price Line Unavailable (${fmt(state.barsReply)})`);
  }
  note("chart-note", notes.join(" · "));
  chart.innerHTML = "";
  chart.hidden = !anchorEnd;
  if (!anchorEnd) return;
  const span = CHART.right - CHART.left;
  const pxPerMinute = CHART.liveShare * span / minutes;
  const endMs = new Date(anchorEnd).getTime();
  const startMs = endMs - span / pxPerMinute * 60000;
  const ms = (iso) => new Date(iso).getTime();
  const x = (iso) => CHART.left + (ms(iso) - startMs) / 60000 * pxPerMinute;
  const boxes = finished.filter((window) => ms(window.window_from) >= startMs && window.half_width !== null);
  const bars = state.bars.filter((bar) => ms(bar.at) >= startMs && ms(bar.at) <= endMs);
  const liveHalf = current ? halfWidth(current.bands, "80") : null;
  const prices = [];
  boxes.forEach((window) => prices.push(window.price + window.half_width, window.price - window.half_width,
                                        window.high, window.low));
  bars.forEach((bar) => prices.push(bar.close));
  if (liveHalf !== null) prices.push(current.price + liveHalf, current.price - liveHalf);
  const known = prices.filter((p) => p !== null && p !== undefined);
  if (!known.length) return;
  const pad = (Math.max(...known) - Math.min(...known)) * CHART_PAD_SHARE || 1;
  const high = Math.max(...known) + pad, low = Math.min(...known) - pad;
  const y = (price) => CHART.top + (high - price) / (high - low) * (CHART.bottom - CHART.top);
  const parts = boxes.map((window) => readBox(window, x, y));
  if (liveHalf !== null) parts.push(liveCone(current, x, y));
  parts.push(priceLines(current, bars, x, y), axisLabels(current, boxes, bars, x));
  chart.innerHTML = parts.join("");
}

function readBox(window, x, y) {
  const x0 = x(window.window_from), x1 = x(window.ends_at);
  const y0 = y(window.price + window.half_width), y1 = y(window.price - window.half_width), yRead = y(window.price);
  const inside = window.grades.ends_inside;
  const colour = inside === null ? COLOUR.grey : inside ? COLOUR.green : COLOUR.rust;
  const gaugeX = x1 - 10;
  const parts = [svgRect(x0 + 1, y0, x1 - x0 - 2, y1 - y0, 6, COLOUR.ink, 0.045),
                 svgRect(gaugeX, y0 + 20, 5, Math.max(0, y1 - y0 - 26), 2.5, COLOUR.ink, 0.08)];
  if (window.end !== null) {
    const yEnd = y(window.end);
    const usedTop = Math.min(yRead, yEnd), usedBottom = Math.max(yRead, yEnd);
    parts.push(svgRect(gaugeX, usedTop, 5, usedBottom - usedTop, 2.5, colour, 0.75),
               svgLine(gaugeX - 3, yEnd, gaugeX + 8, yEnd, COLOUR.ink, 1.6));
  }
  const cx = gaugeX + 2.5, cy = y0 + 8;
  parts.push(`<circle cx="${cx}" cy="${cy}" r="6" fill="${colour}"/>`);
  if (inside === true) {
    parts.push(`<path d="M${cx - 3.5} ${cy}l2.5 2.5 4-4.5" fill="none" stroke="${COLOUR.white}" stroke-width="1.8" `
               + `stroke-linecap="round" stroke-linejoin="round"/>`);
  } else if (inside === false) {
    parts.push(`<path d="M${cx - 3} ${cy - 3}l6 6M${cx + 3} ${cy - 3}l-6 6" stroke="${COLOUR.white}" `
               + `stroke-width="1.8" stroke-linecap="round"/>`);
  }
  return parts.join("");
}

function liveCone(current, x, y) {
  const xr = x(current.read_at), xe = x(current.ends_at);
  const cone = (reachAtEnd, opacity) => {
    const upper = [], lower = [];
    for (let step = 0; step <= CONE_STEPS; step += 1) {
      const t = step / CONE_STEPS, px = xr + t * (xe - xr), reach = reachAtEnd * Math.sqrt(t);
      upper.push(`${px.toFixed(1)},${y(current.price + reach).toFixed(1)}`);
      lower.unshift(`${px.toFixed(1)},${y(current.price - reach).toFixed(1)}`);
    }
    return `<polygon points="${upper.concat(lower).join(" ")}" fill="${COLOUR.blue}" fill-opacity="${opacity}"/>`;
  };
  const endHalf = halfWidth(current.bands, "80"), averageHalf = halfWidth(current.bands, "average_80");
  const label = `font-size="10" text-anchor="end" fill="${COLOUR.blue}"`;
  return cone(endHalf, 0.12) + (averageHalf !== null ? cone(averageHalf, 0.2) : "")
       + svgText(xe, y(current.price + endHalf) - 4, fmt(current.price + endHalf), label)
       + svgText(xe, y(current.price - endHalf) + 11, fmt(current.price - endHalf), label);
}

function priceLines(current, bars, x, y) {
  if (!bars.length) return "";
  const fromMs = current ? new Date(current.window_from).getTime() : Infinity;
  const before = bars.filter((bar) => new Date(bar.at).getTime() < fromMs);
  const after = bars.filter((bar) => new Date(bar.at).getTime() >= fromMs);
  const point = (bar) => `${x(bar.at).toFixed(1)},${y(bar.close).toFixed(1)}`;
  const round = `stroke-linejoin="round" stroke-linecap="round"`;
  const parts = [];
  if (before.length) parts.push(svgPolyline(before.map(point), COLOUR.ink, 2, round));
  const last = bars[bars.length - 1];
  const lastX = x(last.at), lastY = y(last.close);
  if (current && after.length) {
    const start = `${x(current.read_at).toFixed(1)},${y(current.price).toFixed(1)}`;
    const averages = [];
    let sum = 0;
    after.forEach((bar, index) => {
      sum += bar.close;
      averages.push(`${x(bar.at).toFixed(1)},${y(sum / (index + 1)).toFixed(1)}`);
    });
    parts.push(svgPolyline([start].concat(after.map(point)), COLOUR.blue, 2.6, round),
               svgPolyline([start].concat(averages), COLOUR.ink, 1.8, `stroke-dasharray="4 2.5"`),
               `<circle cx="${lastX}" cy="${lastY}" r="9" fill="${COLOUR.blue}" fill-opacity="0.15"/>`,
               `<circle cx="${lastX}" cy="${lastY}" r="5" fill="${COLOUR.blue}" stroke="${COLOUR.cream}" `
               + `stroke-width="2"/>`,
               `<circle cx="${lastX}" cy="${y(sum / after.length)}" r="4.5" fill="${COLOUR.cream}" `
               + `stroke="${COLOUR.ink}" stroke-width="2"/>`);
  } else {
    parts.push(`<circle cx="${lastX}" cy="${lastY}" r="5" fill="${COLOUR.blue}" stroke="${COLOUR.cream}" `
               + `stroke-width="2"/>`);
  }
  return parts.join("");
}

function axisLabels(current, boxes, bars, x) {
  const parts = boxes.map((window) => svgText((x(window.window_from) + x(window.ends_at)) / 2, CHART.labelY,
                                              clock(window.read_at),
                                              `font-size="10" text-anchor="middle" fill="${COLOUR.faint}"`));
  const last = bars[bars.length - 1];
  const nowX = clamp(last ? x(last.at) : current ? x(current.read_at) : CHART.width - 12, 12, CHART.width - 12);
  parts.push(svgText(nowX, CHART.labelY, isLive() ? "Now" : "Last",
                     `font-size="10" text-anchor="middle" fill="${COLOUR.ink}" font-weight="700"`));
  return parts.join("");
}

function renderStrip() {
  const current = liveWindow();
  const strip = el("strip");
  const verdict = el("strip-verdict");
  const endHalf = current ? halfWidth(current.bands, "80") : null;
  note("strip-note", endHalf ? "" : emptyReason());
  strip.hidden = !endHalf;
  if (!endHalf) {
    strip.innerHTML = "";
    verdict.textContent = "—";
    verdict.className = "faint";
    return;
  }
  const averageHalf = halfWidth(current.bands, "average_80");
  const scale = STRIP.reach / endHalf;
  const edgeLeft = STRIP.centre - STRIP.reach, edgeRight = STRIP.centre + STRIP.reach;
  const at = (move) => clamp(STRIP.centre + move * scale, STRIP.edge, STRIP.width - STRIP.edge);
  const move = current.now.move, averageMove = current.now.average_move;
  const inside = current.average_inside;
  verdict.textContent = inside === null ? "—" : inside ? "Inside ✓" : "Outside ✗";
  verdict.className = inside === null ? "faint" : inside ? "inside" : "outside";
  const liveX = at(move || 0), averageX = averageMove === null ? null : at(averageMove);
  const liveLeft = averageX === null || liveX <= averageX;
  const small = `font-size="10" text-anchor="middle"`;
  const bold = `font-size="10" font-weight="700"`;
  const dashed = `stroke-dasharray="3 2"`;
  const parts = [
    svgText(liveX + (liveLeft ? 3 : -3), 10, `${isLive() ? "Live" : "Last"} ${fmt(move, 1, true)}`,
            `${bold} text-anchor="${liveLeft ? "end" : "start"}" fill="${COLOUR.blue}"`),
    svgLine(0, 32, STRIP.width, 32, COLOUR.sand, 1),
  ];
  if (averageHalf !== null) {
    const bandLeft = STRIP.centre - averageHalf * scale, bandRight = STRIP.centre + averageHalf * scale;
    parts.push(svgRect(bandLeft, 21, bandRight - bandLeft, 22, 5, COLOUR.band),
               svgText(bandLeft, 60, fmt(-averageHalf, 1), `${small} fill="${COLOUR.faint}"`),
               svgText(bandRight, 60, fmt(averageHalf, 1, true), `${small} fill="${COLOUR.faint}"`));
  }
  parts.push(svgLine(edgeLeft, 20, edgeLeft, 44, COLOUR.rust, 1.5, dashed),
             svgLine(edgeRight, 20, edgeRight, 44, COLOUR.rust, 1.5, dashed),
             svgLine(STRIP.centre, 17, STRIP.centre, 47, COLOUR.ink, 2.5),
             svgLine(liveX, 15, liveX, 49, COLOUR.blue, 3));
  if (averageX !== null) {
    parts.push(svgText(averageX + (liveLeft ? -4 : 4), 10, `Average ${fmt(averageMove, 1, true)}`,
                       `${bold} text-anchor="${liveLeft ? "start" : "end"}" fill="${COLOUR.ink}"`),
               `<circle cx="${averageX}" cy="32" r="6" fill="${COLOUR.cream}" stroke="${COLOUR.ink}" `
               + `stroke-width="2" ${dashed}/>`);
  }
  parts.push(svgText(STRIP.centre, 60, `Read ${fmt(current.price, 1)}`,
                     `${bold} text-anchor="middle" fill="${COLOUR.ink}"`),
             svgText(edgeLeft, 60, fmt(-endHalf, 1), `${small} fill="${COLOUR.rust}"`),
             svgText(edgeRight, 60, fmt(endHalf, 1, true), `${small} fill="${COLOUR.rust}"`));
  strip.innerHTML = parts.join("");
}

function renderReadsList() {
  const current = liveWindow();
  const finished = windows() ? windows().finished[state.channel] : [];
  const rows = [];
  if (current && halfWidth(current.bands, "80") !== null) {
    const word = isLive() ? "Live" : current.ended ? "Ended" : "Last";
    rows.push(readRow(current.read_at, halfWidth(current.bands, "80"), current.now.farthest_up,
                      current.now.farthest_down, current.now.move, word, "live"));
  }
  finished.forEach((window) => {
    const inside = window.grades.ends_inside;
    const word = inside === null ? titleCase(window.status || "pending") : inside ? "Inside" : "Outside";
    rows.push(readRow(window.read_at, window.half_width, window.farthest_up, window.farthest_down, window.end_move,
                      word, inside === null ? "faint" : inside ? "inside" : "outside"));
  });
  const shown = state.showAll ? rows : rows.slice(0, LIST_ROWS_SHOWN);
  const more = rows.length > shown.length
             ? `<button class="show-all" data-show-all>Show All ${fmt(rows.length)}</button>` : "";
  el("reads-list").innerHTML = shown.join("") + more || `<p class="block-note">${esc(emptyReason())}</p>`;
}

function readRow(readAt, half, up, down, endMove, word, kind) {
  const colour = kind === "live" ? COLOUR.blue : kind === "inside" ? COLOUR.green
               : kind === "outside" ? COLOUR.rust : COLOUR.grey;
  const scale = half ? ROW.reach / half : 0;
  const spanLeft = clamp(ROW.centre - (down || 0) * scale, 2, ROW.width - 2);
  const spanRight = clamp(ROW.centre + (up || 0) * scale, 2, ROW.width - 2);
  const bar = [svgRect(ROW.trackLeft, 2, ROW.trackRight - ROW.trackLeft, 6, 3, COLOUR.track)];
  if (up !== null && down !== null && half) {
    const innerLeft = Math.max(spanLeft, ROW.trackLeft), innerRight = Math.min(spanRight, ROW.trackRight);
    if (innerRight > innerLeft) bar.push(svgRect(innerLeft, 3.5, innerRight - innerLeft, 3, 1.5, colour, 0.45));
    if (spanLeft < ROW.trackLeft) bar.push(svgRect(spanLeft, 3.5, ROW.trackLeft - spanLeft, 3, 1.5, colour, 0.6));
    if (spanRight > ROW.trackRight) {
      bar.push(svgRect(ROW.trackRight, 3.5, spanRight - ROW.trackRight, 3, 1.5, colour, 0.6));
    }
  }
  if (endMove !== null && half) {
    const dotX = clamp(ROW.centre + endMove * scale, 5, ROW.width - 5);
    bar.push(`<circle cx="${dotX}" cy="5" r="3.5" fill="${colour}"/>`);
  }
  const travelled = up === null || down === null ? "—" : `${fmt(up, 0, true)} −${fmt(down)}`;
  const span = half ? `${travelled} · ±${fmt(half)}` : "—";
  return `<div class="read-row"><span class="when ${kind === "live" ? "live" : ""}">${clock(readAt)}</span>`
       + `<svg viewBox="0 0 ${ROW.width} 10" aria-hidden="true">${bar.join("")}</svg>`
       + `<span class="span">${span}</span><span class="word ${kind}">${esc(word)}</span></div>`;
}

function renderScores() {
  const score = windows() ? windows().score[state.channel] : null;
  const read = explainedRead();
  const promised = bandPortion();
  const targets = { ends_inside: promised, average_inside: promised,
                    never_left: read ? read.stays_inside_share : null };
  const labels = { ends_inside: "Ends Inside", average_inside: "Average Inside", never_left: "Never Left" };
  el("scores").innerHTML = Object.keys(labels).map((word) => {
    const counts = score && score[word];
    const graded = counts ? counts.graded : 0;
    const good = graded && targets[word] !== null ? counts.yes / graded >= targets[word] : null;
    const colour = good === null ? "faint" : good ? "inside" : "outside";
    const yes = graded ? fmt(counts.yes) : "—";
    return `<div class="score"><b class="${colour}">${yes}<small>/${fmt(graded)}</small></b>`
         + `<span>${labels[word]}</span></div>`;
  }).join("");
}

// ---- Sight: how far, which way, feeds ------------------------------------------------------------------------------

function readLine(read, minutes, short = false) {
  if (!read) return "—";
  const window = short ? "" : ` · ${minutes}-Min Window`;
  const ends = read.ends_at ? ` · Ends ${clock(read.ends_at)}` : "";
  const capped = read.clipped ? " · Capped At The Close" : "";
  const newer = newestReadDiffers(read) ? ` · Newest Read ${clock(range().read_at)}` : "";
  const thin = read.reason && String(read.reason).includes("thin_fit") ? ` · ${titleCase(read.reason)}` : "";
  return `Read At ${clock(read.read_at)}${window}${ends}${capped}${newer}${thin}`;
}

function renderSight() {
  const read = explainedRead();
  const minutes = CHANNELS[state.channel];
  const usable = read && read.status !== "blind" && halfWidth(read.bands, "80") !== null;
  note("sight-note", usable ? "" : emptyReason());
  el("sight-read-line").textContent = readLine(read, minutes, true);
  renderSpeedBars(usable ? read : null);
  el("speed-value").textContent = usable ? fmt(read.speed, 2) : "—";
  renderChain(usable ? read : null, minutes);
  renderRangeBar(usable ? read : null);
  renderSightChecks(usable ? read : null);
}

function renderSpeedBars(read) {
  const bars = read ? [["Fast", "5-Min Memory", read.speed_fast, 0.5], ["Used", "15-Min Memory", read.speed, 0.98],
                       ["Slow", "30-Min Memory", read.speed_slow, 0.5]] : [];
  const most = Math.max(...bars.map((bar) => bar[2] || 0), 0.01);
  const barHeight = (value) => (value === null ? 0 : (SPEED.baseline - SPEED.top) * value / most);
  const parts = bars.map(([name, memory, value, opacity], index) => {
    const x = SPEED.left + index * SPEED.step, height = barHeight(value), y = SPEED.baseline - height;
    const inkOrWhite = opacity > 0.9 ? COLOUR.white : COLOUR.ink;
    const middle = x + SPEED.barWidth / 2;
    return svgRect(x, y, SPEED.barWidth, height, 5, COLOUR.blue, opacity)
         + svgText(middle, SPEED.baseline - 6, fmt(value, 2),
                   `font-size="10" font-weight="700" text-anchor="middle" fill="${inkOrWhite}"`)
         + svgText(middle, 62, name, `font-size="9.5" font-weight="700" text-anchor="middle" fill="${COLOUR.faint}"`)
         + svgText(middle, 72, memory, `font-size="8.5" text-anchor="middle" fill="${COLOUR.faint}"`);
  });
  if (read && read.speed !== null) {
    const usedY = SPEED.baseline - barHeight(read.speed);
    parts.push(svgLine(0, usedY, SPEED.width, usedY, COLOUR.ink, 1.4, `stroke-dasharray="4 3"`));
  }
  parts.push(svgLine(0, SPEED.baseline, SPEED.width, SPEED.baseline, COLOUR.sand, 1));
  el("speed-bars").innerHTML = parts.join("");
}

function dialsApplied(read) {
  return read && read.dials_product ? read.dials_product.applied : null;
}

function renderChain(read, minutes) {
  const bandHalf = read ? halfWidth(read.bands, "80") : null;
  const applied = dialsApplied(read);
  el("chain").innerHTML = `<span class="box">${read ? fmt(read.speed, 2) : "—"}</span>`
    + `<span class="arrow">× √${minutes} →</span><span class="box">${plusMinus(read && read.swing)} Swing</span>`
    + `<span class="arrow">× ${read ? fmt(read.stretch, 2) : "—"} × ${fmt(applied, 2)} Dials →</span>`
    + `<span class="box box-blue">${plusMinus(bandHalf)}</span>`;
}

function renderRangeBar(read) {
  const bandHalf = read ? halfWidth(read.bands, "80") : null;
  el("range-bar").hidden = !bandHalf;
  if (!bandHalf) {
    el("range-bar").innerHTML = "";
    return;
  }
  const scale = RANGE_BAR.reach / bandHalf, averageHalf = halfWidth(read.bands, "average_80");
  const rung = Number(read.rung_points);
  const bold = `font-size="10" font-weight="700" text-anchor="middle"`;
  const parts = [svgRect(RANGE_BAR.centre - bandHalf * scale, 8, 2 * bandHalf * scale, 20, 6, COLOUR.blue, 0.14)];
  if (averageHalf !== null) {
    parts.push(svgRect(RANGE_BAR.centre - averageHalf * scale, 8, 2 * averageHalf * scale, 20, 5, COLOUR.blue, 0.22),
               svgText(RANGE_BAR.centre - averageHalf * scale + 3, 22, `Avg ${plusMinus(averageHalf)}`,
                       `font-size="9.5" fill="${COLOUR.blue}"`));
  }
  const rungLeft = RANGE_BAR.centre - rung * scale;
  const rungRight = clamp(RANGE_BAR.centre + rung * scale, 0, RANGE_BAR.width);
  const dashed = `stroke-dasharray="2 2"`;
  const labelFits = rungRight + 24 < RANGE_BAR.centre + bandHalf * scale;
  const labelX = !Number.isFinite(rung) ? RANGE_BAR.width - 20 : labelFits ? rungRight + 3 : rungRight - 3;
  if (Number.isFinite(rung)) {
    parts.push(svgLine(rungLeft, 4, rungLeft, 32, COLOUR.ink, 1, dashed),
               svgLine(rungRight, 4, rungRight, 32, COLOUR.ink, 1, dashed));
  }
  parts.push(svgLine(RANGE_BAR.centre, 3, RANGE_BAR.centre, 33, COLOUR.ink, 2.5),
             svgText(RANGE_BAR.centre - bandHalf * scale, 45, fmt(read.bands["80"].low),
                     `${bold} fill="${COLOUR.blue}"`),
             svgText(RANGE_BAR.centre, 45, `Read ${fmt(read.price)}`, `${bold} fill="${COLOUR.ink}"`),
             svgText(RANGE_BAR.centre + bandHalf * scale, 45, fmt(read.bands["80"].high),
                     `${bold} fill="${COLOUR.blue}"`),
             svgText(labelX, 22, `±${fmt(rung)}`,
                     `font-size="9.5" text-anchor="${labelFits ? "start" : "end"}" fill="${COLOUR.ink}"`));
  el("range-bar").innerHTML = parts.join("");
}

function optionsWord(read) {
  const check = read ? read.options_check : null;
  return { word: check === "agree" ? "Agree" : check === "differ" ? "Differ" : "—",
           mark: check === "agree" ? "✓" : check === "differ" ? "!" : "·", good: check === "agree" };
}

function newsCheck(read) {
  const block = range();
  const covered = valueOf("does_calendar_cover_day");
  const next = block && block.releases && block.releases.length ? block.releases[0] : null;
  const dial = read.dials ? read.dials.release_window : null;
  const quiet = !next && (!dial || dial.raw === 1);
  if (covered === 0 || covered === false) {
    return { name: "Calendar Doesn't Cover Today", value: "—", good: false };
  }
  const name = next ? `${esc(titleCase(next.name))} At ${clock(next.at)}`
             : quiet ? "No News Left Today" : "News In The Window";
  return { name, value: dial ? `×${fmt(dial.applied, 2)}` : "—", good: quiet };
}

function checkRow(mark, name, value, cls) {
  return `<div class="check"><span class="name">${mark}${name}</span><span class="value ${cls}">${value}</span></div>`;
}

function markIcon(good, glyph) {
  return `<i class="mark ${good ? "mark-green" : "mark-amber"}">${glyph}</i>`;
}

function renderSightChecks(read) {
  if (!read) {
    el("sight-checks").innerHTML = "";
    return;
  }
  const options = optionsWord(read);
  const news = newsCheck(read);
  const share = read.stays_inside_share;
  el("sight-checks").innerHTML =
    checkRow(markIcon(options.good, options.mark), `Options ${options.word}`,
             `${plusMinus(read.options_swing)} vs ${plusMinus(read.swing)}`, options.good ? "inside" : "")
    + checkRow(markIcon(news.good, news.good ? "✓" : "!"), news.name, news.value, news.good ? "inside" : "")
    + checkRow(`<i class="dot-sand"></i>`, `Never Leaves ±${fmt(read.rung_points)}`,
               share === null ? "—" : `${fmt(share * 100)}% At Today's Speed`, "");
}

function leanMeter(lean, width = METER.width) {
  const mid = width / 2;
  const track = svgRect(0, 4, width, 4, 2, COLOUR.rule) + svgLine(mid, 1, mid, 11, COLOUR.faint, 1);
  if (lean === null || lean === undefined) return track;
  const x = clamp(lean * width, 4, width - 4);
  const tilt = lean - 0.5;
  const colour = Math.abs(tilt) <= LEAN_DEAD_ZONE ? COLOUR.grey : tilt < 0 ? COLOUR.rust : COLOUR.blue;
  const fill = Math.abs(x - mid) > 0.5 ? svgRect(Math.min(x, mid), 4, Math.abs(x - mid), 4, 2, colour, 0.45) : "";
  return track + fill + `<circle cx="${x}" cy="6" r="4" fill="${colour}"/>`;
}

/** The call in words: the engine's text once any trust is earned, Coin Flip while the applied trust is 0. */
function directionWords(direction) {
  if (!direction || direction.chance_up_if_moves === null || direction.chance_up_if_moves === undefined) return "—";
  const words = direction.trust_applied === 0 || !direction.text ? "Coin Flip" : titleCase(direction.text);
  return `Up ${fmt(direction.chance_up_if_moves * 100)}% · ${words}`;
}

function renderPush() {
  const block = range(), ch = channel();
  const push = block ? block.push : null;
  const direction = ch ? ch.direction : null;
  const weight = direction ? direction.trust_applied : null;
  note("push-note", block ? "" : emptyReason());
  el("push-weight").textContent = weight === null ? "—" : `Weight ${fmt(weight, weight ? 2 : 0)}`;
  const failed = reasons().includes("push_failed");
  const rows = (push || []).map((entry) => {
    const entryWeight = entry.weight_by_channel ? entry.weight_by_channel[state.channel] : 0;
    const value = entry.reason ? titleCase(entry.reason) : entry.value;
    const label = PUSH_LABELS[entry.name] || titleCase(entry.name);
    return `<div class="push-row"><span>${esc(label)}${entryWeight ? ` · ${fmt(entryWeight, 2)}` : ""}</span>`
         + `<svg viewBox="0 0 ${METER.width} 12" aria-hidden="true">${leanMeter(entry.lean_if_trusted)}</svg>`
         + `<span class="value">${esc(value)}</span></div>`;
  });
  const empty = failed ? "Readings Failed On This Read" : block ? "No Readings On This Read" : "";
  el("push-rows").innerHTML = rows.join("") || (empty ? `<p class="block-note">${empty}</p>` : "");
  el("push-rows").classList.toggle("earned", Boolean(weight));
  el("push-call-label").textContent = weight ? `Counted At Weight ${fmt(weight, 2)} →`
                                             : "Not Counted Until Earned →";
  el("push-call").textContent = directionWords(direction);
}

function renderFeeds() {
  const feeds = state.feed && state.feed.feeds ? state.feed.feeds : {};
  const names = Object.keys(feeds);
  const live = names.filter((name) => feeds[name].status === "live").length;
  note("feeds-note", names.length ? "" : emptyReason());
  el("feeds-pill").textContent = names.length ? `${live} Of ${names.length} Live` : "—";
  el("feeds-pill").className = "pill " + (live === names.length ? "pill-green" : "pill-purple");
  el("feeds-grid").innerHTML = names.map((name) => {
    const feed = feeds[name];
    const age = feed.age_s === null || feed.age_s === undefined ? "" : ago(feed.age_s);
    const standing = feed.status === "live" ? age || "Live" : `${titleCase(feed.status)}${age ? " · " + age : ""}`;
    const words = feed.note ? `${standing} · ${feed.note}` : standing;
    return `<div class="feed"><span class="name"><i class="feed-dot ${esc(feed.status)}"></i>`
         + `${esc(FEED_LABELS[name] || titleCase(name))}</span><span class="age">${esc(words)}</span></div>`;
  }).join("");
  const login = state.feed && state.feed.login;
  if (!login || !login.state) {
    el("login-line").textContent = "Schwab Login · —";
  } else if (login.state === "ok" && login.expires_at) {
    el("login-line").textContent = `Schwab Login Good Until ${dayWord(login.expires_at)} · ${fmt(login.hours_left)} `
                                 + "H Left";
  } else {
    el("login-line").textContent = `Schwab Login ${titleCase(login.state)}`;
  }
  const nightly = state.feed && state.feed.nightly;
  if (!nightly || !nightly.expected) {
    el("nightly-line").textContent = "Nightly · —";
    el("nightly-line").className = "faint-small";
  } else {
    const done = Boolean(nightly.done_for) && nightly.done_for >= nightly.expected;
    el("nightly-line").textContent = done ? `Nightly Done For ${dayWord(nightly.expected)}`
                                          : `Nightly Not Done For ${dayWord(nightly.expected)}`
                                            + `${nightly.done_for ? ` · Last ${dayWord(nightly.done_for)}` : ""}`;
    el("nightly-line").className = done ? "faint-small" : "faint-small outside";
  }
}

// ---- Steps: the build-up, the checks, the answer -------------------------------------------------------------------

function renderSteps() {
  const read = explainedRead();
  const minutes = CHANNELS[state.channel];
  const usable = read && read.status !== "blind" && halfWidth(read.bands, "80") !== null;
  el("steps-sub").textContent = readLine(read, minutes);
  note("steps-note", usable ? "" : emptyReason());
  renderStepBars(usable ? read : null, minutes);
  renderStepChecks(usable ? read : null, minutes);
}

function tag(kind) {
  return `<span class="tag tag-${kind}">${titleCase(kind)}</span>`;
}

function stepBar(number, name, kind, value, sub, svg) {
  return `<div class="step"><div class="step-head"><span class="name">${number} · ${name}${tag(kind)}</span>`
       + `<span class="value">${value}</span></div><span class="faint-small">${sub}</span>${svg}</div>`;
}

function renderStepBars(read, minutes) {
  if (!read) {
    el("steps-bars").innerHTML = "";
    return;
  }
  const bandHalf = halfWidth(read.bands, "80"), averageHalf = halfWidth(read.bands, "average_80");
  const scale = RANGE_BAR.reach / bandHalf;
  const edge = (reach) => (RANGE_BAR.centre - reach * scale).toFixed(1);
  const farEdge = (reach) => (RANGE_BAR.centre + reach * scale).toFixed(1);
  const rect = (reach, opacity) => svgRect(edge(reach), 8, (2 * reach * scale).toFixed(1), 14, 5, COLOUR.blue,
                                           opacity);
  const centreLine = svgLine(RANGE_BAR.centre, 5, RANGE_BAR.centre, 25, COLOUR.ink, 1.6);
  const funnel = (from, to) =>
    `<path d="M${edge(from)} 0 L${edge(to)} 8 M${farEdge(from)} 0 L${farEdge(to)} 8" stroke="${COLOUR.sand}" `
    + `stroke-width="1" stroke-dasharray="2 2" fill="none"/>`;
  const bar = (inner, height = 26) =>
    `<svg viewBox="0 0 ${RANGE_BAR.width} ${height}" aria-hidden="true">${inner}</svg>`;
  const bold = `font-size="10" font-weight="700" text-anchor="middle" fill="${COLOUR.blue}"`;
  const speed = read.speed, swing = read.swing, share = read.stays_inside_share, windowsCount = read.fit_windows;
  const neverSub = share === null ? "—"
    : windowsCount ? `${fmt(Math.round(share * windowsCount))} Of ${fmt(windowsCount)} Past Windows At Today's Speed`
    : `${fmt(share * 100)}% Of Past Windows At Today's Speed`;
  const rung = Number(read.rung_points);
  const promised = bandPortion();
  const bandName = promised === null ? "End Band" : `End Band · ${fmt(promised * 10)} In 10`;
  const dashedBox = Number.isFinite(rung) ? svgRect(edge(rung), 8, (2 * rung * scale).toFixed(1), 14, 5, "none", 1,
                                                    `stroke="${COLOUR.ink}" stroke-width="1.3" stroke-dasharray="3 2"`)
                                          : "";
  el("steps-bars").innerHTML =
    stepBar(1, "Speed", "today", fmt(speed, 2), "One Minute's Typical Move · 15-Min Memory",
            bar(rect(speed, 0.55) + centreLine))
    + stepBar(2, "Swing", "today", plusMinus(swing), `Over ${minutes} Minutes · ${fmt(speed, 2)} × √${minutes}`,
              bar(funnel(speed, swing) + rect(swing, 0.35) + centreLine))
    + stepBar("3–4", bandName, "history", plusMinus(bandHalf),
              `Stretched By Past Days · ${fmt(swing, 1)} × ${fmt(read.stretch, 2)} × ${fmt(dialsApplied(read), 2)} `
              + "Dials",
              bar(funnel(swing, bandHalf) + rect(bandHalf, 0.18) + centreLine
                  + svgText(edge(bandHalf), 34, fmt(read.bands["80"].low), bold)
                  + svgText(farEdge(bandHalf), 34, fmt(read.bands["80"].high), bold), 36))
    + stepBar("4b", "Average Band", "history", plusMinus(averageHalf),
              `Where The Average Lands · ${fmt(swing, 1)} × ${fmt(read.average_stretch, 2)}`,
              bar((averageHalf === null ? "" : rect(averageHalf, 0.3)) + centreLine))
    + stepBar(5, `Never Leaves ±${fmt(rung)}`, "history", share === null ? "—" : `${fmt(share * 100)}%`,
              neverSub, bar(dashedBox + centreLine));
}

function dialGauge(applied) {
  const angle = applied === null ? 0 : clamp((applied - 1) * 150, -90, 90) * Math.PI / 180;
  const x = (28 + 17 * Math.sin(angle)).toFixed(1), y = (28 - 17 * Math.cos(angle)).toFixed(1);
  return `<path d="M8 28 A 20 20 0 0 1 48 28" fill="none" stroke="${COLOUR.rule}" stroke-width="5" `
       + `stroke-linecap="round"/>`
       + svgLine(28, 28, x, y, COLOUR.ink, 2, `stroke-linecap="round"`) + `<circle cx="28" cy="28" r="3" `
       + `fill="${COLOUR.ink}"/>`;
}

function stepCheck(number, name, kind, sub, svg, value) {
  return `<div class="step-check"><div class="text"><span class="name">${number} · ${name}${tag(kind)}</span>`
       + `<span class="sub">${sub}</span></div>${svg}<span class="value">${value}</span></div>`;
}

function dialsLine(read) {
  const dials = read.dials || {};
  const names = Object.keys(dials);
  const earned = names.filter((name) => dials[name].weight);
  const moving = names.filter((name) => dials[name].raw !== 1 && dials[name].raw !== null);
  const raw = read.dials_product ? read.dials_product.raw : null;
  if (earned.length) return `Earned: ${earned.map((name) => DIAL_LABELS[name]).join(" · ")}`;
  if (moving.length) return `${moving.map((name) => DIAL_LABELS[name]).join(" · ")} · Raw ×${fmt(raw, 2)}`;
  return raw !== null && raw !== 1 ? `Five Dials · Raw ×${fmt(raw, 2)}` : "All Five Dials At Rest";
}

function renderStepChecks(read, minutes) {
  if (!read) {
    el("steps-checks").innerHTML = "";
    return;
  }
  const options = optionsWord(read);
  const most = Math.max(read.options_swing || 0, read.swing || 0, 0.01);
  const used = read.minutes_left === null ? minutes : Math.min(minutes, read.minutes_left);
  const chance = read.chance_up_if_moves;
  const trust = read.trust_applied === null ? "—" : fmt(read.trust_applied, read.trust_applied ? 2 : 0);
  const applied = dialsApplied(read);
  el("steps-checks").innerHTML =
    stepCheck(6, "Options", "check", `${fmt(read.straddle, 1)} × √(${fmt(used)} ÷ ${fmt(read.minutes_left)})`,
              `<svg viewBox="0 0 56 34" role="img" aria-label="Options swing next to tape swing">`
              + svgRect(3, 7, (50 * (read.options_swing || 0) / most).toFixed(1), 8, 3, COLOUR.greenDeep, 0.55)
              + svgRect(3, 19, (50 * (read.swing || 0) / most).toFixed(1), 8, 3, COLOUR.blue, 0.45) + `</svg>`,
              `${plusMinus(read.options_swing)} ${options.mark}`)
    + stepCheck(7, "Dials", "earned", dialsLine(read),
                `<svg viewBox="0 0 56 34" role="img" aria-label="Dial at times ${fmt(applied, 2)}">`
                + `${dialGauge(applied)}</svg>`, `×${fmt(applied, 2)}`)
    + stepCheck(8, "Direction", "earned", chance === null ? "—" : `50 + ${trust} × (Lean − 50)`,
                `<svg viewBox="0 0 56 34" role="img" aria-label="Direction meter"><g transform="translate(3,11)">`
                + `${leanMeter(chance, METER.small)}</g>`
                + svgText(3, 32, "Down", `font-size="7" fill="${COLOUR.rust}"`)
                + svgText(53, 32, "Up", `font-size="7" text-anchor="end" fill="${COLOUR.blue}"`) + `</svg>`,
                chance === null ? "—" : `Up ${fmt(chance * 100)}%`);
}

function renderAnswer() {
  const read = explainedRead();
  const band = read ? read.bands["80"] : null, chance = read ? read.chance_up_if_moves : null;
  const between = band && band.half_width !== null ? `${fmt(band.low)} – ${fmt(band.high)}` : "—";
  el("answer").innerHTML =
    `<div><span>Ends Between</span><span>${between}</span></div>`
    + `<div><span>Average Within</span><span>${plusMinus(read && halfWidth(read.bands, "average_80"))}</span></div>`
    + `<div><span>Direction</span><span>${chance === null ? "—" : `Up ${fmt(chance * 100)}%`}</span></div>`;
}

// ---- tabs, switches, timers ---------------------------------------------------------------------------------------

function showTab(name) {
  remember("tab", name);
  document.querySelectorAll(".view").forEach((view) => { view.hidden = view.id !== `view-${name}`; });
  document.querySelectorAll(".tabs button").forEach((button) => {
    if (button.dataset.tab === name) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  window.scrollTo(0, 0);
}

document.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-tab]");
  const switchButton = event.target.closest("[data-channel]");
  if (tab) showTab(tab.dataset.tab);
  if (switchButton) {
    state.channel = switchButton.dataset.channel;
    state.showAll = false;
    remember("channel", state.channel);
    render();
  }
  if (event.target.closest("[data-show-all]")) {
    state.showAll = true;
    render();
  }
});
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadFeed(); });
state.channel = CHANNELS[remembered("channel", state.channel)] ? remembered("channel", state.channel) : state.channel;
showTab(["read", "sight", "steps"].includes(remembered("tab", "read")) ? remembered("tab", "read") : "read");
loadFeed();
